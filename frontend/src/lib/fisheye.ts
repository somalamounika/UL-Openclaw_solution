/**
 * Cartesian fisheye (Sarkar & Brown). Magnifies nodes near the cursor and compresses
 * the periphery so local structure is readable without losing global position.
 *
 * Applied to rendered positions only — the layout's own positions are never mutated,
 * so releasing the lens restores the map exactly.
 */

export interface FisheyeConfig {
  /** Focus point in graph (model) coordinates. */
  focusX: number
  focusY: number
  /** Lens radius in graph units; adjustable with the wheel while Space is held. */
  radius: number
  /** Distortion strength. 0 is identity; 2–5 reads well. */
  distortion: number
}

export const DEFAULT_FISHEYE_RADIUS = 260
export const MIN_FISHEYE_RADIUS = 90
export const MAX_FISHEYE_RADIUS = 700
export const FISHEYE_DISTORTION = 3

/** Sarkar–Brown transfer along one axis, normalised to [-1, 1]. */
function transfer(normalized: number, distortion: number): number {
  const sign = Math.sign(normalized)
  const magnitude = Math.abs(normalized)
  return sign * (((distortion + 1) * magnitude) / (distortion * magnitude + 1))
}

/** Map a true position to its lensed position. Outside the radius it is identity. */
export function applyFisheye(
  x: number,
  y: number,
  { focusX, focusY, radius, distortion }: FisheyeConfig,
): { x: number; y: number; scale: number } {
  const dx = x - focusX
  const dy = y - focusY
  const distance = Math.hypot(dx, dy)
  if (distance === 0) return { x, y, scale: 1 + distortion * 0.25 }
  if (distance >= radius) return { x, y, scale: 1 }

  const normalized = distance / radius
  const distorted = transfer(normalized, distortion)
  const factor = distorted / normalized
  // Nodes at the focus grow; nodes at the rim keep their size so the seam is invisible.
  const scale = 1 + (1 - normalized) * distortion * 0.22

  return {
    x: focusX + dx * factor,
    y: focusY + dy * factor,
    scale,
  }
}
