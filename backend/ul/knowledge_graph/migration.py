"""Optional migration of legacy graph_id-scoped Neo4j nodes onto canonical IDs.

This script does not run automatically during ingest. It assigns canonical_ids,
rewires relationships onto a surviving node per identity, and only then
detaches leftover duplicate nodes.

Run:

    python -m ul.knowledge_graph.migration
"""

from __future__ import annotations

import logging
from typing import Any

from ul.knowledge_graph.canonical_ids import canonical_id_for
from ul.knowledge_graph.labels import canonical_entity_type, to_label
from ul.knowledge_graph.neo4j_upsert import (
    _first_record,
    _records,
    ensure_constraints,
    merge_document,
    merge_entity,
    merge_ul,
)
from ul.knowledge_graph.normalization import normalize_for_type
from ul.knowledge_graph.provenance import utc_now_iso

logger = logging.getLogger(__name__)


def _driver():
    import config
    from neo4j import GraphDatabase

    if not config.NEO4J_PASSWORD:
        raise ValueError("NEO4J_PASSWORD is not configured")
    return GraphDatabase.driver(
        config.NEO4J_URI,
        auth=(config.NEO4J_USERNAME, config.NEO4J_PASSWORD),
    )


def migrate_legacy_graph(session) -> dict[str, int]:
    """Migrate graph_id-scoped entities to canonical_id identity.

    Never deletes a node until its relationships and descriptions have been
    copied onto the surviving canonical entity.
    """
    ensure_constraints(session)
    merge_ul(session)
    stats = {
        "documents_created": 0,
        "nodes_labeled": 0,
        "duplicates_merged": 0,
        "nodes_labeled": 0,
    }

    kg_rows = _records(
        session.run(
            """
            MATCH (g:KnowledgeGraph)
            WHERE g.graph_id IS NOT NULL
            RETURN g.graph_id AS graph_id,
                   g.document_id AS document_id,
                   g.source_files AS source_files,
                   g.created_at AS created_at
            """
        )
    )
    for row in kg_rows:
        graph_id = str(row.get("graph_id") or "").strip()
        document_id = str(row.get("document_id") or graph_id).strip()
        if not graph_id:
            continue
        files = row.get("source_files") or []
        if not isinstance(files, list):
            files = [files] if files else []
        merge_document(
            session,
            document_id=document_id,
            file_name=str(files[0] if files else graph_id),
            source_files=[str(item) for item in files],
            ingestion_id=graph_id,
            uploaded_at=str(row.get("created_at") or utc_now_iso()),
            source="legacy_knowledge_graph",
        )
        stats["documents_created"] += 1

    unlabeled = _records(
        session.run(
            """
            MATCH (n)
            WHERE n.canonical_id IS NULL
              AND n.name IS NOT NULL
              AND NOT n:KnowledgeGraph
              AND NOT n:Document
              AND NOT n:Evidence
            RETURN n, labels(n)[0] AS label, elementId(n) AS eid
            """
        )
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in unlabeled:
        node = row.get("n")
        label = str(row.get("label") or "Entity")
        name = ""
        graph_id = ""
        description = ""
        try:
            name = str(node.get("name") or "")
            graph_id = str(node.get("graph_id") or "")
            description = str(node.get("description") or "")
        except Exception:
            continue
        if not name:
            continue
        entity_type = canonical_entity_type(label)
        cid = canonical_id_for(entity_type, name)
        session.run(
            """
            MATCH (n)
            WHERE elementId(n) = $eid
            SET n.canonical_id = $canonical_id,
                n.normalized_name = $normalized_name,
                n.entity_type = $entity_type
            """,
            eid=row.get("eid"),
            canonical_id=cid,
            normalized_name=normalize_for_type(entity_type, name),
            entity_type=entity_type,
        )
        stats["nodes_labeled"] += 1
        groups.setdefault((entity_type, cid), []).append(
            {
                "eid": row.get("eid"),
                "name": name,
                "graph_id": graph_id,
                "description": description,
            }
        )

    for (entity_type, cid), members in groups.items():
        survivor = members[0]
        merge_entity(
            session,
            entity_type,
            cid,
            name=survivor["name"],
            normalized_name=normalize_for_type(entity_type, survivor["name"]),
            aliases=[member["name"] for member in members],
        )
        label = to_label(entity_type)
        for member in members:
            if member["eid"] == survivor["eid"]:
                continue
            session.run(
                f"""
                MATCH (dup) WHERE elementId(dup) = $dup_id
                MATCH (keep:{label} {{canonical_id: $canonical_id}})
                OPTIONAL MATCH (dup)-[r]->(other)
                WHERE other <> keep
                FOREACH (_ IN CASE WHEN r IS NULL THEN [] ELSE [1] END |
                  MERGE (keep)-[nr:RELATED_TO]->(other)
                )
                WITH dup, keep
                OPTIONAL MATCH (other2)-[r2]->(dup)
                WHERE other2 <> keep
                FOREACH (_ IN CASE WHEN r2 IS NULL THEN [] ELSE [1] END |
                  MERGE (other2)-[nr2:RELATED_TO]->(keep)
                )
                WITH dup
                DETACH DELETE dup
                """,
                dup_id=member["eid"],
                canonical_id=cid,
            )
            stats["duplicates_merged"] += 1

    logger.info("Legacy graph migration stats: %s", stats)
    return stats


def run_migration() -> dict[str, int]:
    driver = _driver()
    try:
        with driver.session() as session:
            return migrate_legacy_graph(session)
    finally:
        driver.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_migration())
