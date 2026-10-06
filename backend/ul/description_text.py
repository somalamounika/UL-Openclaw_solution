"""Concise description helpers for graph entities and relationships."""

from __future__ import annotations

import re

_DEFAULT_MAX_WORDS = 18
_DEFAULT_MAX_CHARS = 140
_PIPE_SEP = re.compile(r"\s*\|\s*")
_SENTENCE_END = re.compile(r"[.!?]\s+")


def normalize_description(
    text: str,
    *,
    max_words: int = _DEFAULT_MAX_WORDS,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Return a short, plain description suitable for nodes and edges."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""

    if " | " in raw:
        parts = [part.strip() for part in _PIPE_SEP.split(raw) if part.strip()]
        raw = min(parts, key=len) if parts else raw

    first_sentence = _SENTENCE_END.split(raw, maxsplit=1)[0].strip()
    if first_sentence and first_sentence != raw:
        if not first_sentence.endswith((".", "!", "?")):
            first_sentence += "."
        raw = first_sentence

    if len(raw.split()) > max_words or len(raw) > max_chars:
        for match in _SENTENCE_END.finditer(raw):
            candidate = raw[: match.end()].strip()
            if (
                candidate
                and len(candidate.split()) <= max_words
                and len(candidate) <= max_chars
            ):
                raw = candidate
                break

    words = raw.split()
    if len(words) > max_words:
        raw = " ".join(words[:max_words]).rstrip(",;:-") + "."

    if len(raw) > max_chars:
        trimmed = raw[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-")
        raw = f"{trimmed}." if trimmed else raw[:max_chars]

    return raw.strip()


def merge_description_field(existing: dict, incoming: dict, field: str) -> None:
    """Keep the shorter plain description when duplicates are merged."""
    extra = normalize_description(str(incoming.get(field) or ""))
    if not extra:
        return
    prior = normalize_description(str(existing.get(field) or ""))
    if not prior:
        existing[field] = extra
        return
    if extra in prior or prior in extra:
        existing[field] = extra if len(extra) <= len(prior) else prior
        return
    existing[field] = extra if len(extra) <= len(prior) else prior
