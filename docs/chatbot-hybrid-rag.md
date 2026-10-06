# UL Knowledge Assistant — Hybrid RAG

This document explains how the knowledge graph is built, how it is stored in Neo4j, and how the chatbot answers questions over that graph plus the source PDFs.

---

## 1. LightRAG extraction, then Neo4j

The pipeline uses **LightRAG only for extraction prompts**. It does **not** use LightRAG’s own vector store, graph store, or embedding pipeline.

After extraction, the graph is written to **local Neo4j**. That Neo4j graph is ordinary labeled nodes and relationships: names, descriptions, and edge types. **It is not vector-embedded, and this project does not create a Neo4j vector index or full-text index for it.**

```text
Uploaded PDFs + backend reference PDFs
        │
        ▼
Page extract  →  LLM semantic chunks
        │
        ▼
LightRAG prompts  (lightrag.prompt.PROMPTS)
  one LLM call per chunk
  → entities + relationships
        │
        ▼
Normalize
  bucket by type, dedupe, hierarchy,
  product/model identity keys, triplets CSV
        │
        ▼
Neo4j  ingest_triplets()
  MERGE nodes + relationships
  properties: name, description, graph_id
  (Product/Model also identity_key)
  NO embeddings on graph nodes
```

Code path:

| Step | File |
|---|---|
| Orchestration | `backend/ul/ul_service.py` → `process_documents()` |
| LightRAG prompts per chunk | `backend/ul/lightrag_extractor.py` → `extract_chunk_with_lightrag()` |
| Merge all chunks | `extract_graph_from_chunks()` |
| Hierarchy / identity | `backend/ul/hierarchy.py`, `backend/ul/product_normalization.py` |
| Write graph | `backend/ul/neo4j_service.py` → `ingest_triplets()` |

---

### 1.1 What LightRAG is doing here

`extract_chunk_with_lightrag()` takes one chunk of document text and calls the chat model with LightRAG’s JSON extraction prompts:

- `entity_extraction_json_system_prompt`
- `entity_extraction_json_user_prompt`

The model returns JSON roughly like:

```json
{
  "entities": [
    { "name": "SmartHub X1", "type": "product", "description": "..." },
    { "name": "PSU-410", "type": "component", "description": "..." }
  ],
  "relationships": [
    {
      "source": "SmartHub X1",
      "target": "PSU-410",
      "keywords": "has_component",
      "description": "The product contains PSU-410."
    }
  ]
}
```

Entity types are mapped onto the UL set:

`company`, `product`, `model`, `component`, `standard`, `clause`, `certification`, `test`

Relationship keywords are mapped onto hierarchy names such as `produces`, `has_model`, `has_component`, `subject_to`, `contains`, `requires_certification`, `uses_test`.

Chunks are extracted in parallel (`EXTRACTION_CONCURRENCY`), then merged:

1. Bucket entities by type
2. Dedupe companies / models / components / standards
3. Optionally keep only a target product neighborhood
4. Fill missing hierarchy hops (for example Company → Product → Model → Component → Standard)
5. Convert relationships into **triplets** for Neo4j

A triplet is:

```text
source_node + source_type  --relationship-->  destination_node + destination_type
+ description (evidence text)
+ keywords
+ identity_key for Product and Model
```

That is the only input Neo4j receives. LightRAG is finished at this point.

---

### 1.2 How the graph is stored in Neo4j

There is **one global UL Knowledge Graph**. New documents are merged into existing
canonical nodes. Identity is `canonical_id`, not `name + graph_id`. Source-specific
descriptions live on `Evidence` nodes.

Full schema, constraints, and Cypher: [ul-knowledge-graph.md](./ul-knowledge-graph.md).

The API still returns `graph_id`; new ingestions use the stable value `ul_global`.
Legacy isolated `kg_…` graphs remain readable until you run
`python -m ul.knowledge_graph.migration`.

#### Legacy metadata node (pre-incremental ingest)

One node recorded an isolated run:

```cypher
MERGE (g:KnowledgeGraph {graph_id: $graph_id})
SET g.source_files = $source_files,
    g.created_at   = $created_at,
    g.document_id  = $document_id
```

| Property | Meaning |
|---|---|
| `graph_id` | Isolates this graph from others |
| `source_files` | Uploaded file names |
| `created_at` | ISO timestamp |
| `document_id` | Upload/process id used later to find PDF chunks |

#### Entity nodes

Each triplet endpoint is merged as a labeled node. Type `company` becomes label `Company`, `has_component` becomes relationship type `HAS_COMPONENT`, and so on.

**Merge key**

| Node type | Unique key inside one `graph_id` |
|---|---|
| Product, Model | `identity_key` + `graph_id` |
| Everything else (Company, Component, Standard, Clause, Certification, Test, …) | `name` + `graph_id` |

**Properties actually stored on the node**

| Property | Stored? | Notes |
|---|---|---|
| `name` | Yes | Display name |
| `description` | Yes | LightRAG entity description / evidence |
| `graph_id` | Yes | Scopes the node to this run |
| `identity_key` | Product and Model only | Canonical id such as `product:smarthub x1` |
| embedding / vector | **No** | Never written |
| chunk_id / page / source_file | **No** | Provenance stays on the triplet/CSV side, not as Neo4j node fields |

Example after ingest:

```cypher
(:KnowledgeGraph {
    graph_id: "kg_a1b2c3d4",
    document_id: "...",
    source_files: ["product.pdf"],
    created_at: "2026-08-17T10:00:00+00:00"
})

(:Product {
    identity_key: "product:smarthub x1",
    name: "SmartHub X1",
    description: "Wi-Fi hub ...",
    graph_id: "kg_a1b2c3d4"
})

(:Component {
    name: "PSU-410",
    description: "40W power supply ...",
    graph_id: "kg_a1b2c3d4"
})
```

The Cypher that creates each pair is:

```cypher
MERGE (s:Product {identity_key: $source_merge_value, graph_id: $graph_id})
SET s.name = $source_name,
    s.description = $source_description
MERGE (d:Component {name: $dest_merge_value, graph_id: $graph_id})
SET d.name = $dest_name,
    d.description = $dest_description
MERGE (s)-[r:HAS_COMPONENT {graph_id: $graph_id}]->(d)
SET r.description = $rel_description,
    r.keywords = $rel_keywords
```

`MERGE` means: if that node/edge already exists in this `graph_id`, reuse it; otherwise create it. Repeating the same product across many triplets does not create duplicate Product nodes.

#### Relationships

Stored as typed edges, not as embedded text.

| Property | Stored? |
|---|---|
| relationship type (`PRODUCES`, `HAS_MODEL`, `HAS_COMPONENT`, `SUBJECT_TO`, …) | Yes (Neo4j type) |
| `graph_id` | Yes |
| `description` | Yes — evidence sentence from LightRAG |
| `keywords` | Yes — original extraction keywords |
| embedding | **No** |

```cypher
(:Product)-[:HAS_COMPONENT {
    graph_id: "kg_a1b2c3d4",
    description: "The product contains PSU-410.",
    keywords: "has_component"
}]->(:Component)
```

Typical hierarchy in the store:

```text
UL Solutions  -[:SERVES]->  Company
Company       -[:PRODUCES]->  Product
Product       -[:HAS_MODEL]->  Model
Product/Model -[:HAS_COMPONENT]->  Component
Component     -[:SUBJECT_TO]->  Standard
Standard      -[:CONTAINS]->  Clause
Standard      -[:REQUIRES_CERTIFICATION]->  Certification
Certification -[:USES_TEST]->  Test
```

---

### 1.3 Is the Neo4j graph embedded? Is it indexed?

**Embedded: no.**

Neo4j nodes and relationships are plain strings. There is no `embedding`, `content_vector`, or similar property on graph data. The chatbot does not embed node names before searching Neo4j.

**Application indexes: none created.**

`neo4j_service.py` never runs `CREATE INDEX`, `CREATE CONSTRAINT`, or Neo4j vector-index commands. There is:

- no vector index on graph nodes
- no full-text index on `name` / `description`
- no uniqueness constraint beyond what `MERGE` does in Cypher

Neo4j still has its own internal store indexes (element ids, labels). That is storage plumbing, not a semantic search index this app configured.

**How the chatbot finds graph nodes then**

It is **keyword Cypher**, not vector search:

```cypher
MATCH (n)
WHERE n.graph_id = $graph_id
  AND n.name IS NOT NULL
  AND (
    any(term IN $terms WHERE
      toLower(n.name) CONTAINS term OR
      toLower(coalesce(n.description, '')) CONTAINS term
    )
    OR any(label IN $labels WHERE
      any(node_label IN labels(n) WHERE toLower(node_label) = label)
    )
  )
```

Then the top matches are used as seeds, and Neo4j walks **2 hops** (3 if the question is about relationships). That walk is the “index” in practice: the graph structure itself.

| Question | What is stored | How it is found |
|---|---|---|
| Graph (Neo4j) | Labels + `name` + `description` + typed edges | `CONTAINS` on text, then hop traversal |
| PDF chunks | Text, and optionally a vector | See section 3 — Azure AI Search or in-memory words |

PDF chunk embeddings are a **separate** store. They are not written into Neo4j.

---

## 2. The chatbot is hybrid RAG on top of that graph

After the graph exists in Neo4j, chat retrieves from **two stores independently**, then asks the LLM to answer only from that context.

| Store | What it holds | Embedded? | How it is searched |
|---|---|---|---|
| **Neo4j** | LightRAG entities and relationships | No | Keyword `CONTAINS` + 2–3 hop walk |
| **PDF chunk index** | Semantic chunks from the same PDFs | Yes, only if Azure AI Search is configured | Vector + keyword hybrid, or word-overlap fallback |

```text
User question
      │
      ▼
POST /api/ul/chat  { question, graph_id, document_id }
      │
      ├── Graph retrieval   Neo4j keyword + hops   (no embeddings)
      ├── Document retrieval  PDF chunks           (vectors only if Azure Search)
      ├── Merge prompt        GRAPH CONTEXT + DOCUMENT EVIDENCE
      ├── Chat model          grounded draft
      └── Formatter           clean Markdown
                │
                ▼
         answer + PDF sources + graph_evidence (subgraph for the canvas)
```

The model never browses the full graph or the full PDFs. It only sees what retrieval returned.

---

## 3. Ingest vs chat

Embeddings, when used, are created at **ingest** for PDF chunks. At chat time the **question** may be embedded so it can be compared to those chunk vectors. Graph nodes are never part of that.

```text
INGEST  (/api/ul/process)                     CHAT  (/api/ul/chat)
─────────────────────────                     ───────────────────
PDF pages                                     User question
  → LLM semantic chunks                         → keyword parse
  → store chunk text (_INDEX)                   → Neo4j keyword + hop search
  → embed chunks (if Azure Search)              → embed question (if Azure Search)
  → LightRAG extract entities                   → hybrid / lexical chunk search
  → MERGE graph into Neo4j                      → LLM answer from retrieved context
```

---

## 4. Document store (the other half of hybrid RAG)

Triggered during the same `/api/ul/process` run, before LightRAG.

### Page extraction and chunking

Uploaded company/product files are split into pages, then `create_semantic_chunks()` asks the LLM to split them into topic chunks (~8000 tokens). Every supported file in `backend/documents/` is included automatically for both graph extraction (standards, certifications, tests) and chatbot retrieval.

```json
{
  "chunk_id": "chunk_000012",
  "source_file": "product.pdf",
  "page_range": { "start": 4, "end": 5 },
  "text": "..."
}
```

### Where chunks are stored

`upsert_chunks()` always writes **text** into an in-process cache:

```text
_INDEX: { document_id → [chunk, chunk, ...] }
```

That cache is not a vector database and is lost when the backend restarts.

**If Azure AI Search is configured**, each chunk is also embedded (`text-embedding-3-small`, 1536 dimensions) and upserted to index `ul-chunks`:

| Field | Content |
|---|---|
| `id`, `chunk_id`, `document_id`, `source_file` | Identity |
| `page_start`, `page_end`, `content` | Text + pages |
| `content_vector` | Embedding |

**If Azure Search is not configured**, no chunk embeddings are created.

---

## 5. Chat flow in detail

Frontend: `frontend/src/components/chat/MockChatbotPanel.tsx` → `frontend/src/api/chat.ts`  
Backend: `backend/ul/chatbot.py` → `answer_question()`

```http
POST /api/ul/chat
{
  "question": "What components does SmartHub X1 contain?",
  "graph_id": "kg_a1b2c3d4",
  "document_id": "..."
}
```

`graph_id` / `document_id` scope retrieval to the graph on screen. If omitted, the latest `KnowledgeGraph` node is used.

### Step A — Understand the question (no LLM)

`understand_query()` pulls intents, entity types, keywords, and capitalized/quoted names. Example:

```text
Question:  What components does SmartHub X1 contain?

intents:          component, relationships
entity_types:     component
keywords:         SmartHub X1, components, contain
likely_entities:  SmartHub X1
```

### Step B — Graph retrieval (Neo4j)

`_retrieve_graph_context()` is read-only.

1. Resolve `graph_id`.
2. Keyword/label Cypher (see 1.3), cap 80 candidates.
3. Score in Python (exact name highest). Keep top **8 seeds**.
4. Walk **2 hops** (3 for relationship questions), same `graph_id`, cap 60 edges.
5. Return entity + relationship records.

| Match | Score |
|---|---|
| Node name equals a likely entity | +30 |
| Node name contains a likely entity | +15 |
| Node name equals a keyword | +12 |
| Node name contains a keyword | +7 |
| Description contains a keyword | +2 |
| Node label is a requested entity type | +3 |

### Step C — Document retrieval (PDF chunks)

`_retrieve_document_context()` → `search_relevant_chunks()`

Searches the graph’s `document_id` plus `ref-standards` and `ref-certifications_tests`. Up to **8 chunks** total.

**Azure Search on:** embed the question, then hybrid search (`search_text` + k-NN on `content_vector`), filtered by `document_id`.

**Azure Search off:** rank `_INDEX` by word overlap:

```text
score = |question_words ∩ chunk_words| / |question_words|
```

### Step D — Merge context

```text
GRAPH CONTEXT:
[G1] Product: SmartHub X1 | Description: ...
[G2] Product: SmartHub X1 -[HAS_COMPONENT]-> Component: PSU-410 | Evidence: ...

DOCUMENT EVIDENCE:
[D1] Source: product.pdf; pages: 4-5; chunk: chunk_000012
<chunk text>
```

Rules: prefer graph edges for structure; prefer PDF text for requirements/clauses; do not invent entities; keep Product, Model, and Component distinct.

### Step E — Generate, then format

1. `_generate_answer()` — grounded draft
2. `format_final_answer()` — presentation only; no new facts
3. Strip `[G1]`, chunk IDs, scores, “based on the retrieved context”

### Step F — Response

```json
{
  "answer": "SmartHub X1 contains ...",
  "sources": [{ "source_file": "product.pdf", "page_range": { "start": 4, "end": 5 } }],
  "graph_context": [],
  "graph_evidence": { "nodes": [], "relationships": [] }
}
```

`graph_evidence` is the retrieved subgraph drawn on the canvas. It is not a second Neo4j query.

---

## 6. What “hybrid” means

### Graph + documents

| Question type | Typical source |
|---|---|
| “What components does product X contain?” | Neo4j relationships |
| “How is PSU-410 related to UL 62368-1?” | Neo4j relationships |
| “What does clause 4.2 require?” | PDF chunks |
| “Is this product certified, and which test applies?” | Both |

### Vector + keyword (PDF index only)

When Azure Search is on:

- **Vector (k-NN)** — meaning similarity
- **Keyword (BM25)** — part numbers, standard IDs

The Neo4j side is not hybrid in that sense. It is lexical + graph walk only.

---

## 7. Worked example

Question: **“What components does SmartHub X1 contain?”**

```text
1. Parser
   likely entity: SmartHub X1

2. Neo4j (not embedded)
   name CONTAINS "smarthub x1"
   seed: (:Product {name: "SmartHub X1"})
   2 hops → [:HAS_COMPONENT] → (:Component {name: "PSU-410"}) ...

3. PDF search (embedded only if Azure Search is on)
   chunks that mention the product and its parts

4. LLM
   lists only components present in retrieved graph/docs
```

---

## 8. Configuration

| Variable | Role |
|---|---|
| `AZURE_OPENAI_CHAT_DEPLOYMENT` / `LLM_MODEL` | Chat, LightRAG extraction, formatting |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Chunk embeddings (`text-embedding-3-small`) |
| `AZURE_OPENAI_EMBEDDING_DIMENSIONS` | 1536 |
| `AZURE_SEARCH_*` | Optional PDF vector index `ul-chunks` |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` | Graph store |

| Setup | Graph in Neo4j | PDF embeddings |
|---|---|---|
| Azure Search configured | Stored as nodes/edges, not embedded | Yes — ingest + question at chat |
| Azure Search not configured | Same Neo4j graph | No — word overlap on `_INDEX` |

---

## 9. Key files

| File | Responsibility |
|---|---|
| `backend/ul/lightrag_extractor.py` | LightRAG prompt extraction |
| `backend/ul/ul_service.py` | Chunk → extract → triplets → Neo4j |
| `backend/ul/neo4j_service.py` | `MERGE` nodes/edges; no vector index |
| `backend/ul/document_chunking.py` | Chunking, optional embeddings, Azure index |
| `backend/ul/chatbot.py` | Keyword graph retrieval + document RAG |
| `frontend/src/components/chat/MockChatbotPanel.tsx` | Chat UI |

---

## 10. Limits

- LightRAG is prompt-only here; its native KV/vector/graph backends are unused.
- Neo4j graph: **not embedded**, **no app-created vector or full-text index**.
- Graph retrieval is read-only and bounded (8 seeds, 2–3 hops, 60 items).
- Document retrieval is bounded (8 chunks).
- `_INDEX` does not survive a backend restart; persistent PDF search needs Azure AI Search.
- Similar names are not merged unless extraction/normalization already treated them as the same entity.
