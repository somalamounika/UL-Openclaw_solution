"""Neo4j labels and canonical entity-type mapping."""

from __future__ import annotations

import re

# Applicant is a party role in extraction, but the global KG stores one Company node.
_TYPE_ALIASES: dict[str, str] = {
    "applicant": "company",
    "organization": "company",
    "org": "company",
    "brand": "company",
    "manufacturer": "company",
    "supplier": "company",
    "customer": "company",
    "certificate": "certification",
    "test_record": "test",
    "testrecord": "test",
    "filenumber": "file_number",
    "testplan": "test_plan",
    "manufacturinglocation": "location",
    "manufacturing_location": "location",
}

CANONICAL_ENTITY_TYPES: tuple[str, ...] = (
    "ul",
    "company",
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
    "requirement",
    "location",
    "address",
)

PROVENANCE_TYPES: tuple[str, ...] = ("document", "evidence")

_PROVENANCE_LABELS = {"Document", "Evidence", "KnowledgeGraph"}


def canonical_entity_type(entity_type: str | None) -> str:
    raw = (entity_type or "entity").strip().lower().replace("-", "_").replace(" ", "_")
    return _TYPE_ALIASES.get(raw, raw)


def to_label(entity_type: str | None) -> str:
    """Convert entity type to a Neo4j node label, e.g. company -> Company."""
    raw = canonical_entity_type(entity_type)
    if raw == "ul":
        return "UL"
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", raw)
    parts = cleaned.split("_")
    return "".join(part.capitalize() for part in parts if part) or "Entity"


def to_relationship_type(relationship: str | None) -> str:
    """Convert relationship name to Neo4j type, e.g. contains -> CONTAINS."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", (relationship or "").strip().lower())
    return cleaned.upper() or "RELATED_TO"


def is_provenance_label(label: str | None) -> bool:
    return (label or "") in _PROVENANCE_LABELS
