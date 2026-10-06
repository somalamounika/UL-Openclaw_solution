import type { GraphModel } from '@/types/graph'

/**
 * Pure, allocation-light traversals over the prebuilt adjacency index.
 * There is no per-node expansion endpoint — expansion is client-side set maths,
 * so every one of these must stay O(visited), never O(graph).
 */

/** Hop distance from a set of roots, breadth-first, stopping at maxHops. */
export function hopDistances(
  model: GraphModel,
  roots: Iterable<string>,
  maxHops: number,
): Map<string, number> {
  const distance = new Map<string, number>()
  let frontier: string[] = []

  for (const root of roots) {
    if (model.nodeById.has(root) && !distance.has(root)) {
      distance.set(root, 0)
      frontier.push(root)
    }
  }

  for (let hop = 1; hop <= maxHops && frontier.length; hop += 1) {
    const next: string[] = []
    for (const id of frontier) {
      for (const neighbour of model.adjacency.get(id) ?? []) {
        if (distance.has(neighbour)) continue
        distance.set(neighbour, hop)
        next.push(neighbour)
      }
    }
    frontier = next
  }

  return distance
}

/** Direct neighbours of a node, as ids. */
export function neighboursOf(model: GraphModel, id: string): string[] {
  return model.adjacency.get(id) ?? []
}

/** Neighbours not currently visible, grouped by their ontology label with counts. */
export function hiddenNeighboursByType(
  model: GraphModel,
  id: string,
  visible: ReadonlySet<string>,
): Map<string, string[]> {
  const grouped = new Map<string, string[]>()
  for (const neighbourId of model.adjacency.get(id) ?? []) {
    if (visible.has(neighbourId)) continue
    const neighbour = model.nodeById.get(neighbourId)
    if (!neighbour) continue
    const bucket = grouped.get(neighbour.label)
    if (bucket) bucket.push(neighbourId)
    else grouped.set(neighbour.label, [neighbourId])
  }
  return grouped
}

/** How many neighbours of `id` are hidden — drives the `+n` ring badge. */
export function hiddenNeighbourCount(
  model: GraphModel,
  id: string,
  visible: ReadonlySet<string>,
): number {
  let count = 0
  for (const neighbourId of model.adjacency.get(id) ?? []) {
    if (!visible.has(neighbourId)) count += 1
  }
  return count
}

/** Ids within `hops` of a root, inclusive — used by Alt+click 2-hop expansion. */
export function expandFrom(model: GraphModel, id: string, hops: number): string[] {
  return [...hopDistances(model, [id], hops).keys()]
}

/**
 * Collapse: hide descendants of `id` that are not reachable from any other visible
 * node without passing through `id`. Prevents a collapse from tearing a hole in a
 * subgraph the user reached by another path.
 */
export function collapsibleDescendants(
  model: GraphModel,
  id: string,
  visible: ReadonlySet<string>,
  anchors: ReadonlySet<string>,
): string[] {
  // Everything reachable among visible nodes without going through `id`.
  const reachable = new Set<string>()
  const queue: string[] = []
  for (const anchor of anchors) {
    if (anchor !== id && visible.has(anchor)) {
      reachable.add(anchor)
      queue.push(anchor)
    }
  }
  while (queue.length) {
    const current = queue.pop()!
    for (const neighbour of model.adjacency.get(current) ?? []) {
      if (neighbour === id || !visible.has(neighbour) || reachable.has(neighbour)) continue
      reachable.add(neighbour)
      queue.push(neighbour)
    }
  }

  const doomed: string[] = []
  for (const neighbourId of model.adjacency.get(id) ?? []) {
    if (!visible.has(neighbourId) || reachable.has(neighbourId)) continue
    doomed.push(neighbourId)
  }

  // Pull in anything that only hung off the doomed set.
  const doomedSet = new Set(doomed)
  const stack = [...doomed]
  while (stack.length) {
    const current = stack.pop()!
    for (const neighbour of model.adjacency.get(current) ?? []) {
      if (neighbour === id || !visible.has(neighbour)) continue
      if (reachable.has(neighbour) || doomedSet.has(neighbour)) continue
      doomedSet.add(neighbour)
      stack.push(neighbour)
    }
  }

  return [...doomedSet]
}

/** Highest-degree nodes, for the screen-reader structural summary. */
export function topDegreeNodes(model: GraphModel, count: number) {
  return [...model.nodes].sort((a, b) => b.degree - a.degree).slice(0, count)
}
