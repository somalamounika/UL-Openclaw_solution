"""Incremental ingestion: normalize → resolve → MERGE canonical graph."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Protocol

from ul.description_text import normalize_description
from ul.hierarchy import is_allowed_pair
from ul.knowledge_graph.canonical_ids import UL_CANONICAL_ID, UL_DISPLAY_NAME, canonical_id_for
from ul.knowledge_graph.entity_resolution import (
    ExtractedEntity,
    Resolution,
    resolve_entity,
)
from ul.knowledge_graph.labels import canonical_entity_type, to_relationship_type
from ul.knowledge_graph.normalization import (
    company_names_equivalent,
    entity_names_equivalent,
    is_ul_entity,
    normalize_for_type,
    preferred_company_name,
    preferred_entity_name,
)
from ul.knowledge_graph.provenance import utc_now_iso
from ul.knowledge_graph.relationship_resolution import relationship_key

logger = logging.getLogger(__name__)

GLOBAL_GRAPH_ID = "ul_global"

_RESOLVE_ORDER = (
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


class GraphStore(Protocol):
    def ensure_constraints(self) -> None: ...
    def find_candidates(self, entity_type: str, **kwargs) -> list[dict[str, Any]]: ...
    def find_document(
        self, *, document_id: str | None = None, content_hash: str | None = None
    ) -> dict[str, Any] | None: ...
    def merge_ul(self, *, aliases: list[str] | None = None, graph_id: str | None = None) -> dict[str, Any]: ...
    def merge_entity(self, entity_type: str, canonical_id: str, **kwargs) -> dict[str, Any]: ...
    def merge_document(self, **kwargs) -> dict[str, Any]: ...
    def merge_relationship(
        self,
        source_type: str,
        source_canonical_id: str,
        relationship: str,
        dest_type: str,
        dest_canonical_id: str,
        **kwargs,
    ) -> None: ...
    def list_entities(self, entity_type: str) -> list[dict[str, Any]]: ...
    def absorb_entity(
        self,
        keep_canonical_id: str,
        drop_canonical_id: str,
        *,
        entity_type: str,
    ) -> None: ...
    def merge_global_graph_metadata(self, **kwargs) -> None: ...


class MemoryGraph:
    """In-memory canonical graph used by tests and as the ingest contract."""

    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict[str, Any]] = {}
        self.relationships: dict[str, dict[str, Any]] = {}
        self.documents: dict[str, dict[str, Any]] = {}
        self.documents_by_hash: dict[str, str] = {}
        self.evidence: dict[str, dict[str, Any]] = {}
        self.evidence_links: list[tuple[str, str, str]] = []
        self.constraints_ensured = False

    def ensure_constraints(self) -> None:
        self.constraints_ensured = True

    def find_candidates(self, entity_type: str, **kwargs) -> list[dict[str, Any]]:
        kind = canonical_entity_type(entity_type)
        canonical_id = str(kwargs.get("canonical_id") or "").strip()
        normalized_name = str(kwargs.get("normalized_name") or "").strip()
        code = str(kwargs.get("code") or "").strip().lower()
        alias_keys = {
            str(alias).strip().lower()
            for alias in (kwargs.get("aliases") or [])
            if str(alias).strip()
        }
        found: list[dict[str, Any]] = []
        for (etype, cid), entity in self.entities.items():
            if etype != kind:
                continue
            aliases = [str(item).lower() for item in entity.get("aliases") or []]
            if canonical_id and cid == canonical_id:
                found.append(dict(entity))
                continue
            if normalized_name and entity.get("normalized_name") == normalized_name:
                found.append(dict(entity))
                continue
            if code and str(entity.get("code") or "").lower() == code:
                found.append(dict(entity))
                continue
            if alias_keys and (alias_keys & set(aliases) or entity.get("normalized_name") in alias_keys):
                found.append(dict(entity))
                continue
            if kind == "company" and normalized_name:
                existing_name = str(entity.get("name") or "")
                existing_norm = str(entity.get("normalized_name") or "")
                if company_names_equivalent(normalized_name, existing_norm) or (
                    alias_keys
                    and any(company_names_equivalent(alias, existing_name) for alias in alias_keys)
                ):
                    found.append(dict(entity))
        return found

    def find_document(
        self, *, document_id: str | None = None, content_hash: str | None = None
    ) -> dict[str, Any] | None:
        if document_id and document_id in self.documents:
            return dict(self.documents[document_id])
        if content_hash and content_hash in self.documents_by_hash:
            existing_id = self.documents_by_hash[content_hash]
            return dict(self.documents[existing_id])
        return None

    def merge_ul(self, *, aliases: list[str] | None = None, graph_id: str | None = None) -> dict[str, Any]:
        key = ("ul", UL_CANONICAL_ID)
        existing = self.entities.get(key)
        alias_list = list(aliases or [UL_DISPLAY_NAME, "UL", "UL Solutions, Inc."])
        if existing is None:
            existing = {
                "entity_type": "ul",
                "canonical_id": UL_CANONICAL_ID,
                "name": UL_DISPLAY_NAME,
                "normalized_name": "ul solutions",
                "aliases": [],
                "evidence_count": 0,
                "graph_id": graph_id or GLOBAL_GRAPH_ID,
            }
            self.entities[key] = existing
        existing["graph_id"] = graph_id or GLOBAL_GRAPH_ID
        _extend_aliases(existing, alias_list)
        return dict(existing)

    def merge_entity(
        self,
        entity_type: str,
        canonical_id: str,
        *,
        name: str,
        normalized_name: str,
        aliases: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        is_new: bool = True,
        graph_id: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        kind = canonical_entity_type(entity_type)
        key = (kind, canonical_id)
        entity = self.entities.get(key)
        if entity is None:
            entity = {
                "entity_type": kind,
                "canonical_id": canonical_id,
                "name": name,
                "normalized_name": normalized_name,
                "aliases": [],
                "evidence_count": 0,
                "graph_id": graph_id or GLOBAL_GRAPH_ID,
                "description": "",
            }
            self.entities[key] = entity
        entity["graph_id"] = graph_id or GLOBAL_GRAPH_ID
        if kind == "company" and company_names_equivalent(entity.get("name"), name):
            entity["name"] = preferred_company_name(entity.get("name"), name)
            entity["normalized_name"] = normalize_for_type(
                "company", str(entity.get("name") or name)
            )
        _extend_aliases(entity, [name, *(aliases or [])])
        text = normalize_description(str(description or ""))
        if text and not str(entity.get("description") or "").strip():
            entity["description"] = text
        extra_props = extra or {}
        for field, value in extra_props.items():
            if field in {"description", "graph_id", "document_id", "ingestion_id"}:
                continue
            if value in (None, "", [], {}):
                continue
            if entity.get(field) in (None, "", [], {}):
                entity[field] = value
        del is_new
        return dict(entity)

    def merge_document(self, **kwargs) -> dict[str, Any]:
        document_id = str(kwargs.get("document_id") or "").strip()
        content_hash = str(kwargs.get("content_hash") or "").strip()
        existing = self.find_document(document_id=document_id, content_hash=content_hash)
        if existing is not None:
            doc_id = str(existing["document_id"])
            doc = self.documents[doc_id]
            doc["last_ingestion_id"] = kwargs.get("ingestion_id") or doc.get("ingestion_id")
            return dict(doc)
        doc = {
            "document_id": document_id,
            "file_name": kwargs.get("file_name") or "",
            "source_files": list(kwargs.get("source_files") or []),
            "ingestion_id": kwargs.get("ingestion_id") or "",
            "content_hash": content_hash,
            "uploaded_at": kwargs.get("uploaded_at") or utc_now_iso(),
            "source": kwargs.get("source") or "",
        }
        self.documents[document_id] = doc
        if content_hash:
            self.documents_by_hash[content_hash] = document_id
        return dict(doc)

    def merge_relationship(
        self,
        source_type: str,
        source_canonical_id: str,
        relationship: str,
        dest_type: str,
        dest_canonical_id: str,
        **kwargs,
    ) -> None:
        key = relationship_key(source_canonical_id, relationship, dest_canonical_id)
        existing = self.relationships.get(key)
        document_id = str(kwargs.get("document_id") or "")
        if existing is None:
            self.relationships[key] = {
                "source_type": canonical_entity_type(source_type),
                "source_canonical_id": source_canonical_id,
                "relationship": to_relationship_type(relationship),
                "dest_type": canonical_entity_type(dest_type),
                "dest_canonical_id": dest_canonical_id,
                "description": kwargs.get("description") or "",
                "keywords": kwargs.get("keywords") or "",
                "graph_id": kwargs.get("graph_id") or GLOBAL_GRAPH_ID,
                "document_ids": [document_id] if document_id else [],
            }
            return
        existing["graph_id"] = kwargs.get("graph_id") or GLOBAL_GRAPH_ID
        if document_id and document_id not in existing["document_ids"]:
            existing["document_ids"].append(document_id)
        if not existing.get("keywords") and kwargs.get("keywords"):
            existing["keywords"] = kwargs["keywords"]

    def merge_global_graph_metadata(self, **kwargs) -> None:
        gid = str(kwargs.get("graph_id") or GLOBAL_GRAPH_ID)
        for entity in self.entities.values():
            entity["graph_id"] = gid
        for rel in self.relationships.values():
            rel["graph_id"] = gid

    def child_parent_map(self, relationship: str) -> dict[str, str]:
        rel = to_relationship_type(relationship)
        mapping: dict[str, str] = {}
        for row in self.relationships.values():
            if row.get("relationship") == rel:
                mapping[str(row["dest_canonical_id"])] = str(row["source_canonical_id"])
        return mapping

    def list_entities(self, entity_type: str) -> list[dict[str, Any]]:
        kind = canonical_entity_type(entity_type)
        return [dict(entity) for (etype, _cid), entity in self.entities.items() if etype == kind]

    def absorb_entity(
        self,
        keep_canonical_id: str,
        drop_canonical_id: str,
        *,
        entity_type: str,
    ) -> None:
        kind = canonical_entity_type(entity_type)
        keep_key = (kind, keep_canonical_id)
        drop_key = (kind, drop_canonical_id)
        keep = self.entities.get(keep_key)
        drop = self.entities.get(drop_key)
        if keep is None or drop is None or keep_canonical_id == drop_canonical_id:
            return
        if kind == "company":
            keep["name"] = preferred_company_name(keep.get("name"), drop.get("name"))
            keep["normalized_name"] = normalize_for_type("company", str(keep.get("name") or ""))
        _extend_aliases(keep, [str(drop.get("name") or ""), *(drop.get("aliases") or [])])
        keep["evidence_count"] = int(keep.get("evidence_count") or 0) + int(
            drop.get("evidence_count") or 0
        )
        rewritten: dict[str, dict[str, Any]] = {}
        for rel in self.relationships.values():
            src = rel["source_canonical_id"]
            dst = rel["dest_canonical_id"]
            if src == drop_canonical_id:
                src = keep_canonical_id
                rel["source_canonical_id"] = src
            if dst == drop_canonical_id:
                dst = keep_canonical_id
                rel["dest_canonical_id"] = dst
            if src == dst:
                continue
            key = relationship_key(src, rel["relationship"], dst)
            existing = rewritten.get(key)
            if existing is None:
                rewritten[key] = rel
                continue
            for doc_id in rel.get("document_ids") or []:
                if doc_id and doc_id not in existing["document_ids"]:
                    existing["document_ids"].append(doc_id)
        self.relationships = rewritten
        for entity in self.entities.values():
            for field in ("company_canonical_id", "product_canonical_id", "parent_canonical_id"):
                if entity.get(field) == drop_canonical_id:
                    entity[field] = keep_canonical_id
        del self.entities[drop_key]

    def count(self, entity_type: str) -> int:
        kind = canonical_entity_type(entity_type)
        return sum(1 for (etype, _cid) in self.entities if etype == kind)

    def drop_disallowed_relationships(self) -> int:
        kept: dict[str, dict[str, Any]] = {}
        removed = 0
        for key, rel in self.relationships.items():
            if _is_disallowed_stored_relationship(rel):
                removed += 1
                continue
            kept[key] = rel
        self.relationships = kept
        return removed

    def collapse_manufacturing_locations(self) -> int:
        changed = 0
        id_map: dict[str, str] = {}
        for key, entity in list(self.entities.items()):
            cid = str(entity.get("canonical_id") or key[1])
            if key[0] != "manufacturing_location" and not cid.startswith("manufacturing_location_"):
                continue
            name = str(entity.get("name") or "").strip()
            new_id = canonical_id_for("location", name) if name else cid
            id_map[cid] = new_id
            entity["entity_type"] = "location"
            entity["canonical_id"] = new_id
            self.entities.pop(key, None)
            keep_key = ("location", new_id)
            keep = self.entities.get(keep_key)
            if keep is None:
                self.entities[keep_key] = entity
            else:
                _extend_aliases(
                    keep,
                    [str(entity.get("name") or ""), *(entity.get("aliases") or [])],
                )
            changed += 1

        rewritten: dict[str, dict[str, Any]] = {}
        for rel in self.relationships.values():
            if rel.get("relationship") == "HAS_MANUFACTURING_LOCATION":
                rel["relationship"] = "HAS_LOCATION"
                changed += 1
            if canonical_entity_type(str(rel.get("source_type") or "")) == "location":
                rel["source_type"] = "location"
            if canonical_entity_type(str(rel.get("dest_type") or "")) == "location":
                rel["dest_type"] = "location"
            src = id_map.get(
                str(rel.get("source_canonical_id") or ""),
                rel.get("source_canonical_id"),
            )
            dst = id_map.get(
                str(rel.get("dest_canonical_id") or ""),
                rel.get("dest_canonical_id"),
            )
            rel["source_canonical_id"] = src
            rel["dest_canonical_id"] = dst
            rewritten[relationship_key(str(src or ""), rel["relationship"], str(dst or ""))] = rel
        self.relationships = rewritten
        return changed

    def link_manufacturers_as_suppliers(self) -> int:
        contains: dict[str, list[tuple[str, str]]] = defaultdict(list)
        has_model: dict[str, list[str]] = defaultdict(list)
        has_product: dict[str, list[str]] = defaultdict(list)
        manufacturers: list[tuple[str, str]] = []
        for rel in self.relationships.values():
            rel_type = rel.get("relationship")
            src = str(rel.get("source_canonical_id") or "")
            dst = str(rel.get("dest_canonical_id") or "")
            src_type = canonical_entity_type(str(rel.get("source_type") or ""))
            dst_type = canonical_entity_type(str(rel.get("dest_type") or ""))
            if not src or not dst:
                continue
            if rel_type == "CONTAINS" and dst_type == "component":
                contains[dst].append((src_type, src))
            elif rel_type == "HAS_MODEL" and src_type == "product" and dst_type == "model":
                has_model[dst].append(src)
            elif rel_type == "HAS_PRODUCT" and dst_type == "product":
                has_product[dst].append(src)
            elif rel_type == "PARTY_ROLE_MANUFACTURER" and src_type == "component":
                manufacturers.append((src, dst))
        added = 0
        for component_id, manufacturer_id in manufacturers:
            owners: set[str] = set()
            for parent_type, parent_id in contains.get(component_id, ()):
                if parent_type == "product":
                    owners.update(has_product.get(parent_id, ()))
                elif parent_type == "model":
                    for product_id in has_model.get(parent_id, ()):
                        owners.update(has_product.get(product_id, ()))
            for owner_id in owners:
                if not owner_id or owner_id == manufacturer_id:
                    continue
                before = len(self.relationships)
                self.merge_relationship(
                    "company",
                    manufacturer_id,
                    "party_role_supplier",
                    "company",
                    owner_id,
                    graph_id=GLOBAL_GRAPH_ID,
                )
                if len(self.relationships) > before:
                    added += 1
        return added

    def link_standard_certifications(self) -> int:
        complies: dict[str, set[str]] = defaultdict(set)
        certs: dict[str, set[str]] = defaultdict(set)
        existing: set[tuple[str, str]] = set()
        for rel in self.relationships.values():
            rel_type = rel.get("relationship")
            src = str(rel.get("source_canonical_id") or "")
            dst = str(rel.get("dest_canonical_id") or "")
            src_type = canonical_entity_type(str(rel.get("source_type") or ""))
            dst_type = canonical_entity_type(str(rel.get("dest_type") or ""))
            if rel_type == "COMPLIES_WITH" and src_type == "component" and dst_type == "standard":
                complies[src].add(dst)
            elif rel_type == "HAS_CERTIFICATION" and src_type == "component" and dst_type == "certification":
                certs[src].add(dst)
            elif rel_type == "HAS_CERTIFICATION" and src_type == "standard" and dst_type == "certification":
                existing.add((src, dst))
        added = 0
        for component_id, standards in complies.items():
            for cert_id in certs.get(component_id, ()):
                for standard_id in standards:
                    if (standard_id, cert_id) in existing:
                        continue
                    existing.add((standard_id, cert_id))
                    self.merge_relationship(
                        "standard",
                        standard_id,
                        "has_certification",
                        "certification",
                        cert_id,
                        graph_id=GLOBAL_GRAPH_ID,
                    )
                    added += 1
        return added

    def drop_evidence_nodes(self) -> int:
        removed = len(self.evidence)
        self.evidence.clear()
        self.evidence_links.clear()
        return removed

    def evidence_for(self, canonical_id: str, document_id: str | None = None) -> list[dict[str, Any]]:
        del canonical_id, document_id
        return []


class Neo4jSessionStore:
    def __init__(self, session) -> None:
        from ul.knowledge_graph import neo4j_upsert as upsert

        self.session = session
        self._upsert = upsert

    def ensure_constraints(self) -> None:
        self._upsert.ensure_constraints(self.session)

    def find_candidates(self, entity_type: str, **kwargs) -> list[dict[str, Any]]:
        return self._upsert.find_candidates(self.session, entity_type, **kwargs)

    def find_document(
        self, *, document_id: str | None = None, content_hash: str | None = None
    ) -> dict[str, Any] | None:
        return self._upsert.find_document(
            self.session, document_id=document_id, content_hash=content_hash
        )

    def merge_ul(self, *, aliases: list[str] | None = None, graph_id: str | None = None) -> dict[str, Any]:
        return self._upsert.merge_ul(self.session, aliases=aliases, graph_id=graph_id)

    def merge_entity(self, entity_type: str, canonical_id: str, **kwargs) -> dict[str, Any]:
        return self._upsert.merge_entity(self.session, entity_type, canonical_id, **kwargs)

    def merge_document(self, **kwargs) -> dict[str, Any]:
        return self._upsert.merge_document(self.session, **kwargs)

    def merge_relationship(
        self,
        source_type: str,
        source_canonical_id: str,
        relationship: str,
        dest_type: str,
        dest_canonical_id: str,
        **kwargs,
    ) -> None:
        self._upsert.merge_relationship(
            self.session,
            source_type,
            source_canonical_id,
            relationship,
            dest_type,
            dest_canonical_id,
            **kwargs,
        )

    def merge_global_graph_metadata(self, **kwargs) -> None:
        self._upsert.merge_global_graph_metadata(self.session, **kwargs)

    def child_parent_map(self, relationship: str) -> dict[str, str]:
        return self._upsert.child_parent_map(self.session, relationship)

    def list_entities(self, entity_type: str) -> list[dict[str, Any]]:
        return self._upsert.list_entities(self.session, entity_type)

    def absorb_entity(
        self,
        keep_canonical_id: str,
        drop_canonical_id: str,
        *,
        entity_type: str,
    ) -> None:
        self._upsert.absorb_entity(
            self.session,
            keep_canonical_id,
            drop_canonical_id,
            entity_type=entity_type,
        )

    def collapse_legacy_org_nodes(self) -> int:
        return self._upsert.collapse_legacy_org_nodes(self.session)

    def drop_disallowed_relationships(self) -> int:
        return self._upsert.drop_disallowed_relationships(self.session)

    def collapse_manufacturing_locations(self) -> int:
        return self._upsert.collapse_manufacturing_locations(self.session)

    def link_manufacturers_as_suppliers(self) -> int:
        return self._upsert.link_manufacturers_as_suppliers(self.session)

    def stamp_missing_canonical_ids(self) -> int:
        return self._upsert.stamp_missing_canonical_ids(self.session)

    def drop_evidence_nodes(self) -> int:
        return self._upsert.drop_evidence_nodes(self.session)

    def link_standard_certifications(self) -> int:
        return self._upsert.link_standard_certifications(self.session)


def _extend_aliases(entity: dict[str, Any], aliases: list[str]) -> None:
    existing = entity.setdefault("aliases", [])
    seen = {str(item).strip().lower() for item in existing if str(item).strip()}
    for alias in aliases:
        text = str(alias or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        existing.append(text)


def _is_disallowed_stored_relationship(rel: dict[str, Any]) -> bool:
    """True for leftover Component -> PARTY_ROLE_SUPPLIER -> Company edges."""
    rel_type = to_relationship_type(str(rel.get("relationship") or ""))
    src_type = canonical_entity_type(str(rel.get("source_type") or ""))
    dst_type = canonical_entity_type(str(rel.get("destination_type") or ""))
    if rel_type != "PARTY_ROLE_SUPPLIER":
        return False
    return src_type == "component" or dst_type == "component"


def _entity_lookup_key(entity_type: str, name: str) -> str:
    from ul.product_normalization import norm_key

    return f"{canonical_entity_type(entity_type)}:{norm_key(name)}"


def _description_from_triplets(
    triplets: list[dict],
    *,
    entity_type: str,
    name: str,
) -> str:
    """Use relationship evidence as a node description when entity text is missing."""
    kind = canonical_entity_type(entity_type)
    target = str(name or "").strip().lower()
    if not target:
        return ""
    best = ""
    for triplet in triplets:
        for role in ("source", "destination"):
            etype, ename = _endpoint(triplet, role)
            if canonical_entity_type(etype) != kind:
                continue
            if str(ename or "").strip().lower() != target:
                continue
            text = normalize_description(str(triplet.get("description") or ""))
            if text and (not best or len(text) < len(best)):
                best = text
    return best


def _endpoint(triplet: dict, role: str) -> tuple[str, str]:
    prefix = "source" if role == "source" else "destination"
    name = str(triplet.get(f"{prefix}_node") or "").strip()
    raw_type = str(triplet.get(f"{prefix}_type") or "entity")
    if is_ul_entity(name):
        return "ul", name
    entity_type = canonical_entity_type(raw_type)
    return entity_type, name


def _endpoint_parent(
    triplet: dict,
    role: str,
    entity_type: str,
    name: str,
) -> str | None:
    """Parent context that must be part of identity for clauses/products/models."""
    src_type, src_name = _endpoint(triplet, "source")
    dst_type, dst_name = _endpoint(triplet, "destination")
    rel = str(triplet.get("relationship") or "").strip().lower()
    if role == "destination" and dst_type == entity_type and dst_name == name:
        if rel == "has_clause" and src_type == "standard":
            return src_name
        if rel in {"has_product", "produces"} and src_type == "company" and entity_type == "product":
            return src_name
        if rel == "has_model" and src_type == "product" and entity_type == "model":
            return src_name
    return None


def _collect_parent_hints(triplets: list[dict]) -> dict[tuple[str, str], set[str]]:
    """Map (entity_type, name) to parent names from structural relationships."""
    parents: dict[tuple[str, str], set[str]] = defaultdict(set)
    for triplet in triplets:
        dst_type, dst_name = _endpoint(triplet, "destination")
        parent = _endpoint_parent(triplet, "destination", dst_type, dst_name)
        if parent:
            parents[(dst_type, dst_name)].add(parent)
    return parents


def _mention_sort_key(item: tuple) -> tuple:
    entity_type, name, _parent = item
    return (
        _RESOLVE_ORDER.index(entity_type) if entity_type in _RESOLVE_ORDER else len(_RESOLVE_ORDER),
        name.lower(),
        str(_parent or ""),
    )


def _cluster_equivalent(items: list[dict[str, Any]], same_fn) -> list[list[dict[str, Any]]]:
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

    for i in range(n):
        for j in range(i + 1, n):
            if same_fn(items[i], items[j]):
                union(i, j)
    clusters: dict[int, list[dict[str, Any]]] = {}
    for i, item in enumerate(items):
        clusters.setdefault(find(i), []).append(item)
    return list(clusters.values())


def _pick_company_survivor(group: list[dict[str, Any]]) -> dict[str, Any]:
    survivor = group[0]
    for member in group[1:]:
        keep_name = str(survivor.get("name") or "")
        other_name = str(member.get("name") or "")
        if preferred_company_name(keep_name, other_name) == other_name:
            survivor = member
    return survivor


def _child_parent_map(store: GraphStore, relationship: str) -> dict[str, str]:
    mapper = getattr(store, "child_parent_map", None)
    if callable(mapper):
        return mapper(relationship)
    mapping: dict[str, str] = {}
    for rel in getattr(store, "relationships", {}).values():
        if to_relationship_type(str(rel.get("relationship") or "")) == to_relationship_type(relationship):
            mapping[str(rel["dest_canonical_id"])] = str(rel["source_canonical_id"])
    return mapping


def _pick_entity_survivor(group: list[dict[str, Any]]) -> dict[str, Any]:
    survivor = group[0]
    for member in group[1:]:
        keep_name = str(survivor.get("name") or "")
        other_name = str(member.get("name") or "")
        if preferred_entity_name(keep_name, other_name) == other_name:
            survivor = member
    return survivor


def collapse_equivalent_entities(store: GraphStore) -> None:
    """Merge already-stored name variants (orgs and other entities) and children."""
    evidence_fn = getattr(store, "drop_evidence_nodes", None)
    if callable(evidence_fn):
        removed = evidence_fn()
        if removed:
            logger.info("Removed %d Evidence node(s)", removed)
    list_fn = getattr(store, "list_entities", None)
    absorb_fn = getattr(store, "absorb_entity", None)
    if not callable(list_fn) or not callable(absorb_fn):
        return

    companies = list_fn("company")
    for group in _cluster_equivalent(
        companies,
        lambda left, right: entity_names_equivalent(
            str(left.get("name") or left.get("normalized_name") or ""),
            str(right.get("name") or right.get("normalized_name") or ""),
        ),
    ):
        if len(group) < 2:
            continue
        survivor = _pick_entity_survivor(group)
        keep_id = str(survivor.get("canonical_id") or "")
        for member in group:
            drop_id = str(member.get("canonical_id") or "")
            if not keep_id or not drop_id or drop_id == keep_id:
                continue
            absorb_fn(keep_id, drop_id, entity_type="company")
            logger.info(
                "Collapsed duplicate company %s into %s",
                drop_id,
                keep_id,
            )

    # Parent-scoped alias collapse for all child entity types (locations, products, ...).
    _collapse_equivalent_under_parent(store, "location", "has_location")
    _collapse_children_by_parent(store, "product", "has_product", "company_canonical_id")
    _collapse_children_by_parent(store, "model", "has_model", "product_canonical_id")
    _collapse_equivalent_under_parent(store, "product", "has_product")
    _collapse_equivalent_under_parent(store, "model", "has_model")
    _collapse_equivalent_under_parent(store, "component", "contains")
    _collapse_equivalent_under_parent(store, "address", "has_address")
    drop_fn = getattr(store, "drop_disallowed_relationships", None)
    if callable(drop_fn):
        dropped = drop_fn()
        if dropped:
            logger.info("Dropped %d disallowed relationship(s)", dropped)
    mfr_loc_fn = getattr(store, "collapse_manufacturing_locations", None)
    if callable(mfr_loc_fn):
        converted = mfr_loc_fn()
        if converted:
            logger.info("Collapsed ManufacturingLocation into Location (%s change(s))", converted)
    legacy_fn = getattr(store, "collapse_legacy_org_nodes", None)
    if callable(legacy_fn):
        legacy_fn()
    stamp_fn = getattr(store, "stamp_missing_canonical_ids", None)
    if callable(stamp_fn):
        stamped = stamp_fn()
        if stamped:
            logger.info("Stamped canonical_id on %d legacy node(s)", stamped)
    link_fn = getattr(store, "link_manufacturers_as_suppliers", None)
    if callable(link_fn):
        linked = link_fn()
        if linked:
            logger.info(
                "Linked %d manufacturer company(ies) as PARTY_ROLE_SUPPLIER of the product applicant",
                linked,
            )
    cert_fn = getattr(store, "link_standard_certifications", None)
    if callable(cert_fn):
        cert_links = cert_fn()
        if cert_links:
            logger.info("Linked %d Standard -> Certification edge(s)", cert_links)


def _collapse_equivalent_under_parent(
    store: GraphStore,
    entity_type: str,
    parent_rel: str,
) -> None:
    """Merge name-equivalent entities that share the same parent relationship."""
    list_fn = getattr(store, "list_entities", None)
    absorb_fn = getattr(store, "absorb_entity", None)
    if not callable(list_fn) or not callable(absorb_fn):
        return
    parents = _child_parent_map(store, parent_rel)
    children = list_fn(entity_type)
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for child in children:
        cid = str(child.get("canonical_id") or "")
        parent_id = str(parents.get(cid) or "").strip()
        if not cid or not parent_id:
            continue
        by_parent[parent_id].append(child)

    for group in by_parent.values():
        if len(group) < 2:
            continue
        for cluster in _cluster_equivalent(
            group,
            lambda left, right: entity_names_equivalent(
                str(left.get("name") or left.get("normalized_name") or ""),
                str(right.get("name") or right.get("normalized_name") or ""),
            ),
        ):
            if len(cluster) < 2:
                continue
            survivor = _pick_entity_survivor(cluster)
            keep_id = str(survivor.get("canonical_id") or "")
            for member in cluster:
                drop_id = str(member.get("canonical_id") or "")
                if not keep_id or not drop_id or drop_id == keep_id:
                    continue
                absorb_fn(keep_id, drop_id, entity_type=entity_type)
                logger.info(
                    "Collapsed duplicate %s %s into %s",
                    entity_type,
                    drop_id,
                    keep_id,
                )


def _collapse_children_by_parent(
    store: GraphStore,
    entity_type: str,
    parent_rel: str,
    parent_field: str,
) -> None:
    list_fn = getattr(store, "list_entities", None)
    absorb_fn = getattr(store, "absorb_entity", None)
    if not callable(list_fn) or not callable(absorb_fn):
        return
    parents = _child_parent_map(store, parent_rel)
    children = list_fn(entity_type)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for child in children:
        cid = str(child.get("canonical_id") or "")
        parent_id = str(
            child.get(parent_field) or parents.get(cid) or ""
        ).strip()
        norm = str(child.get("normalized_name") or normalize_for_type(entity_type, child.get("name")))
        if not cid or not parent_id or not norm:
            continue
        groups.setdefault((parent_id, norm), []).append(child)
    for group in groups.values():
        if len(group) < 2:
            continue
        survivor = max(group, key=lambda item: len(str(item.get("name") or "")))
        keep_id = str(survivor.get("canonical_id") or "")
        for member in group:
            drop_id = str(member.get("canonical_id") or "")
            if not keep_id or not drop_id or drop_id == keep_id:
                continue
            absorb_fn(keep_id, drop_id, entity_type=entity_type)
            logger.info(
                "Collapsed duplicate %s %s into %s",
                entity_type,
                drop_id,
                keep_id,
            )


def ingest_to_store(
    triplets: list[dict],
    store: GraphStore,
    *,
    document_id: str,
    ingestion_id: str = "",
    source_files: list[str] | None = None,
    file_name: str = "",
    content_hash: str = "",
    created_at: str | None = None,
    entity_descriptions: dict[str, str] | None = None,
    source: str = "",
    graph_id: str | None = None,
) -> int:
    """Ingest triplets into any GraphStore. Returns number of allowed triplets written."""
    del graph_id  # Per-document kg_* ids are provenance only; the graph tag is always ul_global.
    store.ensure_constraints()
    created = created_at or utc_now_iso()
    files = [str(name) for name in (source_files or []) if str(name).strip()]
    descriptions = entity_descriptions or {}

    existing_doc = store.find_document(document_id=document_id, content_hash=content_hash or None)
    resolved_document_id = document_id
    if existing_doc and content_hash and existing_doc.get("content_hash") == content_hash:
        resolved_document_id = str(existing_doc.get("document_id") or document_id)
        logger.info(
            "Reusing existing Document %s for content_hash %s",
            resolved_document_id,
            content_hash[:12],
        )

    store.merge_ul(
        aliases=[UL_DISPLAY_NAME, "UL", "UL Solutions, Inc."],
        graph_id=GLOBAL_GRAPH_ID,
    )
    store.merge_document(
        document_id=resolved_document_id,
        file_name=file_name or (files[0] if files else ""),
        source_files=files,
        ingestion_id=ingestion_id,
        content_hash=content_hash,
        uploaded_at=created,
        source=source,
    )
    store.merge_global_graph_metadata(
        graph_id=GLOBAL_GRAPH_ID,
        source_files=files,
        document_id=resolved_document_id,
        created_at=created,
    )

    allowed: list[dict] = []
    for triplet in triplets:
        if not is_allowed_pair(
            str(triplet.get("source_type") or ""),
            str(triplet.get("relationship") or ""),
            str(triplet.get("destination_type") or ""),
        ):
            logger.warning(
                "Skipping unsupported Neo4j pair %s (%s) -[%s]-> %s (%s)",
                triplet.get("source_node"),
                triplet.get("source_type"),
                triplet.get("relationship"),
                triplet.get("destination_node"),
                triplet.get("destination_type"),
            )
            continue
        allowed.append(triplet)

    parent_hints = _collect_parent_hints(allowed)
    mentions: dict[tuple[str, str, str | None], list[dict]] = defaultdict(list)
    for triplet in allowed:
        for role in ("source", "destination"):
            entity_type, name = _endpoint(triplet, role)
            if not name:
                continue
            role_parent = _endpoint_parent(triplet, role, entity_type, name)
            known = parent_hints.get((entity_type, name)) or set()
            if role_parent:
                parent_values: set[str | None] = {role_parent}
            elif known:
                parent_values = set(known)
            else:
                parent_values = {None}
            for parent in parent_values:
                mentions[(entity_type, name, parent)].append(triplet)

    resolved_by_key: dict[tuple[str, str, str | None], Resolution] = {}
    resolved_by_name: dict[tuple[str, str], Resolution] = {}
    resolved_by_norm: dict[tuple[str, str], Resolution] = {}

    def _lookup(entity_type: str, name: str, parent: str | None = None) -> Resolution | None:
        found = resolved_by_key.get((entity_type, name, parent))
        if found is not None:
            return found
        if parent is None:
            return resolved_by_name.get((entity_type, name)) or resolved_by_norm.get(
                (entity_type, normalize_for_type(entity_type, name))
            )
        return None

    def _parent_ids(entity_type: str, name: str, parent_hint: str | None) -> dict[str, str | None]:
        company_id = None
        product_id = None
        parent_id = None
        if entity_type == "product" and parent_hint:
            company_res = _lookup("company", parent_hint)
            company_id = company_res.canonical_id if company_res else None
        elif entity_type == "model" and parent_hint:
            product_res = _lookup("product", parent_hint)
            product_id = product_res.canonical_id if product_res else None
        elif entity_type == "clause" and parent_hint:
            std_res = _lookup("standard", parent_hint)
            parent_id = std_res.canonical_id if std_res else None
        return {
            "company_canonical_id": company_id,
            "product_canonical_id": product_id,
            "parent_canonical_id": parent_id,
        }

    ordered_keys = sorted(mentions.keys(), key=_mention_sort_key)

    for entity_type, name, parent_hint in ordered_keys:
        parents = _parent_ids(entity_type, name, parent_hint)
        extracted = ExtractedEntity(
            entity_type=entity_type,
            name=name,
            description=descriptions.get(_entity_lookup_key(entity_type, name), ""),
            company_canonical_id=parents["company_canonical_id"],
            product_canonical_id=parents["product_canonical_id"],
            parent_canonical_id=parents["parent_canonical_id"],
            code=name if entity_type in {"standard", "certification"} else None,
        )
        proposed_id = canonical_id_for(
            entity_type,
            name,
            company_canonical_id=extracted.company_canonical_id,
            product_canonical_id=extracted.product_canonical_id,
            parent_canonical_id=extracted.parent_canonical_id,
            code=extracted.code,
        )
        candidates = store.find_candidates(
            entity_type,
            canonical_id=proposed_id,
            normalized_name=normalize_for_type(entity_type, name),
            code=extracted.code,
            aliases=[name],
        )
        resolution = resolve_entity(extracted, candidates)
        description = normalize_description(
            str(extracted.description or "")
        ) or _description_from_triplets(
            allowed,
            entity_type=entity_type,
            name=name,
        )
        extra = {
            key: value
            for key, value in {
                "company_canonical_id": extracted.company_canonical_id,
                "product_canonical_id": extracted.product_canonical_id,
                "parent_canonical_id": extracted.parent_canonical_id,
                "code": name if entity_type == "standard" else None,
            }.items()
            if value
        }
        store.merge_entity(
            resolution.entity_type,
            resolution.canonical_id,
            name=resolution.name,
            normalized_name=resolution.normalized_name,
            aliases=resolution.aliases,
            extra=extra,
            is_new=resolution.is_new,
            graph_id=GLOBAL_GRAPH_ID,
            description=description,
        )
        resolved_by_key[(entity_type, name, parent_hint)] = resolution
        resolved_by_name[(entity_type, name)] = resolution
        resolved_by_norm[(entity_type, resolution.normalized_name)] = resolution
        if is_ul_entity(name):
            resolved_by_name[("ul", name)] = resolution
            resolved_by_key[("ul", name, None)] = resolution

    for triplet in allowed:
        src_type, src_name = _endpoint(triplet, "source")
        dst_type, dst_name = _endpoint(triplet, "destination")
        src_parent = _endpoint_parent(triplet, "source", src_type, src_name)
        dst_parent = _endpoint_parent(triplet, "destination", dst_type, dst_name)
        if src_parent is None:
            known_src = parent_hints.get((src_type, src_name)) or set()
            src_parent = next(iter(known_src), None) if len(known_src) == 1 else src_parent
        if dst_parent is None:
            known_dst = parent_hints.get((dst_type, dst_name)) or set()
            dst_parent = next(iter(known_dst), None) if len(known_dst) == 1 else dst_parent
        src_res = _lookup(src_type, src_name, src_parent) or _lookup(src_type, src_name)
        dst_res = _lookup(dst_type, dst_name, dst_parent) or _lookup(dst_type, dst_name)
        if src_res is None or dst_res is None:
            continue
        rel = str(triplet.get("relationship") or "")
        store.merge_relationship(
            src_res.entity_type,
            src_res.canonical_id,
            rel,
            dst_res.entity_type,
            dst_res.canonical_id,
            description=normalize_description(str(triplet.get("description") or "")),
            keywords=str(triplet.get("keywords") or "").strip(),
            document_id=resolved_document_id,
            graph_id=GLOBAL_GRAPH_ID,
        )

    collapse_equivalent_entities(store)

    logger.info(
        "Incrementally ingested %d triplets into canonical UL graph (document_id=%s)",
        len(allowed),
        resolved_document_id,
    )
    return len(allowed)


def ingest_triplets_incremental(
    triplets: list[dict],
    *,
    document_id: str,
    graph_id: str | None = None,
    source_files: list[str] | None = None,
    ingestion_id: str = "",
    created_at: str | None = None,
    entity_descriptions: dict[str, str] | None = None,
    content_hash: str = "",
    file_name: str = "",
    source: str = "",
    store: GraphStore | None = None,
    session=None,
) -> int:
    """Write extracted triplets into the one global UL Knowledge Graph."""
    if store is not None:
        return ingest_to_store(
            triplets,
            store,
            document_id=document_id,
            ingestion_id=ingestion_id,
            source_files=source_files,
            file_name=file_name,
            content_hash=content_hash,
            created_at=created_at,
            entity_descriptions=entity_descriptions,
            source=source,
            graph_id=graph_id,
        )
    if session is not None:
        return ingest_to_store(
            triplets,
            Neo4jSessionStore(session),
            document_id=document_id,
            ingestion_id=ingestion_id,
            source_files=source_files,
            file_name=file_name,
            content_hash=content_hash,
            created_at=created_at,
            entity_descriptions=entity_descriptions,
            source=source,
            graph_id=graph_id,
        )
    raise ValueError("ingest_triplets_incremental requires a store or Neo4j session")
