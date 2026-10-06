import { useEffect, useMemo, useRef, useState } from 'react'
import { Search } from 'lucide-react'
import * as RadixDialog from '@radix-ui/react-dialog'
import { useGraphStore } from '@/state/graphStore'
import { canvas } from '@/lib/canvasApi'
import { fuzzyScore } from '@/lib/utils'
import { typeDisplay, typeFill } from '@/lib/ontology'
import { cn } from '@/lib/utils'

const MAX_RESULTS = 40

/** Fuzzy over name + type + description, grouped by type, arrow-navigable. */
export function GraphSearch() {
  const open = useGraphStore((s) => s.searchOpen)
  const setOpen = useGraphStore((s) => s.setSearchOpen)
  const model = useGraphStore((s) => s.model)
  const selectNode = useGraphStore((s) => s.selectNode)
  const expandIds = useGraphStore((s) => s.expandIds)
  const setHovered = useGraphStore((s) => s.setHovered)

  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const listRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (open) {
      setQuery('')
      setActive(0)
    }
  }, [open])

  const results = useMemo(() => {
    if (!query.trim()) {
      return [...model.nodes].sort((a, b) => b.degree - a.degree).slice(0, MAX_RESULTS)
    }
    const scored = model.nodes
      .map((node) => {
        const name = fuzzyScore(query, node.name)
        const type = fuzzyScore(query, node.label) * 0.4
        const description = node.description ? fuzzyScore(query, node.description) * 0.2 : -1
        const best = Math.max(name, type, description)
        return { node, score: best }
      })
      .filter((entry) => entry.score > 0)
    scored.sort((a, b) => b.score - a.score)
    return scored.slice(0, MAX_RESULTS).map((entry) => entry.node)
  }, [query, model])

  const grouped = useMemo(() => {
    const groups = new Map<string, typeof results>()
    for (const node of results) {
      const bucket = groups.get(node.label)
      if (bucket) bucket.push(node)
      else groups.set(node.label, [node])
    }
    return [...groups.entries()]
  }, [results])

  const flat = useMemo(() => grouped.flatMap(([, nodes]) => nodes), [grouped])

  useEffect(() => {
    if (!open || !flat[active]) return
    setHovered(flat[active].id)
    return () => setHovered(null)
  }, [open, active, flat, setHovered])

  const commit = (index: number) => {
    const node = flat[index]
    if (!node) return
    // A match may be inside a collapsed branch, or outside the current focus; reveal it
    // before flying there so the camera never lands on a node that is not drawn.
    if (useGraphStore.getState().nodeFocus) useGraphStore.getState().exitFocus()
    expandIds([node.id])
    selectNode(node.id)
    setOpen(false)
    window.setTimeout(() => canvas()?.centerOn(node.id, 1.4), 30)
  }

  return (
    <RadixDialog.Root open={open} onOpenChange={setOpen}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-[90] bg-black/45 backdrop-blur-[2px] animate-fade-in" />
        <RadixDialog.Content
          className="fixed left-1/2 top-[16%] z-[91] w-full max-w-xl -translate-x-1/2 overflow-hidden rounded-xl border border-[var(--border-strong)] bg-[var(--surface)] shadow-2xl animate-fade-in"
          onOpenAutoFocus={(event) => event.preventDefault()}
        >
          <RadixDialog.Title className="sr-only">Search the graph</RadixDialog.Title>
          <div className="flex items-center gap-2 border-b border-[var(--border)] px-3">
            <Search className="h-4 w-4 shrink-0 text-fg-subtle" />
            <input
              autoFocus
              value={query}
              onChange={(event) => {
                setQuery(event.target.value)
                setActive(0)
              }}
              onKeyDown={(event) => {
                if (event.key === 'ArrowDown') {
                  event.preventDefault()
                  setActive((i) => Math.min(i + 1, flat.length - 1))
                } else if (event.key === 'ArrowUp') {
                  event.preventDefault()
                  setActive((i) => Math.max(i - 1, 0))
                } else if (event.key === 'Enter') {
                  event.preventDefault()
                  commit(active)
                }
              }}
              placeholder="Search nodes by name, type or description…"
              className="h-11 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-fg-subtle"
              aria-label="Search the graph"
              role="combobox"
              aria-expanded
              aria-controls="graph-search-results"
            />
            <kbd className="rounded border border-[var(--border)] px-1.5 py-0.5 text-2xs text-fg-subtle">esc</kbd>
          </div>

          <div id="graph-search-results" ref={listRef} className="max-h-[52vh] overflow-y-auto p-1.5" role="listbox">
            {flat.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-fg-muted">
                No node matches “{query}”.
              </div>
            ) : (
              grouped.map(([label, nodes]) => (
                <div key={label} className="mb-1">
                  <div className="flex items-center gap-1.5 px-2 py-1 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{ backgroundColor: typeFill(label) }}
                      aria-hidden
                    />
                    {typeDisplay(label)}
                    <span className="font-normal normal-case tracking-normal">({nodes.length})</span>
                  </div>
                  {nodes.map((node) => {
                    const index = flat.indexOf(node)
                    return (
                      <button
                        key={node.id}
                        type="button"
                        role="option"
                        aria-selected={index === active}
                        onMouseEnter={() => setActive(index)}
                        onClick={() => commit(index)}
                        className={cn(
                          'flex w-full items-baseline gap-2 rounded px-2 py-1.5 text-left',
                          index === active ? 'bg-[var(--surface-3)]' : 'hover:bg-[var(--surface-3)]',
                        )}
                      >
                        <span className="truncate text-xs text-fg">{node.name}</span>
                        {node.description ? (
                          <span className="min-w-0 flex-1 truncate text-2xs text-fg-subtle">
                            {node.description}
                          </span>
                        ) : null}
                        <span className="shrink-0 text-2xs tabular-nums text-fg-subtle">
                          {node.degree} edges
                        </span>
                      </button>
                    )
                  })}
                </div>
              ))
            )}
          </div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  )
}
