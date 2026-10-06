import { useState } from 'react'
import { Check, ChevronRight, Copy, Database } from 'lucide-react'
import { Button } from '@/components/ui/button'

/**
 * Collapsed-by-default view of the Cypher that produced the lineage graph.
 *
 * Shown so the query can be copied into Neo4j Browser and compared against the
 * rendered graph — the two are expected to match exactly.
 */
export function GeneratedCypherSection({
  cypher,
  parameters,
}: {
  cypher?: string
  parameters?: Record<string, unknown>
}) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  if (!cypher) return null

  const entries = Object.entries(parameters ?? {})

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(cypher)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="shrink-0 border-t border-[var(--border)]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-4 py-2 text-2xs font-semibold uppercase tracking-wide text-fg-subtle transition-colors hover:text-fg-muted"
      >
        <ChevronRight
          className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-90' : ''}`}
        />
        <Database className="h-3.5 w-3.5 text-accent" />
        Generated Cypher
      </button>

      {open && (
        <div className="max-h-64 overflow-auto px-4 pb-3">
          <div className="mb-2 flex justify-end">
            <Button variant="ghost" size="sm" onClick={copy} aria-label="Copy Cypher">
              {copied ? (
                <>
                  <Check className="mr-1 h-3 w-3" /> Copied
                </>
              ) : (
                <>
                  <Copy className="mr-1 h-3 w-3" /> Copy
                </>
              )}
            </Button>
          </div>

          <pre className="overflow-x-auto rounded border border-[var(--border)] bg-[var(--surface-muted,#0f172a0d)] p-3 font-mono text-2xs leading-relaxed text-fg-muted">
            {cypher}
          </pre>

          {entries.length > 0 && (
            <div className="mt-3">
              <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
                Parameters
              </p>
              <dl className="space-y-0.5 font-mono text-2xs text-fg-muted">
                {entries.map(([key, value]) => (
                  <div key={key} className="flex gap-2">
                    <dt className="text-fg-subtle">{key}:</dt>
                    <dd className="min-w-0 break-all">{JSON.stringify(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
