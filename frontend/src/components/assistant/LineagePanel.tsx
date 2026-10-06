import { GitBranch, X } from 'lucide-react'
import { useChatStore } from '@/state/chatStore'
import { LineageGraphCanvas } from './LineageGraphCanvas'
import { GeneratedCypherSection } from './GeneratedCypherSection'
import { Button } from '@/components/ui/button'

/**
 * Left popup panel for answer-specific knowledge-graph lineage.
 * Opened only when the user clicks "View lineage".
 */
export function LineagePanel() {
  const panel = useChatStore((s) => s.lineagePanel)
  const closeLineagePanel = useChatStore((s) => s.closeLineagePanel)

  if (!panel) return null

  const { question, response } = panel
  const { nodes, relationships } = response.graph_evidence

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--surface)]">
      <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[var(--border)] px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
            <GitBranch className="h-3.5 w-3.5 text-accent" />
            Supporting lineage
          </div>
          <p className="mt-1 truncate text-xs text-fg-muted" title={question}>
            {question}
          </p>
          <p className="mt-0.5 text-2xs text-fg-subtle">
            {nodes.length} nodes · {relationships.length} relationships
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={closeLineagePanel} aria-label="Close lineage">
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="min-h-0 flex-1">
        <LineageGraphCanvas nodes={nodes} relationships={relationships} />
      </div>

      <GeneratedCypherSection
        cypher={response.generated_cypher}
        parameters={response.query_parameters}
      />
    </div>
  )
}
