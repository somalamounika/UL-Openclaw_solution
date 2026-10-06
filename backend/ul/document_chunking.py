"""PDF extraction and LLM semantic chunking for the UL pipeline."""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from pathlib import Path

import fitz

from ul.llm_client import chat_json

logger = logging.getLogger(__name__)

# chars ≈ tokens * 4 for batching/packing estimates.
_CHARS_PER_TOKEN = 4
# How much page text to send to the chunking LLM per call.
_LLM_INPUT_CHUNK_TOKENS = 20_000
_MAX_BATCH_CHARS = _LLM_INPUT_CHUNK_TOKENS * _CHARS_PER_TOKEN
# Stored/indexed chunk size (must stay under embedding max ~8192).
_CHUNK_SIZE_TOKENS = 8_000
_TARGET_CHUNK_CHARS = _CHUNK_SIZE_TOKENS * _CHARS_PER_TOKEN

# document_id -> list of chunk records (local mapping cache)
_INDEX: dict[str, list[dict]] = {}

CHUNKING_SYSTEM_PROMPT = f"""You are processing a technical product document.

Split the provided content into semantically meaningful sections.

Rules:
- Preserve the original source meaning.
- Do not summarize the source.
- Do not invent content.
- Keep headings with their associated content.
- Keep related paragraphs together.
- Keep tables with their relevant surrounding context.
- Keep product/component descriptions and their specifications together.
- Avoid splitting sentences or logical sections midway.
- Each chunk should represent one coherent topic.
- Prefer approximately {_CHUNK_SIZE_TOKENS} tokens per chunk (~{_TARGET_CHUNK_CHARS} characters).
- Prefer fewer, larger sections near the target size over many small ones.
- Only split earlier when a hard topic boundary requires it.
- Semantic coherence is more important than exact token count.

Return ONLY valid JSON with this exact structure:
{{
  "sections": [
    {{"text": "verbatim section text from the source"}}
  ]
}}

Do not include chunk_id, source_file, page numbers, or page_range.
Do not include explanations or markdown."""

CHUNKING_USER_PROMPT_TEMPLATE = """Split the following document content into semantic sections.

Content (page markers are for your reference only; do not emit page metadata):

---
{content}
---

Return JSON: {{"sections": [{{"text": "..."}}]}}"""


# ---------------------------------------------------------------------------
# 1. PDF extraction
# ---------------------------------------------------------------------------


def extract_pages_from_pdf(file_path: str) -> list[dict]:
    """
    Extract non-empty pages from a PDF.

    Returns a list of dicts with source_file, page_number, and text.
    Empty pages are skipped. Extraction errors are handled per page when possible.
    """
    path = Path(file_path)
    source_file = path.name
    logger.info("Extracting page-level text from PDF: %s", source_file)

    pages: list[dict] = []

    try:
        doc = fitz.open(file_path)
    except Exception as exc:
        raise ValueError(f"Failed to open PDF '{source_file}': {exc}") from exc

    try:
        for page_index, page in enumerate(doc):
            page_number = page_index + 1
            try:
                page_text = page.get_text("text").strip()
            except Exception as exc:
                logger.warning(
                    "Failed to extract text from %s page %d: %s",
                    source_file,
                    page_number,
                    exc,
                )
                continue

            if not page_text:
                continue

            pages.append(
                {
                    "source_file": source_file,
                    "page_number": page_number,
                    "text": page_text,
                }
            )
    finally:
        doc.close()

    if not pages:
        raise ValueError(f"No text could be extracted from {source_file}")

    logger.info("Extracted %d non-empty page(s) from %s", len(pages), source_file)
    return pages


def extract_text_from_pdf(file_path: str) -> str:
    """Extract readable text from a PDF file (all non-empty pages joined)."""
    pages = extract_pages_from_pdf(file_path)
    text = "\n\n".join(page["text"] for page in pages).strip()
    logger.info("Extracted %d characters from %s", len(text), Path(file_path).name)
    return text


# ---------------------------------------------------------------------------
# 2. LLM semantic chunking
# ---------------------------------------------------------------------------


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _parse_sections_response(content: str) -> list[str]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("LLM chunking response is not a JSON object")

    sections = data.get("sections", [])
    if not isinstance(sections, list):
        raise ValueError("LLM chunking response must contain a sections array")

    texts: list[str] = []
    for section in sections:
        if isinstance(section, str):
            text = section.strip()
        elif isinstance(section, dict):
            text = str(section.get("text", "")).strip()
        else:
            continue
        if text:
            texts.append(text)
    return texts


def _page_range_for_text(section_text: str, pages: list[dict]) -> dict[str, int]:
    """Derive page_range from page content overlap (never from the LLM)."""
    needle = _collapse_ws(section_text)
    hits: list[int] = []

    for page in pages:
        hay = _collapse_ws(page["text"])
        if not hay:
            continue
        if hay in needle or needle in hay:
            hits.append(int(page["page_number"]))
            continue

        page_words = set(hay.split())
        if not page_words:
            continue
        section_words = set(needle.split())
        overlap = len(page_words & section_words) / len(page_words)
        if overlap >= 0.45:
            hits.append(int(page["page_number"]))

    if not hits:
        return {
            "start": int(pages[0]["page_number"]),
            "end": int(pages[-1]["page_number"]),
        }
    return {"start": min(hits), "end": max(hits)}


def _format_pages_for_prompt(pages: list[dict]) -> str:
    parts: list[str] = []
    for page in pages:
        parts.append(f"--- Page {page['page_number']} ---\n{page['text']}")
    return "\n\n".join(parts)


def _batch_pages(pages: list[dict]) -> list[list[dict]]:
    """Group pages into ~_LLM_INPUT_CHUNK_TOKENS batches for a single LLM call."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0

    for page in pages:
        page_chars = len(page["text"])
        if current and current_chars + page_chars > _MAX_BATCH_CHARS:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(page)
        current_chars += page_chars

    if current:
        batches.append(current)
    return batches


def _merge_texts_to_target(texts: list[str]) -> list[str]:
    """Merge section texts up to the target chunk size."""
    if not texts:
        return []

    merged: list[str] = []
    current = ""
    for text in texts:
        candidate = f"{current}\n\n{text}".strip() if current else text
        if current and len(candidate) > _TARGET_CHUNK_CHARS:
            merged.append(current)
            current = text
        else:
            current = candidate
    if current:
        merged.append(current)
    return merged


def _fallback_chunks(
    pages: list[dict],
    start_index: int,
    *,
    chunk_id_prefix: str = "chunk",
) -> list[dict]:
    """Pack pages into ~_CHUNK_SIZE_TOKENS chunks when LLM chunking fails."""
    chunks: list[dict] = []
    current_pages: list[dict] = []
    current_chars = 0
    next_index = start_index

    def flush() -> None:
        nonlocal next_index, current_pages, current_chars
        if not current_pages:
            return
        text = "\n\n".join(page["text"] for page in current_pages).strip()
        chunks.append(
            {
                "chunk_id": f"{chunk_id_prefix}_{next_index:06d}",
                "source_file": current_pages[0]["source_file"],
                "page_range": {
                    "start": int(current_pages[0]["page_number"]),
                    "end": int(current_pages[-1]["page_number"]),
                },
                "text": text,
            }
        )
        next_index += 1
        current_pages = []
        current_chars = 0

    for page in pages:
        page_chars = len(page["text"])
        if current_pages and current_chars + page_chars > _TARGET_CHUNK_CHARS:
            flush()
        current_pages.append(page)
        current_chars += page_chars

    flush()
    return chunks


def _chunk_page_batch(
    pages: list[dict],
    start_index: int,
    *,
    chunk_id_prefix: str = "chunk",
) -> list[dict]:
    source_file = pages[0]["source_file"]
    prompt_content = _format_pages_for_prompt(pages)

    try:
        raw = chat_json(
            CHUNKING_SYSTEM_PROMPT,
            CHUNKING_USER_PROMPT_TEMPLATE.format(content=prompt_content),
        )
        section_texts = _parse_sections_response(raw)
    except Exception as exc:
        logger.warning(
            "Semantic chunking failed for %s (pages %d-%d): %s; using size fallback",
            source_file,
            pages[0]["page_number"],
            pages[-1]["page_number"],
            exc,
        )
        return _fallback_chunks(pages, start_index, chunk_id_prefix=chunk_id_prefix)

    if not section_texts:
        logger.warning(
            "LLM returned no sections for %s (pages %d-%d); using size fallback",
            source_file,
            pages[0]["page_number"],
            pages[-1]["page_number"],
        )
        return _fallback_chunks(pages, start_index, chunk_id_prefix=chunk_id_prefix)

    section_texts = _merge_texts_to_target(section_texts)

    chunks: list[dict] = []
    for offset, text in enumerate(section_texts):
        chunk_index = start_index + offset
        chunks.append(
            {
                "chunk_id": f"{chunk_id_prefix}_{chunk_index:06d}",
                "source_file": source_file,
                "page_range": _page_range_for_text(text, pages),
                "text": text,
            }
        )
    return chunks


def create_semantic_chunks(
    pages: list[dict],
    *,
    start_index: int = 1,
    chunk_id_prefix: str = "chunk",
) -> list[dict]:
    """
    Split page-level content into semantic chunks via LLM.

    Metadata (chunk_id, source_file, page_range) is attached programmatically.
    """
    if not pages:
        return []

    source_file = pages[0]["source_file"]
    logger.info(
        "Semantic chunking %d page(s) from %s "
        "(llm_input ~%d tokens/batch, stored chunk ~%d tokens)",
        len(pages),
        source_file,
        _LLM_INPUT_CHUNK_TOKENS,
        _CHUNK_SIZE_TOKENS,
    )

    all_chunks: list[dict] = []
    next_index = start_index

    for batch in _batch_pages(pages):
        batch_chunks = _chunk_page_batch(
            batch,
            next_index,
            chunk_id_prefix=chunk_id_prefix,
        )
        all_chunks.extend(batch_chunks)
        next_index += len(batch_chunks)

    logger.info(
        "Created %d semantic chunk(s) from %s",
        len(all_chunks),
        source_file,
    )
    return all_chunks


# ---------------------------------------------------------------------------
# In-memory chunk store for component/entity mapping retrieval.
# Chatbot retrieval uses LightRAG native mix search, not this in-memory index.
# ---------------------------------------------------------------------------


def _upsert_memory(document_id: str, chunks: list[dict]) -> int:
    existing = {c["chunk_id"]: c for c in _INDEX.get(document_id, []) if c.get("chunk_id")}
    written = 0
    for chunk in chunks:
        chunk_id = chunk.get("chunk_id")
        if not chunk_id:
            logger.warning("Skipping chunk without chunk_id")
            continue
        existing[chunk_id] = deepcopy(chunk)
        written += 1
    _INDEX[document_id] = list(existing.values())
    return written


def _get_memory(document_id: str, *, source_file: str | None = None) -> list[dict]:
    chunks = _INDEX.get(document_id, [])
    if source_file is None:
        return deepcopy(chunks)
    return deepcopy([c for c in chunks if c.get("source_file") == source_file])


def upsert_chunks(document_id: str, chunks: list[dict]) -> int:
    """Store chunks in the local mapping index used by component_extractor."""
    if not document_id:
        raise ValueError("document_id is required to index chunks")

    valid_chunks = [
        c for c in chunks if c.get("chunk_id") and str(c.get("text") or "").strip()
    ]
    skipped = len(chunks) - len(valid_chunks)
    if skipped:
        logger.warning("Skipping %d chunk(s) missing chunk_id or text", skipped)

    if not valid_chunks:
        return 0

    return _upsert_memory(document_id, valid_chunks)


def get_chunks(
    document_id: str,
    *,
    source_file: str | None = None,
) -> list[dict]:
    """Return locally stored chunks for component/entity mapping."""
    return _get_memory(document_id, source_file=source_file)


def list_document_ids() -> list[str]:
    """Return document ids known in the local cache."""
    return sorted(_INDEX.keys())


def clear_chunks(document_id: str | None = None) -> None:
    """Clear local chunk cache used by component/entity mapping."""
    if document_id is None:
        _INDEX.clear()
        logger.info("Cleared entire local chunk cache")
        return
    _INDEX.pop(document_id, None)
    logger.info("Cleared local chunk cache for document_id=%s", document_id)


def reference_document_id(source_file: str) -> str:
    """Stable document_id for a backend reference PDF."""
    stem = Path(source_file).stem.lower().replace(" ", "-")
    return f"ref-{stem}"


def ensure_pdf_indexed(
    file_path: str | Path,
    *,
    document_id: str | None = None,
    chunk_id_prefix: str = "chunk",
    force: bool = False,
) -> tuple[str, int]:
    """
    Extract, chunk, and store a PDF once in the local mapping index.

    Reuses existing chunks for document_id when present.
    Returns (document_id, chunk_count).
    """
    path = Path(file_path)
    doc_id = document_id or reference_document_id(path.name)

    if not force:
        existing = get_chunks(doc_id, source_file=path.name)
        if existing:
            logger.info(
                "Reusing %d indexed chunk(s) for %s (document_id=%s)",
                len(existing),
                path.name,
                doc_id,
            )
            return doc_id, len(existing)

    pages = extract_pages_from_pdf(str(path))
    chunks = create_semantic_chunks(
        pages,
        start_index=1,
        chunk_id_prefix=chunk_id_prefix,
    )
    upsert_chunks(doc_id, chunks)
    return doc_id, len(chunks)


def _memory_rank_chunks(query: str, chunks: list[dict], *, top_k: int) -> list[dict]:
    """Simple lexical ranking for in-memory mapping retrieval."""
    q_words = set(_collapse_ws(query).split())
    if not q_words:
        return chunks[:top_k]

    scored: list[tuple[float, dict]] = []
    for chunk in chunks:
        text = _collapse_ws(str(chunk.get("text") or ""))
        if not text:
            continue
        words = set(text.split())
        overlap = len(q_words & words) / max(len(q_words), 1)
        scored.append((overlap, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [deepcopy(chunk) for score, chunk in scored[:top_k] if score > 0] or [
        deepcopy(c) for c in chunks[:top_k]
    ]


def search_relevant_chunks(
    query: str,
    *,
    document_id: str,
    source_file: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """Retrieve the most relevant locally stored chunks for a mapping query."""
    query = (query or "").strip()
    if not query:
        return []
    return _memory_rank_chunks(
        query,
        _get_memory(document_id, source_file=source_file),
        top_k=top_k,
    )


def format_chunks_for_prompt(chunks: list[dict], *, max_chars: int = 12000) -> str:
    """Format retrieved chunks for an LLM prompt without logging full content."""
    parts: list[str] = []
    used = 0
    for chunk in chunks:
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        header = (
            f"[source_file={chunk.get('source_file')} "
            f"chunk_id={chunk.get('chunk_id')} "
            f"pages={chunk.get('page_range')}]\n"
        )
        block = header + text
        if used + len(block) > max_chars and parts:
            break
        parts.append(block)
        used += len(block)
    return "\n\n---\n\n".join(parts)
