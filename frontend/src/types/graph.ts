import type { OntologyTier } from '@/lib/ontology'

/** Domain node. Components consume this; only the adapter knows the wire shape. */
export interface GraphNode {
  /** `<Label>:<name>` — the join key shared with chat graph_evidence. */
  id: string
  label: string
  name: string
  description: string
  sourceFiles: string[]
  /** Raw Neo4j properties (aliases, canonical_id, created_at, …). */
  properties: Record<string, unknown>
  tier: OntologyTier
  degree: number
  /** Neighbour counts by type, for the inspector's degree breakdown. */
  degreeByType: Record<string, number>
}

export interface GraphEdge {
  id: string
  source: string
  sourceName: string
  sourceLabel: string
  target: string
  targetName: string
  targetLabel: string
  relationship: string
  description: string
  keywords: string
  sourceFiles: string[]
  pageRanges: string[]
  chunkIds: string[]
  /** Raw Neo4j relationship properties. */
  properties: Record<string, unknown>
  /** Index among edges sharing the same endpoint pair — drives bundling offsets. */
  parallelIndex: number
  parallelCount: number
}

export interface GraphModel {
  graphId: string | null
  nodes: GraphNode[]
  edges: GraphEdge[]
  nodeById: Map<string, GraphNode>
  edgeById: Map<string, GraphEdge>
  /** node id -> neighbour node ids. Built once on load; expansion reads it. */
  adjacency: Map<string, string[]>
  /** node id -> incident edge ids. */
  incidentEdges: Map<string, string[]>
  /** Counts reported by the server, which may exceed the returned arrays at the cap. */
  reportedNodeCount: number
  reportedRelationshipCount: number
  typeCounts: Map<string, number>
}

export interface EvidenceSelection {
  question: string
  nodeIds: string[]
  /** Ids cited by chat that are not present in the loaded graph — surfaced, never dropped. */
  missingNodeIds: string[]
  edgeKeys: string[]
  /** node id -> the evidence sentence chat gave for it. */
  nodeEvidence: Map<string, string>
}
