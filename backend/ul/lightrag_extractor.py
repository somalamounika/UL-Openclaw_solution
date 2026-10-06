"""LightRAG-prompt-based entity/relationship extraction for the UL pipeline.

Uses ``lightrag.prompt.PROMPTS`` (not ``component_extractor``) to extract a
typed knowledge graph from all uploaded document chunks, then maps results
into Neo4j-ready triplets and summary entity lists.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

import config
from lightrag import LightRAG
from lightrag.base import StoragesStatus
from lightrag.llm.openai import azure_openai_embed, openai_complete_if_cache, openai_embed
from lightrag.prompt import PROMPTS
from lightrag.utils import EmbeddingFunc

from ul.company_mentions import (
    extract_company_mentions,
    is_plausible_company_name,
    text_supports_applicant_role,
    text_supports_manufacturer_role,
)
from ul.description_text import merge_description_field, normalize_description
from ul.extraction_prompts import (
    overlay_entity_types_guidance,
    overlay_json_system_prompt,
)
from ul.hierarchy import (
    REL_ALIASES,
    TYPE_PAIR_REL,
    UL_ROOT_NAME,
    types_are_related,
)
from ul.knowledge_graph.normalization import (
    entity_names_equivalent,
    preferred_company_name,
    preferred_entity_name,
)
from ul.llm_client import chat_json

logger = logging.getLogger(__name__)

_LIGHTRAG_INSTANCE: LightRAG | None = None
_LIGHTRAG_LOCK = threading.Lock()
_LIGHTRAG_LOOP: asyncio.AbstractEventLoop | None = None
_LIGHTRAG_THREAD: threading.Thread | None = None
_LIGHTRAG_LOOP_READY = threading.Event()


def _lightrag_chat_model() -> str:
    use_azure = bool(config.AZURE_OPENAI_API_KEY and config.AZURE_OPENAI_ENDPOINT)
    if use_azure:
        model = config.AZURE_OPENAI_CHAT_DEPLOYMENT
        if not model:
            raise ValueError("AZURE_OPENAI_CHAT_DEPLOYMENT is not configured")
        return model
    if not config.LLM_API_KEY:
        raise ValueError("LLM_API_KEY is not configured")
    return config.LLM_MODEL


async def _lightrag_llm_model_func(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> str:
    """LLM adapter for LightRAG indexing (not used by UL extraction)."""
    if history_messages is None:
        history_messages = []
    model = _lightrag_chat_model()
    if str(model).lower().startswith("gpt-5"):
        kwargs.pop("temperature", None)

    use_azure = bool(config.AZURE_OPENAI_API_KEY and config.AZURE_OPENAI_ENDPOINT)
    if use_azure:
        return await openai_complete_if_cache(
            model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            api_key=config.AZURE_OPENAI_API_KEY,
            base_url=config.AZURE_OPENAI_ENDPOINT,
            api_version=config.AZURE_OPENAI_API_VERSION,
            use_azure=True,
            azure_deployment=model,
            **kwargs,
        )

    return await openai_complete_if_cache(
        model,
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
        **kwargs,
    )


def _lightrag_embedding_func() -> EmbeddingFunc:
    """Embedding adapter matching this project's Azure OpenAI / OpenAI config."""
    embedding_dim = config.AZURE_OPENAI_EMBEDDING_DIMENSIONS
    azure_key = config.AZURE_OPENAI_EMBEDDING_API_KEY
    azure_endpoint = config.AZURE_OPENAI_EMBEDDING_ENDPOINT
    azure_deployment = config.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    is_ada = str(azure_deployment or "").lower().startswith("text-embedding-ada")

    if azure_key and azure_endpoint and azure_deployment:
        return EmbeddingFunc(
            embedding_dim=embedding_dim,
            max_token_size=8192,
            model_name=azure_deployment,
            send_dimensions=not is_ada,
            func=partial(
                azure_openai_embed.func,
                model=azure_deployment,
                base_url=azure_endpoint,
                api_key=azure_key,
                api_version=config.AZURE_OPENAI_EMBEDDING_API_VERSION,
            ),
        )

    if not config.LLM_API_KEY:
        raise ValueError("Embedding credentials are not configured for LightRAG")

    return EmbeddingFunc(
        embedding_dim=embedding_dim,
        max_token_size=8192,
        model_name="text-embedding-3-small",
        send_dimensions=True,
        func=partial(
            openai_embed.func,
            model="text-embedding-3-small",
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
        ),
    )


def _lightrag_loop_main() -> None:
    global _LIGHTRAG_LOOP
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _LIGHTRAG_LOOP = loop
    _LIGHTRAG_LOOP_READY.set()
    loop.run_forever()


def _ensure_lightrag_loop() -> asyncio.AbstractEventLoop:
    """Return the long-lived loop LightRAG storages are bound to."""
    global _LIGHTRAG_THREAD
    loop = _LIGHTRAG_LOOP
    thread = _LIGHTRAG_THREAD
    if loop is not None and not loop.is_closed() and thread is not None and thread.is_alive():
        return loop

    with _LIGHTRAG_LOCK:
        loop = _LIGHTRAG_LOOP
        thread = _LIGHTRAG_THREAD
        if loop is not None and not loop.is_closed() and thread is not None and thread.is_alive():
            return loop
        _LIGHTRAG_LOOP_READY.clear()
        _LIGHTRAG_THREAD = threading.Thread(
            target=_lightrag_loop_main,
            name="lightrag-loop",
            daemon=True,
        )
        _LIGHTRAG_THREAD.start()

    if not _LIGHTRAG_LOOP_READY.wait(timeout=10):
        raise RuntimeError("Timed out starting LightRAG event loop")
    if _LIGHTRAG_LOOP is None or _LIGHTRAG_LOOP.is_closed():
        raise RuntimeError("LightRAG event loop failed to start")
    return _LIGHTRAG_LOOP


def _run_on_lightrag_loop(coro: Any) -> Any:
    """Run a LightRAG coroutine on the dedicated loop from sync FastAPI code."""
    loop = _ensure_lightrag_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


async def _aget_lightrag_instance() -> LightRAG:
    global _LIGHTRAG_INSTANCE
    if _LIGHTRAG_INSTANCE is None:
        rag = LightRAG(
            working_dir=str(config.LIGHTRAG_WORKING_DIR),
            llm_model_func=_lightrag_llm_model_func,
            llm_model_name=_lightrag_chat_model(),
            embedding_func=_lightrag_embedding_func(),
        )
        await rag.initialize_storages()
        _LIGHTRAG_INSTANCE = rag
    elif _LIGHTRAG_INSTANCE._storages_status != StoragesStatus.INITIALIZED:
        await _LIGHTRAG_INSTANCE.initialize_storages()
    return _LIGHTRAG_INSTANCE


_COMPANY_FILE_HINTS = ("company", "supplier", "applicant", "manufacturer")

# Keep the UL prompt.py types as first-class graph nodes. Unknown LLM types
# used to collapse to "entity" and then get dropped from buckets/CSV/Neo4j.
_PROMPT_ENTITY_TYPES = (
    "ul",
    "applicant",
    "product",
    "model",
    "component",
    "standard",
    "clause",
    "certification",
    "test",
    "test_plan",
    "file_number",
    "volume",
    "section",
    "deliverable",
    "company",
    "location",
    "address",
)

_UL_ENTITY_TYPES = {
    *_PROMPT_ENTITY_TYPES,
}

_TYPE_TO_BUCKET = {
    "ul": "uls",
    "applicant": "applicants",
    "product": "products",
    "model": "models",
    "component": "components",
    "standard": "standards",
    "clause": "clauses",
    "certification": "certifications",
    "test": "tests",
    "test_plan": "test_plans",
    "file_number": "file_numbers",
    "volume": "volumes",
    "section": "sections",
    "deliverable": "deliverables",
    "company": "companies",
    "location": "locations",
    "address": "addresses",
}

_MAX_TOTAL_RECORDS = 80
_MAX_ENTITY_RECORDS = 40
_LANGUAGE = "English"

# Variant words that distinguish separate models within the same product line.
_MODEL_VARIANT_MARKERS = frozenset(
    {"ultra", "plus", "pro", "max", "mini", "lite", "se", "fe", "edge", "note"}
)
# Legal-entity suffixes only (longest first). Do NOT include descriptive words
# like "technologies" / "group" — those cause unsafe over-merges.
_COMPANY_LEGAL_SUFFIXES = (
    "incorporated",
    "corporation",
    "limited",
    "gmbh",
    "pllc",
    "pvt ltd",
    "pty ltd",
    "s a s",
    "s a",
    "llc",
    "llp",
    "pty",
    "plc",
    "corp",
    "inc",
    "ltd",
    "pvt",
    "lp",
    "nv",
    "ag",
    "bv",
    "oy",
    "ab",
    "kk",
    "co",
    "sa",
    "sas",
)


def is_company_source_file(filename: str) -> bool:
    """True when the upload name looks like a company/supplier PDF."""
    stem = Path(filename).stem.lower().replace("-", "_").replace(" ", "_")
    return any(hint in stem for hint in _COMPANY_FILE_HINTS)


def _norm_key(name: str) -> str:
    text = re.sub(r"[\s_\-]+", " ", (name or "").strip().lower())
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


def _normalize_volume_name(name: str) -> str:
    match = re.match(r"^(?:vol(?:ume)?)\.?\s*(\d+)\s*$", (name or "").strip(), re.I)
    if match:
        return f"Volume {match.group(1)}"
    return (name or "").strip()


def _normalize_section_name(name: str) -> str:
    match = re.match(r"^(?:sec(?:tion)?)\.?\s*(\d+)\s*$", (name or "").strip(), re.I)
    if match:
        return f"Section {match.group(1)}"
    return (name or "").strip()


def _normalize_entity_type(raw: str) -> str:
    """Preserve prompt.py types such as Applicant, FileNumber, and ManufacturingLocation."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw or "")
    cleaned = re.sub(r"[^a-zA-Z0-9_\s]", "", text).strip().lower()
    cleaned = re.sub(r"[\s]+", "_", cleaned).strip("_")
    aliases = {
        "organization": "company",
        "org": "company",
        "manufacturer": "company",
        "supplier": "company",
        "customer": "company",
        "brand": "company",
        "part": "component",
        "assembly": "component",
        "subassembly": "component",
        "bom": "component",
        "spec": "standard",
        "regulation": "standard",
        "article": "clause",
        "cert": "certification",
        "testing": "test",
        "test_record": "test",
        "testrecord": "test",
        "filenumber": "file_number",
        "testplan": "test_plan",
        "manufacturinglocation": "location",
        "manufacturing_location": "location",
        "ulsolutions": "ul",
        "ul_solutions": "ul",
    }
    mapped = aliases.get(cleaned, cleaned)
    if mapped in _UL_ENTITY_TYPES:
        return mapped
    return "entity"


def _parse_json_object(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Keep outermost object if the model adds chatter.
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LightRAG extraction response is not a JSON object")
    return data


def _relationship_from_keywords(
    keywords: str,
    source_type: str,
    target_type: str,
) -> str:
    """Map LightRAG relationship keywords onto UL hierarchy relation names."""
    raw = (keywords or "").strip().lower().replace("-", "_")
    # Split on separators BEFORE collapsing spaces so "HAS_MODEL, foo" stays has_model.
    rough_tokens = [t.strip() for t in re.split(r"[,;/|]+", raw) if t.strip()]
    tokens = [
        re.sub(r"[\s]+", "_", t).strip("_")
        for t in rough_tokens
    ]
    collapsed = re.sub(r"[\s]+", "_", raw).strip("_")
    candidates = [t for t in tokens if t] + ([collapsed] if collapsed else [])

    for token in candidates:
        alias = REL_ALIASES.get(token)
        if alias:
            return alias
        if token in REL_ALIASES.values():
            return token

    preferred = TYPE_PAIR_REL.get((source_type, target_type))
    if preferred:
        return preferred

    if candidates:
        return candidates[0]
    return "related_to"


def _chunk_project_id(chunk: dict) -> str:
    """Read project provenance from the chunk. Never infer it from names or text."""
    value = chunk.get("project_id")
    if value is not None and str(value).strip():
        return str(value).strip()
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        meta_value = metadata.get("project_id")
        if meta_value is not None and str(meta_value).strip():
            return str(meta_value).strip()
    return ""


def _heading_context_block(chunk: dict) -> str:
    """Same-chunk heading/section context only. Never retrieve other projects."""
    heading = str(
        chunk.get("heading_context")
        or chunk.get("heading")
        or chunk.get("section")
        or ""
    ).strip()
    if not heading:
        return ""
    return f"---Section Context---\n{heading}\n\n"


def _build_extraction_prompts(input_text: str, chunk: dict | None = None) -> tuple[str, str]:
    guidance = overlay_entity_types_guidance(
        str(PROMPTS.get("default_entity_types_guidance") or "")
    )
    examples_list = PROMPTS.get("entity_extraction_json_examples") or [""]
    examples = "\n".join(str(item) for item in examples_list)
    heading_block = _heading_context_block(chunk or {})

    system = overlay_json_system_prompt(
        str(PROMPTS["entity_extraction_json_system_prompt"])
    ).format(
        entity_types_guidance=guidance,
        examples=examples,
        language=_LANGUAGE,
        max_total_records=_MAX_TOTAL_RECORDS,
        max_entity_records=_MAX_ENTITY_RECORDS,
    )
    user = str(PROMPTS["entity_extraction_json_user_prompt"]).format(
        entity_types_guidance=guidance,
        language=_LANGUAGE,
        max_total_records=_MAX_TOTAL_RECORDS,
        max_entity_records=_MAX_ENTITY_RECORDS,
        heading_context_block=heading_block,
        input_text=input_text,
    )
    return system, user


def _build_continuation_user_prompt(input_text: str, previous_json: str) -> str:
    """Continue on the SAME chunk. Previous JSON is bookkeeping, not new evidence."""
    continue_core = str(
        PROMPTS.get("entity_continue_extraction_json_user_prompt") or ""
    ).format(
        language=_LANGUAGE,
        max_total_records=_MAX_TOTAL_RECORDS,
        max_entity_records=_MAX_ENTITY_RECORDS,
    )
    return (
        f"{continue_core.rstrip()}\n\n"
        "---Previous Response (bookkeeping only; not evidence)---\n"
        f"{previous_json}\n\n"
        "---Input Text---\n"
        f"```\n{input_text}\n```\n\n"
        "---Output---\n"
    )


def _select_chunks_for_project(
    chunks: list[dict],
    current_project_id: str | None,
) -> tuple[list[dict], str]:
    """Keep only chunks for the current project. Fail closed if mixed data remains."""
    current = str(current_project_id or "").strip()
    incoming_ids = {pid for chunk in chunks if (pid := _chunk_project_id(chunk))}

    if not current:
        if len(incoming_ids) > 1:
            raise ValueError(
                "Mixed-project chunks were provided without current_project_id; "
                f"found project_ids={sorted(incoming_ids)}"
            )
        if len(incoming_ids) == 1:
            current = next(iter(incoming_ids))
        else:
            return list(chunks), current

    scoped: list[dict] = []
    dropped = 0
    for chunk in chunks:
        pid = _chunk_project_id(chunk)
        if pid != current:
            dropped += 1
            logger.warning(
                "Ignoring chunk from project %s while extracting project %s",
                pid or "(missing)",
                current,
            )
            continue
        scoped.append(chunk)

    remaining_ids = {_chunk_project_id(chunk) for chunk in scoped}
    remaining_ids.discard("")
    if remaining_ids and remaining_ids != {current}:
        raise ValueError(
            f"Mixed-project chunks remain after filtering for project {current}: "
            f"{sorted(remaining_ids)}"
        )

    logger.info(
        "Project-scoped chunks=%d dropped_mismatched_chunks=%d",
        len(scoped),
        dropped,
    )
    return scoped, current


def _lightrag_index_id(chunk: dict, run_id: str | None, index: int) -> str:
    """LightRAG document id. Does not mutate the UL chunk_id used by extraction."""
    chunk_id = str(chunk.get("chunk_id") or "").strip() or f"chunk_{index + 1:06d}"
    prefix = str(run_id or "").strip()
    if prefix:
        return f"{prefix}:{chunk_id}"
    return chunk_id


def _prepare_lightrag_index_payload(
    chunks: list[dict],
    run_id: str | None = None,
) -> tuple[list[str], list[str], list[str]]:
    texts: list[str] = []
    ids: list[str] = []
    file_paths: list[str] = []
    for index, chunk in enumerate(chunks):
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        texts.append(text)
        ids.append(_lightrag_index_id(chunk, run_id, index))
        file_paths.append(str(chunk.get("source_file") or "").strip())
    return texts, ids, file_paths


def index_chunks_with_lightrag(
    chunks: list[dict],
    *,
    run_id: str | None = None,
) -> int:
    """
    Index semantic chunks into LightRAG.
    This does not change UL entity/relationship extraction.
    """
    if not chunks:
        return 0

    texts, ids, file_paths = _prepare_lightrag_index_payload(chunks, run_id)
    if not texts:
        return 0

    return _run_on_lightrag_loop(_aindex_chunks_with_lightrag(texts, ids, file_paths))


async def _aindex_chunks_with_lightrag(
    texts: list[str],
    ids: list[str],
    file_paths: list[str],
) -> int:
    rag = await _aget_lightrag_instance()
    # LightRAG 1.5.6 native indexing: omit process_options so KG extraction runs.
    # Semantic chunks are still the documents we enqueue; skip-KG ("!") is not used.
    await rag.apipeline_enqueue_documents(
        texts,
        ids=ids,
        file_paths=file_paths,
    )
    await rag.apipeline_process_enqueue_documents()
    logger.info(
        "Indexed %d semantic chunks into LightRAG with native KG extraction",
        len(texts),
    )
    return len(texts)


def _at_extraction_cap(entities: list[dict], relationships: list[dict]) -> bool:
    return (
        len(entities) >= _MAX_ENTITY_RECORDS
        or (len(entities) + len(relationships)) >= _MAX_TOTAL_RECORDS
    )


def _chunk_provenance(chunk: dict) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "source_file": str(chunk.get("source_file") or ""),
        "page_range": chunk.get("page_range"),
        "chunk_id": str(chunk.get("chunk_id") or ""),
    }
    project_id = _chunk_project_id(chunk)
    if project_id:
        provenance["project_id"] = project_id
    return provenance


def _entities_from_extraction_json(data: dict, chunk: dict) -> list[dict]:
    provenance = _chunk_provenance(chunk)
    entities: list[dict] = []
    for item in data.get("entities") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        entity_type = _normalize_entity_type(str(item.get("type") or "entity"))
        if entity_type == "volume":
            name = _normalize_volume_name(name)
        elif entity_type == "section":
            name = _normalize_section_name(name)
        elif entity_type == "ul" or _is_ul_org_company(name):
            name = UL_ROOT_NAME
            entity_type = "ul"
        entities.append(
            {
                "name": name,
                "entity_type": entity_type,
                "description": normalize_description(str(item.get("description") or "")),
                **provenance,
            }
        )
    return entities


def extract_chunk_with_lightrag(chunk: dict) -> tuple[list[dict], list[dict]]:
    """Run LightRAG JSON extraction prompts on one chunk."""
    text = str(chunk.get("text") or "").strip()
    if not text:
        return [], []

    system, user = _build_extraction_prompts(text, chunk)
    chunk_id = str(chunk.get("chunk_id") or "")

    try:
        raw = chat_json(system, user)
        data = _parse_json_object(raw)
    except Exception as exc:
        logger.warning(
            "LightRAG extraction failed for chunk_id=%s: %s",
            chunk_id or "?",
            exc,
        )
        return [], []

    entities = _entities_from_extraction_json(data, chunk)
    relationships = _relationships_from_extraction_json(data, chunk, entities)

    if _at_extraction_cap(entities, relationships) and PROMPTS.get(
        "entity_continue_extraction_json_user_prompt"
    ):
        try:
            continue_user = _build_continuation_user_prompt(text, raw)
            continue_raw = chat_json(system, continue_user)
            continue_data = _parse_json_object(continue_raw)
        except Exception as exc:
            logger.warning(
                "LightRAG continuation extraction failed for chunk_id=%s: %s",
                chunk_id or "?",
                exc,
            )
        else:
            extra_entities = _entities_from_extraction_json(continue_data, chunk)
            seen_entities = {
                (_norm_key(e["name"]), e["entity_type"]) for e in entities
            }
            for extra in extra_entities:
                key = (_norm_key(extra["name"]), extra["entity_type"])
                if key in seen_entities:
                    continue
                entities.append(extra)
                seen_entities.add(key)
            extra_relationships = _relationships_from_extraction_json(
                continue_data, chunk, entities
            )
            seen_rels = {
                (
                    r.get("source_type"),
                    _norm_key(str(r.get("source_node") or "")),
                    _norm_key(str(r.get("relationship") or "")),
                    r.get("destination_type"),
                    _norm_key(str(r.get("destination_node") or "")),
                )
                for r in relationships
            }
            for extra in extra_relationships:
                key = (
                    extra.get("source_type"),
                    _norm_key(str(extra.get("source_node") or "")),
                    _norm_key(str(extra.get("relationship") or "")),
                    extra.get("destination_type"),
                    _norm_key(str(extra.get("destination_node") or "")),
                )
                if key in seen_rels:
                    continue
                relationships.append(extra)
                seen_rels.add(key)

    logger.info(
        "LightRAG chunk_id=%s → %d entit(y/ies), %d relationship(s)",
        chunk_id or "?",
        len(entities),
        len(relationships),
    )
    return entities, relationships


def _relationships_from_extraction_json(
    data: dict,
    chunk: dict,
    entities: list[dict],
) -> list[dict]:
    provenance = _chunk_provenance(chunk)
    known_by_exact_name: dict[str, list[dict]] = {}
    known_by_norm_name: dict[str, list[dict]] = {}
    for entity in entities:
        name = entity["name"]
        known_by_exact_name.setdefault(name, []).append(entity)
        known_by_norm_name.setdefault(_norm_key(name), []).append(entity)

    def _candidates_for(name: str) -> list[dict]:
        exact = known_by_exact_name.get(name) or []
        if exact:
            return exact
        for alt in (_normalize_volume_name(name), _normalize_section_name(name)):
            if alt != name:
                exact = known_by_exact_name.get(alt) or []
                if exact:
                    return exact
        return known_by_norm_name.get(_norm_key(name)) or []

    def _pick_entity(
        name: str,
        *,
        other_type: str | None = None,
    ) -> dict | None:
        candidates = _candidates_for(name)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if other_type:
            typed = []
            for candidate in candidates:
                if types_are_related(candidate["entity_type"], other_type):
                    typed.append(candidate)
            if len(typed) == 1:
                return typed[0]
        return None

    relationships: list[dict] = []
    for item in data.get("relationships") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        if not source or not target:
            continue

        source_meta = _pick_entity(source)
        target_meta = _pick_entity(
            target,
            other_type=source_meta["entity_type"] if source_meta else None,
        )
        if source_meta is None:
            source_meta = _pick_entity(
                source,
                other_type=target_meta["entity_type"] if target_meta else None,
            )

        source_type = source_meta["entity_type"] if source_meta else "entity"
        target_type = target_meta["entity_type"] if target_meta else "entity"
        keywords = str(item.get("keywords") or "").strip()
        relationships.append(
            {
                "source_node": source_meta["name"] if source_meta else source,
                "source_type": source_type,
                "relationship": _relationship_from_keywords(
                    keywords, source_type, target_type
                ),
                "destination_node": target_meta["name"] if target_meta else target,
                "destination_type": target_type,
                "keywords": keywords,
                "evidence": normalize_description(str(item.get("description") or "")),
                **provenance,
            }
        )
    return relationships


def _empty_graph_data() -> dict[str, Any]:
    buckets = {bucket: [] for bucket in set(_TYPE_TO_BUCKET.values())}
    buckets.setdefault("normalized_components", [])
    buckets["relationships"] = []
    return buckets


def _bucket_entities(entities: list[dict]) -> dict[str, list[dict]]:
    buckets = _empty_graph_data()
    seen: dict[str, set[str]] = {
        key: set() for key in buckets if key != "relationships"
    }

    for entity in entities:
        name = str(entity.get("name") or "").strip()
        entity_type = str(entity.get("entity_type") or "entity").strip().lower()
        if entity_type == "volume":
            name = _normalize_volume_name(name)
        elif entity_type == "section":
            name = _normalize_section_name(name)
        bucket = _TYPE_TO_BUCKET.get(entity_type)
        if not bucket or not name:
            continue
        if bucket in {"companies", "applicants"} and not is_plausible_company_name(name):
            continue
        key = _norm_key(name)
        if key in seen[bucket]:
            continue
        seen[bucket].add(key)

        if bucket == "companies":
            source_file = entity.get("source_file")
            source_files = [
                str(item).strip()
                for item in (entity.get("source_files") or [])
                if str(item).strip()
            ]
            if source_file and str(source_file).strip() not in source_files:
                source_files.append(str(source_file).strip())
            buckets[bucket].append(
                {
                    "name": name,
                    "company_name": name,
                    "evidence": entity.get("description") or "",
                    "source_file": source_file,
                    "source_files": source_files,
                    "mention_kinds": list(entity.get("mention_kinds") or []),
                    "page_range": entity.get("page_range"),
                    "chunk_id": entity.get("chunk_id"),
                }
            )
        elif bucket == "components":
            record = {
                "name": name,
                "component_name": name,
                "canonical_name": name,
                "component_description": entity.get("description") or "",
                "evidence": entity.get("description") or "",
                "source_file": entity.get("source_file"),
                "page_range": entity.get("page_range"),
                "chunk_id": entity.get("chunk_id"),
                "aliases": [name],
            }
            buckets["components"].append(record)
            buckets["normalized_components"].append(dict(record))
        elif bucket == "products":
            buckets[bucket].append(
                {
                    "name": name,
                    "entity_type": "product",
                    "canonical_name": name,
                    "original_name": name,
                    "aliases": [name],
                    "evidence": entity.get("description") or "",
                    "source_file": entity.get("source_file"),
                    "page_range": entity.get("page_range"),
                    "chunk_id": entity.get("chunk_id"),
                    "identity_key": f"product:{key}",
                }
            )
        elif bucket == "models":
            buckets[bucket].append(
                {
                    "name": name,
                    "entity_type": "model",
                    "canonical_name": name,
                    "original_name": name,
                    "aliases": [name],
                    "evidence": entity.get("description") or "",
                    "source_file": entity.get("source_file"),
                    "page_range": entity.get("page_range"),
                    "chunk_id": entity.get("chunk_id"),
                    "identity_key": f"model:{key}",
                }
            )
        else:
            buckets[bucket].append(
                {
                    "name": name,
                    "entity_type": entity_type,
                    "evidence": entity.get("description") or "",
                    "source_file": entity.get("source_file"),
                    "page_range": entity.get("page_range"),
                    "chunk_id": entity.get("chunk_id"),
                }
            )
        project_id = str(entity.get("project_id") or "").strip()
        if project_id:
            buckets[bucket][-1]["project_id"] = project_id
            if bucket == "components":
                buckets["normalized_components"][-1]["project_id"] = project_id
    return buckets


def _company_name(item: dict) -> str:
    return str(item.get("name") or item.get("company_name") or "").strip()


def _company_core_key(name: str) -> str:
    """Identity key after stripping generic legal suffixes (Inc, Ltd, Corp, ...)."""
    tokens = _norm_key(name).split()
    suffixes = sorted(_COMPANY_LEGAL_SUFFIXES, key=len, reverse=True)
    changed = True
    while changed and tokens:
        changed = False
        for suffix in suffixes:
            suffix_tokens = suffix.split()
            count = len(suffix_tokens)
            if count <= len(tokens) and tokens[-count:] == suffix_tokens:
                tokens = tokens[:-count]
                changed = True
                break
    return " ".join(tokens)


def _companies_are_same(left: str, right: str) -> bool:
    """True when two names refer to the same organization or alias variant."""
    return entity_names_equivalent(left, right)


def _find_equivalent_org(name: str, orgs: list[dict]) -> dict | None:
    for org in orgs:
        if _companies_are_same(name, _company_name(org) or str(org.get("name") or "")):
            return org
    return None


def _has_legal_suffix(name: str) -> bool:
    return _company_core_key(name) != _norm_key(name)


def _merge_text_field(existing: dict, incoming: dict, field: str) -> None:
    if field in {"description", "evidence", "component_description"}:
        merge_description_field(existing, incoming, field)
        return
    extra = str(incoming.get(field) or "").strip()
    if not extra:
        return
    prior = str(existing.get(field) or "").strip()
    if not prior:
        existing[field] = extra
    elif extra not in prior:
        existing[field] = f"{prior} | {extra}".strip(" |")


def _rewrite_relationship_names(relationships: list[dict], rename: dict[str, str]) -> None:
    if not rename:
        return
    for rel in relationships:
        for field in ("source_node", "destination_node", "source", "target"):
            value = str(rel.get(field) or "").strip()
            if not value:
                continue
            replacement = (
                rename.get(value)
                or rename.get(_norm_key(value))
                or rename.get(_standard_norm(value))
            )
            if replacement:
                rel[field] = replacement


def _dedupe_relationships(relationships: list[dict]) -> list[dict]:
    """Dedupe by typed endpoints + relationship; merge evidence/keywords."""
    deduped: list[dict] = []
    seen: dict[tuple[str, str, str, str, str], dict] = {}
    for rel in relationships:
        key = (
            str(rel.get("source_type") or "").strip().lower(),
            _norm_key(str(rel.get("source_node") or "")),
            _norm_key(str(rel.get("relationship") or "")),
            str(rel.get("destination_type") or rel.get("target_type") or "")
            .strip()
            .lower(),
            _norm_key(str(rel.get("destination_node") or "")),
        )
        if not key[1] or not key[4]:
            continue
        existing = seen.get(key)
        if existing is not None:
            _merge_text_field(existing, rel, "evidence")
            _merge_text_field(existing, rel, "description")
            _merge_text_field(existing, rel, "keywords")
            continue
        row = dict(rel)
        seen[key] = row
        deduped.append(row)
    return deduped


def _component_name(item: dict) -> str:
    return str(
        item.get("name")
        or item.get("component_name")
        or item.get("canonical_name")
        or ""
    ).strip()


def _component_identity_key(name: str) -> str:
    """Normalize case/punct/whitespace and safe singular/plural endings."""
    key = _norm_key(name)
    tokens = key.split()
    if not tokens:
        return key
    last = tokens[-1]
    if len(last) < 4:
        return key
    if last.endswith(("ss", "us", "is", "ous", "ics")):
        return key
    if last.endswith("ies") and len(last) >= 5:
        tokens[-1] = last[:-3] + "y"
    elif last.endswith(("ches", "shes", "xes", "zes")):
        tokens[-1] = last[:-2]
    elif last.endswith("s"):
        tokens[-1] = last[:-1]
    return " ".join(tokens)


_PART_NUMBER_RE = re.compile(
    r"\b(?=[A-Z0-9\-/]*\d)[A-Z]{1,8}\d*[A-Z0-9]*[-/][A-Z0-9]+(?:[-/][A-Z0-9]+)*\b",
    re.I,
)
_STANDARD_ORG_PREFIXES = frozenset({"ul", "iec", "iso", "ieee", "csa", "en", "ansi", "astm"})
_DEV_STAGE_RE = re.compile(r"^(?:dvt|evt|pvt)\d*$", re.I)


def _component_part_numbers(*texts: str) -> set[str]:
    """Extract explicit part-number tokens. Does not treat standard designations as PNs."""
    found: set[str] = set()
    for text in texts:
        for match in _PART_NUMBER_RE.finditer(text or ""):
            token = match.group(0).upper()
            prefix = re.split(r"[\d\-/]", token, maxsplit=1)[0].lower()
            if prefix in _STANDARD_ORG_PREFIXES:
                continue
            found.add(token)
    return found


def _components_are_same(left: str, right: str) -> bool:
    left_pns = _component_part_numbers(left)
    right_pns = _component_part_numbers(right)
    if left_pns and right_pns:
        return bool(left_pns & right_pns)
    if left_pns or right_pns:
        return False
    a = _component_identity_key(left)
    b = _component_identity_key(right)
    return bool(a and b and a == b)


def _pick_canonical_component(group: list[dict]) -> dict:
    """Prefer a readable type + manufacturer PN name when part numbers exist."""

    def _score(item: dict) -> tuple:
        name = _component_name(item)
        pns = _component_part_numbers(name, str(item.get("evidence") or ""))
        type_words = [
            word
            for word in re.findall(r"[A-Za-z]+", name)
            if word.lower() not in {"rev", "revision"}
        ]
        starts_with_type = bool(type_words) and not _PART_NUMBER_RE.match(name or "")
        return (
            int(bool(pns) and starts_with_type),
            len(type_words),
            len(name),
        )

    return max(group, key=_score)


def _cluster_by_predicate(items: list[dict], name_fn, same_fn) -> list[list[dict]]:
    """Union-find clustering so equivalence is transitive and order-independent."""
    n = len(items)
    if n == 0:
        return []
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    names = [name_fn(item) for item in items]
    for i in range(n):
        for j in range(i + 1, n):
            if same_fn(names[i], names[j]):
                union(i, j)

    clusters: dict[int, list[dict]] = {}
    for i, item in enumerate(items):
        clusters.setdefault(find(i), []).append(item)
    return list(clusters.values())


def _component_identity_text(item: dict) -> str:
    return " ".join(
        part
        for part in (
            _component_name(item),
            str(item.get("evidence") or ""),
            str(item.get("component_description") or ""),
        )
        if str(part).strip()
    )


def _dedupe_components(graph_data: dict) -> None:
    """Merge only high-confidence component name variants (not partial names)."""
    components = [
        c for c in (graph_data.get("components") or []) if _component_name(c)
    ]
    if len(components) <= 1:
        graph_data["normalized_components"] = [
            dict(c) for c in (graph_data.get("components") or [])
        ]
        return

    groups = _cluster_by_predicate(
        components, _component_identity_text, _components_are_same
    )

    merged: list[dict] = []
    rename: dict[str, str] = {}
    for group in groups:
        canonical = _pick_canonical_component(group)
        canonical_name = _component_name(canonical)
        aliases = {canonical_name}
        for component in group:
            old = _component_name(component)
            aliases.add(old)
            for alias in component.get("aliases") or []:
                alias_name = str(alias).strip()
                if alias_name:
                    aliases.add(alias_name)
            if old != canonical_name:
                rename[old] = canonical_name
                rename[_norm_key(old)] = canonical_name
            if component is not canonical:
                _merge_text_field(canonical, component, "evidence")
                _merge_text_field(canonical, component, "component_description")
        canonical["name"] = canonical_name
        canonical["component_name"] = canonical_name
        canonical["canonical_name"] = canonical_name
        canonical["aliases"] = sorted(aliases)
        merged.append(canonical)

    graph_data["components"] = merged
    graph_data["normalized_components"] = [dict(c) for c in merged]
    _rewrite_relationship_names(graph_data.get("relationships") or [], rename)
    logger.info("Deduped components to %d unique component node(s)", len(merged))


def _standard_norm(name: str) -> str:
    """Keep designation punctuation (dots/hyphens) while normalizing separators."""
    text = re.sub(r"[\s_/|,;]+", " ", (name or "").strip().lower())
    text = re.sub(r"[^\w\s.\-]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _standard_identity_parts(name: str) -> tuple[str, frozenset[str]] | None:
    """Return (designation, org_tokens) when a clear standard identifier exists."""
    key = _standard_norm(name)
    match = re.search(
        r"^(?P<prefix>.*?)(?P<num>\d+[a-z0-9]*(?:[.\-]\d+[a-z0-9]*)*)(?P<suffix>.*)$",
        key,
    )
    if not match:
        return None
    num = match.group("num").strip()
    prefix_tokens = [t for t in match.group("prefix").split() if t]
    # Org tokens are short alphanumeric prefixes (ul, iec, iso, ieee, csa, ...).
    orgs = frozenset(t for t in prefix_tokens if 1 < len(t) <= 5 and t.isalpha())
    if not num or not orgs:
        return None
    return num, orgs


def _standards_are_same(left: str, right: str) -> bool:
    if _standard_norm(left) == _standard_norm(right):
        return True
    parts_left = _standard_identity_parts(left)
    parts_right = _standard_identity_parts(right)
    if not parts_left or not parts_right:
        return False
    num_left, orgs_left = parts_left
    num_right, orgs_right = parts_right
    if num_left != num_right:
        return False
    if orgs_left == orgs_right:
        return True
    # Allow multi-body naming variants (UL/CSA vs UL/CSA/IEC), but do NOT
    # collapse single-body citations (UL 62133-2 vs IEC 62133-2).
    if len(orgs_left) >= 2 and len(orgs_right) >= 2:
        return orgs_left.issubset(orgs_right) or orgs_right.issubset(orgs_left)
    return False


def _dedupe_standards(graph_data: dict) -> None:
    """Merge equivalent standard naming variants when designation identity is clear."""
    standards = [
        s for s in (graph_data.get("standards") or []) if str(s.get("name") or "").strip()
    ]
    if len(standards) <= 1:
        return

    groups = _cluster_by_predicate(
        standards,
        lambda item: str(item.get("name") or "").strip(),
        _standards_are_same,
    )

    merged: list[dict] = []
    rename: dict[str, str] = {}
    for group in groups:
        # Prefer the more specific (longer) designation string as canonical.
        canonical = max(group, key=lambda item: len(str(item.get("name") or "")))
        canonical_name = str(canonical.get("name") or "").strip()
        aliases = {canonical_name}
        for standard in group:
            old = str(standard.get("name") or "").strip()
            aliases.add(old)
            if old != canonical_name:
                rename[old] = canonical_name
                rename[_norm_key(old)] = canonical_name
                rename[_standard_norm(old)] = canonical_name
            if standard is not canonical:
                _merge_text_field(canonical, standard, "evidence")
        canonical["name"] = canonical_name
        canonical["aliases"] = sorted(aliases)
        merged.append(canonical)

    graph_data["standards"] = merged
    _rewrite_relationship_names(graph_data.get("relationships") or [], rename)
    logger.info("Deduped standards to %d unique standard node(s)", len(merged))


def _dedupe_companies(graph_data: dict) -> None:
    """Merge legal-form and generic-descriptor variants of the same company."""
    _dedupe_org_bucket(graph_data, "companies")


def _dedupe_applicants(graph_data: dict) -> None:
    """Merge equivalent Applicant names so UL has one party per organization."""
    _dedupe_org_bucket(graph_data, "applicants")


def _location_parents(graph_data: dict) -> dict[str, set[str]]:
    parents: dict[str, set[str]] = {}
    for rel in graph_data.get("relationships") or []:
        if _rel_name(rel) != "has_location":
            continue
        src = _norm_key(str(rel.get("source_node") or ""))
        dst = _norm_key(str(rel.get("destination_node") or ""))
        if src and dst:
            parents.setdefault(dst, set()).add(src)
    return parents


def _dedupe_alias_entities(graph_data: dict, bucket: str) -> None:
    """Merge token-suffix / normalized aliases within a named entity bucket.

    When parent links exist (e.g. Company->HAS_LOCATION->Location), only merge
    aliases that share a parent so unrelated sites stay distinct.
    """
    items = [
        item
        for item in (graph_data.get(bucket) or [])
        if str(item.get("name") or "").strip()
    ]
    if len(items) <= 1:
        return

    parents = _location_parents(graph_data) if bucket == "locations" else {}

    def _same(left: str, right: str) -> bool:
        if not entity_names_equivalent(left, right):
            return False
        if bucket != "locations" or not parents:
            return True
        left_parents = parents.get(_norm_key(left), set())
        right_parents = parents.get(_norm_key(right), set())
        if left_parents and right_parents:
            return bool(left_parents & right_parents)
        return True

    groups = _cluster_by_predicate(
        items,
        lambda item: str(item.get("name") or "").strip(),
        _same,
    )
    merged: list[dict] = []
    rename: dict[str, str] = {}
    for group in groups:
        canonical_name = str(group[0].get("name") or "").strip()
        canonical = group[0]
        for item in group[1:]:
            candidate = str(item.get("name") or "").strip()
            preferred = preferred_entity_name(canonical_name, candidate)
            if preferred == candidate:
                canonical = item
                canonical_name = candidate
        aliases = {canonical_name}
        for item in group:
            old = str(item.get("name") or "").strip()
            aliases.add(old)
            for alias in item.get("aliases") or []:
                alias_name = str(alias).strip()
                if alias_name:
                    aliases.add(alias_name)
            if old != canonical_name:
                rename[old] = canonical_name
                rename[_norm_key(old)] = canonical_name
            if item is not canonical:
                _merge_text_field(canonical, item, "evidence")
        canonical["name"] = canonical_name
        canonical["aliases"] = sorted(aliases)
        merged.append(canonical)
    graph_data[bucket] = merged
    if rename:
        _rewrite_relationship_names(graph_data.get("relationships") or [], rename)


def _dedupe_org_bucket(graph_data: dict, bucket: str) -> None:
    orgs = [item for item in (graph_data.get(bucket) or []) if _company_name(item)]
    if len(orgs) <= 1:
        return

    groups = _cluster_by_predicate(orgs, _company_name, _companies_are_same)

    merged: list[dict] = []
    rename: dict[str, str] = {}
    for group in groups:
        canonical = max(
            group,
            key=lambda item: (
                int(_has_legal_suffix(_company_name(item))),
                len(_company_name(item)),
            ),
        )
        canonical_name = preferred_company_name(
            _company_name(canonical),
            max((_company_name(item) for item in group), key=len),
        )
        aliases = {canonical_name}
        for org in group:
            old = _company_name(org)
            aliases.add(old)
            if old != canonical_name:
                rename[old] = canonical_name
                rename[_norm_key(old)] = canonical_name
            if org is not canonical:
                _merge_text_field(canonical, org, "evidence")
        canonical["name"] = canonical_name
        if bucket == "companies":
            canonical["company_name"] = canonical_name
        canonical["aliases"] = sorted(aliases)
        mention_kinds: list[str] = []
        source_files: list[str] = []
        for org in group:
            for kind in org.get("mention_kinds") or []:
                kind_name = str(kind).strip()
                if kind_name and kind_name not in mention_kinds:
                    mention_kinds.append(kind_name)
            for file_name in [org.get("source_file"), *(org.get("source_files") or [])]:
                cleaned = str(file_name or "").strip()
                if cleaned and cleaned not in source_files:
                    source_files.append(cleaned)
        canonical["mention_kinds"] = mention_kinds
        canonical["source_files"] = source_files
        merged.append(canonical)

    graph_data[bucket] = merged
    _rewrite_relationship_names(graph_data.get("relationships") or [], rename)
    logger.info("Deduped %s to %d unique node(s)", bucket, len(merged))


def _model_tokens(name: str) -> set[str]:
    return {token for token in _norm_key(name).split() if token}


def _model_spec_tokens(name: str) -> dict[str, set[str]]:
    """Extract comparable spec tokens so distinct variants are not merged."""
    key = _norm_key(name)
    return {
        "year": set(re.findall(r"\b20\d{2}\b", key)),
        "size": set(re.findall(r"\b\d+\s*(?:inch|in|cm|mm)\b", key)),
        "generation": set(re.findall(r"\b(?:gen(?:eration)?\s*\d+|g\d+)\b", key)),
        "revision": set(re.findall(r"\b(?:v\d+|rev\s*\d+|r\d+)\b", key)),
    }


def _model_variant_markers(name: str) -> set[str]:
    return _model_tokens(name) & _MODEL_VARIANT_MARKERS


def _models_have_spec_conflict(left: str, right: str) -> bool:
    specs_left = _model_spec_tokens(left)
    specs_right = _model_spec_tokens(right)
    for field in ("year", "size", "generation", "revision"):
        if specs_left[field] and specs_right[field] and specs_left[field] != specs_right[field]:
            return True
    if _model_variant_markers(left) != _model_variant_markers(right):
        return True
    return False


def _models_are_aliases(left: str, right: str) -> bool:
    """True when two model names are equivalent mentions of the same variant."""
    if _norm_key(left) == _norm_key(right):
        return True
    if _models_have_spec_conflict(left, right):
        return False
    tokens_left = _model_tokens(left)
    tokens_right = _model_tokens(right)
    if not tokens_left or not tokens_right:
        return False
    shorter, longer = (
        (tokens_left, tokens_right)
        if len(tokens_left) <= len(tokens_right)
        else (tokens_right, tokens_left)
    )
    if len(shorter) < 2:
        return False
    return shorter.issubset(longer)


def _model_product_parents(graph_data: dict) -> dict[str, set[str]]:
    parents: dict[str, set[str]] = {}
    for rel in graph_data.get("relationships") or []:
        rel_name = str(rel.get("relationship") or "").strip().lower()
        src_type = str(rel.get("source_type") or "").strip().lower()
        dst_type = str(rel.get("destination_type") or "").strip().lower()
        if rel_name != "has_model" or src_type != "product" or dst_type != "model":
            continue
        product_key = _norm_key(str(rel.get("source_node") or ""))
        model_key = _norm_key(str(rel.get("destination_node") or ""))
        if product_key and model_key:
            parents.setdefault(model_key, set()).add(product_key)
    return parents


def _pick_canonical_model(group: list[dict]) -> dict:
    return max(
        group,
        key=lambda item: (
            len(_model_tokens(str(item.get("name") or ""))),
            len(str(item.get("name") or "")),
        ),
    )


def _dedupe_models(graph_data: dict) -> None:
    """Merge alias model names that refer to the same variant of the same product."""
    models = [m for m in (graph_data.get("models") or []) if str(m.get("name") or "").strip()]
    if len(models) <= 1:
        return

    parents = _model_product_parents(graph_data)

    def _same_model(left: str, right: str) -> bool:
        if not _models_are_aliases(left, right):
            return False
        left_parents = parents.get(_norm_key(left), set())
        right_parents = parents.get(_norm_key(right), set())
        if left_parents and right_parents:
            return bool(left_parents & right_parents)
        # Only merge unlinked aliases with other unlinked aliases, or with a
        # linked alias when one side has no parent evidence yet.
        return True

    groups = _cluster_by_predicate(
        models,
        lambda item: str(item.get("name") or "").strip(),
        _same_model,
    )

    merged: list[dict] = []
    rename: dict[str, str] = {}
    for group in groups:
        canonical = _pick_canonical_model(group)
        canonical_name = str(canonical.get("name") or "").strip()
        aliases = set(str(a).strip() for a in (canonical.get("aliases") or []) if str(a).strip())
        aliases.add(canonical_name)
        for model in group:
            old = str(model.get("name") or "").strip()
            aliases.add(old)
            for alias in model.get("aliases") or []:
                alias_name = str(alias).strip()
                if alias_name:
                    aliases.add(alias_name)
            if old != canonical_name:
                rename[old] = canonical_name
                rename[_norm_key(old)] = canonical_name
            if model is not canonical:
                _merge_text_field(canonical, model, "evidence")
        canonical["name"] = canonical_name
        canonical["canonical_name"] = canonical_name
        canonical["aliases"] = sorted(aliases)
        canonical["identity_key"] = f"model:{_norm_key(canonical_name)}"
        merged.append(canonical)

    graph_data["models"] = merged
    _rewrite_relationship_names(graph_data.get("relationships") or [], rename)
    logger.info("Deduped models to %d unique model node(s)", len(merged))


def _append_relationship(
    graph_data: dict,
    *,
    source_node: str,
    source_type: str,
    relationship: str,
    destination_node: str,
    destination_type: str,
    evidence: str = "",
    keywords: str = "",
) -> None:
    rels = graph_data.setdefault("relationships", [])
    row = {
        "source_node": source_node,
        "source_type": source_type,
        "relationship": relationship,
        "destination_node": destination_node,
        "destination_type": destination_type,
        "evidence": evidence,
        "keywords": keywords or relationship,
    }
    project_id = str(graph_data.get("_project_id") or "").strip()
    if project_id:
        row["project_id"] = project_id
    rels.append(row)


def _is_ul_org_company(name: str) -> bool:
    """True for UL Solutions / UL LLC style org nodes (not brand customers)."""
    key = _norm_key(name)
    core = _company_core_key(name)
    if key == _norm_key(UL_ROOT_NAME) or core == _norm_key(UL_ROOT_NAME):
        return True
    return core in {"ul", "ul llc", "ul solutions"} or key.startswith("ul llc")


def _rel_name(rel: dict) -> str:
    key = str(rel.get("relationship") or "").strip().lower().replace(" ", "_").replace("-", "_")
    return REL_ALIASES.get(key, key)


def _canonicalize_extracted_relationships(graph_data: dict) -> None:
    for rel in graph_data.get("relationships") or []:
        rel["relationship"] = _rel_name(rel)
        src_type = str(rel.get("source_type") or "").strip().lower()
        dst_type = str(rel.get("destination_type") or "").strip().lower()
        if src_type in {"manufacturer", "supplier", "customer"}:
            rel["source_type"] = "company"
        if dst_type in {"manufacturer", "supplier", "customer"}:
            rel["destination_type"] = "company"
        if src_type in {"manufacturinglocation", "manufacturing_location"}:
            rel["source_type"] = "location"
            src_type = "location"
        if dst_type in {"manufacturinglocation", "manufacturing_location"}:
            rel["destination_type"] = "location"
            dst_type = "location"
        if src_type == "volume":
            rel["source_node"] = _normalize_volume_name(str(rel.get("source_node") or ""))
        if dst_type == "volume":
            rel["destination_node"] = _normalize_volume_name(str(rel.get("destination_node") or ""))
        if src_type == "section":
            rel["source_node"] = _normalize_section_name(str(rel.get("source_node") or ""))
        if dst_type == "section":
            rel["destination_node"] = _normalize_section_name(str(rel.get("destination_node") or ""))


def _variant_is_development_stage(name: str, evidence: str = "") -> bool:
    if not _DEV_STAGE_RE.match((name or "").strip()):
        return False
    text = f"{name} {evidence}".lower()
    return not any(
        marker in text
        for marker in ("configuration", "config", "market", "region", "dual-sim", "unlocked")
    )


def _drop_development_stage_variants(graph_data: dict) -> None:
    """Drop Variant nodes and HAS_VARIANT edges; they are not in prompt.py."""
    drop_keys: set[str] = set()
    for variant in graph_data.get("variants") or []:
        name = str(variant.get("name") or "").strip()
        if name:
            drop_keys.add(_norm_key(name))

    kept_rels: list[dict] = []
    dropped = 0
    for rel in graph_data.get("relationships") or []:
        src_name = str(rel.get("source_node") or "").strip()
        dst_name = str(rel.get("destination_node") or "").strip()
        if _rel_name(rel) == "has_variant":
            dropped += 1
            continue
        if _norm_key(src_name) in drop_keys or _norm_key(dst_name) in drop_keys:
            dropped += 1
            continue
        if _variant_is_development_stage(src_name) or _variant_is_development_stage(dst_name):
            dropped += 1
            continue
        kept_rels.append(rel)

    if "variants" in graph_data:
        graph_data["variants"] = []
    graph_data["relationships"] = kept_rels
    if dropped:
        logger.info("Dropped %d Variant / HAS_VARIANT relationship(s)", dropped)


_ORG_ENDPOINT_TYPES = frozenset(
    {
        "company",
        "applicant",
        "brand",
        "organization",
        "manufacturer",
        "supplier",
    }
)


def _drop_implausible_org_relationships(graph_data: dict) -> None:
    """Drop edges whose company/applicant endpoint is a generic descriptor."""
    kept: list[dict] = []
    dropped = 0
    for rel in graph_data.get("relationships") or []:
        src_type = str(rel.get("source_type") or "").strip().lower()
        dst_type = str(rel.get("destination_type") or "").strip().lower()
        src = str(rel.get("source_node") or "").strip()
        dst = str(rel.get("destination_node") or "").strip()
        if src_type in _ORG_ENDPOINT_TYPES and not is_plausible_company_name(src):
            dropped += 1
            continue
        if dst_type in _ORG_ENDPOINT_TYPES and not is_plausible_company_name(dst):
            dropped += 1
            continue
        kept.append(rel)
    graph_data["relationships"] = kept
    if dropped:
        logger.info("Dropped %d relationship(s) with implausible org names", dropped)


def _drop_product_named_models_when_distinct_ids_exist(graph_data: dict) -> None:
    """Drop Model nodes that merely repeat a Product name when real model IDs exist.

    Keeps same-name Product/Model pairs when that shared name is the only model
    (for example Widget X product + Widget X model).
    """
    product_keys = {
        _norm_key(str(product.get("name") or ""))
        for product in (graph_data.get("products") or [])
        if str(product.get("name") or "").strip()
    }
    if not product_keys:
        return

    product_to_models: dict[str, set[str]] = {}
    for rel in graph_data.get("relationships") or []:
        if _rel_name(rel) != "has_model":
            continue
        if str(rel.get("source_type") or "").lower() != "product":
            continue
        if str(rel.get("destination_type") or "").lower() != "model":
            continue
        product_key = _norm_key(str(rel.get("source_node") or ""))
        model_key = _norm_key(str(rel.get("destination_node") or ""))
        if product_key and model_key:
            product_to_models.setdefault(product_key, set()).add(model_key)

    drop_keys = {
        product_key
        for product_key, model_keys in product_to_models.items()
        if product_key in product_keys
        and any(model_key != product_key for model_key in model_keys)
    }
    if not drop_keys:
        return

    before = len(graph_data.get("models") or [])
    graph_data["models"] = [
        model
        for model in (graph_data.get("models") or [])
        if _norm_key(str(model.get("name") or "")) not in drop_keys
    ]
    kept_rels: list[dict] = []
    for rel in graph_data.get("relationships") or []:
        src_type = str(rel.get("source_type") or "").lower()
        dst_type = str(rel.get("destination_type") or "").lower()
        src = _norm_key(str(rel.get("source_node") or ""))
        dst = _norm_key(str(rel.get("destination_node") or ""))
        if src_type == "model" and src in drop_keys:
            continue
        if dst_type == "model" and dst in drop_keys:
            continue
        kept_rels.append(rel)
    graph_data["relationships"] = kept_rels
    dropped_models = before - len(graph_data.get("models") or [])
    if dropped_models:
        logger.info(
            "Dropped %d product-named Model(s) where distinct model IDs exist",
            dropped_models,
        )


def _ensure_product_model_links(graph_data: dict) -> int:
    """Add Product → HAS_MODEL → Model when names clearly support the hierarchy."""
    products = [
        p for p in (graph_data.get("products") or []) if str(p.get("name") or "").strip()
    ]
    models = [
        m for m in (graph_data.get("models") or []) if str(m.get("name") or "").strip()
    ]
    if not products or not models:
        return 0

    linked_models = {
        _norm_key(str(rel.get("destination_node") or ""))
        for rel in graph_data.get("relationships") or []
        if _rel_name(rel) == "has_model"
        and str(rel.get("source_type") or "").lower() == "product"
        and str(rel.get("destination_type") or "").lower() == "model"
    }
    added = 0
    for model in models:
        model_name = str(model.get("name") or "").strip()
        model_key = _norm_key(model_name)
        if not model_name or model_key in linked_models:
            continue
        model_tokens = _model_tokens(model_name)
        candidates: list[dict] = []
        for product in products:
            product_name = str(product.get("name") or "").strip()
            product_tokens = _model_tokens(product_name)
            if not product_tokens:
                continue
            # Product tokens must be a non-empty subset of model tokens.
            # Require at least one token with length >= 3 to avoid "PC"/"TV" noise.
            if not product_tokens.issubset(model_tokens):
                continue
            if not any(len(tok) >= 3 for tok in product_tokens):
                continue
            candidates.append(product)
        if len(candidates) != 1:
            continue
        product_name = str(candidates[0].get("name") or "").strip()
        _append_relationship(
            graph_data,
            source_node=product_name,
            source_type="product",
            relationship="has_model",
            destination_node=model_name,
            destination_type="model",
            evidence=(
                f"Model {model_name} belongs to product {product_name}."
            ),
            keywords="HAS_MODEL",
        )
        linked_models.add(model_key)
        added += 1
    if added:
        logger.info("Added %d Product → Model HAS_MODEL link(s)", added)
    return added


def _ensure_model_component_links(graph_data: dict) -> int:
    """When a product has exactly one model, re-home product components onto that model.

    Only adds Model → CONTAINS when the component is not already on any model.
    Does not invent components; only reuses explicit product-level component edges.
    """
    product_to_models: dict[str, set[str]] = {}
    for rel in graph_data.get("relationships") or []:
        if _rel_name(rel) != "has_model":
            continue
        if str(rel.get("source_type") or "").lower() != "product":
            continue
        if str(rel.get("destination_type") or "").lower() != "model":
            continue
        pk = _norm_key(str(rel.get("source_node") or ""))
        mk = _norm_key(str(rel.get("destination_node") or ""))
        if pk and mk:
            product_to_models.setdefault(pk, set()).add(
                str(rel.get("destination_node") or "").strip()
            )

    model_components = {
        (
            _norm_key(str(rel.get("source_node") or "")),
            _norm_key(str(rel.get("destination_node") or "")),
        )
        for rel in graph_data.get("relationships") or []
        if _rel_name(rel) == "contains"
        and str(rel.get("source_type") or "").lower() == "model"
    }

    added = 0
    for rel in list(graph_data.get("relationships") or []):
        if _rel_name(rel) != "contains":
            continue
        if str(rel.get("source_type") or "").lower() != "product":
            continue
        if str(rel.get("destination_type") or "").lower() != "component":
            continue
        product_name = str(rel.get("source_node") or "").strip()
        component_name = str(rel.get("destination_node") or "").strip()
        models = product_to_models.get(_norm_key(product_name)) or set()
        if len(models) != 1:
            continue
        model_name = next(iter(models))
        key = (_norm_key(model_name), _norm_key(component_name))
        if key in model_components:
            continue
        evidence = normalize_description(str(rel.get("evidence") or "")) or (
            f"Model {model_name} contains {component_name}."
        )
        _append_relationship(
            graph_data,
            source_node=model_name,
            source_type="model",
            relationship="contains",
            destination_node=component_name,
            destination_type="component",
            evidence=evidence,
            keywords=str(rel.get("keywords") or "CONTAINS"),
        )
        model_components.add(key)
        added += 1
    if added:
        logger.info("Added %d Model → Component CONTAINS link(s)", added)
    return added


_BRAND_OWNER_MENTION_KINDS = frozenset(
    {"copyright", "testing_by", "products", "support"}
)


def _entity_core_key(entity: dict) -> str:
    return _company_core_key(str(entity.get("name") or "").strip())


def _chunks_mentioning_company(chunks: list[dict], name: str) -> list[dict]:
    core = _company_core_key(name)
    if not core:
        return []
    matched: list[dict] = []
    for chunk in chunks:
        text_key = _norm_key(str(chunk.get("text") or ""))
        if core and core in text_key:
            matched.append(chunk)
    return matched


def _merge_company_mentions_from_chunks(
    chunks: list[dict],
    entities: list[dict],
) -> None:
    """Add Company nodes from ownership/publisher cues, without assigning roles."""
    by_core: dict[str, dict] = {}
    for entity in entities:
        entity_type = str(entity.get("entity_type") or "").strip().lower()
        if entity_type not in {"company", "applicant", "brand"}:
            continue
        core = _entity_core_key(entity)
        if core and core not in by_core:
            by_core[core] = entity

    for chunk in chunks:
        provenance = _chunk_provenance(chunk)
        for mention in extract_company_mentions(
            str(chunk.get("text") or ""),
            source_file=str(provenance.get("source_file") or ""),
            chunk_id=str(provenance.get("chunk_id") or ""),
            page_range=provenance.get("page_range") if isinstance(provenance.get("page_range"), dict) else None,
        ):
            core = _company_core_key(mention.name)
            if not core:
                continue
            existing = by_core.get(core)
            if existing is None:
                record = {
                    "name": mention.name,
                    "entity_type": "company",
                    "description": mention.evidence,
                    "source_file": mention.source_file,
                    "chunk_id": mention.chunk_id,
                    "page_range": mention.page_range,
                    "mention_kinds": list(mention.kinds or [mention.kind]),
                    "source_files": [mention.source_file] if mention.source_file else [],
                }
                if provenance.get("project_id"):
                    record["project_id"] = provenance["project_id"]
                entities.append(record)
                by_core[core] = record
                continue

            kinds = existing.setdefault("mention_kinds", [])
            for kind in mention.kinds or [mention.kind]:
                if kind not in kinds:
                    kinds.append(kind)
            files = existing.setdefault("source_files", [])
            if mention.source_file and mention.source_file not in files:
                files.append(mention.source_file)
            _merge_text_field(existing, {"description": mention.evidence}, "description")
            if _has_legal_suffix(mention.name) and not _has_legal_suffix(
                str(existing.get("name") or "")
            ):
                existing["name"] = mention.name
            elif len(mention.name) > len(str(existing.get("name") or "")) and _company_core_key(
                mention.name
            ) == core:
                if _has_legal_suffix(mention.name) or not _has_legal_suffix(
                    str(existing.get("name") or "")
                ):
                    existing["name"] = mention.name


def _demote_unsupported_applicant_roles(
    chunks: list[dict],
    entities: list[dict],
    relationships: list[dict],
) -> None:
    """Keep Applicant only when the source text supports that role."""
    demoted_cores: set[str] = set()
    for entity in entities:
        if str(entity.get("entity_type") or "").strip().lower() != "applicant":
            continue
        name = str(entity.get("name") or "").strip()
        mentioning = _chunks_mentioning_company(chunks, name)
        if not mentioning:
            continue
        supporting = "\n".join(str(chunk.get("text") or "") for chunk in mentioning)
        if text_supports_applicant_role(supporting):
            continue
        kinds = {str(kind).strip() for kind in (entity.get("mention_kinds") or [])}
        if kinds & _BRAND_OWNER_MENTION_KINDS:
            continue
        entity["entity_type"] = "company"
        kinds = entity.setdefault("mention_kinds", [])
        if "demoted_applicant" not in kinds:
            kinds.append("demoted_applicant")
        core = _company_core_key(name)
        if core:
            demoted_cores.add(core)

    if not demoted_cores:
        return

    kept: list[dict] = []
    for rel in relationships:
        rel_name = _rel_name(rel)
        src_core = _company_core_key(str(rel.get("source_node") or ""))
        dst_core = _company_core_key(str(rel.get("destination_node") or ""))
        if rel_name == "party_role_applicant" and dst_core in demoted_cores:
            continue
        if str(rel.get("source_type") or "").strip().lower() == "applicant" and src_core in demoted_cores:
            rel["source_type"] = "company"
        if str(rel.get("destination_type") or "").strip().lower() == "applicant" and dst_core in demoted_cores:
            rel["destination_type"] = "company"
        kept.append(rel)
    relationships[:] = kept


def _drop_unsupported_manufacturer_roles(
    chunks: list[dict],
    relationships: list[dict],
) -> None:
    """Drop manufacturer edges that are not supported by manufacturing language."""
    kept: list[dict] = []
    for rel in relationships:
        if _rel_name(rel) != "party_role_manufacturer":
            kept.append(rel)
            continue
        company_name = str(rel.get("destination_node") or rel.get("source_node") or "")
        mentioning = _chunks_mentioning_company(chunks, company_name)
        if not mentioning:
            kept.append(rel)
            continue
        supporting = "\n".join(str(chunk.get("text") or "") for chunk in mentioning)
        evidence = str(rel.get("evidence") or rel.get("description") or "")
        if text_supports_manufacturer_role(f"{supporting}\n{evidence}"):
            kept.append(rel)
            continue
        logger.info(
            "Dropping unsupported manufacturer role %s -[%s]-> %s",
            rel.get("source_node"),
            rel.get("relationship"),
            rel.get("destination_node"),
        )
    relationships[:] = kept


def _product_name_starts_with_company(product_name: str, company_name: str) -> bool:
    product_tokens = _norm_key(product_name).split()
    company_tokens = _company_core_key(company_name).split()
    if not product_tokens or not company_tokens:
        return False
    if len(product_tokens) < len(company_tokens):
        return False
    return product_tokens[: len(company_tokens)] == company_tokens


def _ensure_company_product_links(graph_data: dict) -> int:
    """Associate brand-owner companies with products they own or brand."""
    companies = [
        company
        for company in (graph_data.get("companies") or [])
        if _company_name(company)
    ]
    products = [
        product
        for product in (graph_data.get("products") or [])
        if str(product.get("name") or "").strip()
    ]
    if not companies or not products:
        return 0

    existing = {
        (
            _company_core_key(str(rel.get("source_node") or "")),
            _norm_key(str(rel.get("destination_node") or "")),
        )
        for rel in graph_data.get("relationships") or []
        if _rel_name(rel) == "has_product"
    }

    added = 0
    for company in companies:
        company_name = _company_name(company)
        kinds = {str(kind).strip() for kind in (company.get("mention_kinds") or [])}
        is_brand_owner = bool(kinds & _BRAND_OWNER_MENTION_KINDS)
        files = {
            str(name).strip()
            for name in [company.get("source_file"), *(company.get("source_files") or [])]
            if str(name).strip()
        }
        for product in products:
            product_name = str(product.get("name") or "").strip()
            key = (_company_core_key(company_name), _norm_key(product_name))
            if not product_name or key in existing:
                continue
            same_file = is_brand_owner and str(product.get("source_file") or "").strip() in files
            branded_name = _product_name_starts_with_company(product_name, company_name)
            if not same_file and not branded_name:
                continue
            evidence = normalize_description(str(company.get("evidence") or "")) or (
                f"{company_name} owns product {product_name}."
            )
            _append_relationship(
                graph_data,
                source_node=company_name,
                source_type="company",
                relationship="has_product",
                destination_node=product_name,
                destination_type="product",
                evidence=evidence,
                keywords="HAS_PRODUCT",
            )
            existing.add(key)
            added += 1
    if added:
        logger.info("Added %d Company -> Product HAS_PRODUCT link(s)", added)
    return added


def _promote_brand_owner_companies_to_applicants(graph_data: dict) -> None:
    """Product brand/owners are UL Applicants. Manufacturers/suppliers are not."""
    product_owner_cores = {
        _company_core_key(str(rel.get("source_node") or ""))
        for rel in graph_data.get("relationships") or []
        if _rel_name(rel) == "has_product"
        and str(rel.get("source_type") or "").strip().lower() in {"company", "applicant"}
    }
    brand_owner_cores = {
        _company_core_key(_company_name(company))
        for company in graph_data.get("companies") or []
        if {str(kind).strip() for kind in (company.get("mention_kinds") or [])}
        & _BRAND_OWNER_MENTION_KINDS
    }
    promote_cores = {core for core in product_owner_cores | brand_owner_cores if core}
    if not promote_cores:
        return

    applicants = list(graph_data.get("applicants") or [])
    applicant_by_core: dict[str, dict] = {}
    for item in applicants:
        core = _company_core_key(str(item.get("name") or ""))
        if core and core not in applicant_by_core:
            applicant_by_core[core] = item

    kept_companies: list[dict] = []
    rename: dict[str, str] = {}
    for company in graph_data.get("companies") or []:
        name = _company_name(company)
        core = _company_core_key(name)
        if core not in promote_cores:
            kept_companies.append(company)
            continue
        existing = applicant_by_core.get(core) or _find_equivalent_org(name, applicants)
        if existing is None:
            record = {
                "name": name,
                "evidence": company.get("evidence") or "",
                "source_file": company.get("source_file"),
                "source_files": list(company.get("source_files") or []),
                "mention_kinds": list(company.get("mention_kinds") or []),
                "page_range": company.get("page_range"),
                "chunk_id": company.get("chunk_id"),
                "aliases": list(company.get("aliases") or ([name] if name else [])),
            }
            applicants.append(record)
            applicant_by_core[core] = record
            applicant_name = name
        else:
            applicant_name = str(existing.get("name") or "").strip() or name
            if name != applicant_name:
                rename[name] = applicant_name
                rename[_norm_key(name)] = applicant_name
        for rel in graph_data.get("relationships") or []:
            if _company_core_key(str(rel.get("source_node") or "")) != core:
                continue
            if str(rel.get("source_type") or "").strip().lower() == "company":
                rel["source_type"] = "applicant"
                rel["source_node"] = applicant_name
        for rel in graph_data.get("relationships") or []:
            if _company_core_key(str(rel.get("destination_node") or "")) != core:
                continue
            if str(rel.get("destination_type") or "").strip().lower() != "company":
                continue
            rel_name = _rel_name(rel)
            if rel_name == "party_role_manufacturer":
                continue
            rel["destination_type"] = "applicant"
            rel["destination_node"] = applicant_name

    graph_data["companies"] = kept_companies
    graph_data["applicants"] = applicants
    _rewrite_relationship_names(graph_data.get("relationships") or [], rename)
    logger.info(
        "Promoted %d brand-owner company(ies) to Applicant",
        len(promote_cores),
    )


def _collapse_applicant_company_duplicates(graph_data: dict) -> None:
    """If Applicant exists, do not keep a second Company node for the same org."""
    applicants = [
        item for item in (graph_data.get("applicants") or []) if str(item.get("name") or "").strip()
    ]
    companies = [
        item for item in (graph_data.get("companies") or []) if _company_name(item)
    ]
    if not applicants or not companies:
        return

    kept_companies: list[dict] = []
    rename: dict[str, str] = {}
    for company in companies:
        name = _company_name(company)
        applicant = _find_equivalent_org(name, applicants)
        if applicant is None:
            kept_companies.append(company)
            continue
        applicant_name = str(applicant.get("name") or "").strip()
        if name != applicant_name:
            rename[name] = applicant_name
            rename[_norm_key(name)] = applicant_name
        for rel in graph_data.get("relationships") or []:
            if not _companies_are_same(str(rel.get("source_node") or ""), name) and not _companies_are_same(
                str(rel.get("destination_node") or ""), name
            ):
                continue
            if str(rel.get("source_type") or "").strip().lower() == "company" and _companies_are_same(
                str(rel.get("source_node") or ""), name
            ):
                rel["source_type"] = "applicant"
                rel["source_node"] = applicant_name
            if str(rel.get("destination_type") or "").strip().lower() == "company":
                if _companies_are_same(str(rel.get("destination_node") or ""), name):
                    rel["destination_type"] = "applicant"
                    rel["destination_node"] = applicant_name
    graph_data["companies"] = kept_companies
    _rewrite_relationship_names(graph_data.get("relationships") or [], rename)


def _ensure_hierarchy_hops(graph_data: dict) -> None:
    """Repair supported product/model/component hops. Do not invent SERVES or HAS_PRODUCT."""
    _canonicalize_extracted_relationships(graph_data)
    _drop_development_stage_variants(graph_data)
    _ensure_product_model_links(graph_data)
    _ensure_model_component_links(graph_data)
    _drop_product_named_models_when_distinct_ids_exist(graph_data)


def _filter_graph_to_target(
    graph_data: dict,
    target_product_name: str | None,
) -> dict:
    """Keep products matching comma-separated targets and connected relationships."""
    raw = (target_product_name or "").strip()
    if not raw:
        return graph_data

    targets = [part.strip() for part in raw.split(",") if part.strip()]
    if not targets:
        return graph_data
    target_keys = {_norm_key(t) for t in targets}

    products = []
    product_keys: set[str] = set()
    for product in graph_data.get("products") or []:
        name = str(product.get("name") or "").strip()
        aliases = [str(a).strip() for a in (product.get("aliases") or [])]
        names = [name, *aliases]
        if any(_norm_key(n) in target_keys for n in names if n):
            # Prefer exact requested casing when possible.
            for target in targets:
                if _norm_key(target) in {_norm_key(n) for n in names if n}:
                    product = {**product, "name": product.get("name") or target}
                    break
            products.append(product)
            product_keys.add(_norm_key(str(product.get("name") or "")))
            for alias in product.get("aliases") or []:
                product_keys.add(_norm_key(str(alias)))

    # Ensure explicit target product nodes exist even if extraction missed them.
    existing = {_norm_key(str(p.get("name") or "")) for p in products}
    for target in targets:
        key = _norm_key(target)
        if key and key not in existing:
            products.append(
                {
                    "name": target,
                    "canonical_name": target,
                    "aliases": [target],
                    "evidence": f"Requested target product: {target}",
                    "identity_key": f"product:{key}",
                }
            )
            product_keys.add(key)
            existing.add(key)

    keep_names = set(product_keys)
    for rel in graph_data.get("relationships") or []:
        src = _norm_key(str(rel.get("source_node") or ""))
        dst = _norm_key(str(rel.get("destination_node") or ""))
        rel_type = str(rel.get("relationship") or "").strip().lower()
        if src in product_keys or dst in product_keys:
            keep_names.add(src)
            keep_names.add(dst)
        if rel_type == "has_model" and src in product_keys:
            keep_names.add(dst)

    for model in graph_data.get("models") or []:
        model_name = _norm_key(str(model.get("name") or ""))
        if model_name in keep_names:
            for alias in model.get("aliases") or []:
                keep_names.add(_norm_key(str(alias)))

    # Expand once more along relationships for 1-hop neighbors of kept set.
    changed = True
    while changed:
        changed = False
        for rel in graph_data.get("relationships") or []:
            src = _norm_key(str(rel.get("source_node") or ""))
            dst = _norm_key(str(rel.get("destination_node") or ""))
            if src in keep_names and dst not in keep_names:
                keep_names.add(dst)
                changed = True
            if dst in keep_names and src not in keep_names:
                keep_names.add(src)
                changed = True

    def _keep_list(items: list[dict], name_keys: tuple[str, ...] = ("name",)) -> list[dict]:
        kept = []
        for item in items:
            name = ""
            for key in name_keys:
                name = str(item.get(key) or "").strip()
                if name:
                    break
            if _norm_key(name) in keep_names:
                kept.append(item)
        return kept

    relationships = [
        rel
        for rel in graph_data.get("relationships") or []
        if _norm_key(str(rel.get("source_node") or "")) in keep_names
        and _norm_key(str(rel.get("destination_node") or "")) in keep_names
    ]

    filtered = dict(graph_data)
    filtered["products"] = products
    filtered["relationships"] = relationships
    name_keys_by_bucket = {
        "companies": ("name", "company_name"),
        "models": ("name", "canonical_name"),
        "components": ("name", "component_name", "canonical_name"),
        "normalized_components": ("name", "component_name", "canonical_name"),
    }
    for bucket, items in graph_data.items():
        if bucket in {"products", "relationships"} or not isinstance(items, list):
            continue
        filtered[bucket] = _keep_list(
            items,
            name_keys_by_bucket.get(bucket, ("name",)),
        )
    return filtered


def _entity_names_from_graph(graph_data: dict, extra_entities: list[dict] | None = None) -> set[str]:
    names: set[str] = set()
    for item in extra_entities or []:
        name = str(item.get("name") or "").strip()
        if name:
            names.add(_norm_key(name))
    for bucket, items in graph_data.items():
        if bucket.startswith("_") or bucket == "relationships" or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("name", "company_name", "canonical_name", "component_name"):
                value = str(item.get(key) or "").strip()
                if value:
                    names.add(_norm_key(value))
            for alias in item.get("aliases") or []:
                alias_name = str(alias).strip()
                if alias_name:
                    names.add(_norm_key(alias_name))
    return names


def _drop_cross_project_relationships(
    graph_data: dict,
    *,
    project_id: str,
    current_run_entities: list[dict],
) -> None:
    """Drop relationships that are not from the current extraction run."""
    current = str(project_id or "").strip()
    if not current:
        return

    allowed_names = _entity_names_from_graph(graph_data, current_run_entities)
    kept: list[dict] = []
    for rel in graph_data.get("relationships") or []:
        rel_project = str(rel.get("project_id") or "").strip()
        if rel_project and rel_project != current:
            logger.warning(
                "Dropping relationship from project %s while writing project %s: %s -[%s]-> %s",
                rel_project,
                current,
                rel.get("source_node"),
                rel.get("relationship"),
                rel.get("destination_node"),
            )
            continue
        source_key = _norm_key(str(rel.get("source_node") or ""))
        target_key = _norm_key(
            str(rel.get("destination_node") or rel.get("target_node") or "")
        )
        if source_key not in allowed_names or target_key not in allowed_names:
            logger.warning(
                "Dropping relationship with endpoint outside current-run entities: "
                "%s -[%s]-> %s",
                rel.get("source_node"),
                rel.get("relationship"),
                rel.get("destination_node"),
            )
            continue
        if not rel_project:
            rel["project_id"] = current
        kept.append(rel)
    graph_data["relationships"] = kept


def extract_graph_from_chunks(
    chunks: list[dict],
    *,
    target_product_name: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """
    Extract a connected knowledge graph from current-project chunks using LightRAG prompts.

    Returns graph_data compatible with the existing process response / Neo4j path.
    """
    if not chunks:
        return _empty_graph_data()

    extraction_chunks, current_project_id = _select_chunks_for_project(
        chunks, project_id
    )
    logger.info(
        "Starting KG extraction project_id=%s chunks=%d",
        current_project_id or "(unscoped)",
        len(extraction_chunks),
    )
    if not extraction_chunks:
        return _empty_graph_data()

    logger.info(
        "LightRAG extraction across %d chunk(s) from %d file(s)",
        len(extraction_chunks),
        len({str(c.get("source_file") or "") for c in extraction_chunks}),
    )

    all_entities: list[dict] = []
    all_relationships: list[dict] = []

    workers = min(config.EXTRACTION_CONCURRENCY, len(extraction_chunks))
    logger.info(
        "Starting parallel LightRAG extraction for %d chunk(s) with %d worker(s)",
        len(extraction_chunks),
        workers,
    )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(extract_chunk_with_lightrag, chunk)
            for chunk in extraction_chunks
        ]
        results = [future.result() for future in futures]

    logger.info(
        "Completed LightRAG extraction for %d chunk(s)",
        len(extraction_chunks),
    )

    for _chunk, (entities, relationships) in zip(extraction_chunks, results):
        all_entities.extend(entities)
        all_relationships.extend(relationships)

    _merge_company_mentions_from_chunks(extraction_chunks, all_entities)
    _demote_unsupported_applicant_roles(
        extraction_chunks, all_entities, all_relationships
    )
    _drop_unsupported_manufacturer_roles(extraction_chunks, all_relationships)

    buckets = _bucket_entities(all_entities)
    graph_data = {
        **buckets,
        "relationships": _dedupe_relationships(all_relationships),
    }
    if current_project_id:
        graph_data["_project_id"] = current_project_id
    _dedupe_companies(graph_data)
    _drop_implausible_org_relationships(graph_data)
    _ensure_company_product_links(graph_data)
    _promote_brand_owner_companies_to_applicants(graph_data)
    _collapse_applicant_company_duplicates(graph_data)
    _dedupe_applicants(graph_data)
    _dedupe_models(graph_data)
    _dedupe_components(graph_data)
    _dedupe_standards(graph_data)
    _dedupe_alias_entities(graph_data, "locations")
    _dedupe_alias_entities(graph_data, "addresses")
    graph_data["relationships"] = _dedupe_relationships(
        graph_data.get("relationships") or []
    )
    graph_data = _filter_graph_to_target(graph_data, target_product_name)
    if current_project_id:
        graph_data["_project_id"] = current_project_id
    _ensure_hierarchy_hops(graph_data)
    graph_data["relationships"] = _dedupe_relationships(
        graph_data.get("relationships") or []
    )
    _drop_cross_project_relationships(
        graph_data,
        project_id=current_project_id,
        current_run_entities=all_entities,
    )
    graph_data.pop("_project_id", None)

    logger.info(
        "Extraction completed project_id=%s entities=%d relationships=%d",
        current_project_id or "(unscoped)",
        sum(
            len(items)
            for key, items in graph_data.items()
            if key != "relationships" and isinstance(items, list)
        ),
        len(graph_data.get("relationships") or []),
    )
    logger.info(
        "LightRAG graph: applicants=%d companies=%d products=%d models=%d "
        "components=%d locations=%d file_numbers=%d volumes=%d "
        "sections=%d standards=%d clauses=%d certifications=%d tests=%d "
        "relationships=%d",
        len(graph_data.get("applicants") or []),
        len(graph_data.get("companies") or []),
        len(graph_data.get("products") or []),
        len(graph_data.get("models") or []),
        len(graph_data.get("components") or []),
        len(graph_data.get("locations") or []),
        len(graph_data.get("file_numbers") or []),
        len(graph_data.get("volumes") or []),
        len(graph_data.get("sections") or []),
        len(graph_data.get("standards") or []),
        len(graph_data.get("clauses") or []),
        len(graph_data.get("certifications") or []),
        len(graph_data.get("tests") or []),
        len(graph_data.get("relationships") or []),
    )
    return graph_data
