import type { ChatEvidenceNode, ChatEvidenceRelationship } from '@/types/api'

/**
 * Deterministic layered positions for the lineage graph.
 *
 * Depth follows the backend's own relationship directions: a node sits one row
 * below its deepest parent, so every edge points downward. For the components
 * path that yields Product on top, Model in the middle, Component at the
 * bottom — and it generalises to the other approved paths without hardcoding
 * labels.
 *
 * The layout never adds, removes, merges or splits anything. It reads
 * `graph_evidence` and returns coordinates keyed by the backend's node ids, so
 * a component shared by two models stays one node with two incoming edges
 * rather than being duplicated under each parent.
 *
 * Determinism: every iteration walks id-sorted collections and every ordering
 * falls back to the node id, so identical evidence always yields identical
 * coordinates.
 */

export const LAYER_HEIGHT = 120
export const NODE_SPACING = 170

export interface LayoutPosition {
  x: number
  y: number
}

interface Edge {
  source: string
  target: string
}

/** Node ids in the same order the canvas registers them, deduplicated. */
function collectNodeIds(
  nodes: ChatEvidenceNode[],
  relationships: ChatEvidenceRelationship[],
): string[] {
  const ids: string[] = []
  const seen = new Set<string>()
  const add = (id: string) => {
    if (!id || seen.has(id)) return
    seen.add(id)
    ids.push(id)
  }
  for (const node of nodes) add(node.id)
  // Endpoints only cited inside a relationship still need a position.
  for (const rel of relationships) {
    add(rel.source)
    add(rel.target)
  }
  return ids
}

function sortedEdges(relationships: ChatEvidenceRelationship[]): Edge[] {
  return relationships
    .filter((rel) => rel.source && rel.target)
    .map((rel) => ({ source: rel.source, target: rel.target }))
    .sort(
      (a, b) => a.source.localeCompare(b.source) || a.target.localeCompare(b.target),
    )
}

/**
 * Longest-path depth, so a shared child sits below *all* of its parents.
 * Relaxation is capped at the node count, which also bounds any cycle.
 */
function computeDepths(ids: string[], edges: Edge[]): Map<string, number> {
  const depth = new Map<string, number>()
  for (const id of ids) depth.set(id, 0)

  for (let pass = 0; pass < ids.length; pass += 1) {
    let changed = false
    for (const edge of edges) {
      const from = depth.get(edge.source)
      const to = depth.get(edge.target)
      if (from === undefined || to === undefined) continue
      if (to < from + 1) {
        depth.set(edge.target, from + 1)
        changed = true
      }
    }
    if (!changed) break
  }
  return depth
}

/**
 * Order a layer by the mean position of its parents in the layer above
 * (barycenter), which keeps edges short and crossings low. Nodes with no
 * parent above are appended. Every comparison falls back to the id.
 */
function orderLayer(
  layer: string[],
  parentsOf: Map<string, string[]>,
  indexAbove: Map<string, number>,
): string[] {
  const barycenter = new Map<string, number>()
  for (const id of layer) {
    const positions = (parentsOf.get(id) ?? [])
      .map((parent) => indexAbove.get(parent))
      .filter((value): value is number => value !== undefined)
    if (positions.length > 0) {
      barycenter.set(id, positions.reduce((a, b) => a + b, 0) / positions.length)
    }
  }
  return [...layer].sort((a, b) => {
    const aCenter = barycenter.get(a)
    const bCenter = barycenter.get(b)
    if (aCenter !== undefined && bCenter !== undefined && aCenter !== bCenter) {
      return aCenter - bCenter
    }
    if (aCenter !== undefined && bCenter === undefined) return -1
    if (aCenter === undefined && bCenter !== undefined) return 1
    return a.localeCompare(b)
  })
}

export function layoutLineage(
  nodes: ChatEvidenceNode[],
  relationships: ChatEvidenceRelationship[],
): Map<string, LayoutPosition> {
  const ids = collectNodeIds(nodes, relationships)
  const positions = new Map<string, LayoutPosition>()
  if (ids.length === 0) return positions

  const edges = sortedEdges(relationships)
  const depth = computeDepths(ids, edges)

  const parentsOf = new Map<string, string[]>()
  for (const edge of edges) {
    const bucket = parentsOf.get(edge.target)
    if (bucket) bucket.push(edge.source)
    else parentsOf.set(edge.target, [edge.source])
  }

  const layers = new Map<number, string[]>()
  for (const id of [...ids].sort((a, b) => a.localeCompare(b))) {
    const level = depth.get(id) ?? 0
    const bucket = layers.get(level)
    if (bucket) bucket.push(id)
    else layers.set(level, [id])
  }

  let indexAbove = new Map<string, number>()
  for (const level of [...layers.keys()].sort((a, b) => a - b)) {
    const ordered = orderLayer(layers.get(level) ?? [], parentsOf, indexAbove)
    const offset = (ordered.length - 1) / 2
    ordered.forEach((id, index) => {
      positions.set(id, {
        x: Math.round((index - offset) * NODE_SPACING),
        y: level * LAYER_HEIGHT,
      })
    })
    indexAbove = new Map(ordered.map((id, index) => [id, index]))
  }

  return positions
}
