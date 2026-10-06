"""Reusable Neo4j MERGE/upsert helpers for the canonical UL Knowledge Graph.

Canonical identity is always ``canonical_id``. ``document_id`` is provenance.
``graph_id`` is a stable query tag on every canonical node/edge (``ul_global``),
never part of MERGE identity and never unique per document.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from ul.knowledge_graph.canonical_ids import UL_CANONICAL_ID, UL_DISPLAY_NAME, canonical_id_for
from ul.knowledge_graph.labels import CANONICAL_ENTITY_TYPES, canonical_entity_type, to_label, to_relationship_type
from ul.knowledge_graph.normalization import (
    company_names_equivalent,
    normalize_for_type,
    preferred_company_name,
)
from ul.knowledge_graph.provenance import utc_now_iso

logger = logging.getLogger(__name__)

# Kept here so upsert can stamp nodes/edges without importing ingest.
STABLE_GRAPH_ID = "ul_global"

_SAFE_CANONICAL_PROPS = (
    "name",
    "normalized_name",
    "code",
    "canonical_id",
    "entity_type",
)

CONSTRAINT_SPECS: tuple[tuple[str, str, str], ...] = (
    ("ul_canonical_id_unique", "UL", "canonical_id"),
    ("company_canonical_id_unique", "Company", "canonical_id"),
    ("product_canonical_id_unique", "Product", "canonical_id"),
    ("model_canonical_id_unique", "Model", "canonical_id"),
    ("component_canonical_id_unique", "Component", "canonical_id"),
    ("standard_canonical_id_unique", "Standard", "canonical_id"),
    ("clause_canonical_id_unique", "Clause", "canonical_id"),
    ("certification_canonical_id_unique", "Certification", "canonical_id"),
    ("test_canonical_id_unique", "Test", "canonical_id"),
    ("test_plan_canonical_id_unique", "TestPlan", "canonical_id"),
    ("file_number_canonical_id_unique", "FileNumber", "canonical_id"),
    ("volume_canonical_id_unique", "Volume", "canonical_id"),
    ("section_canonical_id_unique", "Section", "canonical_id"),
    ("deliverable_canonical_id_unique", "Deliverable", "canonical_id"),
    ("requirement_canonical_id_unique", "Requirement", "canonical_id"),
    ("location_canonical_id_unique", "Location", "canonical_id"),
    ("address_canonical_id_unique", "Address", "canonical_id"),
    ("document_id_unique", "Document", "document_id"),
    ("evidence_id_unique", "Evidence", "evidence_id"),
)

INDEX_SPECS: tuple[tuple[str, str, str], ...] = (
    ("company_normalized_name", "Company", "normalized_name"),
    ("product_normalized_name", "Product", "normalized_name"),
    ("component_normalized_name", "Component", "normalized_name"),
    ("standard_code", "Standard", "code"),
    ("document_content_hash", "Document", "content_hash"),
)


def _first_record(result: Any) -> Any | None:
    if result is None:
        return None
    single = getattr(result, "single", None)
    if callable(single):
        try:
            record = single()
            if record is not None:
                return record
        except Exception:
            pass
    try:
        for record in result:
            return record
    except TypeError:
        return None
    return None


def _records(result: Any) -> list[Any]:
    if result is None:
        return []
    try:
        return list(result)
    except TypeError:
        return []


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _node_to_dict(node: Any, entity_type: str) -> dict[str, Any]:
    if node is None:
        return {}
    if isinstance(node, dict):
        data = dict(node)
    else:
        try:
            data = dict(node)
        except Exception:
            data = {}
            for key in (
                "canonical_id",
                "name",
                "normalized_name",
                "aliases",
                "code",
                "description",
                "parent_canonical_id",
                "company_canonical_id",
                "product_canonical_id",
                "part_number",
                "model_number",
            ):
                try:
                    data[key] = node.get(key)
                except Exception:
                    pass
    data["entity_type"] = data.get("entity_type") or entity_type
    data["aliases"] = _as_str_list(data.get("aliases"))
    return data


def ensure_constraints(session) -> None:
    for name, label, prop in CONSTRAINT_SPECS:
        session.run(
            f"CREATE CONSTRAINT {name} IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
        )
    for name, label, prop in INDEX_SPECS:
        session.run(
            f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
        )


def find_candidates(
    session,
    entity_type: str,
    *,
    canonical_id: str | None = None,
    normalized_name: str | None = None,
    code: str | None = None,
    aliases: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    kind = canonical_entity_type(entity_type)
    label = to_label(entity_type)
    alias_keys = [str(item).strip().lower() for item in (aliases or []) if str(item).strip()]
    first_token = (normalized_name or "").split()[0] if normalized_name else ""
    if kind == "company":
        result = session.run(
            """
            MATCH (n)
            WHERE n.canonical_id IS NOT NULL
              AND (n:Company OR n:Applicant)
              AND (
                ($canonical_id IS NOT NULL AND n.canonical_id = $canonical_id)
                OR ($normalized_name IS NOT NULL AND n.normalized_name = $normalized_name)
                OR ($code IS NOT NULL AND toLower(coalesce(n.code, '')) = toLower($code))
                OR any(alias IN coalesce(n.aliases, [])
                     WHERE toLower(alias) IN $alias_keys
                        OR n.normalized_name IN $alias_keys)
                OR (
                  $first_token <> '' AND (
                    n.normalized_name = $first_token
                    OR n.normalized_name STARTS WITH $first_token + ' '
                    OR $normalized_name STARTS WITH coalesce(n.normalized_name, '') + ' '
                  )
                )
              )
            RETURN n
            LIMIT 100
            """,
            canonical_id=canonical_id or None,
            normalized_name=normalized_name or None,
            code=code or None,
            alias_keys=alias_keys,
            first_token=first_token,
        )
    else:
        result = session.run(
            f"""
            MATCH (n:{label})
            WHERE n.canonical_id IS NOT NULL
              AND (
                ($canonical_id IS NOT NULL AND n.canonical_id = $canonical_id)
                OR ($normalized_name IS NOT NULL AND n.normalized_name = $normalized_name)
                OR ($code IS NOT NULL AND toLower(coalesce(n.code, '')) = toLower($code))
                OR any(alias IN coalesce(n.aliases, [])
                     WHERE toLower(alias) IN $alias_keys
                        OR n.normalized_name IN $alias_keys)
              )
            RETURN n
            LIMIT 25
            """,
            canonical_id=canonical_id or None,
            normalized_name=normalized_name or None,
            code=code or None,
            alias_keys=alias_keys,
        )
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in _records(result):
        try:
            node = record["n"]
        except Exception:
            continue
        data = _node_to_dict(node, entity_type)
        cid = str(data.get("canonical_id") or "")
        if not cid or cid in seen:
            continue
        if kind == "company" and normalized_name:
            candidate_name = str(data.get("name") or "")
            candidate_norm = str(data.get("normalized_name") or "")
            exact = (
                (canonical_id and cid == canonical_id)
                or candidate_norm == normalized_name
                or (
                    code
                    and str(data.get("code") or "").strip().lower()
                    == str(code).strip().lower()
                )
            )
            equivalent = company_names_equivalent(
                normalized_name, candidate_norm or candidate_name
            )
            alias_hit = any(
                company_names_equivalent(alias, candidate_name)
                or alias in {str(item).strip().lower() for item in (data.get("aliases") or [])}
                for alias in alias_keys
            )
            if not (exact or equivalent or alias_hit):
                continue
        seen.add(cid)
        found.append(data)
    return found


def find_document(
    session,
    *,
    document_id: str | None = None,
    content_hash: str | None = None,
) -> dict[str, Any] | None:
    result = session.run(
        """
        MATCH (d:Document)
        WHERE ($document_id IS NOT NULL AND d.document_id = $document_id)
           OR ($content_hash IS NOT NULL AND $content_hash <> '' AND d.content_hash = $content_hash)
        RETURN d
        LIMIT 1
        """,
        document_id=document_id or None,
        content_hash=content_hash or None,
    )
    record = _first_record(result)
    if record is None:
        return None
    try:
        return _node_to_dict(record["d"], "document")
    except Exception:
        return None


def merge_ul(
    session,
    *,
    aliases: list[str] | None = None,
    graph_id: str | None = None,
) -> dict[str, Any]:
    ts = utc_now_iso()
    result = session.run(
        """
        MERGE (u:UL {canonical_id: $canonical_id})
        ON CREATE SET
          u.name = $name,
          u.normalized_name = 'ul solutions',
          u.entity_type = 'ul',
          u.aliases = $aliases,
          u.created_at = $ts,
          u.evidence_count = 0,
          u.graph_id = $graph_id
        ON MATCH SET
          u.name = coalesce(u.name, $name),
          u.graph_id = $graph_id
        WITH u
        FOREACH (alias IN $aliases |
          FOREACH (_ IN CASE WHEN alias IN coalesce(u.aliases, []) THEN [] ELSE [1] END |
            SET u.aliases = coalesce(u.aliases, []) + alias
          )
        )
        RETURN u
        """,
        canonical_id=UL_CANONICAL_ID,
        name=UL_DISPLAY_NAME,
        aliases=_as_str_list(aliases) or [UL_DISPLAY_NAME, "UL", "UL Solutions, Inc."],
        ts=ts,
        graph_id=graph_id or STABLE_GRAPH_ID,
    )
    record = _first_record(result)
    if record is None:
        return {
            "canonical_id": UL_CANONICAL_ID,
            "name": UL_DISPLAY_NAME,
            "entity_type": "ul",
        }
    try:
        return _node_to_dict(record["u"], "ul")
    except Exception:
        return {"canonical_id": UL_CANONICAL_ID, "name": UL_DISPLAY_NAME, "entity_type": "ul"}


def merge_entity(
    session,
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
    """MERGE a canonical entity. Sets description on create; fills only if currently empty."""
    del is_new  # MERGE is idempotent; ON CREATE vs ON MATCH handles first write.
    label = to_label(entity_type)
    ts = utc_now_iso()
    description_text = str(description or "").strip()
    extra_props = {
        key: value
        for key, value in (extra or {}).items()
        if key not in {"description", "graph_id", "document_id", "ingestion_id"}
        and value not in (None, "", [], {})
    }
    # Only set extra canonical attributes on create, or when currently empty.
    result = session.run(
        f"""
        MERGE (n:{label} {{canonical_id: $canonical_id}})
        ON CREATE SET
          n.name = $name,
          n.normalized_name = $normalized_name,
          n.entity_type = $entity_type,
          n.aliases = $aliases,
          n.created_at = $ts,
          n.evidence_count = 0,
          n.graph_id = $graph_id,
          n.description = $description
        ON MATCH SET
          n.normalized_name = coalesce(n.normalized_name, $normalized_name),
          n.updated_at = $ts,
          n.graph_id = $graph_id,
          n.description = CASE
            WHEN coalesce(n.description, '') = '' THEN $description
            ELSE n.description
          END
        WITH n
        FOREACH (alias IN $aliases |
          FOREACH (_ IN CASE WHEN alias IN coalesce(n.aliases, []) THEN [] ELSE [1] END |
            SET n.aliases = coalesce(n.aliases, []) + alias
          )
        )
        RETURN n
        """,
        canonical_id=canonical_id,
        name=name,
        normalized_name=normalized_name,
        entity_type=entity_type,
        aliases=_as_str_list(aliases),
        ts=ts,
        graph_id=graph_id or STABLE_GRAPH_ID,
        description=description_text,
    )
    if extra_props:
        sets = []
        params: dict[str, Any] = {"canonical_id": canonical_id}
        for index, (key, value) in enumerate(extra_props.items()):
            if key in _SAFE_CANONICAL_PROPS:
                continue
            param = f"extra_{index}"
            # Set only when the canonical property is currently missing.
            sets.append(f"n.{key} = coalesce(n.{key}, ${param})")
            params[param] = value
        if sets:
            session.run(
                f"""
                MATCH (n:{label} {{canonical_id: $canonical_id}})
                SET {", ".join(sets)}
                """,
                **params,
            )
    record = _first_record(result)
    if record is None:
        return {
            "canonical_id": canonical_id,
            "name": name,
            "normalized_name": normalized_name,
            "entity_type": entity_type,
            "aliases": _as_str_list(aliases),
        }
    try:
        data = _node_to_dict(record["n"], entity_type)
    except Exception:
        return {
            "canonical_id": canonical_id,
            "name": name,
            "entity_type": entity_type,
        }
    if canonical_entity_type(entity_type) == "company":
        current = str(data.get("name") or "")
        better = preferred_company_name(current, name)
        if better and better != current and company_names_equivalent(current or better, name):
            better_norm = normalize_for_type("company", better)
            session.run(
                f"""
                MATCH (n:{label} {{canonical_id: $canonical_id}})
                SET n.name = $name, n.normalized_name = $normalized_name
                """,
                canonical_id=canonical_id,
                name=better,
                normalized_name=better_norm,
            )
            data["name"] = better
            data["normalized_name"] = better_norm
    return data


def merge_company(session, canonical_id: str, **kwargs) -> dict[str, Any]:
    return merge_entity(session, "company", canonical_id, **kwargs)


def merge_product(session, canonical_id: str, **kwargs) -> dict[str, Any]:
    return merge_entity(session, "product", canonical_id, **kwargs)


def merge_model(session, canonical_id: str, **kwargs) -> dict[str, Any]:
    return merge_entity(session, "model", canonical_id, **kwargs)


def merge_component(session, canonical_id: str, **kwargs) -> dict[str, Any]:
    return merge_entity(session, "component", canonical_id, **kwargs)


def merge_standard(session, canonical_id: str, **kwargs) -> dict[str, Any]:
    return merge_entity(session, "standard", canonical_id, **kwargs)


def merge_clause(session, canonical_id: str, **kwargs) -> dict[str, Any]:
    return merge_entity(session, "clause", canonical_id, **kwargs)


def merge_certification(session, canonical_id: str, **kwargs) -> dict[str, Any]:
    return merge_entity(session, "certification", canonical_id, **kwargs)


def merge_test(session, canonical_id: str, **kwargs) -> dict[str, Any]:
    return merge_entity(session, "test", canonical_id, **kwargs)


def merge_requirement(session, canonical_id: str, **kwargs) -> dict[str, Any]:
    return merge_entity(session, "requirement", canonical_id, **kwargs)


def merge_document(
    session,
    *,
    document_id: str,
    file_name: str = "",
    source_files: list[str] | None = None,
    ingestion_id: str = "",
    content_hash: str = "",
    uploaded_at: str | None = None,
    source: str = "",
) -> dict[str, Any]:
    ts = uploaded_at or utc_now_iso()
    files = _as_str_list(source_files) or ([file_name] if file_name else [])
    result = session.run(
        """
        MERGE (d:Document {document_id: $document_id})
        ON CREATE SET
          d.file_name = $file_name,
          d.source_files = $source_files,
          d.ingestion_id = $ingestion_id,
          d.content_hash = $content_hash,
          d.uploaded_at = $uploaded_at,
          d.source = $source,
          d.created_at = $uploaded_at
        ON MATCH SET
          d.last_ingestion_id = $ingestion_id,
          d.updated_at = $uploaded_at,
          d.source_files = CASE
            WHEN size(coalesce(d.source_files, [])) > 0 THEN d.source_files
            ELSE $source_files
          END
        RETURN d
        """,
        document_id=document_id,
        file_name=file_name or (files[0] if files else ""),
        source_files=files,
        ingestion_id=ingestion_id,
        content_hash=content_hash,
        uploaded_at=ts,
        source=source,
    )
    record = _first_record(result)
    if record is None:
        return {"document_id": document_id, "file_name": file_name}
    try:
        return _node_to_dict(record["d"], "document")
    except Exception:
        return {"document_id": document_id, "file_name": file_name}


def merge_evidence(
    session,
    evidence: dict[str, Any],
    *,
    about_canonical_id: str,
    about_type: str,
) -> dict[str, Any]:
    """Evidence nodes are not stored. Kept as a no-op for legacy callers."""
    del session, about_canonical_id, about_type
    return dict(evidence)


def drop_evidence_nodes(session) -> int:
    """Delete all Evidence nodes. Does not touch UL/Company/Product or other entities."""
    result = session.run("MATCH (e:Evidence) DETACH DELETE e")
    deleted = 0
    consume = getattr(result, "consume", None)
    if callable(consume):
        try:
            deleted = int(consume().counters.nodes_deleted)
        except Exception:
            deleted = 0
    if deleted:
        logger.info("Deleted %d Evidence node(s)", deleted)
    return deleted


def merge_relationship(
    session,
    source_type: str,
    source_canonical_id: str,
    relationship: str,
    dest_type: str,
    dest_canonical_id: str,
    *,
    description: str = "",
    keywords: str = "",
    document_id: str = "",
    graph_id: str | None = None,
) -> None:
    src_label = to_label(source_type)
    dst_label = to_label(dest_type)
    rel_type = to_relationship_type(relationship)
    session.run(
        f"""
        MATCH (s:{src_label} {{canonical_id: $source_canonical_id}})
        MATCH (d:{dst_label} {{canonical_id: $dest_canonical_id}})
        MERGE (s)-[r:{rel_type}]->(d)
        ON CREATE SET
          r.description = $description,
          r.keywords = $keywords,
          r.created_at = $ts,
          r.graph_id = $graph_id,
          r.document_ids = CASE WHEN $document_id = '' THEN [] ELSE [$document_id] END
        ON MATCH SET
          r.keywords = CASE
            WHEN coalesce(r.keywords, '') = '' THEN $keywords
            ELSE r.keywords
          END,
          r.graph_id = $graph_id,
          r.document_ids = CASE
            WHEN $document_id = '' OR $document_id IN coalesce(r.document_ids, [])
            THEN coalesce(r.document_ids, [])
            ELSE coalesce(r.document_ids, []) + $document_id
          END
        """,
        source_canonical_id=source_canonical_id,
        dest_canonical_id=dest_canonical_id,
        description=description,
        keywords=keywords,
        document_id=document_id or "",
        graph_id=graph_id or STABLE_GRAPH_ID,
        ts=utc_now_iso(),
    )


def merge_global_graph_metadata(
    session,
    *,
    graph_id: str,
    source_files: list[str] | None = None,
    document_id: str | None = None,
    created_at: str | None = None,
) -> None:
    """Keep a single KnowledgeGraph metadata node for API compatibility."""
    session.run(
        """
        MERGE (g:KnowledgeGraph {graph_id: $graph_id})
        ON CREATE SET
          g.created_at = $created_at,
          g.document_id = $document_id,
          g.source_files = $source_files
        ON MATCH SET
          g.updated_at = $created_at,
          g.document_id = coalesce(g.document_id, $document_id)
        WITH g
        FOREACH (name IN $source_files |
          FOREACH (_ IN CASE WHEN name IN coalesce(g.source_files, []) THEN [] ELSE [1] END |
            SET g.source_files = coalesce(g.source_files, []) + name
          )
        )
        """,
        graph_id=graph_id or STABLE_GRAPH_ID,
        source_files=_as_str_list(source_files),
        document_id=document_id or "",
        created_at=created_at or utc_now_iso(),
    )
    _stamp_stable_graph_id(session, graph_id=graph_id or STABLE_GRAPH_ID)


def _stamp_stable_graph_id(session, *, graph_id: str) -> None:
    """Backfill the stable query tag on canonical nodes and entity-entity edges."""
    session.run(
        """
        MATCH (n)
        WHERE n.canonical_id IS NOT NULL
          AND NOT n:Document AND NOT n:Evidence AND NOT n:KnowledgeGraph
        SET n.graph_id = $graph_id
        """,
        graph_id=graph_id,
    )
    session.run(
        """
        MATCH (s)-[r]->(t)
        WHERE s.canonical_id IS NOT NULL
          AND t.canonical_id IS NOT NULL
          AND type(r) <> 'PROVIDES_EVIDENCE'
          AND type(r) <> 'ABOUT'
        SET r.graph_id = $graph_id
        """,
        graph_id=graph_id,
    )


def child_parent_map(session, relationship: str) -> dict[str, str]:
    rel_type = _safe_rel_type(to_relationship_type(relationship))
    result = session.run(
        f"""
        MATCH (s)-[r:{rel_type}]->(d)
        WHERE s.canonical_id IS NOT NULL AND d.canonical_id IS NOT NULL
        RETURN d.canonical_id AS child, s.canonical_id AS parent
        """
    )
    mapping: dict[str, str] = {}
    for record in _records(result):
        child = str(record.get("child") or "")
        parent = str(record.get("parent") or "")
        if child and parent:
            mapping[child] = parent
    return mapping


def list_entities(session, entity_type: str) -> list[dict[str, Any]]:
    kind = canonical_entity_type(entity_type)
    if kind == "company":
        result = session.run(
            """
            MATCH (n)
            WHERE n.canonical_id IS NOT NULL AND (n:Company OR n:Applicant)
            RETURN n
            """
        )
    else:
        label = to_label(entity_type)
        result = session.run(
            f"""
            MATCH (n:{label})
            WHERE n.canonical_id IS NOT NULL
            RETURN n
            """
        )
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in _records(result):
        try:
            data = _node_to_dict(record["n"], entity_type)
        except Exception:
            continue
        cid = str(data.get("canonical_id") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        found.append(data)
    return found


def _safe_rel_type(rel_type: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9_]", "_", (rel_type or "").upper())
    return cleaned or "RELATED_TO"


def absorb_entity(
    session,
    keep_canonical_id: str,
    drop_canonical_id: str,
    *,
    entity_type: str,
) -> None:
    """Rewire relationships and evidence from drop onto keep, then delete drop."""
    if not keep_canonical_id or not drop_canonical_id or keep_canonical_id == drop_canonical_id:
        return
    kind = canonical_entity_type(entity_type)
    keep_rows = _records(
        session.run(
            """
            MATCH (n {canonical_id: $canonical_id})
            WHERE NOT n:Document AND NOT n:Evidence AND NOT n:KnowledgeGraph
            RETURN n
            LIMIT 1
            """,
            canonical_id=keep_canonical_id,
        )
    )
    drop_rows = _records(
        session.run(
            """
            MATCH (n {canonical_id: $canonical_id})
            WHERE NOT n:Document AND NOT n:Evidence AND NOT n:KnowledgeGraph
            RETURN n
            LIMIT 1
            """,
            canonical_id=drop_canonical_id,
        )
    )
    if not keep_rows or not drop_rows:
        return
    keep = _node_to_dict(keep_rows[0]["n"], entity_type)
    drop = _node_to_dict(drop_rows[0]["n"], entity_type)
    aliases = _as_str_list(keep.get("aliases")) + _as_str_list(drop.get("aliases"))
    aliases.append(str(drop.get("name") or ""))
    keep_name = str(keep.get("name") or "")
    drop_name = str(drop.get("name") or "")
    if kind == "company":
        keep_name = preferred_company_name(keep_name, drop_name)
        session.run(
            """
            MATCH (keep {canonical_id: $keep_id})
            WHERE NOT keep:Document AND NOT keep:Evidence AND NOT keep:KnowledgeGraph
            SET keep:Company
            REMOVE keep:Applicant
            """,
            keep_id=keep_canonical_id,
        )
    session.run(
        """
        MATCH (keep {canonical_id: $keep_id})
        WHERE NOT keep:Document AND NOT keep:Evidence AND NOT keep:KnowledgeGraph
        SET keep.aliases = $aliases,
            keep.name = $name,
            keep.normalized_name = $normalized_name,
            keep.evidence_count = coalesce(keep.evidence_count, 0) + $extra_evidence
        """,
        keep_id=keep_canonical_id,
        aliases=_as_str_list(aliases),
        name=keep_name,
        normalized_name=normalize_for_type(kind, keep_name),
        extra_evidence=int(drop.get("evidence_count") or 0),
    )

    rels = _records(
        session.run(
            """
            MATCH (drop {canonical_id: $drop_id})-[r]-(other)
            WHERE drop.canonical_id IS NOT NULL
              AND other.canonical_id IS NOT NULL
              AND other.canonical_id <> $keep_id
              AND type(r) <> 'PROVIDES_EVIDENCE'
              AND type(r) <> 'ABOUT'
            RETURN type(r) AS rel_type,
                   startNode(r).canonical_id AS src,
                   endNode(r).canonical_id AS dst,
                   coalesce(startNode(r).entity_type, labels(startNode(r))[0]) AS src_type,
                   coalesce(endNode(r).entity_type, labels(endNode(r))[0]) AS dst_type,
                   coalesce(r.description, '') AS description,
                   coalesce(r.keywords, '') AS keywords
            """,
            drop_id=drop_canonical_id,
            keep_id=keep_canonical_id,
        )
    )
    for row in rels:
        src = str(row.get("src") or "")
        dst = str(row.get("dst") or "")
        if src == drop_canonical_id:
            src = keep_canonical_id
        if dst == drop_canonical_id:
            dst = keep_canonical_id
        if not src or not dst or src == dst:
            continue
        src_type = canonical_entity_type(str(row.get("src_type") or entity_type))
        dst_type = canonical_entity_type(str(row.get("dst_type") or entity_type))
        if src == keep_canonical_id:
            src_type = kind
        if dst == keep_canonical_id:
            dst_type = kind
        rel_type = _safe_rel_type(str(row.get("rel_type") or ""))
        merge_relationship(
            session,
            src_type,
            src,
            rel_type,
            dst_type,
            dst,
            description=str(row.get("description") or ""),
            keywords=str(row.get("keywords") or ""),
        )

    session.run(
        """
        MATCH (e:Evidence)-[a:ABOUT]->(drop {canonical_id: $drop_id})
        MATCH (keep {canonical_id: $keep_id})
        WHERE NOT keep:Document AND NOT keep:Evidence AND NOT keep:KnowledgeGraph
        MERGE (e)-[:ABOUT]->(keep)
        SET e.canonical_id = $keep_id
        DELETE a
        """,
        drop_id=drop_canonical_id,
        keep_id=keep_canonical_id,
    )
    session.run(
        """
        MATCH (n)
        WHERE n.company_canonical_id = $drop_id
        SET n.company_canonical_id = $keep_id
        """,
        drop_id=drop_canonical_id,
        keep_id=keep_canonical_id,
    )
    session.run(
        """
        MATCH (n)
        WHERE n.product_canonical_id = $drop_id
        SET n.product_canonical_id = $keep_id
        """,
        drop_id=drop_canonical_id,
        keep_id=keep_canonical_id,
    )
    session.run(
        """
        MATCH (n)
        WHERE n.parent_canonical_id = $drop_id
        SET n.parent_canonical_id = $keep_id
        """,
        drop_id=drop_canonical_id,
        keep_id=keep_canonical_id,
    )
    session.run(
        """
        MATCH (drop {canonical_id: $drop_id})
        WHERE NOT drop:Document AND NOT drop:Evidence AND NOT drop:KnowledgeGraph
        DETACH DELETE drop
        """,
        drop_id=drop_canonical_id,
    )


def drop_disallowed_relationships(session) -> int:
    """Remove leftover Component -> PARTY_ROLE_SUPPLIER edges of any company.

    Supplier is Company -> Applicant only. Manufacturer stays on Component.
    """
    result = session.run(
        """
        MATCH (c:Component)-[r:PARTY_ROLE_SUPPLIER]-()
        WITH collect(DISTINCT r) AS rels
        FOREACH (rel IN rels | DELETE rel)
        RETURN size(rels) AS dropped
        """
    )
    record = _first_record(result)
    dropped = 0
    if record is not None:
        try:
            dropped = int(record["dropped"] or 0)
        except Exception:
            dropped = 0
    if dropped:
        logger.info("Dropped %d Component PARTY_ROLE_SUPPLIER relationship(s)", dropped)
    return dropped


def collapse_manufacturing_locations(session) -> int:
    """Fold ManufacturingLocation into Location and HAS_MANUFACTURING_LOCATION into HAS_LOCATION."""
    relabel = _first_record(
        session.run(
            """
            MATCH (m:ManufacturingLocation)
            WITH collect(m) AS nodes
            FOREACH (n IN nodes |
              SET n:Location, n.entity_type = 'location'
              REMOVE n:ManufacturingLocation
            )
            RETURN size(nodes) AS n
            """
        )
    )
    relabeled = 0
    if relabel is not None:
        try:
            relabeled = int(relabel["n"] or 0)
        except Exception:
            relabeled = 0

    session.run(
        """
        MATCH (a)-[r:HAS_MANUFACTURING_LOCATION]->(b)
        MERGE (a)-[n:HAS_LOCATION]->(b)
        ON CREATE SET
          n.graph_id = coalesce(r.graph_id, $graph_id),
          n.created_at = coalesce(r.created_at, $ts)
        ON MATCH SET
          n.graph_id = coalesce(n.graph_id, r.graph_id, $graph_id)
        DELETE r
        """,
        graph_id=STABLE_GRAPH_ID,
        ts=utc_now_iso(),
    )

    rows = _records(
        session.run(
            """
            MATCH (n:Location)
            WHERE n.name IS NOT NULL
              AND (
                n.canonical_id STARTS WITH 'manufacturing_location_'
                OR n.entity_type = 'manufacturing_location'
              )
            RETURN elementId(n) AS eid, n.name AS name, n.canonical_id AS cid
            """
        )
    )
    converted = 0
    for row in rows:
        name = str(row.get("name") or "").strip()
        old_cid = str(row.get("cid") or "")
        if not name:
            continue
        new_cid = canonical_id_for("location", name)
        if new_cid == old_cid:
            session.run(
                """
                MATCH (n:Location {canonical_id: $cid})
                SET n.entity_type = 'location'
                """,
                cid=old_cid,
            )
            continue
        existing = _first_record(
            session.run(
                """
                MATCH (n:Location {canonical_id: $cid})
                RETURN n.canonical_id AS cid
                LIMIT 1
                """,
                cid=new_cid,
            )
        )
        if existing is not None and old_cid and old_cid != new_cid:
            absorb_entity(session, new_cid, old_cid, entity_type="location")
        elif old_cid:
            session.run(
                """
                MATCH (n:Location {canonical_id: $old_cid})
                SET n.canonical_id = $new_cid,
                    n.entity_type = 'location',
                    n.normalized_name = coalesce(n.normalized_name, $normalized_name)
                """,
                old_cid=old_cid,
                new_cid=new_cid,
                normalized_name=normalize_for_type("location", name),
            )
        converted += 1
    changed = relabeled + converted
    if changed:
        logger.info(
            "Collapsed ManufacturingLocation into Location (%s relabeled, %s retargeted)",
            relabeled,
            converted,
        )
    return changed


def link_manufacturers_as_suppliers(session) -> int:
    """Company that manufactures a component also supplies that product's applicant.

    Walks Component <- CONTAINS <- Model <- HAS_MODEL <- Product <- HAS_PRODUCT <- Company.
    Does not invent Component -> PARTY_ROLE_SUPPLIER and does not name specific companies.
    """
    result = session.run(
        """
        MATCH (component:Component)-[:PARTY_ROLE_MANUFACTURER]->(manufacturer)
        WHERE manufacturer.canonical_id IS NOT NULL
        OPTIONAL MATCH (model:Model)-[:CONTAINS]->(component)
        OPTIONAL MATCH (product_via_model:Product)-[:HAS_MODEL]->(model)
        OPTIONAL MATCH (product_direct:Product)-[:CONTAINS]->(component)
        WITH manufacturer,
             coalesce(product_via_model, product_direct) AS product
        WHERE product IS NOT NULL
        MATCH (owner)-[:HAS_PRODUCT]->(product)
        WHERE owner.canonical_id IS NOT NULL
          AND owner.canonical_id <> manufacturer.canonical_id
        MERGE (manufacturer)-[r:PARTY_ROLE_SUPPLIER]->(owner)
        ON CREATE SET
          r.graph_id = $graph_id,
          r.created_at = $ts
        ON MATCH SET
          r.graph_id = $graph_id
        RETURN count(r) AS linked
        """,
        graph_id=STABLE_GRAPH_ID,
        ts=utc_now_iso(),
    )
    record = _first_record(result)
    linked = 0
    if record is not None:
        try:
            linked = int(record["linked"] or 0)
        except Exception:
            linked = 0
    if linked:
        logger.info(
            "Linked manufacturer companies as PARTY_ROLE_SUPPLIER of product applicants (%s edge(s))",
            linked,
        )
    return linked


def stamp_missing_canonical_ids(session) -> int:
    """Assign canonical_id to named entity nodes that predate canonical ingest."""
    rows = _records(
        session.run(
            """
            MATCH (n)
            WHERE n.canonical_id IS NULL
              AND n.name IS NOT NULL
              AND NOT n:Document AND NOT n:Evidence AND NOT n:KnowledgeGraph
            RETURN elementId(n) AS eid, labels(n) AS labels, n.name AS name
            """
        )
    )
    stamped = 0
    for row in rows:
        name = str(row.get("name") or "").strip()
        eid = str(row.get("eid") or "")
        labels = [str(item) for item in (row.get("labels") or []) if str(item).strip()]
        if not name or not eid:
            continue
        label = next((item for item in labels if item not in {"Entity"}), labels[0] if labels else "Entity")
        entity_type = canonical_entity_type(label)
        cid = canonical_id_for(entity_type, name)
        existing = _first_record(
            session.run(
                """
                MATCH (n {canonical_id: $cid})
                WHERE NOT n:Document AND NOT n:Evidence AND NOT n:KnowledgeGraph
                RETURN n.canonical_id AS cid
                LIMIT 1
                """,
                cid=cid,
            )
        )
        if existing is not None:
            absorb_legacy_node(session, eid, cid) if entity_type == "company" else _absorb_legacy_by_id(
                session, eid, cid, entity_type
            )
            stamped += 1
            continue
        session.run(
            """
            MATCH (n)
            WHERE elementId(n) = $eid
            SET n.canonical_id = $cid,
                n.normalized_name = coalesce(n.normalized_name, $normalized_name),
                n.entity_type = coalesce(n.entity_type, $entity_type),
                n.graph_id = coalesce(n.graph_id, $graph_id)
            """,
            eid=eid,
            cid=cid,
            normalized_name=normalize_for_type(entity_type, name),
            entity_type=entity_type,
            graph_id=STABLE_GRAPH_ID,
        )
        stamped += 1
    if stamped:
        logger.info("Stamped canonical_id on %d legacy entity node(s)", stamped)
    return stamped


def _absorb_legacy_by_id(session, element_id: str, keep_canonical_id: str, entity_type: str) -> None:
    """Rewire a nameless-id node onto an existing canonical entity of any type."""
    rels = _records(
        session.run(
            """
            MATCH (drop)-[r]-(other)
            WHERE elementId(drop) = $eid
              AND other.canonical_id IS NOT NULL
              AND other.canonical_id <> $keep_id
              AND type(r) <> 'PROVIDES_EVIDENCE'
              AND type(r) <> 'ABOUT'
            RETURN type(r) AS rel_type,
                   startNode(r).canonical_id AS src,
                   endNode(r).canonical_id AS dst,
                   coalesce(startNode(r).entity_type, labels(startNode(r))[0]) AS src_type,
                   coalesce(endNode(r).entity_type, labels(endNode(r))[0]) AS dst_type,
                   elementId(startNode(r)) = $eid AS from_drop
            """,
            eid=element_id,
            keep_id=keep_canonical_id,
        )
    )
    kind = canonical_entity_type(entity_type)
    for row in rels:
        rel_type = _safe_rel_type(str(row.get("rel_type") or ""))
        from_drop = bool(row.get("from_drop"))
        other_id = str(row.get("dst") if from_drop else row.get("src") or "")
        other_type = canonical_entity_type(
            str(row.get("dst_type") if from_drop else row.get("src_type") or kind)
        )
        if not other_id:
            continue
        if from_drop:
            merge_relationship(session, kind, keep_canonical_id, rel_type, other_type, other_id)
        else:
            merge_relationship(session, other_type, other_id, rel_type, kind, keep_canonical_id)
    session.run(
        """
        MATCH (e:Evidence)-[a:ABOUT]->(drop)
        WHERE elementId(drop) = $eid
        MATCH (keep {canonical_id: $keep_id})
        MERGE (e)-[:ABOUT]->(keep)
        DELETE a
        """,
        eid=element_id,
        keep_id=keep_canonical_id,
    )
    session.run(
        "MATCH (drop) WHERE elementId(drop) = $eid DETACH DELETE drop",
        eid=element_id,
    )


def link_standard_certifications(session) -> int:
    """Standard -> HAS_CERTIFICATION -> Certification via shared components."""
    result = session.run(
        """
        MATCH (component:Component)-[:COMPLIES_WITH]->(std:Standard)
        MATCH (component)-[:HAS_CERTIFICATION]->(cert:Certification)
        WHERE std.canonical_id IS NOT NULL AND cert.canonical_id IS NOT NULL
        MERGE (std)-[r:HAS_CERTIFICATION]->(cert)
        ON CREATE SET r.graph_id = $graph_id, r.created_at = $ts
        ON MATCH SET r.graph_id = $graph_id
        RETURN count(r) AS linked
        """,
        graph_id=STABLE_GRAPH_ID,
        ts=utc_now_iso(),
    )
    record = _first_record(result)
    linked = 0
    if record is not None:
        try:
            linked = int(record["linked"] or 0)
        except Exception:
            linked = 0
    if linked:
        logger.info("Linked Standard -> HAS_CERTIFICATION -> Certification (%s edge(s))", linked)
    return linked


def absorb_legacy_node(session, element_id: str, keep_canonical_id: str) -> None:
    """Fold a pre-canonical Company/Applicant node into a canonical company."""
    if not element_id or not keep_canonical_id:
        return
    drop_rows = _records(
        session.run(
            """
            MATCH (drop)
            WHERE elementId(drop) = $eid
            RETURN drop.name AS name, labels(drop) AS labels
            """,
            eid=element_id,
        )
    )
    if not drop_rows:
        return
    drop_name = str(drop_rows[0].get("name") or "")
    session.run(
        """
        MATCH (keep {canonical_id: $keep_id})
        WHERE NOT keep:Document AND NOT keep:Evidence AND NOT keep:KnowledgeGraph
        SET keep:Company
        REMOVE keep:Applicant
        FOREACH (_ IN CASE WHEN $drop_name <> '' AND NOT $drop_name IN coalesce(keep.aliases, [])
          THEN [1] ELSE [] END |
          SET keep.aliases = coalesce(keep.aliases, []) + $drop_name
        )
        SET keep.name = CASE
          WHEN size($drop_name) > size(coalesce(keep.name, '')) THEN $drop_name
          ELSE keep.name
        END
        """,
        keep_id=keep_canonical_id,
        drop_name=drop_name,
    )
    # Preferred display name (legal form / longer) after the crude length SET above.
    keep_rows = _records(
        session.run(
            """
            MATCH (keep {canonical_id: $keep_id})
            RETURN keep.name AS name
            LIMIT 1
            """,
            keep_id=keep_canonical_id,
        )
    )
    if keep_rows:
        better = preferred_company_name(str(keep_rows[0].get("name") or ""), drop_name)
        session.run(
            """
            MATCH (keep {canonical_id: $keep_id})
            SET keep.name = $name, keep.normalized_name = $normalized_name
            """,
            keep_id=keep_canonical_id,
            name=better,
            normalized_name=normalize_for_type("company", better),
        )

    rels = _records(
        session.run(
            """
            MATCH (drop)-[r]-(other)
            WHERE elementId(drop) = $eid
              AND other.canonical_id IS NOT NULL
              AND other.canonical_id <> $keep_id
              AND type(r) <> 'PROVIDES_EVIDENCE'
              AND type(r) <> 'ABOUT'
            RETURN type(r) AS rel_type,
                   startNode(r).canonical_id AS src,
                   endNode(r).canonical_id AS dst,
                   coalesce(startNode(r).entity_type, labels(startNode(r))[0]) AS src_type,
                   coalesce(endNode(r).entity_type, labels(endNode(r))[0]) AS dst_type,
                   coalesce(r.description, '') AS description,
                   coalesce(r.keywords, '') AS keywords,
                   elementId(startNode(r)) = $eid AS from_drop
            """,
            eid=element_id,
            keep_id=keep_canonical_id,
        )
    )
    for row in rels:
        rel_type = _safe_rel_type(str(row.get("rel_type") or ""))
        from_drop = bool(row.get("from_drop"))
        other_id = str(row.get("dst") if from_drop else row.get("src") or "")
        other_type = canonical_entity_type(
            str(row.get("dst_type") if from_drop else row.get("src_type") or "company")
        )
        if not other_id:
            continue
        if from_drop:
            merge_relationship(
                session,
                "company",
                keep_canonical_id,
                rel_type,
                other_type,
                other_id,
                description=str(row.get("description") or ""),
                keywords=str(row.get("keywords") or ""),
            )
        else:
            merge_relationship(
                session,
                other_type,
                other_id,
                rel_type,
                "company",
                keep_canonical_id,
                description=str(row.get("description") or ""),
                keywords=str(row.get("keywords") or ""),
            )

    session.run(
        """
        MATCH (e:Evidence)-[a:ABOUT]->(drop)
        WHERE elementId(drop) = $eid
        MATCH (keep {canonical_id: $keep_id})
        MERGE (e)-[:ABOUT]->(keep)
        SET e.canonical_id = $keep_id
        DELETE a
        """,
        eid=element_id,
        keep_id=keep_canonical_id,
    )
    session.run(
        """
        MATCH (drop)
        WHERE elementId(drop) = $eid
        DETACH DELETE drop
        """,
        eid=element_id,
    )


def collapse_legacy_org_nodes(session) -> int:
    """Merge Company/Applicant nodes that predate canonical_id into matching companies."""
    canonical = list_entities(session, "company")
    if not canonical:
        return 0
    legacy = _records(
        session.run(
            """
            MATCH (n)
            WHERE (n:Company OR n:Applicant)
              AND n.canonical_id IS NULL
              AND n.name IS NOT NULL
            RETURN elementId(n) AS eid, n.name AS name
            """
        )
    )
    merged = 0
    for row in legacy:
        name = str(row.get("name") or "")
        eid = str(row.get("eid") or "")
        if not name or not eid:
            continue
        keep = next(
            (
                company
                for company in canonical
                if company_names_equivalent(name, str(company.get("name") or ""))
            ),
            None,
        )
        if keep is None:
            continue
        absorb_legacy_node(session, eid, str(keep.get("canonical_id") or ""))
        merged += 1
        logger.info(
            "Collapsed legacy org %s into %s",
            name,
            keep.get("canonical_id"),
        )
    return merged
