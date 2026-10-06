import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config
from ul.csv_generator import write_triplets_csv
from ul.document_chunking import create_semantic_chunks
from ul.document_extractor import (
    is_supported_upload_filename,
    extract_pages_from_document,
)
from ul.description_text import normalize_description
from ul.hierarchy import enforce_hierarchy
from ul.knowledge_graph.ingest import GLOBAL_GRAPH_ID
from ul.knowledge_graph.provenance import compute_content_hash
from ul.lightrag_extractor import (
    _TYPE_TO_BUCKET,
    extract_graph_from_chunks,
    index_chunks_with_lightrag,
    is_company_source_file,
)
from ul.neo4j_service import ingest_triplets
from ul.product_normalization import apply_product_identity_to_triplets

logger = logging.getLogger(__name__)


_REFERENCE_SKIP_NAMES = {"readme.md", "readme.txt"}


def get_reference_document_paths() -> list[Path]:
    """Return every supported file in backend/documents/ (PDF, DOCX, email).

    Named catalogues are listed first when they exist. Missing names are skipped;
    other files in the folder are still included.
    """
    folder = config.REFERENCE_DOCUMENTS_DIR
    if not folder.is_dir():
        return []

    paths: list[Path] = []
    seen: set[str] = set()
    for name in config.REFERENCE_DOCUMENT_NAMES:
        path = folder / name
        key = path.name.lower()
        if path.is_file() and is_supported_upload_filename(path.name) and key not in seen:
            paths.append(path)
            seen.add(key)
    for path in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        key = path.name.lower()
        if key in seen or key in _REFERENCE_SKIP_NAMES:
            continue
        if is_supported_upload_filename(path.name):
            paths.append(path)
            seen.add(key)
    return paths


def save_uploaded_files(files: list[tuple[str, bytes]]) -> dict:
    """
    Save uploaded source documents locally (PDF, DOCX, or email).
    files: list of (filename, file_bytes) tuples.
    """
    document_id = str(uuid.uuid4())
    upload_dir = config.UPLOAD_DIR / document_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_files: list[str] = []
    for filename, content in files:
        safe_name = Path(filename).name
        if not is_supported_upload_filename(safe_name):
            raise ValueError(
                f"Unsupported file type: {safe_name}. "
                "Accepted types: PDF (.pdf), DOCX (.docx), Email (.eml, .msg)."
            )

        file_path = upload_dir / safe_name
        file_path.write_bytes(content)
        saved_files.append(safe_name)
        logger.info("Saved upload: %s", file_path)

    return {"document_id": document_id, "files": saved_files}


def _resolve_source_paths(document_id: str | None, file_paths: list[Path] | None) -> list[Path]:
    if file_paths:
        return file_paths

    if not document_id:
        raise ValueError("Either source documents or document_id must be provided")

    upload_dir = config.UPLOAD_DIR / document_id
    if not upload_dir.exists():
        raise ValueError(f"Document not found: {document_id}")

    sources = sorted(
        path
        for path in upload_dir.iterdir()
        if path.is_file() and is_supported_upload_filename(path.name)
    )
    if not sources:
        raise ValueError(
            f"No PDF, DOCX, or email files found for document_id: {document_id}"
        )

    return sources


def _stamp_project_id(chunks: list[dict], project_id: str) -> None:
    """Attach current project provenance to chunks created for this run."""
    project_id = str(project_id or "").strip()
    if not project_id:
        return
    for chunk in chunks:
        chunk["project_id"] = project_id
        metadata = chunk.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["project_id"] = project_id
        chunk["metadata"] = metadata


def _process_single_document(
    source_path: Path,
    *,
    chunk_start_index: int,
    chunk_id_prefix: str = "chunk",
) -> tuple[list[dict], int, int, int]:
    """
    Extract and chunk one source document for indexing and entity extraction.

    PDF extraction is unchanged. DOCX and email are normalized to the same
    page dicts (source_file, page_number, text) before existing chunking.

    Returns (chunks, page_count, next_chunk_index, chunk_count).
    """
    pages = extract_pages_from_document(str(source_path))

    chunks = create_semantic_chunks(
        pages,
        start_index=chunk_start_index,
        chunk_id_prefix=chunk_id_prefix,
    )

    next_index = chunk_start_index + len(chunks)

    return chunks, len(pages), next_index, len(chunks)


def _relationships_to_triplets(relationships: list[dict]) -> list[dict]:
    """Convert graph-ready relationships into Neo4j triplet records."""
    triplets: list[dict] = []
    for rel in relationships:
        source = str(rel.get("source_node") or rel.get("source") or "").strip()
        target = str(
            rel.get("target_node")
            or rel.get("target")
            or rel.get("destination")
            or rel.get("destination_node")
            or ""
        ).strip()
        relationship = str(rel.get("relationship") or "").strip()
        source_type = str(rel.get("source_type") or "entity").strip().lower()
        target_type = str(
            rel.get("target_type") or rel.get("destination_type") or "entity"
        ).strip().lower()
        description = normalize_description(
            str(rel.get("description") or rel.get("evidence") or "")
        )
        keywords = str(rel.get("keywords") or "").strip()

        if not source or not target or not relationship:
            logger.warning("Skipping incomplete relationship: %s", rel)
            continue

        triplet = {
            "source_node": source,
            "source_type": source_type,
            "relationship": relationship,
            "destination_node": target,
            "destination_type": target_type,
        }
        if description:
            triplet["description"] = description
        if keywords:
            triplet["keywords"] = keywords
        for key in ("project_id", "source_file", "page_range", "chunk_id"):
            value = rel.get(key)
            if value not in (None, ""):
                triplet[key] = value
        triplets.append(triplet)
    return triplets


def _entity_description_key(entity_type: str, name: str) -> str:
    from ul.product_normalization import norm_key
    from ul.knowledge_graph.labels import canonical_entity_type

    return f"{canonical_entity_type(entity_type)}:{norm_key(name)}"


def _description_from_entity_item(item: dict) -> str:
    for key in ("evidence", "component_description", "description"):
        value = normalize_description(str(item.get(key) or ""))
        if value:
            return value
    return ""


def _entity_names_from_item(item: dict) -> list[str]:
    names: list[str] = []
    for key in ("name", "company_name", "component_name", "canonical_name"):
        value = str(item.get(key) or "").strip()
        if value:
            names.append(value)
    for alias in item.get("aliases") or []:
        alias_name = str(alias).strip()
        if alias_name:
            names.append(alias_name)
    return names


def _build_entity_description_map(graph_data: dict) -> dict[str, str]:
    """Map normalized entity keys to LightRAG descriptions from graph_data buckets."""
    mapping: dict[str, str] = {}
    bucket_types = {
        bucket: entity_type for entity_type, bucket in _TYPE_TO_BUCKET.items()
    }
    bucket_types["normalized_components"] = "component"
    bucket_types["companies"] = "company"

    for bucket, entity_type in bucket_types.items():
        for item in graph_data.get(bucket) or []:
            if not isinstance(item, dict):
                continue
            description = _description_from_entity_item(item)
            if not description:
                continue
            for name in _entity_names_from_item(item):
                key = _entity_description_key(entity_type, name)
                existing = mapping.get(key, "")
                if not existing:
                    mapping[key] = description
                elif description not in existing:
                    shorter = description if len(description) <= len(existing) else existing
                    mapping[key] = shorter
    return mapping


def _unique_entity_count(graph_data: dict) -> int:
    names: set[str] = set()
    for key, items in graph_data.items():
        if key == "relationships" or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (
                item.get("name")
                or item.get("company_name")
                or item.get("canonical_name")
                or item.get("component_name")
            )
            if name:
                names.add(str(name).strip())
    return len(names)


def _filter_triplets_for_project(triplets: list[dict], project_id: str) -> list[dict]:
    """Keep only current-run triplets before CSV/Neo4j write."""
    current = str(project_id or "").strip()
    if not current:
        return triplets
    kept: list[dict] = []
    for triplet in triplets:
        rel_project = str(triplet.get("project_id") or "").strip()
        if rel_project and rel_project != current:
            logger.warning(
                "Dropping triplet from project %s while writing project %s: "
                "%s -[%s]-> %s",
                rel_project,
                current,
                triplet.get("source_node"),
                triplet.get("relationship"),
                triplet.get("destination_node"),
            )
            continue
        if not rel_project:
            triplet = {**triplet, "project_id": current}
        kept.append(triplet)
    return kept


def process_documents(
    document_id: str | None = None,
    file_paths: list[Path] | None = None,
    target_product_name: str | None = None,
) -> dict:
    """
    Run source-document ingestion through LightRAG prompt extraction, then local Neo4j.

    Flow:
      PDF/DOCX/email uploads plus backend/documents catalogues
      → page extract → semantic chunks
      → LightRAG native indexing (chunks + KG for chatbot retrieval)
      → LightRAG prompt.py entity/relationship extraction → CSV → Neo4j.

    Standards, certifications, tests, and other typed entities are extracted from
    both the uploaded files and every supported file in backend/documents/.
    """
    uploaded_paths = _resolve_source_paths(document_id, file_paths)
    target_product = (target_product_name or "").strip() or None
    if not uploaded_paths:
        raise ValueError("At least one company/product document is required")

    run_id = document_id or str(uuid.uuid4())
    logger.info(
        "Processing %d source document(s) via LightRAG prompts "
        "(run_id=%s, target_product=%s)",
        len(uploaded_paths),
        run_id,
        target_product or "(all)",
    )

    all_chunks: list[dict] = []
    documents_processed = 0
    pages_extracted = 0
    chunks_created = 0
    warnings: list[str] = []
    chunk_start_index = 1

    for source_path in uploaded_paths:
        try:
            chunks, page_count, chunk_start_index, chunk_count = _process_single_document(
                source_path,
                chunk_start_index=chunk_start_index,
            )
            _stamp_project_id(chunks, run_id)
            all_chunks.extend(chunks)
            if is_company_source_file(source_path.name):
                logger.info(
                    "Indexed company-oriented document for LightRAG: %s",
                    source_path.name,
                )
            pages_extracted += page_count
            chunks_created += chunk_count
            documents_processed += 1
        except Exception as exc:
            msg = f"Failed to process {source_path.name}: {exc}"
            logger.warning(msg)
            warnings.append(msg)

    if documents_processed == 0:
        raise ValueError("No documents were successfully processed")

    # backend/documents catalogues: index for chatbot AND extract into the graph
    # (standards, certifications, tests, and other typed entities found there).
    reference_chunks: list[dict] = []
    reference_paths = get_reference_document_paths()
    for ref_path in reference_paths:
        try:
            prefix = f"ref_{ref_path.stem}"
            ref_chunks, page_count, _next_index, chunk_count = _process_single_document(
                ref_path,
                chunk_start_index=1,
                chunk_id_prefix=prefix,
            )
            _stamp_project_id(ref_chunks, run_id)
            reference_chunks.extend(ref_chunks)
            pages_extracted += page_count
            chunks_created += chunk_count
            documents_processed += 1
            logger.info(
                "Processed reference document for extraction and LightRAG: %s (%d chunk(s))",
                ref_path.name,
                len(ref_chunks),
            )
        except Exception as exc:
            msg = f"Failed to process reference {ref_path.name}: {exc}"
            logger.warning(msg)
            warnings.append(msg)

    # Index all source + reference chunks into LightRAG for chatbot retrieval.
    try:
        index_chunks_with_lightrag(all_chunks + reference_chunks, run_id=run_id)
    except Exception as exc:
        msg = f"LightRAG indexing failed: {exc}"
        logger.warning(msg)
        warnings.append(msg)

    graph_data: dict = {
        "companies": [],
        "products": [],
        "models": [],
        "components": [],
        "normalized_components": [],
        "standards": [],
        "clauses": [],
        "certifications": [],
        "tests": [],
        "relationships": [],
    }

    try:
        graph_data = extract_graph_from_chunks(
            all_chunks + reference_chunks,
            target_product_name=target_product,
            project_id=run_id,
        )
    except Exception as exc:
        msg = f"LightRAG extraction failed: {exc}"
        logger.warning(msg)
        warnings.append(msg)
    else:
        if target_product and not (graph_data.get("products") or []):
            warnings.append(
                f"No products or components matched target product(s) '{target_product}'."
            )

    all_components = graph_data.get("components") or []
    all_companies = graph_data.get("companies") or []

    extra_companies = [
        str(c.get("name") or c.get("company_name") or "").strip()
        for c in all_companies
        if str(c.get("name") or c.get("company_name") or "").strip()
    ]
    all_triplets = enforce_hierarchy(
        apply_product_identity_to_triplets(
            _relationships_to_triplets(graph_data.get("relationships") or []),
            graph_data.get("products") or [],
            graph_data.get("models") or [],
        ),
        extra_companies=extra_companies,
    )
    all_triplets = _filter_triplets_for_project(all_triplets, run_id)

    csv_filename = f"triplets_{run_id}.csv"
    csv_file = write_triplets_csv(all_triplets, filename=csv_filename)

    graph_id = GLOBAL_GRAPH_ID
    ingestion_id = f"ing_{uuid.uuid4().hex[:12]}"
    source_files = [path.name for path in uploaded_paths]
    for path in reference_paths:
        if path.name not in source_files:
            source_files.append(path.name)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    content_hash = compute_content_hash([*uploaded_paths, *reference_paths])

    neo4j_ingested = False
    try:
        ingest_triplets(
            all_triplets,
            graph_id=graph_id,
            source_files=source_files,
            document_id=run_id,
            created_at=created_at,
            entity_descriptions=_build_entity_description_map(graph_data),
            content_hash=content_hash,
            ingestion_id=ingestion_id,
            file_name=source_files[0] if source_files else "",
        )
        neo4j_ingested = True
    except Exception as exc:
        msg = f"Neo4j ingestion failed: {exc}"
        logger.warning(msg)
        warnings.append(msg)

    entities_extracted = _unique_entity_count(graph_data)

    return {
        "status": "completed",
        "document_id": run_id,
        "graph_id": graph_id,
        "source_files": source_files,
        "created_at": created_at,
        "target_product_name": target_product,
        "uploaded_documents": len(uploaded_paths),
        "reference_documents": len(reference_paths),
        "total_documents_processed": documents_processed,
        "documents_processed": documents_processed,
        "pages_extracted": pages_extracted,
        "chunks_created": chunks_created,
        "components_extracted": len(all_components),
        "entities_extracted": entities_extracted,
        "relationships_extracted": len(all_triplets),
        "triplets_created": len(all_triplets),
        "csv_file": csv_file,
        "neo4j_ingested": neo4j_ingested,
        "reference_files": [p.name for p in reference_paths],
        "extraction_engine": "lightrag.prompt",
        "vector_storage": "LightRAG",
        "components": all_components,
        "companies": all_companies,
        "products": graph_data.get("products", []),
        "models": graph_data.get("models", []),
        "normalized_components": graph_data.get("normalized_components", []),
        "standards": graph_data.get("standards", []),
        "clauses": graph_data.get("clauses", []),
        "certifications": graph_data.get("certifications", []),
        "tests": graph_data.get("tests", []),
        "relationships": graph_data.get("relationships", []),
        "graph_ready": {
            "companies": all_companies,
            "products": graph_data.get("products", []),
            "models": graph_data.get("models", []),
            "components": graph_data.get("normalized_components", []),
            "standards": graph_data.get("standards", []),
            "clauses": graph_data.get("clauses", []),
            "certifications": graph_data.get("certifications", []),
            "tests": graph_data.get("tests", []),
            "relationships": graph_data.get("relationships", []),
        },
        "warnings": warnings,
    }


def save_and_process_uploads(files: list[tuple[str, bytes]]) -> dict:
    """Save uploads then run the ingestion pipeline on them."""
    upload_result = save_uploaded_files(files)
    document_id = upload_result["document_id"]
    source_paths = [config.UPLOAD_DIR / document_id / name for name in upload_result["files"]]

    process_result = process_documents(document_id=document_id, file_paths=source_paths)
    process_result["document_id"] = document_id
    return process_result
