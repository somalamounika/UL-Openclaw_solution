import type { LayoutOptions } from 'cytoscape'

export type LayoutName = 'fcose' | 'concentric' | 'breadthfirst' | 'grid'

export const LAYOUT_LABELS: Record<LayoutName, string> = {
  fcose: 'Force (fCoSE)',
  concentric: 'Concentric by tier',
  breadthfirst: 'Breadth-first',
  grid: 'Grid',
}

/**
 * fCoSE quality is chosen from the visible node count, not hard-coded: 'proof' is
 * unusable above a few hundred nodes and 'draft' looks sloppy below that.
 */
export function fcoseQuality(visibleNodes: number): 'proof' | 'default' | 'draft' {
  if (visibleNodes < 300) return 'proof'
  if (visibleNodes <= 800) return 'default'
  return 'draft'
}

export interface LayoutArgs {
  name: LayoutName
  visibleNodes: number
  animate: boolean
  /** Incremental run: existing nodes are already fixed, only new ones settle. */
  incremental?: boolean
  fixedNodeConstraint?: Array<{ nodeId: string; position: { x: number; y: number } }>
  onStop?: () => void
}

export function buildLayout({
  name,
  visibleNodes,
  animate,
  incremental,
  fixedNodeConstraint,
  onStop,
}: LayoutArgs): LayoutOptions {
  const base = {
    animate,
    animationDuration: animate ? 420 : 0,
    animationEasing: 'ease-out' as const,
    fit: false,
    padding: 80,
    stop: onStop,
  }

  switch (name) {
    case 'concentric':
      return {
        ...base,
        name: 'concentric',
        // Tier 1 innermost: concentric puts the highest value at the centre.
        concentric: (node: cytoscape.NodeSingular) => 10 - Number(node.data('tier') ?? 5),
        levelWidth: () => 1,
        minNodeSpacing: 60,
        spacingFactor: 1.35,
      } as unknown as LayoutOptions

    case 'breadthfirst':
      return {
        ...base,
        name: 'breadthfirst',
        directed: true,
        spacingFactor: 1.5,
        grid: false,
        // Edges are not a DAG (SUPPLIED_BY / MANUFACTURED_BY point upward), so seed
        // roots from tier 1 rather than letting Cytoscape guess.
        roots: undefined,
      } as unknown as LayoutOptions

    case 'grid':
      return { ...base, name: 'grid', avoidOverlap: true, spacingFactor: 1.4 } as unknown as LayoutOptions

    case 'fcose':
    default:
      return {
        ...base,
        name: 'fcose',
        quality: fcoseQuality(visibleNodes),
        randomize: !incremental,
        // An incremental run must not reshuffle the existing map under the user.
        fixedNodeConstraint,
        // Discs carry their caption inside and every edge carries its type on the line,
        // so the layout has to leave room for text, not just for circles.
        nodeDimensionsIncludeLabels: true,
        uniformNodeDimensions: false,
        packComponents: true,
        nodeSeparation: 110,
        nodeRepulsion: () => 14000,
        idealEdgeLength: (edge: cytoscape.EdgeSingular) => {
          // Long enough for the relationship type to sit on the line without colliding
          // with either endpoint; deep leaves still sit closer than the structural spine.
          const tier = Math.max(
            Number(edge.source().data('tier') ?? 5),
            Number(edge.target().data('tier') ?? 5),
          )
          return tier >= 7 ? 115 : tier >= 5 ? 140 : 175
        },
        edgeElasticity: () => 0.32,
        gravity: 0.12,
        gravityRange: 4.2,
        numIter: visibleNodes > 800 ? 1400 : 2500,
        tile: true,
        tilingPaddingVertical: 30,
        tilingPaddingHorizontal: 30,
      } as unknown as LayoutOptions
  }
}
