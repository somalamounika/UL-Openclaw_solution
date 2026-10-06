import { useEffect, useMemo, useRef } from 'react'
import type { Core } from 'cytoscape'
import { useGraphStore } from '@/state/graphStore'
import { typeFill, withAlpha } from '@/lib/ontology'
import type { Theme } from '@/hooks/useTheme'

const WIDTH = 176
const HEIGHT = 118

/**
 * Full-graph overview drawn to a 2D canvas (not DOM nodes) so it stays cheap, with a
 * draggable viewport rectangle. In focus mode it also draws the focus ring.
 */
export function Minimap({ cy, version, theme }: { cy: Core | null; version: number; theme: Theme }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const dragging = useRef(false)
  const model = useGraphStore((s) => s.model)
  const visible = useGraphStore((s) => s.visible)
  const nodeFocus = useGraphStore((s) => s.nodeFocus)
  const evidence = useGraphStore((s) => s.evidence)

  const focusRoot = nodeFocus?.nodeId ?? null

  const bounds = useMemo(() => {
    if (!cy) return null
    const box = cy.elements(':visible').boundingBox()
    if (!Number.isFinite(box.w) || box.w === 0) return null
    return box
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cy, visible, model, version])

  useEffect(() => {
    const element = canvasRef.current
    if (!element || !cy || !bounds) return
    const context = element.getContext('2d')
    if (!context) return

    const dpr = Math.min(2, window.devicePixelRatio || 1)
    element.width = WIDTH * dpr
    element.height = HEIGHT * dpr
    context.setTransform(dpr, 0, 0, dpr, 0, 0)
    context.clearRect(0, 0, WIDTH, HEIGHT)

    const padding = 6
    const scale = Math.min((WIDTH - padding * 2) / bounds.w, (HEIGHT - padding * 2) / bounds.h)
    const offsetX = padding + (WIDTH - padding * 2 - bounds.w * scale) / 2
    const offsetY = padding + (HEIGHT - padding * 2 - bounds.h * scale) / 2
    const toMini = (x: number, y: number) => ({
      x: offsetX + (x - bounds.x1) * scale,
      y: offsetY + (y - bounds.y1) * scale,
    })

    cy.nodes(':visible').forEach((node) => {
      const position = node.position()
      const { x, y } = toMini(position.x, position.y)
      const isEvidence = evidence ? evidence.nodeIds.includes(node.id()) : false
      context.beginPath()
      context.arc(x, y, isEvidence ? 2.6 : 1.6, 0, Math.PI * 2)
      context.fillStyle = isEvidence
        ? theme === 'dark'
          ? '#7dd3fc'
          : '#0ea5e9'
        : typeFill(String(node.data('label')))
      context.globalAlpha = evidence && !isEvidence ? 0.25 : 0.9
      context.fill()
    })
    context.globalAlpha = 1

    if (focusRoot) {
      const node = cy.getElementById(focusRoot)
      if (node.nonempty()) {
        const { x, y } = toMini(node.position('x'), node.position('y'))
        context.beginPath()
        context.arc(x, y, 11, 0, Math.PI * 2)
        context.strokeStyle = theme === 'dark' ? '#38bdf8' : '#0284c7'
        context.lineWidth = 1.2
        context.stroke()
      }
    }

    // Viewport rectangle.
    const extent = cy.extent()
    const topLeft = toMini(extent.x1, extent.y1)
    const bottomRight = toMini(extent.x2, extent.y2)
    context.strokeStyle = theme === 'dark' ? 'rgba(232,236,244,0.75)' : 'rgba(15,23,42,0.6)'
    context.lineWidth = 1
    context.strokeRect(
      topLeft.x,
      topLeft.y,
      Math.max(4, bottomRight.x - topLeft.x),
      Math.max(4, bottomRight.y - topLeft.y),
    )
    context.fillStyle = withAlpha(theme === 'dark' ? '#e8ecf4' : '#0f172a', 0.07)
    context.fillRect(
      topLeft.x,
      topLeft.y,
      Math.max(4, bottomRight.x - topLeft.x),
      Math.max(4, bottomRight.y - topLeft.y),
    )
  }, [cy, bounds, version, theme, focusRoot, evidence])

  const panTo = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!cy || !bounds) return
    const rect = event.currentTarget.getBoundingClientRect()
    const padding = 6
    const scale = Math.min((WIDTH - padding * 2) / bounds.w, (HEIGHT - padding * 2) / bounds.h)
    const offsetX = padding + (WIDTH - padding * 2 - bounds.w * scale) / 2
    const offsetY = padding + (HEIGHT - padding * 2 - bounds.h * scale) / 2
    const graphX = (event.clientX - rect.left - offsetX) / scale + bounds.x1
    const graphY = (event.clientY - rect.top - offsetY) / scale + bounds.y1
    cy.center({ position: { x: graphX, y: graphY } } as never)
    const zoom = cy.zoom()
    cy.pan({ x: cy.width() / 2 - graphX * zoom, y: cy.height() / 2 - graphY * zoom })
  }

  if (!cy || !bounds) return null

  return (
    <div className="canvas-overlay-panel absolute bottom-3 right-3 z-20 overflow-hidden p-1">
      <canvas
        ref={canvasRef}
        style={{ width: WIDTH, height: HEIGHT }}
        className="block cursor-crosshair rounded"
        aria-label="Graph minimap. Click or drag to pan the viewport."
        onPointerDown={(event) => {
          dragging.current = true
          event.currentTarget.setPointerCapture(event.pointerId)
          panTo(event)
        }}
        onPointerMove={(event) => {
          if (dragging.current) panTo(event)
        }}
        onPointerUp={(event) => {
          dragging.current = false
          event.currentTarget.releasePointerCapture(event.pointerId)
        }}
      />
    </div>
  )
}
