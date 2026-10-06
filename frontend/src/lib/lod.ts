/**
 * Semantic zoom. Four discrete levels driven by the ontology tier ladder, with a
 * ±0.08 hysteresis dead zone so the view never flickers at a threshold boundary.
 */

export type Lod = 0 | 1 | 2 | 3

export interface LodSpec {
  level: Lod
  name: string
  /** Deepest ontology tier rendered at this level. */
  maxTier: number
  /** Node caption truncation; 0 means captions are off. */
  captionChars: number
  /** Aggregate edges into one thick edge per type-pair. */
  aggregateEdges: boolean
  /** Edge labels: never | always. Relationship names are drawn on the line, not on hover. */
  edgeLabels: 'never' | 'always'
}

export const LOD_SPECS: Record<Lod, LodSpec> = {
  0: { level: 0, name: 'Overview', maxTier: 3, captionChars: 0, aggregateEdges: true, edgeLabels: 'never' },
  1: { level: 1, name: 'Structure', maxTier: 5, captionChars: 14, aggregateEdges: false, edgeLabels: 'always' },
  2: { level: 2, name: 'Detail', maxTier: 9, captionChars: 20, aggregateEdges: false, edgeLabels: 'always' },
  3: { level: 3, name: 'Inspect', maxTier: 9, captionChars: 28, aggregateEdges: false, edgeLabels: 'always' },
}

/** Upper zoom bound of each level; the last level is unbounded. */
const THRESHOLDS: Array<{ level: Lod; upTo: number }> = [
  { level: 0, upTo: 0.42 },
  { level: 1, upTo: 0.72 },
  { level: 2, upTo: 1.6 },
]

const HYSTERESIS = 0.08

/**
 * Map zoom -> LOD, biased to keep `current` until the zoom clears the dead zone.
 * At L1 with a 0.72 boundary the view holds L1 until 0.80, and holds L2 until 0.64.
 */
export function lodForZoom(zoom: number, current: Lod): Lod {
  let next: Lod = 3
  for (const { level, upTo } of THRESHOLDS) {
    if (zoom < upTo) {
      next = level
      break
    }
  }
  if (next === current) return current

  // Only cross a boundary once the zoom is HYSTERESIS clear of it.
  if (next > current) {
    const boundary = THRESHOLDS[current]?.upTo
    if (boundary !== undefined && zoom < boundary + HYSTERESIS) return current
  } else {
    const boundary = THRESHOLDS[next]?.upTo
    if (boundary !== undefined && zoom > boundary - HYSTERESIS) return current
  }
  return next
}

export function truncate(text: string, max: number): string {
  if (max <= 0) return ''
  if (text.length <= max) return text
  return `${text.slice(0, Math.max(1, max - 1))}…`
}
