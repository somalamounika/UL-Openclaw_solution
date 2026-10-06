# UL Solutions Knowledge Graph schema

The Neo4j store is **one global canonical graph**. Documents add knowledge incrementally.
Canonical entities represent *what* is known. `Document` records the upload; Evidence
nodes are not stored.

```text
PDF → extraction → chunks → entities/relationships
    → normalize → resolve against existing canonical nodes
    → MERGE entities → MERGE relationships
```

Extraction (LightRAG prompts, hierarchy, CSV) is unchanged. Only Neo4j identity and
provenance changed.

---

## Node types

| Label | Identity property | Role |
|---|---|---|
| `UL` | `canonical_id` (`ul_solutions`) | Single UL Solutions root |
| `Company` | `canonical_id` | Applicants, manufacturers, suppliers |
| `Product` | `canonical_id` | Product (company-qualified when known) |
| `Model` | `canonical_id` | Product model |
| `Component` | `canonical_id` | Shared across products when they are the same part |
| `Standard` | `canonical_id` | IEC / UL / ISO identifiers |
| `Clause` | `canonical_id` | Clause **plus parent standard** |
| `Certification` | `canonical_id` | Certification / certificate |
| `Test` | `canonical_id` | Test |
| `Requirement` | `canonical_id` | Requirement (when extracted) |
| `Document` | `document_id` | Source file / ingestion |
| `KnowledgeGraph` | `graph_id` (`ul_global`) | API metadata only; not entity identity |

Applicant records from extraction are stored as `Company`. Visualization omits
`Document` and `KnowledgeGraph`.

### Important properties

**Canonical entities**

| Property | Meaning |
|---|---|
| `canonical_id` | Stable identity. Never includes document/graph/ingestion ids |
| `name` | Display name (set on create, not overwritten) |
| `normalized_name` | Conservative normalized form used for matching |
| `aliases` | Other observed surface forms |
| `code` | Standard / certification code when known |
| `description` | First non-empty entity/relationship evidence (set once; not overwritten) |

Page numbers and chunk ids are **not** canonical properties.

**Document**

| Property | Meaning |
|---|---|
| `document_id` | Upload/process id (provenance) |
| `ingestion_id` | This process run |
| `content_hash` | Duplicate-ingestion detector |
| `file_name` / `source_files` | Original files |
| `uploaded_at` | Timestamp |

`graph_id` on a process response is `ul_global` (the living graph). It is **not**
part of node identity. Legacy pre-migration graphs may still have `graph_id` on nodes.

---

## Relationships

Existing extraction relationship names are kept.

| Source | Relationship | Target | Canonical? | Provenance |
|---|---|---|---|---|
| UL | `PARTY_ROLE_APPLICANT` | Company | Yes — one edge | Evidence on endpoints |
| Company | `HAS_PRODUCT` | Product | Yes | — |
| Product | `HAS_MODEL` | Model | Yes | — |
| Model | `CONTAINS` | Component | Yes | — |
| Component | `COMPLIES_WITH` | Standard | Yes | — |
| Component | `HAS_CERTIFICATION` | Certification | Yes | — |
| Standard | `HAS_CERTIFICATION` | Certification | Yes | — |
| Component | `PARTY_ROLE_MANUFACTURER` | Company | Yes | — |
| Company | `PARTY_ROLE_SUPPLIER` | Applicant (Company) | Yes | — |
| Company | `HAS_LOCATION` | Location | Yes | — |
| Standard | `HAS_CLAUSE` | Clause | Yes | — |
| Certification | `REQUIRES_TEST` | Test | Yes | — |
| Certification | `HAS_TEST_PLAN` | TestPlan | Yes | — |
| Certification | `HAS_FILE_NUMBER` | FileNumber | Yes | — |
| Certification | `HAS_VOLUME` | Volume | Yes | — |
| Certification | `HAS_DELIVERABLE` | Deliverable | Yes | — |
| Location | `HAS_ADDRESS` | Address | Yes | — |

Canonical relationships are `MERGE`d on the two endpoint `canonical_id`s plus type.
They are not duplicated per document.

The spec example `HAS_COMPANY` is `PARTY_ROLE_APPLICANT` in this schema.

---

## Constraints

Uniqueness is on canonical identity, never on `name + graph_id`.

```cypher
CREATE CONSTRAINT ul_canonical_id_unique IF NOT EXISTS
FOR (n:UL) REQUIRE n.canonical_id IS UNIQUE;
```

The same pattern is applied to Company, Product, Model, Component, Standard, Clause,
Certification, Test, Requirement, plus `Document.document_id`.

---

## Representative queries

### Complete UL graph (visualization)

```cypher
MATCH (n)
WHERE n.canonical_id IS NOT NULL
  AND NOT n:Document AND NOT n:KnowledgeGraph
OPTIONAL MATCH (n)-[r]->(m)
WHERE m.canonical_id IS NOT NULL
  AND NOT m:Document AND NOT m:KnowledgeGraph
RETURN n, r, m
```

`GET /api/ul/graph` runs this shape (capped) and does **not** filter by document.

### UL → Company

```cypher
MATCH (u:UL {canonical_id: 'ul_solutions'})-[:PARTY_ROLE_APPLICANT]->(c:Company)
RETURN u, c
```

### Company → Product

```cypher
MATCH (c:Company)-[:HAS_PRODUCT]->(p:Product)
WHERE c.canonical_id = $canonical_id
RETURN c, p
```

### Product → Component (via Model)

```cypher
MATCH (p:Product)-[:HAS_MODEL]->(m:Model)-[:CONTAINS]->(comp:Component)
WHERE p.canonical_id = $canonical_id
RETURN p, m, comp
```

### Component → Standard

```cypher
MATCH (comp:Component)-[:COMPLIES_WITH]->(s:Standard)
WHERE comp.canonical_id = $canonical_id
RETURN comp, s
```

---

## Chatbot retrieval

- **Global:** canonical Neo4j entities plus LightRAG mix retrieval.
- **Document-specific:** pass `document_id` (or select a document in the UI). LightRAG
  chunks are restricted to that document.

---

## Migration of legacy `graph_id` graphs

Old ingest keyed every node on `name + graph_id`. New ingest does not convert those
automatically. After backing up Neo4j:

```bash
cd backend
python -m ul.knowledge_graph.migration
```

The script assigns `canonical_id`s, rewires relationships, then deletes only confirmed
duplicate nodes.
