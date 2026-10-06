import { useMemo, useState } from 'react'
import { Search, X } from 'lucide-react'
import { useGraphStore } from '@/state/graphStore'
import { canvas } from '@/lib/canvasApi'
import { ONTOLOGY, NODE_INK, typeFill } from '@/lib/ontology'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

const MAX_MATCHES = 40

/**
 * The right rail when nothing is selected — Neo4j's Result Overview.
 *
 * Every chip is also the type filter, which is why the count reads `drawn/total`: with
 * progressive disclosure the graph nearly always holds more of a type than the canvas
 * is currently drawing, and a bare total would look like a bug.
 */
export function ResultOverview() {
  const [query, setQuery] = useState('')

  const model = useGraphStore((s) => s.model)
  const visible = useGraphStore((s) => s.visible)
  const hiddenTypes = useGraphStore((s) => s.hiddenTypes)
  const toggleType = useGraphStore((s) => s.toggleType)
  const showAllTypes = useGraphStore((s) => s.showAllTypes)
  const selectNode = useGraphStore((s) => s.selectNode)
  const expandIds = useGraphStore((s) => s.expandIds)
  const setHovered = useGraphStore((s) => s.setHovered)

  const nodeRows = useMemo(() => {
    const drawnByType = new Map<string, number>()
    for (const id of visible) {
      const node = model.nodeById.get(id)
      if (!node) continue
      drawnByType.set(node.label, (drawnByType.get(node.label) ?? 0) + 1)
    }
    // Only types actually present in this graph — an exhaustive 26-row key is noise.
    const order = new Map(ONTOLOGY.map((t, index) => [t.label, index]))
    return [...model.typeCounts.entries()]
      .sort((a, b) => (order.get(a[0]) ?? 99) - (order.get(b[0]) ?? 99))
      .map(([label, total]) => ({ label, total, drawn: drawnByType.get(label) ?? 0 }))
  }, [model, visible])

  const relRows = useMemo(() => {
    const counts = new Map<string, number>()
    for (const edge of model.edges) {
      counts.set(edge.relationship, (counts.get(edge.relationship) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [model])

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (q.length < 2) return []
    const found: Array<{ id: string; name: string; label: string }> = []
    for (const node of model.nodes) {
      if (
        node.name.toLowerCase().includes(q) ||
        node.label.toLowerCase().includes(q) ||
        node.description.toLowerCase().includes(q)
      ) {
        found.push({ id: node.id, name: node.name, label: node.label })
        if (found.length >= MAX_MATCHES) break
      }
    }
    return found
  }, [query, model])

  /** Reveal the node if progressive disclosure is still hiding it, then fly to it. */
  const revealAndSelect = (id: string) => {
    if (!visible.has(id)) expandIds([id])
    selectNode(id)
    window.setTimeout(() => canvas()?.centerOn(id), 60)
  }

  const searching = query.trim().length >= 2

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--surface)]">
      <div className="shrink-0 px-4 pb-3 pt-4">
        <h2 className="text-lg font-semibold tracking-tight text-fg">Result Overview</h2>
        <div className="relative mt-3">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-subtle" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search On Node Properties"
            aria-label="Search on node properties"
            className={cn(
              'h-9 w-full rounded-full border border-[var(--border)] bg-[var(--surface)] pl-8 pr-8 text-xs text-fg',
              'placeholder:text-fg-subtle focus-visible:border-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
            )}
          />
          {query ? (
            <button
              type="button"
              onClick={() => setQuery('')}
              aria-label="Clear search"
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-fg-subtle hover:text-fg"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-5 px-4 pb-6">
          {searching ? (
            <section>
              <SectionLabel>
                Matches ({matches.length}
                {matches.length === MAX_MATCHES ? '+' : ''})
              </SectionLabel>
              {matches.length === 0 ? (
                <p className="mt-2 text-xs text-fg-muted">Nothing matches “{query.trim()}”.</p>
              ) : (
                <ul className="mt-2 space-y-0.5">
                  {matches.map((match) => (
                    <li key={match.id}>
                      <button
                        type="button"
                        onClick={() => revealAndSelect(match.id)}
                        onMouseEnter={() => setHovered(match.id)}
                        onMouseLeave={() => setHovered(null)}
                        className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-[var(--surface-3)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)]"
                      >
                        <span
                          className="h-2.5 w-2.5 shrink-0 rounded-full"
                          style={{ backgroundColor: typeFill(match.label) }}
                          aria-hidden
                        />
                        <span className="min-w-0 flex-1 truncate text-xs text-fg">{match.name}</span>
                        <span className="shrink-0 text-2xs text-fg-subtle">{match.label}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ) : null}

          <section>
            <div className="flex items-baseline justify-between">
              <SectionLabel>Total Nodes ({model.reportedNodeCount || model.nodes.length})</SectionLabel>
              {hiddenTypes.size > 0 ? (
                <button
                  type="button"
                  onClick={showAllTypes}
                  className="text-2xs text-accent hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)]"
                >
                  reset filters
                </button>
              ) : null}
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {nodeRows.map((row) => {
                const filtered = hiddenTypes.has(row.label)
                return (
                  <button
                    key={row.label}
                    type="button"
                    onClick={() => toggleType(row.label)}
                    aria-pressed={!filtered}
                    title={`${row.drawn} of ${row.total} drawn · click to ${filtered ? 'show' : 'hide'}`}
                    className={cn(
                      'rounded-full px-2.5 py-1 text-[11px] font-medium transition-opacity',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                      filtered ? 'opacity-35' : 'hover:opacity-85',
                    )}
                    style={{ backgroundColor: typeFill(row.label), color: NODE_INK }}
                  >
                    {row.label} ({row.total})
                  </button>
                )
              })}
            </div>
            <p className="mt-2 text-2xs text-fg-subtle">
              {visible.size} drawn on the canvas · double-click a node to isolate it
            </p>
          </section>

          {relRows.length > 0 ? (
            <section>
              <SectionLabel>
                Total Relationships ({model.reportedRelationshipCount || model.edges.length})
              </SectionLabel>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {relRows.map(([relationship, count]) => (
                  <span
                    key={relationship}
                    className="rounded-full border border-[var(--border)] bg-[var(--surface-3)] px-2.5 py-1 font-mono text-[10px] text-fg-muted"
                  >
                    {relationship} ({count})
                  </span>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      </ScrollArea>
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <span className="text-2xs font-semibold text-fg-muted">{children}</span>
}
