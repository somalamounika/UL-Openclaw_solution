import type { ElementDefinition } from 'cytoscape'
import type { GraphModel } from '@/types/graph'
import { humanizeRelationship, nodeRadius } from './ontology'
import { truncate } from './lod'

/**
 * Builds every Cytoscape element once, for the whole graph. Elements are added to the
 * instance a single time and visibility is toggled with a class — nothing is added or
 * removed on expansion, which is what makes expansion feel instant.
 */
export function buildElements(model: GraphModel): ElementDefinition[] {
  const elements: ElementDefinition[] = []

  for (const node of model.nodes) {
    elements.push({
      group: 'nodes',
      data: {
        id: node.id,
        label: node.label,
        name: node.name,
        tier: node.tier,
        degree: node.degree,
        size: nodeRadius(node.label, node.degree) * 2,
        caption: node.name,
      },
    })
  }

  for (const edge of model.edges) {
    elements.push({
      group: 'edges',
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        relationship: edge.relationship,
        // Neo4j writes the relationship type verbatim on the line, and so do we — the
        // type is what a Cypher query would name, so prettifying it only obscures it.
        caption: edge.relationship,
        // Parallel edges fan symmetrically around the straight line between endpoints.
        bundleOffset: bundleOffset(edge.parallelIndex, edge.parallelCount),
      },
    })
  }

  return elements
}

function bundleOffset(index: number, count: number): number {
  if (count <= 1) return 0
  const spread = 34
  return (index - (count - 1) / 2) * spread
}

/** Caption variants for a LOD change. Applied in one batch, never per frame. */
export function captionFor(name: string, chars: number): string {
  return truncate(name, chars)
}

/**
 * L0 aggregation: one edge per ordered type-pair, thickness scaled by member count.
 * Produced only while the overview level is active and removed on the way out.
 */
export function buildAggregateEdges(
  model: GraphModel,
  visibleNodeIds: ReadonlySet<string>,
  maxTier: number,
): ElementDefinition[] {
  const groups = new Map<string, { source: string; target: string; count: number }>()

  for (const edge of model.edges) {
    const source = model.nodeById.get(edge.source)
    const target = model.nodeById.get(edge.target)
    if (!source || !target) continue
    if (!visibleNodeIds.has(source.id) || !visibleNodeIds.has(target.id)) continue
    if (source.tier > maxTier || target.tier > maxTier) continue
    if (source.label === target.label && source.id === target.id) continue

    const key = `${source.label}|${target.label}`
    const existing = groups.get(key)
    if (existing) existing.count += 1
    else groups.set(key, { source: source.id, target: target.id, count: 1 })
  }

  const elements: ElementDefinition[] = []
  for (const [key, group] of groups) {
    const [sourceLabel, targetLabel] = key.split('|')
    elements.push({
      group: 'edges',
      classes: 'aggregate',
      data: {
        id: `agg:${key}`,
        source: group.source,
        target: group.target,
        weight: Math.max(1.5, Math.min(12, 1.2 + Math.log2(1 + group.count) * 2.1)),
        count: group.count,
        caption: `${humanizeRelationship(sourceLabel)} → ${humanizeRelationship(targetLabel)}`,
        bundleOffset: 0,
      },
    })
  }
  return elements
}
