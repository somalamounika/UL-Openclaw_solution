"""Entity resolution against existing canonical UL Knowledge Graph nodes.

Resolution order:
  1. Deterministic identifiers (standard code, clause+standard, model/part numbers)
  2. Proposed canonical_id exact match
  3. Normalized-name or alias exact match
  4. Strong attribute match (product+company, etc.)
  5. Conservative string similarity fallback

Low-confidence matches are never merged; they create a new canonical entity
and are logged as REVIEW_REQUIRED.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable

from ul.knowledge_graph.canonical_ids import (
    UL_CANONICAL_ID,
    UL_DISPLAY_NAME,
    canonical_id_for,
)
from ul.knowledge_graph.labels import canonical_entity_type
from ul.knowledge_graph.normalization import (
    company_names_equivalent,
    entity_names_equivalent,
    is_ul_entity,
    normalize_entity_name,
    normalize_for_type,
    preferred_company_name,
    preferred_entity_name,
)

logger = logging.getLogger(__name__)

ENTITY_MATCH_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.75

MATCHED_EXISTING = "MATCHED_EXISTING"
CREATED_NEW = "CREATED_NEW"
REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass
class ExtractedEntity:
    entity_type: str
    name: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    code: str | None = None
    model_number: str | None = None
    part_number: str | None = None
    company_name: str | None = None
    company_canonical_id: str | None = None
    product_canonical_id: str | None = None
    parent_canonical_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return canonical_entity_type(self.entity_type)


@dataclass
class Resolution:
    entity_type: str
    name: str
    normalized_name: str
    canonical_id: str
    decision: str
    confidence: float
    matched_canonical_id: str | None = None
    aliases: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    is_new: bool = True


def _unique_aliases(*groups: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for group in groups:
        for item in group:
            text = str(item or "").strip()
            if not text:
                continue
            key = normalize_entity_name(text)
            if not key or key in seen:
                continue
            seen.add(key)
            values.append(text)
    return values


def _candidate_names(candidate: dict) -> list[str]:
    names = [str(candidate.get("name") or "")]
    names.extend(str(alias) for alias in (candidate.get("aliases") or []))
    if candidate.get("normalized_name"):
        names.append(str(candidate["normalized_name"]))
    if candidate.get("code"):
        names.append(str(candidate["code"]))
    return [name for name in names if str(name).strip()]


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _log_resolution(extracted: ExtractedEntity, resolution: Resolution) -> None:
    extra = ""
    if resolution.decision == MATCHED_EXISTING:
        extra = f" Existing canonical_id: {resolution.canonical_id}"
    logger.info(
        "Entity: %s | Type: %s | Normalized: %s | Resolution: %s | "
        "canonical_id: %s | Confidence: %.2f%s",
        extracted.name,
        extracted.kind,
        resolution.normalized_name,
        resolution.decision,
        resolution.canonical_id,
        resolution.confidence,
        extra,
    )


def _ul_resolution(extracted: ExtractedEntity) -> Resolution:
    aliases = _unique_aliases([extracted.name], extracted.aliases, [UL_DISPLAY_NAME, "UL", "UL Solutions, Inc."])
    resolution = Resolution(
        entity_type="ul",
        name=UL_DISPLAY_NAME,
        normalized_name="ul solutions",
        canonical_id=UL_CANONICAL_ID,
        decision=MATCHED_EXISTING,
        confidence=1.0,
        matched_canonical_id=UL_CANONICAL_ID,
        aliases=aliases,
        is_new=False,
    )
    _log_resolution(extracted, resolution)
    return resolution


def resolve_entity(
    extracted: ExtractedEntity,
    existing: list[dict] | None = None,
    *,
    match_threshold: float = ENTITY_MATCH_THRESHOLD,
) -> Resolution:
    """Resolve one extracted entity against existing canonical candidates."""
    kind = extracted.kind
    name = (extracted.name or "").strip()
    if kind == "ul" or is_ul_entity(name):
        return _ul_resolution(extracted)

    normalized = normalize_for_type(kind, name)
    proposed_id = canonical_id_for(
        kind,
        name,
        parent_canonical_id=extracted.parent_canonical_id,
        company_canonical_id=extracted.company_canonical_id,
        product_canonical_id=extracted.product_canonical_id,
        code=extracted.code,
        model_number=extracted.model_number,
        part_number=extracted.part_number,
    )
    aliases = _unique_aliases([name], extracted.aliases)
    attributes = {
        "code": extracted.code,
        "model_number": extracted.model_number,
        "part_number": extracted.part_number,
        "company_canonical_id": extracted.company_canonical_id,
        "product_canonical_id": extracted.product_canonical_id,
        "parent_canonical_id": extracted.parent_canonical_id,
        **(extracted.extra or {}),
    }

    best: tuple[float, dict, str] | None = None
    for candidate in existing or []:
        if canonical_entity_type(str(candidate.get("entity_type") or kind)) != kind:
            continue
        candidate_id = str(candidate.get("canonical_id") or "").strip()
        score, reason = _score_candidate(extracted, normalized, proposed_id, candidate)
        if best is None or score > best[0]:
            best = (score, candidate, reason)

    decision = CREATED_NEW
    confidence = 1.0
    matched_id = None
    is_new = True
    canonical_id = proposed_id
    display_name = name

    if best is not None:
        score, candidate, reason = best
        if score >= match_threshold:
            decision = MATCHED_EXISTING
            confidence = score
            matched_id = str(candidate.get("canonical_id") or proposed_id)
            canonical_id = matched_id
            candidate_name = str(candidate.get("name") or name)
            display_name = preferred_entity_name(candidate_name, name)
            aliases = _unique_aliases(aliases, _candidate_names(candidate))
            is_new = False
        elif score >= REVIEW_THRESHOLD:
            decision = REVIEW_REQUIRED
            confidence = score
            logger.info(
                "Entity: %s | Type: %s | Normalized: %s | Resolution: %s | "
                "proposed: %s | candidate: %s | Confidence: %.2f | reason: %s",
                name,
                kind,
                normalized,
                REVIEW_REQUIRED,
                proposed_id,
                candidate.get("canonical_id"),
                score,
                reason,
            )
        else:
            confidence = 1.0

    resolution = Resolution(
        entity_type=kind,
        name=display_name,
        normalized_name=normalized,
        canonical_id=canonical_id,
        decision=decision,
        confidence=confidence,
        matched_canonical_id=matched_id,
        aliases=aliases,
        attributes=attributes,
        is_new=is_new,
    )
    if decision != REVIEW_REQUIRED:
        _log_resolution(extracted, resolution)
    elif decision == REVIEW_REQUIRED:
        # Safe path: do not merge. Create a distinct canonical node.
        resolution.is_new = True
        resolution.canonical_id = proposed_id
        resolution.name = name
        _log_resolution(extracted, resolution)
    return resolution


def _score_candidate(
    extracted: ExtractedEntity,
    normalized: str,
    proposed_id: str,
    candidate: dict,
) -> tuple[float, str]:
    kind = extracted.kind
    candidate_id = str(candidate.get("canonical_id") or "").strip()
    candidate_norm = str(
        candidate.get("normalized_name")
        or normalize_for_type(kind, str(candidate.get("name") or ""))
    ).strip()
    candidate_aliases = {
        normalize_entity_name(str(alias))
        for alias in _candidate_names(candidate)
        if str(alias).strip()
    }

    if candidate_id and candidate_id == proposed_id:
        return 1.0, "canonical_id"

    if kind == "standard":
        extracted_code = normalize_for_type("standard", extracted.code or extracted.name)
        candidate_code = normalize_for_type(
            "standard",
            str(candidate.get("code") or candidate.get("name") or ""),
        )
        if extracted_code and extracted_code == candidate_code:
            return 0.99, "standard_code"

    if kind == "clause":
        extracted_parent = str(extracted.parent_canonical_id or "").strip()
        candidate_parent = str(candidate.get("parent_canonical_id") or "").strip()
        if extracted_parent and candidate_parent and extracted_parent != candidate_parent:
            return 0.0, "different_parent_standard"
        if extracted_parent and candidate_parent and extracted_parent == candidate_parent:
            if normalized == candidate_norm or proposed_id == candidate_id:
                return 0.99, "clause_number_and_standard"

    if kind == "certification":
        extracted_code = normalize_for_type("certification", extracted.code or extracted.name)
        candidate_code = normalize_for_type(
            "certification",
            str(candidate.get("code") or candidate.get("name") or ""),
        )
        if extracted_code and extracted_code == candidate_code:
            return 0.99, "certification_number"

    if kind in {"model", "component"}:
        extracted_part = normalize_entity_name(
            extracted.part_number or extracted.model_number or ""
        )
        candidate_part = normalize_entity_name(
            str(candidate.get("part_number") or candidate.get("model_number") or "")
        )
        if extracted_part and extracted_part == candidate_part:
            return 0.99, "part_or_model_number"

    if kind == "product":
        extracted_company = str(extracted.company_canonical_id or "").strip()
        candidate_company = str(candidate.get("company_canonical_id") or "").strip()
        if (
            extracted_company
            and candidate_company
            and extracted_company == candidate_company
            and normalized == candidate_norm
        ):
            return 0.97, "product_name_and_company"

    if normalized and normalized == candidate_norm:
        if kind == "product":
            extracted_company = str(extracted.company_canonical_id or "").strip()
            candidate_company = str(candidate.get("company_canonical_id") or "").strip()
            if extracted_company and candidate_company and extracted_company != candidate_company:
                return 0.0, "same_product_name_different_company"
        return 0.95, "normalized_name"

    incoming_aliases = {
        normalize_entity_name(alias)
        for alias in [extracted.name, *(extracted.aliases or [])]
        if alias
    }
    if incoming_aliases & candidate_aliases:
        return 0.93, "alias"

    if kind == "company" and (
        company_names_equivalent(extracted.name, str(candidate.get("name") or ""))
        or company_names_equivalent(normalized, candidate_norm)
    ):
        return 0.94, "company_variant"

    if entity_names_equivalent(extracted.name, str(candidate.get("name") or "")) or (
        normalized and candidate_norm and entity_names_equivalent(normalized, candidate_norm)
    ):
        return 0.94, "entity_name_variant"

    best_sim = 0.0
    for left in incoming_aliases:
        for right in candidate_aliases:
            best_sim = max(best_sim, _similarity(left, right))
    if best_sim:
        return best_sim, "string_similarity"
    return 0.0, "no_match"
