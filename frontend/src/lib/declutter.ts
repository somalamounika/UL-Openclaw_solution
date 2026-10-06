import type { Core, NodeCollection } from 'cytoscape'

/**
 * Force layouts *tend* toward separation; they do not guarantee it. fCoSE will happily
 * leave two discs overlapping when the edge forces pulling them together beat the
 * repulsion pushing them apart, and an overlapped pair is unreadable — one caption sits
 * on top of the other.
 *
 * So every layout is followed by this pass: a relaxation that pushes overlapping pairs
 * apart until each pair clears `gap`, and stops as soon as nothing overlaps. It only
 * ever moves nodes that are too close, so a settled layout comes through untouched.
 */

export const MIN_NODE_GAP = 26

interface Placed {
  id: string
  x: number
  y: number
  r: number
  frozen: boolean
}

/**
 * Neighbour lookup is bucketed by a grid sized to the largest node, so each node is only
 * tested against the nine cells around it rather than against all n.
 */
function bucketKey(x: number, y: number, cell: number): string {
  return `${Math.floor(x / cell)}:${Math.floor(y / cell)}`
}

export function separateNodes(
  cy: Core,
  nodes: NodeCollection,
  options: { gap?: number; iterations?: number; frozen?: ReadonlySet<string> } = {},
): void {
  const gap = options.gap ?? MIN_NODE_GAP
  const iterations = options.iterations ?? 120
  const frozenIds = options.frozen

  const placed: Placed[] = []
  let maxRadius = 0
  nodes.forEach((node) => {
    // width() is the rendered model width, so it already includes the density factor.
    const r = node.width() / 2
    maxRadius = Math.max(maxRadius, r)
    placed.push({
      id: node.id(),
      x: node.position('x'),
      y: node.position('y'),
      r,
      frozen: frozenIds?.has(node.id()) ?? false,
    })
  })
  if (placed.length < 2) return

  const cell = maxRadius * 2 + gap
  const grid = new Map<string, Placed[]>()

  for (let iteration = 0; iteration < iterations; iteration += 1) {
    grid.clear()
    for (const item of placed) {
      const key = bucketKey(item.x, item.y, cell)
      const bucket = grid.get(key)
      if (bucket) bucket.push(item)
      else grid.set(key, [item])
    }

    let moved = false
    for (const a of placed) {
      const gx = Math.floor(a.x / cell)
      const gy = Math.floor(a.y / cell)
      for (let dx = -1; dx <= 1; dx += 1) {
        for (let dy = -1; dy <= 1; dy += 1) {
          const bucket = grid.get(`${gx + dx}:${gy + dy}`)
          if (!bucket) continue
          for (const b of bucket) {
            // Each unordered pair is resolved once.
            if (a.id >= b.id) continue
            const minimum = a.r + b.r + gap
            let vx = b.x - a.x
            let vy = b.y - a.y
            let distance = Math.hypot(vx, vy)
            if (distance >= minimum) continue
            if (distance < 1e-6) {
              // Exactly coincident: pick a deterministic direction rather than NaN.
              const angle = (a.id.length + b.id.length) % 360
              vx = Math.cos(angle)
              vy = Math.sin(angle)
              distance = 1
            }
            const push = (minimum - distance) / distance
            const dxPush = vx * push
            const dyPush = vy * push
            if (a.frozen && b.frozen) continue
            if (a.frozen) {
              b.x += dxPush
              b.y += dyPush
            } else if (b.frozen) {
              a.x -= dxPush
              a.y -= dyPush
            } else {
              a.x -= dxPush / 2
              a.y -= dyPush / 2
              b.x += dxPush / 2
              b.y += dyPush / 2
            }
            moved = true
          }
        }
      }
    }
    if (!moved) break
  }

  cy.batch(() => {
    for (const item of placed) {
      if (item.frozen) continue
      const node = cy.getElementById(item.id)
      if (node.nonempty()) node.position({ x: item.x, y: item.y })
    }
  })
}
