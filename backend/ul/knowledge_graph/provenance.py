"""Document / evidence identity and provenance helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def compute_content_hash(paths: list[Path] | list[str] | None) -> str:
    """Stable hash of source file names + bytes for duplicate-ingestion detection."""
    digest = hashlib.sha256()
    files = [Path(path) for path in (paths or [])]
    files.sort(key=lambda path: path.name.lower())
    if not files:
        return ""
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def make_evidence_id(
    *,
    document_id: str,
    kind: str,
    canonical_id: str,
    chunk_id: str = "",
    page_number: int | str | None = None,
    rel_key: str = "",
) -> str:
    """Deterministic evidence id so re-ingestion cannot duplicate evidence."""
    raw = "|".join(
        [
            str(document_id or "").strip(),
            str(kind or "entity").strip(),
            str(canonical_id or "").strip(),
            str(chunk_id or "").strip(),
            str(page_number if page_number not in (None, "") else ""),
            str(rel_key or "").strip(),
        ]
    )
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def page_number_from_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("start", "end", "page", "page_number"):
            parsed = page_number_from_value(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def provenance_from_triplet(triplet: dict | None) -> dict[str, Any]:
    row = triplet or {}
    page_range = row.get("page_range")
    page_number = page_number_from_value(row.get("page_number"))
    if page_number is None:
        page_number = page_number_from_value(page_range)
    chunk_id = str(row.get("chunk_id") or "").strip()
    source_file = str(row.get("source_file") or "").strip()
    source_section = str(row.get("source_section") or row.get("section") or "").strip()
    return {
        "chunk_id": chunk_id,
        "page_number": page_number,
        "page_range": page_range if isinstance(page_range, dict) else {},
        "source_file": source_file,
        "source_section": source_section,
        "source_text": str(row.get("source_text") or row.get("description") or "").strip(),
        "confidence": row.get("confidence"),
    }


def evidence_record(
    *,
    document_id: str,
    canonical_id: str,
    description: str = "",
    kind: str = "entity",
    rel_key: str = "",
    provenance: dict | None = None,
    extraction_timestamp: str | None = None,
) -> dict[str, Any]:
    meta = provenance or {}
    chunk_id = str(meta.get("chunk_id") or "").strip()
    page_number = page_number_from_value(meta.get("page_number"))
    evidence_id = make_evidence_id(
        document_id=document_id,
        kind=kind,
        canonical_id=canonical_id,
        chunk_id=chunk_id,
        page_number=page_number,
        rel_key=rel_key,
    )
    return {
        "evidence_id": evidence_id,
        "document_id": document_id,
        "kind": kind,
        "canonical_id": canonical_id,
        "description": (description or str(meta.get("source_text") or "")).strip(),
        "page_number": page_number,
        "chunk_id": chunk_id,
        "source_file": str(meta.get("source_file") or "").strip(),
        "source_section": str(meta.get("source_section") or "").strip(),
        "source_text": str(meta.get("source_text") or description or "").strip(),
        "confidence": meta.get("confidence"),
        "extraction_timestamp": extraction_timestamp or utc_now_iso(),
        "rel_key": rel_key,
    }
