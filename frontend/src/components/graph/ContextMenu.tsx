import { useEffect, useMemo, useRef } from 'react'
import { useGraphStore } from '@/state/graphStore'
import { hiddenNeighboursByType } from '@/lib/adjacency'
import { typeDisplay, typeFill } from '@/lib/ontology'
import type { GraphNode } from '@/types/graph'

export interface ContextMenuState {
  nodeId: string
  x: number
  y: number
}

/**
 * Expand-by-type. A user chasing standards should be able to pull in the standards
 * without also drowning in every clause hanging off them.
 */
export function ContextMenu({
  state,
  node,
  onClose,
}: {
  state: ContextMenuState
  node: GraphNode
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  const model = useGraphStore((s) => s.model)
  const visible = useGraphStore((s) => s.visible)
  const expandIds = useGraphStore((s) => s.expandIds)
  const expandNode = useGraphStore((s) => s.expandNode)
  const collapseNode = useGraphStore((s) => s.collapseNode)
  const focusNode = useGraphStore((s) => s.focusNode)

  const grouped = useMemo(
    () => [...hiddenNeighboursByType(model, state.nodeId, visible)].sort((a, b) => b[1].length - a[1].length),
    [model, state.nodeId, visible],
  )

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])

  return (
    <div
      ref={ref}
      className="canvas-overlay-panel absolute z-40 w-56 p-1 text-xs"
      style={{ left: Math.min(state.x, (ref.current?.parentElement?.clientWidth ?? 800) - 240), top: state.y }}
      role="menu"
    >
      <div className="truncate px-2 py-1.5 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
        {node.name}
      </div>

      {grouped.length > 0 ? (
        <>
          <div className="px-2 pb-1 text-2xs text-fg-subtle">Expand by type</div>
          {grouped.map(([label, ids]) => (
            <button
              key={label}
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-fg hover:bg-[var(--surface-3)]"
              onClick={() => {
                expandIds(ids)
                onClose()
              }}
            >
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: typeFill(label) }}
                aria-hidden
              />
              <span className="flex-1 truncate">{typeDisplay(label)}</span>
              <span className="tabular-nums text-fg-subtle">{ids.length}</span>
            </button>
          ))}
          <div className="my-1 h-px bg-[var(--border)]" />
        </>
      ) : (
        <div className="px-2 py-1.5 text-fg-subtle">All neighbours already shown</div>
      )}

      <button
        type="button"
        role="menuitem"
        className="w-full rounded px-2 py-1.5 text-left text-fg hover:bg-[var(--surface-3)]"
        onClick={() => {
          expandNode(state.nodeId, 2)
          onClose()
        }}
      >
        Expand 2 hops
      </button>
      <button
        type="button"
        role="menuitem"
        className="w-full rounded px-2 py-1.5 text-left text-fg hover:bg-[var(--surface-3)]"
        onClick={() => {
          focusNode(state.nodeId)
          onClose()
        }}
      >
        Focus here
      </button>
      <button
        type="button"
        role="menuitem"
        className="w-full rounded px-2 py-1.5 text-left text-fg hover:bg-[var(--surface-3)]"
        onClick={() => {
          collapseNode(state.nodeId)
          onClose()
        }}
      >
        Collapse
      </button>
    </div>
  )
}
