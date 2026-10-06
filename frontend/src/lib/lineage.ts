import type { ChatEvidenceNode, ChatEvidenceRelationship, ChatResponse } from '@/types/api'

/**
 * Turns the assistant's `graph_evidence` into ordered lineage chains for LineageView.
 *
 * Every node and every hop here comes from the backend's own evidence payload — this
 * module orders and groups what it was given, and never invents a relationship or
 * infers a link the backend did not return.
 */

export interface LineageStep {
  /** The relationship traversed to reach `to`, exactly as the backend named it. */
  relationship: string
  description: string
  from: ChatEvidenceNode
  to: ChatEvidenceNode
}

export interface LineageChain {
  /** Ordered nodes, root first. */
  nodes: ChatEvidenceNode[]
  /** steps[i] connects nodes[i] to nodes[i + 1]. */
  steps: LineageStep[]
}

/**
 * Questions that are really about how things connect. When one of these is asked and
 * the answer carries relationships, the reply must show the lineage, not just prose.
 */
const LINEAGE_TERMS = [
  'lineage',
  'upstream',
  'downstream',
  'dependency',
  'dependencies',
  'depends on',
  'depend on',
  'relationship',
  'relationships',
  'related',
  'supplied by',
  'supplier',
  'supplies',
  'manufactured by',
  'manufacturer',
  'based on',
  'connected to',
  'connects',
  'path between',
  'path from',
  'trace',
  'traceability',
  'leads to',
  'where did',
  'come from',
  'comes from',
  'affect',
  'affects',
  'impact',
  'chain',
  'flow',
]

export function isLineageQuestion(question: string): boolean {
  const q = question.toLowerCase()
  return LINEAGE_TERMS.some((term) => q.includes(term))
}

/**
 * Show lineage whenever the (already filtered) evidence contains hops.
 * Backend sends a small answer-relevant subgraph, so any relationship is worth drawing.
 */
export function shouldShowLineage(_question: string, response: ChatResponse): boolean {
  return response.graph_evidence.relationships.length > 0
}

interface Indexed {
  byId: Map<string, ChatEvidenceNode>
  outgoing: Map<string, ChatEvidenceRelationship[]>
  incomingCount: Map<string, number>
}

function index(response: ChatResponse): Indexed {
  const byId = new Map<string, ChatEvidenceNode>()
  for (const node of response.graph_evidence.nodes) byId.set(node.id, node)

  const outgoing = new Map<string, ChatEvidenceRelationship[]>()
  const incomingCount = new Map<string, number>()

  for (const rel of response.graph_evidence.relationships) {
    // Endpoints are occasionally cited only inside a relationship; synthesise the node
    // from the relationship's own fields rather than dropping the hop.
    if (!byId.has(rel.source)) {
      byId.set(rel.source, { id: rel.source, name: rel.source_name, type: rel.source_type, description: '' })
    }
    if (!byId.has(rel.target)) {
      byId.set(rel.target, { id: rel.target, name: rel.target_name, type: rel.target_type, description: '' })
    }
    const bucket = outgoing.get(rel.source)
    if (bucket) bucket.push(rel)
    else outgoing.set(rel.source, [rel])
    incomingCount.set(rel.target, (incomingCount.get(rel.target) ?? 0) + 1)
  }

  return { byId, outgoing, incomingCount }
}

/**
 * Builds the longest directed chains through the evidence, roots first. Each edge is
 * used once, so a fan-out becomes several chains rather than one arbitrary path.
 */
export function buildLineageChains(response: ChatResponse, maxChains = 6): LineageChain[] {
  const { byId, outgoing, incomingCount } = index(response)
  const relationships = response.graph_evidence.relationships
  if (relationships.length === 0) return []

  const used = new Set<ChatEvidenceRelationship>()
  const chains: LineageChain[] = []

  const roots = [...byId.keys()].filter((id) => (incomingCount.get(id) ?? 0) === 0 && outgoing.has(id))
  // A pure cycle has no root; start somewhere deterministic so it still renders.
  const starts = roots.length > 0 ? roots : [...outgoing.keys()].slice(0, 1)

  const walk = (startId: string) => {
    const nodes: ChatEvidenceNode[] = []
    const steps: LineageStep[] = []
    const seen = new Set<string>()
    let currentId = startId

    for (;;) {
      if (seen.has(currentId)) break
      seen.add(currentId)
      const current = byId.get(currentId)
      if (!current) break
      nodes.push(current)

      const next = (outgoing.get(currentId) ?? []).find((rel) => !used.has(rel))
      if (!next) break
      used.add(next)
      const to = byId.get(next.target)
      if (!to) break
      steps.push({ relationship: next.relationship, description: next.description, from: current, to })
      currentId = next.target
    }

    if (steps.length > 0) chains.push({ nodes, steps })
  }

  for (const start of starts) {
    // One root can head several branches; keep walking while it still has unused edges.
    while ((outgoing.get(start) ?? []).some((rel) => !used.has(rel))) {
      walk(start)
      if (chains.length >= maxChains) return chains
    }
  }

  // Anything left over (a branch starting mid-graph) still deserves to be shown.
  for (const rel of relationships) {
    if (used.has(rel)) continue
    walk(rel.source)
    if (chains.length >= maxChains) break
  }

  return chains
}

/** Nodes cited in the answer that no chain covers — shown as standalone chips. */
export function unchainedNodes(response: ChatResponse, chains: LineageChain[]): ChatEvidenceNode[] {
  const inChains = new Set<string>()
  for (const chain of chains) for (const node of chain.nodes) inChains.add(node.id)
  return response.graph_evidence.nodes.filter((node) => !inChains.has(node.id))
}
