"""Normalize triplets into the UL knowledge-graph model.

Canonicalizes types and relationship aliases, validates allowed
source/target pairs from prompt.py, and adds UL -> PARTY_ROLE_APPLICANT
structural links when Applicant nodes are present.

Does not re-orient relationships using numeric hierarchy levels.
Business relationship direction always wins.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from ul.description_text import normalize_description

logger = logging.getLogger(__name__)

UL_ROOT_NAME = "UL Solutions"
UL_ROOT_TYPE = "ul"

_TYPE_ALIASES: dict[str, str] = {
    "organization": "company",
    "org": "company",
    "brand": "company",
    "manufacturer": "company",
    "supplier": "company",
    "customer": "company",
    "country": "location",
    "facility": "location",
    "test_record": "test",
    "testrecord": "test",
    "manufacturing_location": "location",
    "manufacturinglocation": "location",
}

# Map messy LLM relationship names onto canonical prompt.py names.
# Do NOT alias SERVES. Do NOT map CONTAINS to HAS_CLAUSE.
REL_ALIASES: dict[str, str] = {
    "party_role_applicant": "party_role_applicant",
    "has_applicant": "party_role_applicant",
    "has_product": "has_product",
    "produces": "has_product",
    "produce": "has_product",
    "produced_by": "has_product",
    "makes": "has_product",
    "has_model": "has_model",
    "model_of": "has_model",
    "contains": "contains",
    "contains_component": "contains",
    "has_component": "contains",
    "part_of": "contains",
    "complies_with": "complies_with",
    "evaluated_against": "complies_with",
    "subject_to": "complies_with",
    "applicable_to": "complies_with",
    "applicable_standard": "complies_with",
    "governed_by": "complies_with",
    "has_certification": "has_certification",
    "party_role_manufacturer": "party_role_manufacturer",
    "manufactured_by": "party_role_manufacturer",
    "manufactures": "party_role_manufacturer",
    "manufacture": "party_role_manufacturer",
    "manufactures_for": "party_role_manufacturer",
    "party_role_supplier": "party_role_supplier",
    "supplied_by": "party_role_supplier",
    "supplies": "party_role_supplier",
    "supplies_to": "party_role_supplier",
    "has_manufacturing_location": "has_location",
    "has_location": "has_location",
    "located_in": "has_location",
    "based_in": "has_location",
    "has_clause": "has_clause",
    "has_relevant_clause": "has_clause",
    "requires_test": "requires_test",
    "has_test": "requires_test",
    "has_test_record": "requires_test",
    "has_test_plan": "has_test_plan",
    "has_file_number": "has_file_number",
    "has_volume": "has_volume",
    "has_deliverable": "has_deliverable",
    "has_address": "has_address",
}

# Write-time allowed (source_type, relationship, destination_type).
ALLOWED_TRIPLETS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("ul", "party_role_applicant", "applicant"),
        ("applicant", "has_product", "product"),
        ("company", "has_product", "product"),
        ("product", "has_model", "model"),
        ("model", "contains", "component"),
        ("component", "complies_with", "standard"),
        ("component", "has_certification", "certification"),
        ("component", "party_role_manufacturer", "company"),
        ("company", "party_role_supplier", "applicant"),
        ("company", "party_role_supplier", "company"),
        ("company", "has_location", "location"),
        ("standard", "has_clause", "clause"),
        ("standard", "has_certification", "certification"),
        ("certification", "requires_test", "test"),
        ("certification", "has_test_plan", "test_plan"),
        ("certification", "has_file_number", "file_number"),
        ("certification", "has_volume", "volume"),
        ("certification", "has_deliverable", "deliverable"),
        ("location", "has_address", "address"),
    }
)


def _unique_type_pair_rels() -> dict[tuple[str, str], str]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for src, rel, dst in ALLOWED_TRIPLETS:
        grouped.setdefault((src, dst), set()).add(rel)
    return {pair: next(iter(rels)) for pair, rels in grouped.items() if len(rels) == 1}


# Preferred relationship only when a type pair has exactly one allowed edge.
TYPE_PAIR_REL: dict[tuple[str, str], str] = _unique_type_pair_rels()
_ALLOWED_REL_NAMES = frozenset(rel for _src, rel, _dst in ALLOWED_TRIPLETS)


def _norm_type(t: str | None) -> str:
    raw = (t or "entity").strip().lower()
    return _TYPE_ALIASES.get(raw, raw)


def _norm_rel(rel: str) -> str:
    key = rel.strip().lower().replace(" ", "_").replace("-", "_")
    return REL_ALIASES.get(key, key)


def is_allowed_pair(source_type: str, relationship: str, destination_type: str) -> bool:
    return (
        _norm_type(source_type),
        _norm_rel(relationship),
        _norm_type(destination_type),
    ) in ALLOWED_TRIPLETS


def types_are_related(source_type: str, destination_type: str) -> bool:
    src = _norm_type(source_type)
    dst = _norm_type(destination_type)
    return any(
        (s == src and d == dst) or (s == dst and d == src)
        for s, _rel, d in ALLOWED_TRIPLETS
    )


_CORE_TRIPLET_FIELDS = frozenset(
    {
        "source_node",
        "source_type",
        "relationship",
        "destination_node",
        "destination_type",
    }
)


def _triplet_key(t: dict) -> tuple:
    return (
        t["source_type"],
        t["source_node"],
        t["relationship"],
        t["destination_type"],
        t["destination_node"],
    )


def _triplet_extras(t: dict) -> dict:
    """Carry non-core triplet metadata such as description and keywords."""
    extras: dict = {}
    for key, value in t.items():
        if key in _CORE_TRIPLET_FIELDS or value is None:
            continue
        extras[key] = value

    description = normalize_description(
        str(t.get("description") or t.get("evidence") or "")
    )
    if description:
        extras["description"] = description
    elif "description" in extras:
        extras["description"] = str(extras.get("description") or "").strip()

    if "keywords" in t or "keywords" in extras:
        extras["keywords"] = str(t.get("keywords") or extras.get("keywords") or "").strip()

    return extras


def _merge_triplet_metadata(existing: dict, incoming: dict) -> None:
    """Fill missing metadata on a deduped triplet from a duplicate edge."""
    for field in ("description", "keywords"):
        if field == "description":
            if str(existing.get(field) or "").strip():
                continue
            value = normalize_description(str(incoming.get(field) or ""))
        else:
            if str(existing.get(field) or "").strip():
                continue
            value = str(incoming.get(field) or "").strip()
        if value:
            existing[field] = value


def _norm_name(name: str) -> str:
    from ul.product_normalization import norm_key

    return norm_key(name)


def is_ul_root_name(name: str) -> bool:
    key = _norm_name(name)
    return key in {"ul solutions", "ul", "ul llc"} or key.startswith("ul llc")


def _canonical_endpoint_type(name: str, raw_type: str | None) -> str:
    if is_ul_root_name(name):
        return UL_ROOT_TYPE
    return _norm_type(raw_type)


def _build_triplet(
    *,
    source_node: str,
    source_type: str,
    relationship: str,
    destination_node: str,
    destination_type: str,
    extras: dict | None = None,
    identity_source: str | None = None,
    identity_dest: str | None = None,
) -> dict:
    row = {
        "source_node": source_node,
        "source_type": source_type,
        "relationship": relationship,
        "destination_node": destination_node,
        "destination_type": destination_type,
    }
    if identity_source is not None:
        row["source_identity_key"] = identity_source
    if identity_dest is not None:
        row["destination_identity_key"] = identity_dest
    if extras:
        for key, value in extras.items():
            if key not in ("source_identity_key", "destination_identity_key"):
                row[key] = value
    return row


def _canonicalize_triplet(t: dict) -> dict | None:
    """Normalize types/aliases and repair only invalid pair direction.

    Never flips a valid business edge because a node looks "higher".
    """
    src_name = str(t.get("source_node") or "").strip()
    dst_name = str(t.get("destination_node") or "").strip()
    if not src_name or not dst_name:
        return None

    src_type = _canonical_endpoint_type(src_name, t.get("source_type"))
    dst_type = _canonical_endpoint_type(dst_name, t.get("destination_type"))
    rel = _norm_rel(str(t.get("relationship", "")))
    extras = _triplet_extras(t)

    if rel == "serves":
        logger.info(
            "Dropping SERVES relationship %s -> %s; UL connects only via PARTY_ROLE_APPLICANT",
            src_name,
            dst_name,
        )
        return None

    src_identity = t.get("source_identity_key")
    dst_identity = t.get("destination_identity_key")

    def _emit(
        source_node: str,
        source_type: str,
        relationship: str,
        destination_node: str,
        destination_type: str,
        *,
        reversed_endpoints: bool = False,
    ) -> dict:
        return _build_triplet(
            source_node=source_node,
            source_type=source_type,
            relationship=relationship,
            destination_node=destination_node,
            destination_type=destination_type,
            extras=extras,
            identity_source=dst_identity if reversed_endpoints else src_identity,
            identity_dest=src_identity if reversed_endpoints else dst_identity,
        )

    if is_allowed_pair(src_type, rel, dst_type):
        return _emit(src_name, src_type, rel, dst_name, dst_type)

    if is_allowed_pair(dst_type, rel, src_type):
        return _emit(
            dst_name,
            dst_type,
            rel,
            src_name,
            src_type,
            reversed_endpoints=True,
        )

    # Do not remap an already-canonical role onto a different pair default.
    # Company -SUPPLIES-> Product must drop, not become HAS_PRODUCT.
    if rel not in _ALLOWED_REL_NAMES:
        preferred = TYPE_PAIR_REL.get((src_type, dst_type))
        if preferred and is_allowed_pair(src_type, preferred, dst_type):
            return _emit(src_name, src_type, preferred, dst_name, dst_type)

        preferred_rev = TYPE_PAIR_REL.get((dst_type, src_type))
        if preferred_rev and is_allowed_pair(dst_type, preferred_rev, src_type):
            return _emit(
                dst_name,
                dst_type,
                preferred_rev,
                src_name,
                src_type,
                reversed_endpoints=True,
            )

    logger.warning(
        "Rejecting unsupported triplet %s (%s) -[%s]-> %s (%s)",
        src_name,
        src_type,
        rel,
        dst_name,
        dst_type,
    )
    return None


def _collect_applicants(triplets: list[dict]) -> dict[str, str]:
    """Map normalized applicant name -> display name.

    An organization is Applicant when typed Applicant, when it HAS_PRODUCT,
    or when UL already links to it via PARTY_ROLE_APPLICANT. Component
    manufacturers/suppliers are not Applicants.
    """
    applicants: dict[str, str] = {}

    def _remember(name: str) -> None:
        cleaned = str(name or "").strip()
        if not cleaned or is_ul_root_name(cleaned):
            return
        # Lazy import avoids circular dependency with normalization.py.
        from ul.knowledge_graph.normalization import is_generic_org_name

        if is_generic_org_name(cleaned):
            return
        applicants.setdefault(_norm_name(cleaned), cleaned)

    for triplet in triplets:
        src_type = _norm_type(triplet.get("source_type"))
        dst_type = _norm_type(triplet.get("destination_type"))
        rel = _norm_rel(str(triplet.get("relationship") or ""))
        src_name = str(triplet.get("source_node") or "")
        dst_name = str(triplet.get("destination_node") or "")
        if src_type == "applicant":
            _remember(src_name)
        if dst_type == "applicant":
            _remember(dst_name)
        if rel == "has_product" and src_type in {"applicant", "company"}:
            _remember(src_name)
        if rel == "party_role_applicant":
            _remember(dst_name)
    return applicants


def _add_ul_applicant_links(
    triplets: list[dict],
    extra_companies: list[str] | None = None,
) -> list[dict]:
    """Add UL -> PARTY_ROLE_APPLICANT for product brand/owners.

    extra_companies is ignored so location-only companies stay unlinked.
    """
    del extra_companies
    applicants = _collect_applicants(triplets)

    existing = {
        _norm_name(str(t.get("destination_node") or ""))
        for t in triplets
        if _norm_rel(str(t.get("relationship") or "")) == "party_role_applicant"
        and _canonical_endpoint_type(str(t.get("source_node") or ""), t.get("source_type"))
        == UL_ROOT_TYPE
    }

    added: list[dict] = []
    for key, name in applicants.items():
        if key in existing:
            continue
        added.append(
            {
                "source_node": UL_ROOT_NAME,
                "source_type": UL_ROOT_TYPE,
                "relationship": "party_role_applicant",
                "destination_node": name,
                "destination_type": "applicant",
                "description": f"UL applicant link for '{name}'.",
                "keywords": "PARTY_ROLE_APPLICANT",
            }
        )
        existing.add(key)
    if added:
        logger.info("Added %d UL -> Applicant PARTY_ROLE_APPLICANT link(s)", len(added))
    return added


def _rewrite_supplier_relationships(triplets: list[dict]) -> list[dict]:
    """Rewrite supplier onto Company -> PARTY_ROLE_SUPPLIER -> Applicant.

    Component -> PARTY_ROLE_SUPPLIER -> Company (and supplied_by aliases) become
    supplier Company -> Applicant. Manufacturer stays Component -> Company, and
    that manufacturer Company is also linked to the product's Applicant as
    PARTY_ROLE_SUPPLIER. Company -> Product SUPPLIES is not invented here.
    """
    applicants = _collect_applicants(triplets)
    product_owners: dict[str, set[str]] = defaultdict(set)
    model_products: dict[str, set[str]] = defaultdict(set)
    component_parents: dict[str, set[tuple[str, str]]] = defaultdict(set)

    for triplet in triplets:
        rel = _norm_rel(str(triplet.get("relationship") or ""))
        src_type = _norm_type(triplet.get("source_type"))
        dst_type = _norm_type(triplet.get("destination_type"))
        src_key = _norm_name(str(triplet.get("source_node") or ""))
        dst_key = _norm_name(str(triplet.get("destination_node") or ""))
        if rel == "has_product" and src_type in {"applicant", "company"} and dst_type == "product":
            if src_key in applicants:
                product_owners[dst_key].add(src_key)
        if rel == "has_model" and src_type == "product" and dst_type == "model":
            model_products[dst_key].add(src_key)
        if rel == "contains" and dst_type == "component":
            component_parents[dst_key].add((src_type, src_key))

    def applicants_for_component(component_key: str, *, fallback: bool = True) -> list[str]:
        keys: set[str] = set()
        for parent_type, parent_key in component_parents.get(component_key, ()):
            if parent_type == "product":
                keys.update(product_owners.get(parent_key, ()))
            elif parent_type == "model":
                for product_key in model_products.get(parent_key, ()):
                    keys.update(product_owners.get(product_key, ()))
        if not keys and fallback:
            keys = set(applicants)
        return [applicants[key] for key in keys if key in applicants]

    rewritten: list[dict] = []
    seen_supplier: set[tuple[str, str]] = set()

    def emit_supplier(supplier_name: str, applicant_name: str, extras: dict) -> None:
        supplier = str(supplier_name or "").strip()
        applicant = str(applicant_name or "").strip()
        if not supplier or not applicant:
            return
        supplier_key = _norm_name(supplier)
        applicant_key = _norm_name(applicant)
        if supplier_key == applicant_key:
            return
        edge_key = (supplier_key, applicant_key)
        if edge_key in seen_supplier:
            return
        seen_supplier.add(edge_key)
        keywords = str(extras.get("keywords") or "").strip() or "PARTY_ROLE_SUPPLIER"
        rewritten.append(
            _build_triplet(
                source_node=supplier,
                source_type="company",
                relationship="party_role_supplier",
                destination_node=applicant,
                destination_type="applicant",
                extras={**extras, "keywords": keywords},
            )
        )

    for triplet in triplets:
        rel = _norm_rel(str(triplet.get("relationship") or ""))
        if rel != "party_role_supplier":
            rewritten.append(triplet)
            continue

        src_name = str(triplet.get("source_node") or "").strip()
        dst_name = str(triplet.get("destination_node") or "").strip()
        src_type = _canonical_endpoint_type(src_name, triplet.get("source_type"))
        dst_type = _canonical_endpoint_type(dst_name, triplet.get("destination_type"))
        extras = _triplet_extras(triplet)
        supplier_name = ""
        targets: list[str] = []

        if src_type == "component" and dst_type in {"company", "supplier"}:
            supplier_name = dst_name
            targets = applicants_for_component(_norm_name(src_name))
        elif dst_type == "component" and src_type in {"company", "supplier"}:
            supplier_name = src_name
            targets = applicants_for_component(_norm_name(dst_name))
        elif src_type in {"company", "supplier"} and dst_type == "applicant":
            supplier_name = src_name
            targets = [dst_name]
        elif src_type == "applicant" and dst_type in {"company", "supplier"}:
            supplier_name = dst_name
            targets = [src_name]
        elif src_type in {"company", "supplier"} and dst_type in {"company", "supplier"}:
            dst_key = _norm_name(dst_name)
            src_key = _norm_name(src_name)
            if dst_key in applicants:
                supplier_name = src_name
                targets = [applicants[dst_key]]
            elif src_key in applicants:
                supplier_name = dst_name
                targets = [applicants[src_key]]
            else:
                supplier_name = src_name
                targets = list(applicants.values())
        else:
            continue

        if not targets:
            targets = list(applicants.values())
        for applicant_name in targets:
            emit_supplier(supplier_name, applicant_name, extras)

    for triplet in triplets:
        rel = _norm_rel(str(triplet.get("relationship") or ""))
        if rel != "party_role_manufacturer":
            continue
        src_name = str(triplet.get("source_node") or "").strip()
        dst_name = str(triplet.get("destination_node") or "").strip()
        src_type = _canonical_endpoint_type(src_name, triplet.get("source_type"))
        dst_type = _canonical_endpoint_type(dst_name, triplet.get("destination_type"))
        manufacturer_name = ""
        component_key = ""
        if src_type == "component" and dst_type in {"company", "manufacturer"}:
            manufacturer_name = dst_name
            component_key = _norm_name(src_name)
        elif dst_type == "component" and src_type in {"company", "manufacturer"}:
            manufacturer_name = src_name
            component_key = _norm_name(dst_name)
        else:
            continue
        for applicant_name in applicants_for_component(component_key, fallback=False):
            emit_supplier(manufacturer_name, applicant_name, _triplet_extras(triplet))

    return rewritten


def _refine_component_ownership(triplets: list[dict]) -> list[dict]:
    """Prefer Model->CONTAINS->Component. Drop Product->Component edges."""
    product_to_models: dict[str, set[str]] = defaultdict(set)
    model_names: set[str] = set()
    model_display: dict[str, str] = {}

    for triplet in triplets:
        rel = _norm_rel(str(triplet.get("relationship") or ""))
        src_type = _norm_type(triplet.get("source_type"))
        dst_type = _norm_type(triplet.get("destination_type"))
        src_node = str(triplet.get("source_node") or "").strip()
        dst_node = str(triplet.get("destination_node") or "").strip()
        if rel == "has_model" and src_type == "product" and dst_type == "model":
            product_to_models[_norm_name(src_node)].add(_norm_name(dst_node))
            model_names.add(_norm_name(dst_node))
            model_display[_norm_name(dst_node)] = dst_node
        if src_type == "model":
            model_names.add(_norm_name(src_node))
            if src_node:
                model_display[_norm_name(src_node)] = src_node
        if dst_type == "model":
            model_names.add(_norm_name(dst_node))
            if dst_node:
                model_display[_norm_name(dst_node)] = dst_node

    model_components = {
        (
            _norm_name(str(triplet.get("source_node") or "")),
            _norm_name(str(triplet.get("destination_node") or "")),
        )
        for triplet in triplets
        if _norm_rel(str(triplet.get("relationship") or "")) == "contains"
        and _norm_type(triplet.get("source_type")) == "model"
    }

    refined: list[dict] = []
    for triplet in triplets:
        row = dict(triplet)
        rel = _norm_rel(str(row.get("relationship") or ""))
        src_type = _norm_type(row.get("source_type"))
        src_name = _norm_name(str(row.get("source_node") or ""))
        dst_name = _norm_name(str(row.get("destination_node") or ""))

        if rel == "contains" and src_type == "product":
            linked_models = product_to_models.get(src_name, set())
            if any((model_key, dst_name) in model_components for model_key in linked_models):
                continue
            # Prefer real Product->HAS_MODEL targets over retyping the product
            # name itself as a Model (avoids Model "iPhone 16" duplicates).
            if len(linked_models) == 1:
                model_key = next(iter(linked_models))
                row["source_type"] = "model"
                row["source_node"] = model_display.get(model_key, model_key)
                row["relationship"] = "contains"
                row["keywords"] = str(row.get("keywords") or "CONTAINS")
            elif len(linked_models) > 1:
                continue
            elif src_name in model_names:
                row["source_type"] = "model"
                row["relationship"] = "contains"
                row["keywords"] = str(row.get("keywords") or "CONTAINS")
            else:
                continue

        refined.append(row)
    return refined


def _link_standard_certifications(triplets: list[dict]) -> list[dict]:
    """If a component complies with a standard and has a certification, link them.

    Standard -> HAS_CERTIFICATION -> Certification. Does not invent certifications.
    """
    component_standards: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    component_certs: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    existing: set[tuple[str, str]] = set()

    for triplet in triplets:
        rel = _norm_rel(str(triplet.get("relationship") or ""))
        src_type = _norm_type(triplet.get("source_type"))
        dst_type = _norm_type(triplet.get("destination_type"))
        src_name = str(triplet.get("source_node") or "").strip()
        dst_name = str(triplet.get("destination_node") or "").strip()
        src_key = _norm_name(src_name)
        dst_key = _norm_name(dst_name)
        extras = _triplet_extras(triplet)
        if rel == "complies_with" and src_type == "component" and dst_type == "standard":
            component_standards[src_key].append((dst_name, extras))
        elif rel == "has_certification" and src_type == "component" and dst_type == "certification":
            component_certs[src_key].append((dst_name, extras))
        elif rel == "has_certification" and src_type == "standard" and dst_type == "certification":
            existing.add((_norm_name(src_name), dst_key))

    added: list[dict] = []
    for component_key, standards in component_standards.items():
        for cert_name, cert_extras in component_certs.get(component_key, ()):
            cert_key = _norm_name(cert_name)
            for standard_name, std_extras in standards:
                edge_key = (_norm_name(standard_name), cert_key)
                if edge_key in existing:
                    continue
                existing.add(edge_key)
                extras = {**std_extras, **cert_extras}
                extras["keywords"] = str(extras.get("keywords") or "").strip() or "HAS_CERTIFICATION"
                added.append(
                    _build_triplet(
                        source_node=standard_name,
                        source_type="standard",
                        relationship="has_certification",
                        destination_node=cert_name,
                        destination_type="certification",
                        extras=extras,
                    )
                )
    if added:
        logger.info("Added %d Standard -> HAS_CERTIFICATION -> Certification link(s)", len(added))
    return triplets + added


def enforce_hierarchy(
    triplets: list[dict],
    extra_companies: list[str] | None = None,
) -> list[dict]:
    """Canonicalize, validate, dedupe, add UL structural links, and check suppliers."""
    triplets = _refine_component_ownership(triplets)
    triplets = _rewrite_supplier_relationships(triplets)
    triplets = _link_standard_certifications(triplets)
    normalized: list[dict] = []
    seen: dict[tuple, dict] = {}

    for raw in triplets:
        canonical = _canonicalize_triplet(raw)
        if canonical is None:
            continue
        key = _triplet_key(canonical)
        existing = seen.get(key)
        if existing is not None:
            _merge_triplet_metadata(existing, canonical)
            continue
        seen[key] = canonical
        normalized.append(canonical)

    for extra in _add_ul_applicant_links(normalized, extra_companies=extra_companies):
        key = _triplet_key(extra)
        if key not in seen:
            seen[key] = extra
            normalized.append(extra)

    logger.info("Hierarchy-normalized triplets: %d", len(normalized))
    return normalized
