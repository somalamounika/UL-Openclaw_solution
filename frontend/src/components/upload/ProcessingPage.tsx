import { motion } from 'framer-motion'
import { formatElapsed, useProcessingProgress } from '@/hooks/useProcessingProgress'
import { useProcessingStore } from '@/state/processingStore'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/**
 * The dedicated wait.
 *
 * One number, one line of plain language, and the time it has taken so far. No pipeline
 * checklist, no service names, no duration promises the run cannot keep — the user is
 * told what is being made, not how the machine is making it.
 */
export function ProcessingPage() {
  const { phase, percent, elapsedMs, caption } = useProcessingProgress()
  const stopWatching = useProcessingStore((s) => s.stopWatching)

  const failed = phase === 'error'
  const done = phase === 'done'
  const shown = done ? 100 : Math.floor(percent)

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
      className="mx-auto flex w-full max-w-xl flex-col items-center px-6 py-24 text-center"
    >
      <h1 className="text-lg font-semibold tracking-tight text-fg">
        {failed ? 'Processing stopped' : done ? 'Knowledge graph ready' : 'Building your knowledge graph'}
      </h1>

      <div className="mt-10 flex items-baseline gap-1">
        <span
          className={cn(
            'text-6xl font-semibold tabular-nums tracking-tight',
            failed ? 'text-[var(--danger)]' : done ? 'text-[var(--success)]' : 'text-fg',
          )}
        >
          {shown}
        </span>
        <span className="text-xl text-fg-muted">%</span>
      </div>

      <div className="mt-6 w-full max-w-md">
        <div
          className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-3)]"
          role="progressbar"
          aria-valuenow={shown}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuetext={`${shown} percent — ${caption}`}
        >
          <div
            className={cn(
              'h-full rounded-full',
              failed ? 'bg-[var(--danger)]' : done ? 'bg-[var(--success)]' : 'bg-accent',
            )}
            style={{
              width: `${Math.max(1.5, done ? 100 : percent)}%`,
              transition: 'width 240ms linear, background-color 220ms ease-out',
            }}
          />
        </div>

        <div className="mt-3 flex items-center justify-between text-2xs text-fg-muted">
          <span aria-live="polite">{failed ? 'Run interrupted' : done ? 'Complete' : caption}</span>
          <span className="tabular-nums text-fg-subtle">{formatElapsed(elapsedMs)}</span>
        </div>
      </div>

      {phase === 'running' ? (
        <Button variant="ghost" size="sm" className="mt-12 text-fg-subtle" onClick={stopWatching}>
          Stop waiting
        </Button>
      ) : null}
    </motion.div>
  )
}
