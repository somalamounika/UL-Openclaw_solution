"""Read queries for the canonical UL Knowledge Graph and provenance."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ul.knowledge_graph.canonical_ids import UL_CANONICAL_ID
from ul.knowledge_graph.labels import is_provenance_label, to_label
from ul.knowledge_graph.neo4j_upsert import _as_str_list, _first_record, _records

logger = logging.getLogger(__name__)

# Intent → (terminal Neo4j labels to prefer, relationship types that mark relevant paths).
# Labels/types only — no product/model/standard instance names.
_LINEAGE_INTENT_TARGETS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "clause": (
        ("Clause",),
        ("HAS_CLAUSE", "COMPLIES_WITH", "SUBJECT_TO"),
    ),
    "standard": (
        ("Standard",),
        ("COMPLIES_WITH", "SUBJECT_TO", "HAS_CLAUSE", "REQUIRES_TEST"),
    ),
    "certification": (
        ("Certification", "Certificate"),
        ("HAS_CERTIFICATION", "COMPLIES_WITH", "SUBJECT_TO"),
    ),
    "test": (
        ("Test", "TestPlan"),
        ("REQUIRES_TEST", "HAS_TEST_PLAN", "COMPLIES_WITH"),
    ),
    "component": (
        ("Component", "Material"),
        ("CONTAINS", "COMPLIES_WITH"),
    ),
    "company": (
        ("Company",),
        (
            "PARTY_ROLE_APPLICANT",
            "PARTY_ROLE_MANUFACTURER",
            "PARTY_ROLE_SUPPLIER",
            "HAS_PRODUCT",
        ),
    ),
    "product": (("Product", "Model"), ("HAS_PRODUCT", "HAS_MODEL", "CONTAINS")),
    "model": (("Model", "Product", "Variant"), ("HAS_MODEL", "CONTAINS", "COMPLIES_WITH")),
    "compliance": (
        ("Standard", "Certification", "Clause", "Test"),
        ("COMPLIES_WITH", "SUBJECT_TO", "HAS_CERTIFICATION", "HAS_CLAUSE", "REQUIRES_TEST"),
    ),
}

_LINEAGE_INTENT_TERMS: dict[str, set[str]] = {
    "company": {"company", "companies", "manufacturer", "supplier", "brand"},
    "product": {"product", "products", "device", "devices"},
    "model": {"model", "models", "variant", "variants"},
    "component": {"component", "components", "part", "parts", "assembly"},
    "standard": {"standard", "standards", "regulation", "regulations"},
    "clause": {"clause", "clauses", "section", "sections", "requirement"},
    "certification": {"certification", "certifications", "certificate", "certified"},
    "test": {"test", "tests", "testing", "evaluation"},
    "compliance": {
        "compliance",
        "compliant",
        "requirement",
        "requirements",
        "required",
        "applicable",
    },
}

_LINEAGE_ALLOWED_TYPES = [
    "HAS_PRODUCT",
    "HAS_MODEL",
    "CONTAINS",
    "COMPLIES_WITH",
    "SUBJECT_TO",
    "REQUIRES_TEST",
    "HAS_CERTIFICATION",
    "HAS_TEST_PLAN",
    "HAS_FILE_NUMBER",
    "HAS_VOLUME",
    "HAS_DELIVERABLE",
    "HAS_CLAUSE",
    "PARTY_ROLE_APPLICANT",
    "PARTY_ROLE_MANUFACTURER",
    "PARTY_ROLE_SUPPLIER",
    "HAS_LOCATION",
    "HAS_ADDRESS",
]


def _jsonable_prop(value: Any) -> Any:
    """Convert Neo4j property values into JSON-safe scalars/lists."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable_prop(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable_prop(item) for key, item in value.items()}
    return str(value)


def _properties_payload(raw: Any, *, exclude: set[str] | None = None) -> dict[str, Any]:
    skip = exclude or set()
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key)
        if name in skip:
            continue
        converted = _jsonable_prop(value)
        if converted in (None, "", [], {}):
            continue
        out[name] = converted
    return out


def _node_payload(record: Any, prefix: str = "n") -> dict[str, Any]:
    label = record.get(f"{prefix}_label") or record.get("label")
    name = record.get(f"{prefix}_name") or record.get("name")
    canonical_id = record.get(f"{prefix}_canonical_id") or record.get("canonical_id") or ""
    description = record.get(f"{prefix}_description") or record.get("description") or ""
    props = _properties_payload(
        record.get(f"{prefix}_properties") or record.get("properties") or {},
        exclude=set(),
    )
    if description and "description" not in props:
        props["description"] = description
    if canonical_id and "canonical_id" not in props:
        props["canonical_id"] = canonical_id
    element_id = str(record.get(f"{prefix}_element_id") or record.get("element_id") or "").strip()
    if element_id:
        props.setdefault("<id>", element_id)
    return {
        "id": f"{label}:{name}",
        "label": label,
        "name": name,
        "canonical_id": canonical_id,
        "description": description or str(props.get("description") or ""),
        "source_files": _as_str_list(props.get("source_files")),
        "properties": props,
    }


def resolve_lineage_targets(question: str | None) -> tuple[list[str], list[str]]:
    """Map a natural-language question to preferred Neo4j labels and rel types."""
    if not (question or "").strip():
        return [], []
    tokens = {
        token.lower().strip("./-_")
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", question or "")
    }
    intents = [
        intent
        for intent, terms in _LINEAGE_INTENT_TERMS.items()
        if any(token in terms for token in tokens)
    ]
    labels: list[str] = []
    rel_types: list[str] = []
    seen_labels: set[str] = set()
    seen_rels: set[str] = set()
    for intent in intents:
        target_labels, preferred_rels = _LINEAGE_INTENT_TARGETS.get(intent, ((), ()))
        for label in target_labels:
            if label not in seen_labels:
                seen_labels.add(label)
                labels.append(label)
        for rel in preferred_rels:
            if rel not in seen_rels:
                seen_rels.add(rel)
                rel_types.append(rel)
    return labels, rel_types


def assemble_prioritized_lineage(
    edge_rows: list[dict[str, Any]],
    *,
    seed_nodes: list[dict[str, Any]],
    max_nodes: int = 60,
    max_edges: int = 90,
    max_generic_contains: int = 8,
) -> dict[str, Any]:
    """Build a bounded subgraph, keeping intent-priority edges before generic CONTAINS.

    ``edge_rows`` are sorted by ascending ``priority`` (0 = on a path that hits
    a question target label). CONTAINS edges that are not on a priority-0 path
    are capped so they cannot crowd out longer compliance paths.
    """
    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen_edge_ids: set[str] = set()
    generic_contains = 0

    def add_node(label: object, name: object, canonical_id: object, description: object = "") -> None:
        clean_label = str(label or "").strip()
        clean_name = str(name or "").strip()
        if not clean_label or not clean_name or is_provenance_label(clean_label):
            return
        node_id = f"{clean_label}:{clean_name}"
        if node_id in nodes_by_id:
            return
        nodes_by_id[node_id] = {
            "id": node_id,
            "label": clean_label,
            "name": clean_name,
            "canonical_id": str(canonical_id or "").strip(),
            "description": str(description or "").strip(),
            "source_files": [],
        }

    for seed in seed_nodes:
        add_node(
            seed.get("label"),
            seed.get("name"),
            seed.get("canonical_id"),
            seed.get("description"),
        )

    # Sort defensively so callers that feed unordered rows still prefer intent paths.
    def _row_priority(row: dict[str, Any]) -> tuple[int, int]:
        try:
            priority = int(row.get("priority", 2))
        except (TypeError, ValueError):
            priority = 2
        # Prefer non-CONTAINS slightly within the same priority band.
        rel = str(row.get("relationship") or "")
        contains_penalty = 1 if rel == "CONTAINS" and priority >= 2 else 0
        return (priority, contains_penalty)

    ordered_rows = sorted(edge_rows, key=_row_priority)

    for record in ordered_rows:
        if not record.get("source_label") or not record.get("destination_label"):
            continue
        relationship = str(record.get("relationship") or "").strip()
        try:
            priority = int(record.get("priority", 2))
        except (TypeError, ValueError):
            priority = 2
        if (
            relationship == "CONTAINS"
            and priority >= 1
            and generic_contains >= max_generic_contains
        ):
            continue

        edge_id = (
            f"{record['source_label']}:{record['source']}"
            f"-[{relationship}]->"
            f"{record['destination_label']}:{record['destination']}"
        )
        if edge_id in seen_edge_ids:
            continue

        # Reserve room for this edge's endpoints before committing.
        provisional = 0
        for label_key, name_key in (
            ("source_label", "source"),
            ("destination_label", "destination"),
        ):
            node_id = f"{record[label_key]}:{record[name_key]}"
            if node_id not in nodes_by_id:
                provisional += 1
        if len(nodes_by_id) + provisional > max_nodes and provisional:
            # Prefer finishing a high-priority path over adding more low-priority nodes.
            if priority >= 1:
                continue
            if len(nodes_by_id) >= max_nodes:
                break

        add_node(
            record.get("source_label"),
            record.get("source"),
            record.get("source_canonical_id"),
            record.get("source_description"),
        )
        add_node(
            record.get("destination_label"),
            record.get("destination"),
            record.get("destination_canonical_id"),
            record.get("destination_description"),
        )
        source_id = f"{record['source_label']}:{record['source']}"
        dest_id = f"{record['destination_label']}:{record['destination']}"
        if source_id not in nodes_by_id or dest_id not in nodes_by_id:
            continue

        seen_edge_ids.add(edge_id)
        # Only priority-0 CONTAINS (on a path that hits the asked-for label) are
        # uncapped; other CONTAINS still compete for the generic budget.
        if relationship == "CONTAINS" and priority >= 1:
            generic_contains += 1
        edges.append(
            {
                "id": edge_id,
                "source": source_id,
                "source_name": record["source"],
                "source_label": record["source_label"],
                "relationship": relationship,
                "destination": dest_id,
                "destination_name": record["destination"],
                "destination_label": record["destination_label"],
                "description": record.get("description") or "",
                "keywords": record.get("keywords") or "",
                "source_files": [],
                "page_ranges": [],
                "chunk_ids": [],
            }
        )
        if len(edges) >= max_edges:
            break

    nodes = list(nodes_by_id.values())[:max_nodes]
    keep_ids = {node["id"] for node in nodes}
    edges = [
        edge
        for edge in edges
        if edge["source"] in keep_ids and edge["destination"] in keep_ids
    ][:max_edges]
    return {"nodes": nodes, "edges": edges}


def fetch_canonical_graph(session, *, node_limit: int = 2000, edge_limit: int = 4000) -> dict[str, Any]:
    """Return the global canonical UL graph, omitting Document/Evidence nodes."""
    stats = _first_record(
        session.run(
            """
            MATCH (n)
            WHERE n.canonical_id IS NOT NULL
              AND NOT n:Document AND NOT n:Evidence AND NOT n:KnowledgeGraph
            WITH count(n) AS node_count
            OPTIONAL MATCH (a)-[r]->(b)
            WHERE a.canonical_id IS NOT NULL
              AND b.canonical_id IS NOT NULL
              AND NOT a:Document AND NOT a:Evidence AND NOT a:KnowledgeGraph
              AND NOT b:Document AND NOT b:Evidence AND NOT b:KnowledgeGraph
              AND type(r) <> 'PROVIDES_EVIDENCE'
              AND type(r) <> 'ABOUT'
            RETURN node_count, count(r) AS relationship_count
            """
        )
    )
    nodes_result = session.run(
        """
        MATCH (n)
        WHERE n.canonical_id IS NOT NULL
          AND n.name IS NOT NULL
          AND NOT n:Document AND NOT n:Evidence AND NOT n:KnowledgeGraph
        RETURN
          labels(n)[0] AS label,
          n.name AS name,
          n.canonical_id AS canonical_id,
          coalesce(n.description, '') AS description,
          properties(n) AS properties,
          elementId(n) AS element_id
        ORDER BY label, name
        LIMIT $limit
        """,
        limit=node_limit,
    )
    nodes = [
        _node_payload(record)
        for record in _records(nodes_result)
        if record.get("label") and record.get("name") and not is_provenance_label(record.get("label"))
    ]

    edges_result = session.run(
        """
        MATCH (s)-[r]->(t)
        WHERE s.canonical_id IS NOT NULL
          AND t.canonical_id IS NOT NULL
          AND s.name IS NOT NULL
          AND t.name IS NOT NULL
          AND NOT s:Document AND NOT s:Evidence AND NOT s:KnowledgeGraph
          AND NOT t:Document AND NOT t:Evidence AND NOT t:KnowledgeGraph
          AND type(r) <> 'PROVIDES_EVIDENCE'
          AND type(r) <> 'ABOUT'
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
        LIMIT $limit
        """,
        limit=edge_limit,
    )
    edges = []
    for record in _records(edges_result):
        if not record.get("source_label") or not record.get("destination_label"):
            continue
        props = _properties_payload(record.get("properties") or {})
        description = record.get("description") or props.get("description") or ""
        keywords = record.get("keywords") or props.get("keywords") or ""
        if description and "description" not in props:
            props["description"] = description
        if keywords and "keywords" not in props:
            props["keywords"] = keywords
        element_id = str(record.get("element_id") or "").strip()
        if element_id:
            props.setdefault("<id>", element_id)
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
                "description": description or "",
                "keywords": keywords or "",
                "source_files": _as_str_list(props.get("source_files")),
                "page_ranges": [],
                "chunk_ids": [],
                "properties": props,
            }
        )

    node_count = len(nodes)
    relationship_count = len(edges)
    if stats is not None:
        try:
            node_count = int(stats["node_count"] or 0)
            relationship_count = int(stats["relationship_count"] or 0)
        except Exception:
            pass
    return {
        "node_count": node_count,
        "relationship_count": relationship_count,
        "nodes": nodes,
        "edges": edges,
    }


def search_entities_by_name(session, name: str, *, limit: int = 8) -> list[dict[str, Any]]:
    result = session.run(
        """
        MATCH (n)
        WHERE n.canonical_id IS NOT NULL
          AND NOT n:Document AND NOT n:Evidence AND NOT n:KnowledgeGraph
          AND (
            toLower(n.name) CONTAINS toLower($name)
            OR toLower(coalesce(n.normalized_name, '')) CONTAINS toLower($name)
            OR any(alias IN coalesce(n.aliases, []) WHERE toLower(alias) CONTAINS toLower($name))
          )
        RETURN labels(n)[0] AS label, n.name AS name, n.canonical_id AS canonical_id,
               coalesce(n.entity_type, toLower(labels(n)[0])) AS entity_type,
               coalesce(n.description, '') AS description
        LIMIT $limit
        """,
        name=name,
        limit=limit,
    )
    found = []
    for record in _records(result):
        found.append(
            {
                "label": record.get("label"),
                "name": record.get("name"),
                "canonical_id": record.get("canonical_id"),
                "entity_type": record.get("entity_type"),
                "description": record.get("description") or "",
            }
        )
    return found


def fetch_lineage_subgraph(
    session,
    seed_names: list[str],
    *,
    question: str | None = None,
    max_nodes: int = 60,
    max_edges: int = 90,
) -> dict[str, Any]:
    """Return a compact Neo4j neighborhood for chat lineage visualization.

    Question-aware: ranks paths that reach intent-relevant labels (Clause,
    Standard, Certification, ...) ahead of generic high-degree edges such as
    Model-[:CONTAINS]->Component, so longer compliance paths are not crowded
    out by node/edge limits.
    """
    cleaned = [str(name or "").strip() for name in seed_names if str(name or "").strip()]
    cleaned = [name for name in cleaned if len(name) >= 3]
    if not cleaned:
        return {"nodes": [], "edges": []}

    # Prefer longer names first so "Galaxy A56R 5G" beats "Galaxy".
    cleaned = sorted(dict.fromkeys(cleaned), key=len, reverse=True)[:12]
    target_labels, preferred_types = resolve_lineage_targets(question)

    seed_result = session.run(
        """
        UNWIND $names AS needle
        MATCH (n)
        WHERE n.canonical_id IS NOT NULL
          AND n.name IS NOT NULL
          AND NOT n:Document AND NOT n:Evidence AND NOT n:KnowledgeGraph
          AND (
            toLower(n.name) = toLower(needle)
            OR (
              size(needle) >= 5
              AND (
                toLower(n.name) CONTAINS toLower(needle)
                OR toLower(needle) CONTAINS toLower(n.name)
                OR toLower(coalesce(n.normalized_name, '')) CONTAINS toLower(needle)
              )
            )
          )
        WITH n, needle,
             CASE
               WHEN toLower(n.name) = toLower(needle) THEN 0
               WHEN n:Product OR n:Model OR n:Standard OR n:Certification OR n:Test OR n:TestPlan THEN 1
               WHEN n:Component OR n:Company OR n:Applicant THEN 2
               ELSE 3
             END AS rank,
             size(n.name) AS namelen
        RETURN DISTINCT
          labels(n)[0] AS label,
          n.name AS name,
          n.canonical_id AS canonical_id,
          coalesce(n.description, '') AS description,
          rank,
          namelen
        ORDER BY rank ASC, namelen DESC
        LIMIT 10
        """,
        names=cleaned,
    )
    seeds = [
        record
        for record in _records(seed_result)
        if record.get("label") and record.get("name") and not is_provenance_label(record.get("label"))
    ]
    if not seeds:
        return {"nodes": [], "edges": []}

    seed_ids = [str(record.get("canonical_id") or "").strip() for record in seeds]
    seed_ids = [cid for cid in seed_ids if cid]
    if not seed_ids:
        return {"nodes": [], "edges": []}

    # Path limit is larger than max_edges: rank first, then assemble a bounded set.
    path_limit = max(max_edges * 2, 120)

    edge_result = session.run(
        """
        MATCH (seed)
        WHERE seed.canonical_id IN $seed_ids
        MATCH path = (seed)-[*1..4]-(nbr)
        WHERE nbr.canonical_id IS NOT NULL
          AND NOT nbr:Document AND NOT nbr:Evidence AND NOT nbr:KnowledgeGraph
          AND ALL(
            rel IN relationships(path)
            WHERE type(rel) IN $allowed_types
          )
          AND ALL(
            node IN nodes(path)
            WHERE node.canonical_id IS NOT NULL
              AND NOT node:Document
              AND NOT node:Evidence
              AND NOT node:KnowledgeGraph
          )
        WITH path,
             CASE
               WHEN size($target_labels) > 0 AND any(
                 n IN nodes(path)
                 WHERE any(lbl IN labels(n) WHERE lbl IN $target_labels)
               ) THEN 0
               WHEN size($preferred_types) > 0 AND any(
                 rel IN relationships(path)
                 WHERE type(rel) IN $preferred_types
               ) THEN 1
               ELSE 2
             END AS priority
        ORDER BY priority ASC, length(path) ASC
        LIMIT $path_limit
        UNWIND relationships(path) AS rel
        RETURN
          priority,
          labels(startNode(rel))[0] AS source_label,
          startNode(rel).name AS source,
          startNode(rel).canonical_id AS source_canonical_id,
          coalesce(startNode(rel).description, '') AS source_description,
          type(rel) AS relationship,
          labels(endNode(rel))[0] AS destination_label,
          endNode(rel).name AS destination,
          endNode(rel).canonical_id AS destination_canonical_id,
          coalesce(endNode(rel).description, '') AS destination_description,
          coalesce(rel.description, '') AS description,
          coalesce(rel.keywords, '') AS keywords
        ORDER BY priority ASC
        """,
        seed_ids=seed_ids,
        allowed_types=_LINEAGE_ALLOWED_TYPES,
        target_labels=target_labels,
        preferred_types=preferred_types,
        path_limit=path_limit,
    )

    return assemble_prioritized_lineage(
        _records(edge_result),
        seed_nodes=seeds,
        max_nodes=max_nodes,
        max_edges=max_edges,
        max_generic_contains=8 if target_labels else 24,
    )


def entity_evidence(
    session,
    canonical_id: str,
    *,
    document_id: str | None = None,
) -> list[dict[str, Any]]:
    del session, canonical_id, document_id
    return []


def documents_for_entity(session, canonical_id: str) -> list[dict[str, Any]]:
    result = session.run(
        """
        MATCH (d:Document)-[:PROVIDES_EVIDENCE]->(e:Evidence)-[:ABOUT]->(n)
        WHERE n.canonical_id = $canonical_id
        RETURN DISTINCT d.document_id AS document_id, d.file_name AS file_name,
               d.source_files AS source_files
        """,
        canonical_id=canonical_id,
    )
    return [
        {
            "document_id": record.get("document_id"),
            "file_name": record.get("file_name") or "",
            "source_files": _as_str_list(record.get("source_files")),
        }
        for record in _records(result)
    ]


def ul_companies(session) -> list[dict[str, Any]]:
    result = session.run(
        """
        MATCH (u:UL {canonical_id: $ul_id})-[r]->(c:Company)
        RETURN u.name AS ul_name, type(r) AS relationship, c.name AS company,
               c.canonical_id AS canonical_id
        """,
        ul_id=UL_CANONICAL_ID,
    )
    return [
        {
            "ul_name": record.get("ul_name"),
            "relationship": record.get("relationship"),
            "company": record.get("company"),
            "canonical_id": record.get("canonical_id"),
        }
        for record in _records(result)
    ]


def count_canonical(session, entity_type: str) -> int:
    label = to_label(entity_type)
    record = _first_record(
        session.run(
            f"MATCH (n:{label}) WHERE n.canonical_id IS NOT NULL RETURN count(n) AS n"
        )
    )
    if record is None:
        return 0
    try:
        return int(record["n"])
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Planned lineage: structured plan -> generated Cypher -> Neo4j -> evidence.
#
# A plan selects one of the approved traversals below; Python builds the query.
# Model output never reaches the query string, so validation is a membership
# check rather than a parser, and the returned paths become the lineage graph
# verbatim -- no traversal, ranking, or expansion happens afterwards.
# ---------------------------------------------------------------------------

# Relationship type -> (start label, end label). Mirrors the live database.
LINEAGE_SCHEMA: dict[str, tuple[str, str]] = {
    "HAS_PRODUCT": ("Company", "Product"),
    "HAS_MODEL": ("Product", "Model"),
    "CONTAINS": ("Model", "Component"),
    "COMPLIES_WITH": ("Component", "Standard"),
    "HAS_CERTIFICATION": ("Component", "Certification"),
    "PARTY_ROLE_MANUFACTURER": ("Component", "Company"),
    "PARTY_ROLE_SUPPLIER": ("Company", "Company"),
    "PARTY_ROLE_APPLICANT": ("UL", "Company"),
    "HAS_FILE_NUMBER": ("Certification", "FileNumber"),
    "HAS_VOLUME": ("Certification", "Volume"),
    "HAS_TEST_PLAN": ("Certification", "TestPlan"),
    "HAS_LOCATION": ("Company", "Location"),
}

KNOWN_LABELS: frozenset[str] = frozenset(
    label for pair in LINEAGE_SCHEMA.values() for label in pair
)

_MAX_LINEAGE_PATHS = 120
DEFAULT_LINEAGE_PATH = "components"


class LineagePlanError(ValueError):
    """Raised when a plan references anything outside the approved schema."""


@dataclass(frozen=True)
class LineageHop:
    """One directed step. ``outgoing`` False means the arrow points back."""

    relationship: str
    outgoing: bool

    def target_label(self) -> str:
        start, end = LINEAGE_SCHEMA[self.relationship]
        return end if self.outgoing else start


@dataclass(frozen=True)
class LineagePathSpec:
    root_label: str
    hops: tuple[LineageHop, ...]
    description: str


def _out(relationship: str) -> LineageHop:
    return LineageHop(relationship, True)


def _in(relationship: str) -> LineageHop:
    return LineageHop(relationship, False)


# Every hop carries an explicit direction. PARTY_ROLE_APPLICANT appears only in
# the UL-rooted path, so Product -> Company -> UL -> Company -> Product cannot
# be reached from any product question.
LINEAGE_PATHS: dict[str, LineagePathSpec] = {
    "models": LineagePathSpec(
        "Product",
        (_out("HAS_MODEL"),),
        "Models of a product.",
    ),
    "components": LineagePathSpec(
        "Product",
        (_out("HAS_MODEL"), _out("CONTAINS")),
        "Parts, hardware and components inside a product's models.",
    ),
    "standards": LineagePathSpec(
        "Product",
        (_out("HAS_MODEL"), _out("CONTAINS"), _out("COMPLIES_WITH")),
        "Standards that a product's components comply with.",
    ),
    "certifications": LineagePathSpec(
        "Product",
        (_out("HAS_MODEL"), _out("CONTAINS"), _out("HAS_CERTIFICATION")),
        "Certifications held by a product's components.",
    ),
    "certification_files": LineagePathSpec(
        "Product",
        (
            _out("HAS_MODEL"),
            _out("CONTAINS"),
            _out("HAS_CERTIFICATION"),
            _out("HAS_FILE_NUMBER"),
        ),
        "File numbers behind a product's certifications.",
    ),
    "component_manufacturers": LineagePathSpec(
        "Product",
        (_out("HAS_MODEL"), _out("CONTAINS"), _out("PARTY_ROLE_MANUFACTURER")),
        "Companies that manufacture a product's components.",
    ),
    "owner_company": LineagePathSpec(
        "Product",
        (_in("HAS_PRODUCT"),),
        "The company that owns a product.",
    ),
    "company_products": LineagePathSpec(
        "Company",
        (_out("HAS_PRODUCT"),),
        "Products belonging to a company.",
    ),
    "company_suppliers": LineagePathSpec(
        "Company",
        (_out("PARTY_ROLE_SUPPLIER"),),
        "Suppliers of a company.",
    ),
    "company_locations": LineagePathSpec(
        "Company",
        (_out("HAS_LOCATION"),),
        "Locations of a company.",
    ),
    "standard_components": LineagePathSpec(
        "Standard",
        (_in("COMPLIES_WITH"),),
        "Components that comply with a standard.",
    ),
    "ul_applicants": LineagePathSpec(
        "UL",
        (_out("PARTY_ROLE_APPLICANT"),),
        "Companies for which UL is the applicant. Organizational questions only.",
    ),
}


@dataclass(frozen=True)
class LineagePlan:
    entity_name: str
    entity_label: str
    path: str
    terminal_contains: str | None = None
    limit: int = _MAX_LINEAGE_PATHS


def validate_lineage_plan(plan: LineagePlan) -> LineagePathSpec:
    """Membership-check a plan. Nothing else may reach the Cypher builder."""
    spec = LINEAGE_PATHS.get(plan.path)
    if spec is None:
        raise LineagePlanError(f"Unknown lineage path: {plan.path!r}")
    if plan.entity_label not in KNOWN_LABELS:
        raise LineagePlanError(f"Unknown label: {plan.entity_label!r}")
    if plan.entity_label != spec.root_label:
        raise LineagePlanError(
            f"Path {plan.path!r} starts at {spec.root_label}, got {plan.entity_label!r}"
        )
    if not str(plan.entity_name or "").strip():
        raise LineagePlanError("entity_name must not be empty")
    if not 1 <= plan.limit <= 500:
        raise LineagePlanError(f"limit out of range: {plan.limit}")
    return spec


def _format_path_pattern(spec: LineagePathSpec) -> str:
    """Render the hop chain across indented lines, one hop per line.

    Cypher ignores the newlines; they exist so the query stays legible in
    Neo4j Browser when a path has three or four hops.
    """
    segments: list[str] = []
    for index, hop in enumerate(spec.hops):
        arrow = (
            f"-[:{hop.relationship}]->" if hop.outgoing else f"<-[:{hop.relationship}]-"
        )
        segments.append(f"{arrow}(n{index}:{hop.target_label()})")
    if not segments:
        return "    (root)"
    lines = [f"    (root){segments[0]}"]
    lines.extend(f"        {segment}" for segment in segments[1:])
    return "\n".join(lines)


def build_lineage_cypher(plan: LineagePlan) -> tuple[str, dict[str, Any]]:
    """Build read-only, parameterized Cypher with explicit hop directions.

    Labels and relationship types are interpolated only after they have been
    validated as keys of ``LINEAGE_SCHEMA`` / ``LINEAGE_PATHS``; the sole
    user-derived value travels as a query parameter.
    """
    spec = validate_lineage_plan(plan)

    parameters: dict[str, Any] = {
        "entity_name": plan.entity_name.strip(),
        "limit": plan.limit,
    }

    match_block = [
        "MATCH path =",
        _format_path_pattern(spec),
    ]
    if plan.terminal_contains and plan.terminal_contains.strip():
        terminal_var = f"n{len(spec.hops) - 1}"
        match_block.append(
            f"WHERE toLower({terminal_var}.name) CONTAINS toLower($terminal_contains)"
        )
        parameters["terminal_contains"] = plan.terminal_contains.strip()

    # Exactness is ranked after the path match so that a dead-end exact node
    # (e.g. "iPhone", which has no HAS_MODEL) cannot shadow "iPhone 16".
    blocks = [
        "\n".join(
            [
                f"MATCH (root:{spec.root_label})",
                "WHERE toLower(root.name) = toLower($entity_name)",
                "   OR toLower(root.name) CONTAINS toLower($entity_name)",
            ]
        ),
        "\n".join(match_block),
        "\n".join(
            [
                "WITH path,",
                "     CASE",
                "         WHEN toLower(root.name) = toLower($entity_name) THEN 0",
                "         ELSE 1",
                "     END AS rank",
            ]
        ),
        "\n".join(
            [
                "WITH min(rank) AS best_rank,",
                "     collect({ path: path, rank: rank }) AS candidates",
            ]
        ),
        "UNWIND [candidate IN candidates WHERE candidate.rank = best_rank] AS candidate",
        "\n".join(
            [
                "RETURN candidate.path AS path",
                "LIMIT $limit",
            ]
        ),
    ]
    return "\n\n".join(blocks), parameters


def execute_lineage_cypher(
    driver, cypher: str, parameters: dict[str, Any]
) -> list[Any]:
    """Run the query in a read transaction; Neo4j rejects writes server-side."""
    with driver.session() as session:
        return session.execute_read(lambda tx: list(tx.run(cypher, parameters)))


def _path_node_label(node: Any) -> str:
    labels = sorted(node.labels)
    return labels[0] if labels else "Entity"


def paths_to_graph_evidence(records: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """Convert Neo4j path objects straight into a ``graph_evidence`` payload.

    A mechanical walk of the returned paths: whatever Neo4j returned is exactly
    what the UI renders. Emits the same keys as ``_build_graph_evidence`` so the
    frontend contract is unchanged.
    """
    nodes: dict[str, dict[str, Any]] = {}
    relationships: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_node(node: Any) -> str:
        label = _path_node_label(node)
        name = str(node.get("name") or "").strip()
        if not name:
            return ""
        node_id = f"{label}:{name}"
        if node_id not in nodes:
            nodes[node_id] = {
                "id": node_id,
                "name": name,
                "type": label,
                "description": str(node.get("description") or "").strip(),
            }
        return node_id

    for record in records:
        path = record.get("path")
        if path is None:
            continue
        for node in path.nodes:
            add_node(node)
        for rel in path.relationships:
            source_id = add_node(rel.start_node)
            target_id = add_node(rel.end_node)
            if not source_id or not target_id:
                continue
            key = (source_id, rel.type, target_id)
            if key in relationships:
                continue
            relationships[key] = {
                "source": source_id,
                "source_name": str(rel.start_node.get("name") or "").strip(),
                "source_type": _path_node_label(rel.start_node),
                "relationship": rel.type,
                "target": target_id,
                "target_name": str(rel.end_node.get("name") or "").strip(),
                "target_type": _path_node_label(rel.end_node),
                "description": str(rel.get("description") or "").strip(),
                "keywords": str(rel.get("keywords") or rel.type).strip(),
            }

    return {
        "nodes": list(nodes.values()),
        "relationships": list(relationships.values()),
    }


# ---------------------------------------------------------------------------
# Planner: the LLM chooses an approved path, never writes Cypher.
# ---------------------------------------------------------------------------

_PLANNER_SYSTEM_PROMPT = """You select a graph traversal plan for a compliance knowledge graph.

You do NOT write Cypher. Respond with ONLY a JSON object of exactly this shape:

{"entity_name": "...", "entity_label": "...", "path": "..."}

Rules:
- "path" MUST be copied exactly from the approved path names provided.
- "entity_label" MUST be the required root label of the chosen path.
- "entity_name" is the specific entity the question is about, copied verbatim
  from the question (for example "iPhone 16"). Do not add words such as
  "product", "device", or model numbers that the question did not mention.
- Never invent labels, relationship types, or path names.
- Choose the path that matches what the question asks for:
  parts/hardware/specifications/capabilities of a product -> "components";
  standards/regulations a product must meet -> "standards";
  certifications/approvals -> "certifications";
  who makes/supplies a part -> "component_manufacturers";
  which company owns a product -> "owner_company".
- When the question is about a product but no other path clearly fits, use
  "components".
"""


def _approved_paths_text() -> str:
    return "\n".join(
        f'- "{name}" (root label: {spec.root_label}): {spec.description}'
        for name, spec in LINEAGE_PATHS.items()
    )


def _approved_labels_text() -> str:
    return ", ".join(sorted(KNOWN_LABELS))


def _parse_plan_json(content: str) -> dict[str, Any]:
    """Tolerate code fences and surrounding chatter; require a JSON object."""
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise LineagePlanError(f"Planner did not return valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LineagePlanError("Planner response is not a JSON object")
    return data


def plan_lineage_query(question: str) -> LineagePlan:
    """Ask the LLM to choose one approved traversal for ``question``.

    The model only selects from ``LINEAGE_PATHS``; the result is validated
    before it can reach :func:`build_lineage_cypher`. Raises
    :class:`LineagePlanError` for anything off-schema or unparseable.
    """
    cleaned = str(question or "").strip()
    if not cleaned:
        raise LineagePlanError("question must not be empty")

    import config
    from ul.llm_client import chat_json

    user_prompt = (
        f"Approved paths:\n{_approved_paths_text()}\n\n"
        f"Approved labels: {_approved_labels_text()}\n\n"
        f"Question: {cleaned}\n\n"
        "Return only the JSON object."
    )
    try:
        raw = chat_json(
            _PLANNER_SYSTEM_PROMPT,
            user_prompt,
            model=config.mapping_chat_model(),
        )
    except LineagePlanError:
        raise
    except Exception as exc:
        raise LineagePlanError(f"Planner call failed: {exc}") from exc

    data = _parse_plan_json(raw)
    plan = LineagePlan(
        entity_name=str(data.get("entity_name") or "").strip(),
        entity_label=str(data.get("entity_label") or "").strip(),
        path=str(data.get("path") or "").strip(),
    )
    validate_lineage_plan(plan)
    logger.info(
        "Lineage plan: entity=%r label=%s path=%s",
        plan.entity_name,
        plan.entity_label,
        plan.path,
    )
    return plan
