import type { ApiGraphEdge, ApiGraphNode, GraphResponse } from '@/types/api'
import type { GraphEdge, GraphModel, GraphNode } from '@/types/graph'
import { resolveType } from './ontology'

/** Stable key for an edge ignoring direction-free duplicates of the same triple. */
function parallelKey(edge: ApiGraphEdge): string {
  return edge.source < edge.destination
    ? `${edge.source}|${edge.destination}`
    : `${edge.destination}|${edge.source}`
}

/**
 * Wire shape -> domain model, plus the adjacency/degree indexes.
 *
 * Built exactly once per graph load and memoised: expansion, focus and LOD all read
 * these maps, so nothing recomputes graph-wide on a click.
 */
export function buildGraphModel(response: GraphResponse): GraphModel {
  const nodeById = new Map<string, GraphNode>()
  const typeCounts = new Map<string, number>()

  for (const raw of response.nodes) {
    const node = adaptNode(raw)
    // The server orders by label,name; duplicate ids cannot occur, but be defensive.
    if (!nodeById.has(node.id)) {
      nodeById.set(node.id, node)
      typeCounts.set(node.label, (typeCounts.get(node.label) ?? 0) + 1)
    }
  }

  const parallelGroups = new Map<string, number>()
  const parallelTotals = new Map<string, number>()
  for (const raw of response.edges) {
    const key = parallelKey(raw)
    parallelTotals.set(key, (parallelTotals.get(key) ?? 0) + 1)
  }

  const edges: GraphEdge[] = []
  const edgeById = new Map<string, GraphEdge>()
  const adjacency = new Map<string, Set<string>>()
  const incidentEdges = new Map<string, string[]>()

  for (const raw of response.edges) {
    // Drop edges whose endpoints fell outside the 2000-node cap — Cytoscape would throw.
    if (!nodeById.has(raw.source) || !nodeById.has(raw.destination)) continue

    const key = parallelKey(raw)
    const index = parallelGroups.get(key) ?? 0
    parallelGroups.set(key, index + 1)

    const edge = adaptEdge(raw, index, parallelTotals.get(key) ?? 1)
    // Neo4j can return the same triple twice across MERGE runs; keep the first.
    if (edgeById.has(edge.id)) continue
    edgeById.set(edge.id, edge)
    edges.push(edge)

    if (!adjacency.has(edge.source)) adjacency.set(edge.source, new Set())
    if (!adjacency.has(edge.target)) adjacency.set(edge.target, new Set())
    adjacency.get(edge.source)!.add(edge.target)
    adjacency.get(edge.target)!.add(edge.source)

    if (!incidentEdges.has(edge.source)) incidentEdges.set(edge.source, [])
    if (!incidentEdges.has(edge.target)) incidentEdges.set(edge.target, [])
    incidentEdges.get(edge.source)!.push(edge.id)
    incidentEdges.get(edge.target)!.push(edge.id)
  }

  const adjacencyArrays = new Map<string, string[]>()
  for (const [id, set] of adjacency) adjacencyArrays.set(id, [...set])

  // Degree is neighbour count (not edge count) so parallel edges don't inflate radius.
  for (const node of nodeById.values()) {
    const neighbours = adjacencyArrays.get(node.id) ?? []
    node.degree = neighbours.length
    const byType: Record<string, number> = {}
    for (const neighbourId of neighbours) {
      const neighbour = nodeById.get(neighbourId)
      if (!neighbour) continue
      byType[neighbour.label] = (byType[neighbour.label] ?? 0) + 1
    }
    node.degreeByType = byType
  }

  return {
    graphId: response.graph_id,
    nodes: [...nodeById.values()],
    edges,
    nodeById,
    edgeById,
    adjacency: adjacencyArrays,
    incidentEdges,
    reportedNodeCount: response.node_count,
    reportedRelationshipCount: response.relationship_count,
    typeCounts,
  }
}

function adaptNode(raw: ApiGraphNode): GraphNode {
  const properties = { ...(raw.properties ?? {}) }
  if (raw.description && !properties.description) properties.description = raw.description
  return {
    id: raw.id,
    label: raw.label,
    name: raw.name,
    description: raw.description ?? String(properties.description ?? ''),
    sourceFiles: raw.source_files ?? [],
    properties,
    tier: resolveType(raw.label).tier,
    degree: 0,
    degreeByType: {},
  }
}

function adaptEdge(raw: ApiGraphEdge, parallelIndex: number, parallelCount: number): GraphEdge {
  const properties = { ...(raw.properties ?? {}) }
  if (raw.description && !properties.description) properties.description = raw.description
  if (raw.keywords && !properties.keywords) properties.keywords = raw.keywords
  return {
    id: raw.id,
    source: raw.source,
    sourceName: raw.source_name,
    sourceLabel: raw.source_label,
    target: raw.destination,
    targetName: raw.destination_name,
    targetLabel: raw.destination_label,
    relationship: raw.relationship,
    description: raw.description ?? String(properties.description ?? ''),
    keywords: raw.keywords ?? String(properties.keywords ?? ''),
    sourceFiles: raw.source_files ?? [],
    pageRanges: raw.page_ranges ?? [],
    chunkIds: raw.chunk_ids ?? [],
    properties,
    parallelIndex,
    parallelCount,
  }
}

export const EMPTY_GRAPH: GraphModel = {
  graphId: null,
  nodes: [],
  edges: [],
  nodeById: new Map(),
  edgeById: new Map(),
  adjacency: new Map(),
  incidentEdges: new Map(),
  reportedNodeCount: 0,
  reportedRelationshipCount: 0,
  typeCounts: new Map(),
}
