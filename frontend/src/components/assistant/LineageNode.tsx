import { useState } from 'react'
import { MessagesSquare, Network, PanelRight } from 'lucide-react'
import type { ChatEvidenceNode } from '@/types/api'
import { typeDisplay, typeFill, withAlpha } from '@/lib/ontology'
import { useGraphStore } from '@/state/graphStore'
import { cn } from '@/lib/utils'

/**
 * One entity in a lineage chain. Clicking opens the three actions the spec calls for;
 * a node the current graph does not contain says so instead of offering dead links.
 */
export function LineageNode({
  node,
  onAsk,
  onShowInGraph,
  onViewDetails,
}: {
  node: ChatEvidenceNode
  onAsk: (node: ChatEvidenceNode) => void
  onShowInGraph: (node: ChatEvidenceNode) => void
  onViewDetails: (node: ChatEvidenceNode) => void
}) {
  const [open, setOpen] = useState(false)
  const inGraph = useGraphStore((s) => s.model.nodeById.has(node.id))
  const setHovered = useGraphStore((s) => s.setHovered)
  const fill = typeFill(node.type)

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => inGraph && setHovered(node.id)}
        onMouseLeave={() => setHovered(null)}
        aria-expanded={open}
        className={cn(
          'w-full min-w-[10rem] max-w-[15rem] rounded-lg border px-3 py-2 text-left transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
          open ? 'border-[var(--accent)]' : 'border-[var(--border)] hover:border-[var(--border-strong)]',
        )}
        style={{ backgroundColor: withAlpha(fill, 0.1) }}
      >
        <div className="flex items-center gap-1.5">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: fill }} aria-hidden />
          <span className="truncate text-[10px] font-semibold uppercase tracking-wide text-fg-subtle">
            {typeDisplay(node.type)}
          </span>
        </div>
        <div className="mt-0.5 break-words text-xs font-medium leading-snug text-fg">{node.name}</div>
      </button>

      {open ? (
        <div className="absolute left-0 top-full z-30 mt-1 w-52 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] p-1 shadow-xl">
          <MenuItem icon={MessagesSquare} label="Ask about this" onClick={() => { onAsk(node); setOpen(false) }} />
          <MenuItem
            icon={Network}
            label="Show in Knowledge Graph"
            disabled={!inGraph}
            onClick={() => { onShowInGraph(node); setOpen(false) }}
          />
          <MenuItem
            icon={PanelRight}
            label="View details"
            disabled={!inGraph}
            onClick={() => { onViewDetails(node); setOpen(false) }}
          />
          {!inGraph ? (
            <p className="px-2 py-1.5 text-[10px] leading-snug text-fg-subtle">
              Not present in the loaded graph — cited from the reference documents.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function MenuItem({
  icon: Icon,
  label,
  onClick,
  disabled,
}: {
  icon: typeof Network
  label: string
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-fg hover:bg-[var(--surface-3)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
    >
      <Icon className="h-3 w-3 shrink-0 text-fg-subtle" />
      {label}
    </button>
  )
}
