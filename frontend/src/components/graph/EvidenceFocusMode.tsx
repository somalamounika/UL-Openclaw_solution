import { Sparkles, X } from 'lucide-react'
import { useGraphStore } from '@/state/graphStore'
import { Button } from '@/components/ui/button'
import { truncate } from '@/lib/lod'

export function EvidenceFocusMode() {
  const evidence = useGraphStore((s) => s.evidence)
  const setEvidence = useGraphStore((s) => s.setEvidence)

  if (!evidence) return null

  return (
    <div className="pointer-events-auto absolute inset-x-0 top-0 z-30 flex justify-center p-3">
      <div className="canvas-overlay-panel flex max-w-3xl items-center gap-3 px-3 py-2">
        <Sparkles className="h-4 w-4 shrink-0 text-accent" />
        <div className="min-w-0 text-xs">
          <div className="truncate text-fg">
            Showing evidence for:{' '}
            <span className="font-medium">“{truncate(evidence.question, 90)}”</span>
          </div>
          <div className="mt-0.5 text-2xs text-fg-muted">
            Highlighting the answer lineage on this graph
            {evidence.nodeIds.length > 0 ? (
              <>
                {' '}
                · <span className="tabular-nums">{evidence.nodeIds.length}</span> nodes
                {evidence.edgeKeys.length > 0 ? (
                  <>
                    {' '}
                    · <span className="tabular-nums">{evidence.edgeKeys.length}</span> relationships
                  </>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
        <Button variant="secondary" size="sm" className="ml-2 shrink-0" onClick={() => setEvidence(null)}>
          <X className="h-3 w-3" />
          Exit evidence view
        </Button>
      </div>
    </div>
  )
}
