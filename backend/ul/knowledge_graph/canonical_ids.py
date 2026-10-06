"""Deterministic canonical IDs. Never include document/ingestion/graph IDs."""

from __future__ import annotations

from ul.knowledge_graph.labels import canonical_entity_type
from ul.knowledge_graph.normalization import (
    is_ul_entity,
    normalize_clause_number,
    normalize_for_type,
    slug_identity,
)

UL_CANONICAL_ID = "ul_solutions"
UL_DISPLAY_NAME = "UL Solutions"


def generate_canonical_id(entity_type: str, normalized_identity: str) -> str:
    """Build a stable id such as standard_iec_61010_1.

    ``normalized_identity`` should already be a type-specific identity string
    (not a random UUID and not a document id).
    """
    kind = canonical_entity_type(entity_type)
    if kind == "ul":
        return UL_CANONICAL_ID
    ident = slug_identity(normalized_identity)
    if not ident:
        ident = "unknown"
    prefix = kind
    if ident == prefix or ident.startswith(f"{prefix}_"):
        return ident
    return f"{prefix}_{ident}"


def identity_key_for(
    entity_type: str,
    name: str,
    *,
    parent_canonical_id: str | None = None,
    company_canonical_id: str | None = None,
    product_canonical_id: str | None = None,
    code: str | None = None,
    model_number: str | None = None,
    part_number: str | None = None,
) -> str:
    """Type-specific identity string used to generate canonical_id."""
    kind = canonical_entity_type(entity_type)
    if kind == "ul" or is_ul_entity(name):
        return "ul_solutions"

    if kind == "standard":
        return normalize_for_type("standard", code or name)

    if kind == "clause":
        clause_no = normalize_clause_number(name)
        parent = slug_identity(parent_canonical_id or "")
        if parent:
            return f"{parent}_{clause_no or normalize_for_type('clause', name)}"
        return clause_no or normalize_for_type("clause", name)

    if kind == "product":
        product = normalize_for_type("product", name)
        company = slug_identity(company_canonical_id or "")
        if company and not company.startswith("ul_"):
            return f"{company}_{product}"
        return product

    if kind == "model":
        model = normalize_for_type("model", model_number or name)
        product = slug_identity(product_canonical_id or "")
        if product:
            return f"{product}_{model}"
        return model

    if kind == "component":
        part = slug_identity(part_number or "")
        component = normalize_for_type("component", name)
        if part:
            return f"{component}_{part}"
        return component

    if kind == "certification":
        return normalize_for_type("certification", code or name)

    if kind == "test":
        return normalize_for_type("test", code or name)

    return normalize_for_type(kind, name)


def canonical_id_for(
    entity_type: str,
    name: str,
    **identity_kwargs,
) -> str:
    return generate_canonical_id(
        entity_type,
        identity_key_for(entity_type, name, **identity_kwargs),
    )
