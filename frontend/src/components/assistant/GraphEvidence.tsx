import { ArrowRight } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useGraphStore } from '@/state/graphStore'
import { typeFill } from '@/lib/ontology'
import type { ChatEvidenceNode, ChatEvidenceRelationship } from '@/types/api'

/**
 * Chips join to canvas nodes on the shared `<Label>:<name>` id. Hovering a chip pulses
 * the matching canvas node before the user has clicked anything.
 */
export function GraphEvidence({
  nodes,
  relationships,
}: {
  nodes: ChatEvidenceNode[]
  relationships: ChatEvidenceRelationship[]
}) {
  const model = useGraphStore((s) => s.model)
  const setHovered = useGraphStore((s) => s.setHovered)
  const selectNode = useGraphStore((s) => s.selectNode)
  const expandIds = useGraphStore((s) => s.expandIds)
  const navigate = useNavigate()

  if (nodes.length === 0 && relationships.length === 0) return null

  return (
    <div className="space-y-1.5">
      <div className="text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
        Graph ({nodes.length})
      </div>

      {nodes.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {nodes.map((node) => {
            const onCanvas = model.nodeById.has(node.id)
            return (
              <button
                key={node.id}
                type="button"
                title={node.description || node.type}
                onMouseEnter={() => onCanvas && setHovered(node.id)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => {
                  if (!onCanvas) return
                  expandIds([node.id])
                  selectNode(node.id)
                  navigate('/graph')
                }}
                disabled={!onCanvas}
                className="inline-flex max-w-full items-center gap-1 rounded border border-[var(--border)] bg-[var(--surface-3)] px-1.5 py-0.5 text-2xs text-fg transition-colors hover:border-[var(--accent)] disabled:opacity-45 disabled:hover:border-[var(--border)]"
              >
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: typeFill(node.type) }}
                  aria-hidden
                />
                <span className="truncate">{node.name}</span>
              </button>
            )
          })}
        </div>
      ) : null}

      {relationships.length > 0 ? (
        <ul className="space-y-0.5">
          {relationships.map((rel, index) => (
            <li
              key={`${rel.source}-${rel.relationship}-${rel.target}-${index}`}
              className="flex flex-wrap items-center gap-1 text-2xs text-fg-muted"
              title={rel.description}
            >
              <span className="text-fg">{rel.source_name}</span>
              <span className="rounded bg-[var(--surface-3)] px-1 font-mono text-[10px]">
                {rel.relationship}
              </span>
              <ArrowRight className="h-2.5 w-2.5 text-fg-subtle" />
              <span className="text-fg">{rel.target_name}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
