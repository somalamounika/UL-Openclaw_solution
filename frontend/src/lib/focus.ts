import type { GraphModel } from '@/types/graph'
import { hopDistances } from './adjacency'

/**
 * Node Focus is isolation, not dimming.
 *
 * Expand and Focus are deliberately different operations:
 *   Expand — reveal more of the graph around a node (additive).
 *   Focus  — remove everything that is not part of one node's neighbourhood.
 *
 * Both run entirely client-side over the adjacency index built at load time; there is
 * no per-node expansion endpoint, and `/graph` already returned the whole graph.
 */

export type FocusDepth = 1 | 2 | 3 | 4

export interface NodeFocus {
  nodeId: string
  depth: FocusDepth
}

/** The node itself plus everything within `depth` hops. Nothing else survives. */
export function focusNeighborhood(model: GraphModel, focus: NodeFocus): Set<string> {
  const distances = hopDistances(model, [focus.nodeId], focus.depth)
  return new Set(distances.keys())
}

/** Camera state captured on entry so exiting focus restores the exact prior view. */
export interface CameraState {
  zoom: number
  pan: { x: number; y: number }
}
