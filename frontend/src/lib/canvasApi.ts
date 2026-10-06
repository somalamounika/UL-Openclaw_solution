import type { Core } from 'cytoscape'

/**
 * Imperative camera handle. GraphCanvas registers itself here on mount so the rails,
 * the chat panel and the keyboard layer can drive the camera without prop drilling
 * or forcing a React re-render of the canvas.
 */
export interface CanvasApi {
  cy: () => Core | null
  fit: () => void
  zoomBy: (factor: number) => void
  centerOn: (nodeId: string, zoom?: number) => void
  fitTo: (nodeIds: string[], padding?: number) => void
  pulse: (nodeId: string | null) => void
  relayout: () => void
}

let api: CanvasApi | null = null

export function registerCanvasApi(next: CanvasApi | null): void {
  api = next
}

export function canvas(): CanvasApi | null {
  return api
}
