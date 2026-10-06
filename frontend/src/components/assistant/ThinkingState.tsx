import { Loader2 } from 'lucide-react'
import type { ThinkingPhase } from '@/state/chatStore'
import { cn } from '@/lib/utils'

const PHASES: Array<{ key: ThinkingPhase; label: string }> = [
  { key: 'graph', label: 'Retrieving graph context' },
  { key: 'documents', label: 'Retrieving document chunks' },
  { key: 'composing', label: 'Composing a grounded answer' },
]

/**
 * The chat endpoint is not streamed, so there are no tokens to type out. This names
 * the phases the backend genuinely runs through instead of faking a typewriter.
 */
export function ThinkingState({ phase }: { phase: ThinkingPhase }) {
  const activeIndex = PHASES.findIndex((p) => p.key === phase)

  return (
    <div className="card p-2.5" aria-live="polite">
      <div className="space-y-1">
        {PHASES.map((entry, index) => (
          <div key={entry.key} className="flex items-center gap-2 text-2xs">
            {index === activeIndex ? (
              <Loader2 className="h-3 w-3 animate-spin text-accent" />
            ) : (
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{
                  backgroundColor: index < activeIndex ? 'var(--success)' : 'var(--border-strong)',
                  marginLeft: 3,
                  marginRight: 3,
                }}
              />
            )}
            <span className={cn(index === activeIndex ? 'text-fg' : 'text-fg-subtle')}>
              {entry.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
