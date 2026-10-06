import { useState } from 'react'
import { AlertTriangle, Check, ExternalLink, Loader2, Network, Trash2 } from 'lucide-react'
import { useDeleteGraph, useGraphList } from '@/hooks/useGraphData'
import { ApiError } from '@/api/client'
import { relativeTime, cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { useGraphStore } from '@/state/graphStore'
import { useNavigate } from 'react-router-dom'

export function SavedGraphs() {
  const selectedGraphId = useGraphStore((s) => s.graphId)
  const setGraphId = useGraphStore((s) => s.setGraphId)
  const navigate = useNavigate()

  const { data, isError, isSuccess, error } = useGraphList()
  const remove = useDeleteGraph()
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)

  if (isError) {
    // The detail names the real cause — usually Neo4j being unreachable, which is a
    // different problem from the API being down and deserves different wording.
    const detail = error instanceof ApiError ? error.detail : ''
    return (
      <div className="rounded-md border border-[var(--danger)]/40 bg-[var(--danger)]/10 p-2">
        <div className="flex items-center gap-1.5 text-2xs font-semibold text-[var(--danger)]">
          <AlertTriangle className="h-3 w-3" />
          Could not read the graph store
        </div>
        {detail ? (
          <p className="mt-1 whitespace-pre-wrap break-words font-mono text-[10px] leading-snug text-fg-muted">
            {detail}
          </p>
        ) : null}
        <p className="mt-1 text-2xs leading-snug text-fg-muted">
          Graphs live in Neo4j. Start it and check NEO4J_URI / NEO4J_PASSWORD in the backend
          environment — this list refreshes on its own.
        </p>
      </div>
    )
  }

  const graphs = data?.graphs ?? []

  // "No data yet" is not the same as "no graphs". Only claim the store is empty once
  // the request has actually succeeded.
  if (!isSuccess) {
    return (
      <div className="flex items-center gap-2 px-1 py-2 text-2xs text-fg-subtle">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading saved graphs…
      </div>
    )
  }

  if (graphs.length === 0) {
    return (
      <p className="px-1 py-2 text-2xs leading-snug text-fg-subtle">
        No graphs yet. Upload PDFs above and build your first one.
      </p>
    )
  }

  return (
    <>
      <ul className="space-y-1">
        {graphs.map((graph) => {
          const isSelected = graph.graph_id === selectedGraphId
          return (
            <li key={graph.graph_id}>
              <div
                className={cn(
                  'group flex items-start gap-2 rounded-md border p-2 transition-colors',
                  isSelected
                    ? 'border-[var(--accent)] bg-[var(--accent-soft)]'
                    : 'border-[var(--border)] bg-[var(--surface-2)] hover:border-[var(--border-strong)]',
                )}
              >
                <button
                  type="button"
                  onClick={() => setGraphId(graph.graph_id)}
                  className="min-w-0 flex-1 text-left focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)]"
                >
                  <div className="flex items-center gap-1.5">
                    {isSelected ? (
                      <Check className="h-3 w-3 shrink-0 text-accent" />
                    ) : (
                      <Network className="h-3 w-3 shrink-0 text-fg-subtle" />
                    )}
                    <span className="truncate font-mono text-2xs text-fg">{graph.graph_id}</span>
                  </div>
                  <div className="mt-0.5 truncate text-2xs text-fg-subtle">
                    {graph.source_files.length ? graph.source_files.join(', ') : 'no source files recorded'}
                  </div>
                  <div className="text-[10px] text-fg-subtle">{relativeTime(graph.created_at)}</div>
                </button>
                <button
                  type="button"
                  title="Open in the Knowledge Graph"
                  aria-label={`Open graph ${graph.graph_id}`}
                  onClick={() => {
                    setGraphId(graph.graph_id)
                    navigate('/graph')
                  }}
                  className="rounded p-0.5 text-fg-subtle opacity-0 transition-opacity hover:text-accent focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)] group-hover:opacity-100"
                >
                  <ExternalLink className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  onClick={() => setPendingDelete(graph.graph_id)}
                  aria-label={`Delete graph ${graph.graph_id}`}
                  className="rounded p-0.5 text-fg-subtle opacity-0 transition-opacity hover:text-[var(--danger)] focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)] group-hover:opacity-100"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            </li>
          )
        })}
      </ul>

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title="Delete this knowledge graph?"
        description={`${pendingDelete ?? ''} will be removed. The global UL canonical graph is not deleted. Deleting a document removes only that document's evidence. This cannot be undone.`}
      >
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={() => setPendingDelete(null)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            size="sm"
            disabled={remove.isPending}
            onClick={() => {
              if (!pendingDelete) return
              remove.mutate(pendingDelete, {
                onSuccess: () => {
                  // Never leave the app pointing at a graph that no longer exists.
                  if (pendingDelete === selectedGraphId) setGraphId(null)
                },
                onSettled: () => setPendingDelete(null),
              })
            }}
          >
            {remove.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
            Delete graph
          </Button>
        </div>
      </Dialog>
    </>
  )
}
