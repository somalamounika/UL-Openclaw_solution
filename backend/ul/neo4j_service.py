import logging
from datetime import datetime, timezone

from neo4j import GraphDatabase

import config
from ul.knowledge_graph.ingest import GLOBAL_GRAPH_ID, ingest_triplets_incremental
from ul.knowledge_graph.labels import to_label as _kg_to_label
from ul.knowledge_graph.labels import to_relationship_type as _kg_to_rel
from ul.knowledge_graph.neo4j_upsert import _as_str_list, _first_record, _records
from ul.knowledge_graph.queries import fetch_canonical_graph

logger = logging.getLogger(__name__)


def _to_label(entity_type: str) -> str:
    """Convert entity type to a Neo4j node label, e.g. company -> Company."""
    return _kg_to_label(entity_type)


def _to_relationship_type(relationship: str) -> str:
    """Convert relationship name to Neo4j relationship type, e.g. contains -> CONTAINS."""
    return _kg_to_rel(relationship)


def _get_driver():
    if not config.NEO4J_PASSWORD:
        raise ValueError("NEO4J_PASSWORD is not configured")
    return GraphDatabase.driver(
        config.neo4j_driver_uri(),
        auth=(config.NEO4J_USERNAME, config.NEO4J_PASSWORD),
    )


def _node_merge_field(entity_type: str, triplet: dict, role: str) -> tuple[str, str]:
    """Return (merge_property, merge_value) for a node endpoint."""
    prefix = "source" if role == "source" else "destination"
    name = str(triplet.get(f"{prefix}_node") or "").strip()
    identity = str(triplet.get(f"{prefix}_identity_key") or "").strip()
    if entity_type.lower() == "product" and identity:
        return "identity_key", identity
    if entity_type.lower() == "model" and identity:
        return "identity_key", identity
    return "name", name


def _entity_description_lookup(
    entity_descriptions: dict[str, str] | None,
    entity_type: str,
    name: str,
) -> str:
    if not entity_descriptions:
        return ""
    from ul.product_normalization import norm_key

    key = f"{entity_type.strip().lower()}:{norm_key(name)}"
    return str(entity_descriptions.get(key) or "").strip()


def ingest_triplets(
    triplets: list[dict],
    *,
    graph_id: str,
    source_files: list[str] | None = None,
    document_id: str | None = None,
    created_at: str | None = None,
    entity_descriptions: dict[str, str] | None = None,
    content_hash: str = "",
    ingestion_id: str = "",
    file_name: str = "",
) -> int:
    """Merge extracted triplets into the one global UL Knowledge Graph.

    ``graph_id`` is accepted for API compatibility and stored as ingestion
    provenance. It is not part of canonical entity identity.
    """
    graph_id = (graph_id or GLOBAL_GRAPH_ID).strip() or GLOBAL_GRAPH_ID
    resolved_document_id = (document_id or graph_id or "").strip()
    if not resolved_document_id:
        raise ValueError("document_id is required for Neo4j ingest")

    created = created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    files = _as_str_list(source_files)
    driver = _get_driver()
    ingested = 0

    try:
        with driver.session() as session:
            ingested = ingest_triplets_incremental(
                triplets,
                document_id=resolved_document_id,
                graph_id=GLOBAL_GRAPH_ID,
                source_files=files,
                ingestion_id=ingestion_id or graph_id,
                created_at=created,
                entity_descriptions=entity_descriptions,
                content_hash=content_hash,
                file_name=file_name or (files[0] if files else ""),
                session=session,
            )
    finally:
        driver.close()

    logger.info(
        "Ingested %d triplets into canonical UL graph (document_id=%s, graph_id=%s)",
        ingested,
        resolved_document_id,
        GLOBAL_GRAPH_ID,
    )
    return ingested


def list_graphs() -> list[dict]:
    """Return the global UL graph plus document provenance rows, newest first."""
    driver = _get_driver()
    graphs: list[dict] = []
    try:
        with driver.session() as session:
            global_row = _first_record(
                session.run(
                    """
                    OPTIONAL MATCH (g:KnowledgeGraph {graph_id: $graph_id})
                    OPTIONAL MATCH (n)
                    WHERE n.canonical_id IS NOT NULL
                      AND NOT n:Document AND NOT n:Evidence AND NOT n:KnowledgeGraph
                    WITH g, count(n) AS canonical_nodes
                    OPTIONAL MATCH (d:Document)
                    WITH g, canonical_nodes, collect(DISTINCT d.file_name) AS names,
                         collect(DISTINCT d.source_files) AS nested
                    RETURN g.source_files AS source_files,
                           g.created_at AS created_at,
                           g.updated_at AS updated_at,
                           g.document_id AS document_id,
                           canonical_nodes AS canonical_nodes,
                           names AS names,
                           nested AS nested
                    """,
                    graph_id=GLOBAL_GRAPH_ID,
                )
            )
            files: list[str] = []
            created_at = ""
            canonical_nodes = 0
            if global_row is not None:
                try:
                    files = _as_str_list(global_row["source_files"])
                    created_at = str(global_row["updated_at"] or global_row["created_at"] or "")
                    extra_names = _as_str_list(global_row["names"])
                    nested = global_row["nested"] or []
                    for group in nested:
                        files.extend(_as_str_list(group))
                    files.extend(extra_names)
                    canonical_nodes = int(global_row["canonical_nodes"] or 0)
                except Exception:
                    canonical_nodes = 0
            seen_files: list[str] = []
            seen_keys: set[str] = set()
            for name in files:
                key = name.strip().lower()
                if not name or key in seen_keys:
                    continue
                seen_keys.add(key)
                seen_files.append(name)
            if canonical_nodes or seen_files or global_row is not None:
                graphs.append(
                    {
                        "graph_id": GLOBAL_GRAPH_ID,
                        "source_files": seen_files,
                        "created_at": created_at,
                        "document_id": "",
                    }
                )

            docs = session.run(
                """
                MATCH (d:Document)
                WHERE d.document_id IS NOT NULL
                RETURN
                  d.document_id AS document_id,
                  d.source_files AS source_files,
                  d.file_name AS file_name,
                  coalesce(d.updated_at, d.uploaded_at, d.created_at) AS created_at
                ORDER BY created_at DESC
                """
            )
            for record in _records(docs):
                doc_id = str(record["document_id"] or "").strip()
                if not doc_id:
                    continue
                doc_files = _as_str_list(record["source_files"])
                file_name = str(record["file_name"] or "").strip()
                if file_name and file_name not in doc_files:
                    doc_files = [file_name, *doc_files]
                graphs.append(
                    {
                        "graph_id": doc_id,
                        "source_files": doc_files,
                        "created_at": str(record["created_at"] or ""),
                        "document_id": doc_id,
                    }
                )

            legacy = session.run(
                """
                MATCH (g:KnowledgeGraph)
                WHERE g.graph_id IS NOT NULL AND g.graph_id <> $global_id
                RETURN
                  g.graph_id AS graph_id,
                  g.source_files AS source_files,
                  g.created_at AS created_at,
                  g.document_id AS document_id
                ORDER BY g.created_at DESC
                """,
                global_id=GLOBAL_GRAPH_ID,
            )
            for record in _records(legacy):
                graphs.append(
                    {
                        "graph_id": str(record["graph_id"]),
                        "source_files": _as_str_list(record["source_files"]),
                        "created_at": str(record["created_at"] or ""),
                        "document_id": str(record["document_id"] or ""),
                    }
                )
    finally:
        driver.close()
    return graphs


def delete_graph(graph_id: str) -> int:
    """Delete document evidence or a legacy graph_id-scoped graph.

    The global canonical UL graph is not deleted. Passing ``ul_global`` is a
    no-op (returns 0). Passing a document_id removes that document's evidence.
    """
    graph_id = (graph_id or "").strip()
    if not graph_id:
        raise ValueError("graph_id is required")

    driver = _get_driver()
    deleted = 0
    try:
        with driver.session() as session:
            if graph_id == GLOBAL_GRAPH_ID:
                logger.info("Refusing to delete the global canonical UL graph")
                return 0

            doc = _first_record(
                session.run(
                    """
                    MATCH (d:Document {document_id: $document_id})
                    RETURN d.document_id AS document_id
                    """,
                    document_id=graph_id,
                )
            )
            if doc is not None:
                result = session.run(
                    """
                    MATCH (d:Document {document_id: $document_id})
                    OPTIONAL MATCH (d)-[:PROVIDES_EVIDENCE]->(e:Evidence)
                    DETACH DELETE e, d
                    """,
                    document_id=graph_id,
                )
                deleted = int(result.consume().counters.nodes_deleted)
            else:
                result = session.run(
                    """
                    MATCH (n {graph_id: $graph_id})
                    DETACH DELETE n
                    """,
                    graph_id=graph_id,
                )
                deleted = int(result.consume().counters.nodes_deleted)
    finally:
        driver.close()

    logger.info("Deleted Neo4j graph_id=%s (%d node(s))", graph_id, deleted)
    return deleted


def _legacy_graph_summary(session, selected: str) -> dict:
    stats = session.run(
        """
        MATCH (n)
        WHERE n.graph_id = $graph_id AND NOT n:KnowledgeGraph
        WITH count(n) AS node_count
        OPTIONAL MATCH (a)-[r {graph_id: $graph_id}]->(b)
        WHERE NOT a:KnowledgeGraph AND NOT b:KnowledgeGraph
        RETURN node_count, count(r) AS relationship_count
        """,
        graph_id=selected,
    ).single()

    nodes_result = session.run(
        """
        MATCH (n)
        WHERE n.graph_id = $graph_id
          AND n.name IS NOT NULL
          AND NOT n:KnowledgeGraph
        RETURN
          labels(n)[0] AS label,
          n.name AS name,
          coalesce(n.description, '') AS description,
          coalesce(n.canonical_id, '') AS canonical_id,
          properties(n) AS properties,
          elementId(n) AS element_id
        ORDER BY label, name
        LIMIT 2000
        """,
        graph_id=selected,
    )
    nodes = []
    for record in nodes_result:
        if not record["label"] or not record["name"]:
            continue
        props = dict(record["properties"] or {})
        if record["description"] and "description" not in props:
            props["description"] = record["description"]
        if record["canonical_id"] and "canonical_id" not in props:
            props["canonical_id"] = record["canonical_id"]
        if record["element_id"]:
            props.setdefault("<id>", record["element_id"])
        nodes.append(
            {
                "id": f"{record['label']}:{record['name']}",
                "label": record["label"],
                "name": record["name"],
                "canonical_id": record["canonical_id"] or "",
                "description": record["description"] or "",
                "source_files": [],
                "properties": props,
            }
        )

    edges_result = session.run(
        """
        MATCH (s)-[r]->(t)
        WHERE s.graph_id = $graph_id
          AND t.graph_id = $graph_id
          AND s.name IS NOT NULL
          AND t.name IS NOT NULL
          AND NOT s:KnowledgeGraph
          AND NOT t:KnowledgeGraph
        RETURN
          labels(s)[0] AS source_label,
          s.name AS source,
          type(r) AS relationship,
          labels(t)[0] AS destination_label,
          t.name AS destination,
          coalesce(r.description, '') AS description,
          coalesce(r.keywords, '') AS keywords,
          properties(r) AS properties,
          elementId(r) AS element_id
        LIMIT 4000
        """,
        graph_id=selected,
    )
    edges = []
    for record in edges_result:
        if not record["source_label"] or not record["destination_label"]:
            continue
        props = dict(record["properties"] or {})
        if record["description"] and "description" not in props:
            props["description"] = record["description"]
        if record["keywords"] and "keywords" not in props:
            props["keywords"] = record["keywords"]
        if record["element_id"]:
            props.setdefault("<id>", record["element_id"])
        props.setdefault("relationship", record["relationship"])
        edges.append(
            {
                "id": (
                    f"{record['source_label']}:{record['source']}"
                    f"-[{record['relationship']}]->"
                    f"{record['destination_label']}:{record['destination']}"
                ),
                "source": f"{record['source_label']}:{record['source']}",
                "source_name": record["source"],
                "source_label": record["source_label"],
                "relationship": record["relationship"],
                "destination": f"{record['destination_label']}:{record['destination']}",
                "destination_name": record["destination"],
                "destination_label": record["destination_label"],
                "description": record["description"] or "",
                "keywords": record["keywords"] or "",
                "source_files": [],
                "page_ranges": [],
                "chunk_ids": [],
                "properties": props,
            }
        )
    return {
        "graph_id": selected,
        "node_count": stats["node_count"] if stats else 0,
        "relationship_count": stats["relationship_count"] if stats else 0,
        "nodes": nodes,
        "edges": edges,
    }


def get_graph_summary(graph_id: str | None = None) -> dict:
    """Return the global canonical UL graph.

    Visualization is not filtered by document/graph_id. Legacy isolated
    graphs (pre-incremental ingest) can still be fetched by their old
    ``kg_…`` graph_id.
    """
    driver = _get_driver()
    selected = (graph_id or "").strip() or None

    try:
        with driver.session() as session:
            if selected and selected != GLOBAL_GRAPH_ID:
                is_document = _first_record(
                    session.run(
                        "MATCH (d:Document {document_id: $document_id}) RETURN d.document_id AS id",
                        document_id=selected,
                    )
                )
                if is_document is None:
                    return _legacy_graph_summary(session, selected)

            summary = fetch_canonical_graph(session)
            if summary["node_count"] == 0 and not summary["nodes"]:
                if selected and selected not in {None, GLOBAL_GRAPH_ID}:
                    return _legacy_graph_summary(session, selected)
                latest = _first_record(
                    session.run(
                        """
                        MATCH (g:KnowledgeGraph)
                        WHERE g.graph_id IS NOT NULL AND g.graph_id <> $global_id
                        RETURN g.graph_id AS graph_id
                        ORDER BY g.created_at DESC
                        LIMIT 1
                        """,
                        global_id=GLOBAL_GRAPH_ID,
                    )
                )
                legacy_id = ""
                if latest is not None:
                    try:
                        legacy_id = str(latest["graph_id"] or "")
                    except Exception:
                        legacy_id = ""
                if legacy_id:
                    return _legacy_graph_summary(session, legacy_id)
                return {
                    "graph_id": None,
                    "node_count": 0,
                    "relationship_count": 0,
                    "nodes": [],
                    "edges": [],
                }
            return {
                "graph_id": GLOBAL_GRAPH_ID,
                **summary,
            }
    finally:
        driver.close()
