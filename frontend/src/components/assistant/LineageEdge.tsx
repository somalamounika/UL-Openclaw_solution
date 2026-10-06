import { ArrowDown } from 'lucide-react'
import { humanizeRelationship } from '@/lib/ontology'
import { Tooltip } from '@/components/ui/tooltip'

/**
 * The hop between two lineage nodes. The relationship name is the backend's own
 * SCREAMING_SNAKE type, shown humanized with the raw type available on hover — the
 * label is never guessed or prettified into something the graph does not contain.
 */
export function LineageEdge({
  relationship,
  description,
}: {
  relationship: string
  description?: string
}) {
  return (
    <div className="flex items-center gap-2 py-1 pl-4">
      <div className="flex h-8 w-4 items-center justify-center">
        <div className="h-full w-px bg-[var(--border-strong)]" />
      </div>
      <Tooltip content={description || relationship}>
        <span className="flex items-center gap-1 rounded bg-[var(--surface-3)] px-1.5 py-0.5 text-[10px] font-medium text-fg-muted">
          <ArrowDown className="h-2.5 w-2.5 text-fg-subtle" />
          {humanizeRelationship(relationship)}
        </span>
      </Tooltip>
    </div>
  )
}
