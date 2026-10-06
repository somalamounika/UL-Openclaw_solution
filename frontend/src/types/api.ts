/**
 * Wire types. These mirror the FastAPI contract exactly (backend/ul/ul_routes.py,
 * backend/ul/chatbot.py, backend/ul/neo4j_service.py). Nothing here is invented:
 * every field is returned by the running service. Fields the backend may omit are
 * marked optional rather than defaulted, so the adapter layer decides the fallback.
 */

/** POST /api/ul/upload */
export interface UploadResponse {
  document_id: string
  files: string[]
}

/** POST /api/ul/process — blocking, minutes-long, no progress events. */
export interface ProcessResponse {
  status: string
  document_id: string
  graph_id: string
  source_files: string[]
  created_at: string
  target_product_name: string | null
  uploaded_documents: number
  reference_documents: number
  total_documents_processed?: number
  documents_processed: number
  pages_extracted: number
  chunks_indexed: number
  azure_search_enabled: boolean
  azure_search_index: string | null
  components_extracted: number
  entities_extracted: number
  relationships_extracted: number
  triplets_created: number
  csv_file: string | null
  neo4j_ingested: boolean
  reference_files: string[]
  extraction_engine: string
  warnings: string[]
  graph_ready?: GraphReady
}

export interface GraphReady {
  companies: unknown[]
  products: unknown[]
  models: unknown[]
  components: unknown[]
  standards: unknown[]
  clauses: unknown[]
  certifications: unknown[]
  tests: unknown[]
  relationships: unknown[]
}

/** GET /api/ul/graphs */
export interface GraphListItem {
  graph_id: string
  source_files: string[]
  created_at: string
  document_id: string
}

export interface GraphListResponse {
  status: string
  graphs: GraphListItem[]
}

/** GET /api/ul/graph — node ids are literally `<Label>:<name>`. */
export interface ApiGraphNode {
  id: string
  label: string
  name: string
  description: string
  source_files: string[]
  /** Neo4j node properties for the inspector Key/Value panel. */
  properties?: Record<string, unknown>
}

export interface ApiGraphEdge {
  id: string
  source: string
  source_name: string
  source_label: string
  relationship: string
  destination: string
  destination_name: string
  destination_label: string
  description: string
  keywords: string
  source_files: string[]
  page_ranges: string[]
  chunk_ids: string[]
  /** Neo4j relationship properties for the inspector Key/Value panel. */
  properties?: Record<string, unknown>
}

export interface GraphResponse {
  status: string
  graph_id: string | null
  node_count: number
  relationship_count: number
  nodes: ApiGraphNode[]
  edges: ApiGraphEdge[]
}

/** DELETE /api/ul/graphs/{graph_id} */
export interface DeleteGraphResponse {
  status: string
  graph_id: string
  deleted_nodes: number
}

/** POST /api/ul/chat — JSON, not streamed. */
export interface ChatRequest {
  question: string
  graph_id: string | null
  document_id: string | null
}

export interface ChatSource {
  source_file: string
  page_range: { start: number; end: number }
  chunk_id: string
  document_id: string
}

/** graph_evidence node ids use the same `<Label>:<name>` scheme as /graph. */
export interface ChatEvidenceNode {
  id: string
  name: string
  type: string
  description: string
}

export interface ChatEvidenceRelationship {
  source: string
  source_name: string
  source_type: string
  relationship: string
  target: string
  target_name: string
  target_type: string
  description: string
  keywords: string
}

export interface ChatResponse {
  answer: string
  sources: ChatSource[]
  graph_context: unknown[]
  graph_evidence: {
    nodes: ChatEvidenceNode[]
    relationships: ChatEvidenceRelationship[]
  }
  /**
   * The exact read-only Cypher whose result is rendered as lineage. Run it in
   * Neo4j Browser to confirm it yields the same graph. Optional for older payloads.
   */
  generated_cypher?: string
  /** Bound parameters for `generated_cypher`. Optional for older payloads. */
  query_parameters?: Record<string, unknown>
}

/**
 * The backend returns this exact sentence when neither the graph nor the
 * documents produced any context (backend/ul/chatbot.py::answer_question).
 * The UI detects it to render an empty-state card instead of an answer bubble.
 */
export const CHAT_NO_RESULTS_ANSWER =
  'No relevant information was found in the selected knowledge graph or its documents.'

export interface HealthResponse {
  status: string
}
