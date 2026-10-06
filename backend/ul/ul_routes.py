import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from neo4j.exceptions import ServiceUnavailable as Neo4jServiceUnavailable

from ul import neo4j_service, ul_service
from ul.document_extractor import is_supported_upload_filename

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ul", tags=["UL Knowledge Graph"])


def _nonzero_uploads(files: list[UploadFile] | None) -> list[UploadFile]:
    """Drop empty multipart file parts (filename-less placeholders from some clients)."""
    return [
        upload
        for upload in (files or [])
        if upload is not None and str(upload.filename or "").strip()
    ]


async def _read_upload_files(files: list[UploadFile] | None) -> list[tuple[str, bytes]]:
    uploads_in = _nonzero_uploads(files)
    if not uploads_in:
        raise HTTPException(
            status_code=400,
            detail="At least one company/product document is required (PDF, DOCX, or email)",
        )

    uploads: list[tuple[str, bytes]] = []
    for upload in uploads_in:
        if not is_supported_upload_filename(upload.filename):
            raise HTTPException(
                status_code=400,
                detail="All files must be PDF, DOCX, or email (.eml, .msg) documents",
            )

        content = await upload.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"Empty file: {upload.filename}")

        uploads.append((upload.filename, content))

    return uploads


@router.post("/upload")
async def upload_documents(
    files: list[UploadFile] | None = File(
        default=None,
        description=(
            "Company/product source documents: PDF, DOCX, or email (.eml, .msg). "
            "Examples: company_supplier.pdf, product_spec.docx, certification_request.eml. "
            "A single product document is enough — company name is extracted from that file. "
            "With multiple files, name the company/supplier file with 'company' or 'supplier'. "
            "Standards and certifications are backend reference files — do not upload them."
        ),
    ),
):
    """
    Upload company/supplier/product documents (PDF, DOCX, or email).
    """
    try:
        uploads = await _read_upload_files(_nonzero_uploads(files))
        result = ul_service.save_uploaded_files(uploads)
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Upload failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/process")
async def process_documents(
    files: list[UploadFile] | None = File(
        default=None,
        description="Company/product PDF, DOCX, or email files (>= 1) for extraction, chunking, and component extraction.",
    ),
    document_id: str | None = Form(
        default=None,
        description="Optional document_id from a prior /api/ul/upload call (company/product documents).",
    ),
    target_product_name: str | None = Form(
        default=None,
        description=(
            "Optional product name(s) to keep. Separate multiple products with commas "
            "(or 'and' / '/'). When set, only those products and their connected "
            "nodes/relationships are mapped and shown. Products from the same company "
            "share one company node."
        ),
    ),
):
    """
    Process company/product documents through stages 1–8, then ingest into local Neo4j:

    page extraction → semantic chunking → index → components →
    product mapping → normalization → standards/clauses/certifications/tests →
    structured graph-ready data → hierarchy → CSV → Neo4j.

    Accepted uploads: PDF, DOCX, and email (.eml, .msg). Every supported file in
    backend/documents/ (standards, certifications, tests, and any other catalogues)
    is loaded automatically and used for both graph extraction and chatbot retrieval.

    Provide files directly, or a document_id from a prior /upload call.
    Optional target_product_name keeps only those product subgraph(s).
    Multiple products may be listed in one field.
    """
    try:
        # Ignore empty multipart file parts so python-multipart does not warn about
        # trailing boundary noise from placeholder `files` fields.
        real_files = _nonzero_uploads(files)
        document_id = (document_id or "").strip() or None
        target_product_name = (target_product_name or "").strip() or None

        if real_files:
            uploads = await _read_upload_files(real_files)
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_paths: list[Path] = []
                for filename, content in uploads:
                    path = Path(tmp_dir) / Path(filename).name
                    path.write_bytes(content)
                    tmp_paths.append(path)
                result = ul_service.process_documents(
                    file_paths=tmp_paths,
                    target_product_name=target_product_name,
                )
        elif document_id:
            result = ul_service.process_documents(
                document_id=document_id,
                target_product_name=target_product_name,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="Provide company/product PDF, DOCX, or email files, or a document_id from /api/ul/upload.",
            )

        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Processing failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/graphs")
async def list_graphs():
    """List the global UL graph plus contributing documents."""
    try:
        graphs = neo4j_service.list_graphs()
        return {"status": "ok", "graphs": graphs}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Neo4jServiceUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Neo4j is not reachable. Start Neo4j Desktop (or Docker) and use "
                "NEO4J_URI=bolt://127.0.0.1:7687 for a local single instance."
            ),
        ) from exc
    except Exception as exc:
        logger.exception("Failed to list graphs")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/graph")
async def get_graph(graph_id: str | None = None):
    """Return the global canonical UL graph (not filtered by document)."""
    try:
        summary = neo4j_service.get_graph_summary(graph_id=graph_id)
        return {
            "status": "ok",
            **summary,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to fetch graph")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/graphs/{graph_id}")
async def delete_graph(graph_id: str):
    """Delete one document's evidence, or a legacy isolated graph. The global UL graph is not deleted."""
    try:
        deleted = neo4j_service.delete_graph(graph_id)
        return {"status": "ok", "graph_id": graph_id, "deleted_nodes": deleted}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete graph %s", graph_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
