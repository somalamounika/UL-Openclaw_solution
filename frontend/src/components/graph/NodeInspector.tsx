import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Crosshair, MessagesSquare, Plus, X } from 'lucide-react'
import { useGraphStore } from '@/state/graphStore'
import { useChatStore } from '@/state/chatStore'
import { canvas } from '@/lib/canvasApi'
import { humanizeRelationship, NODE_INK, typeDisplay, typeFill } from '@/lib/ontology'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { CitationRow } from '@/components/assistant/DocumentEvidence'

const NODE_PROPERTY_ORDER = [
  '<id>',
  'entity_type',
  'canonical_id',
  'name',
  'normalized_name',
  'aliases',
  'description',
  'code',
  'created_at',
  'updated_at',
  'graph_id',
]

const EDGE_PROPERTY_ORDER = [
  '<id>',
  'relationship',
  'description',
  'keywords',
  'document_ids',
  'created_at',
  'graph_id',
]

/**
 * Fills the right rail whenever something is selected — Neo4j Browser style:
 * type badge, name/triple, then a Key/Value property table.
 */
export function NodeInspector() {
  const navigate = useNavigate()
  const model = useGraphStore((s) => s.model)
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId)
  const selectedEdgeId = useGraphStore((s) => s.selectedEdgeId)
  const visible = useGraphStore((s) => s.visible)
  const nodeFocus = useGraphStore((s) => s.nodeFocus)
  const selectNode = useGraphStore((s) => s.selectNode)
  const selectEdge = useGraphStore((s) => s.selectEdge)
  const expandNode = useGraphStore((s) => s.expandNode)
  const expandIds = useGraphStore((s) => s.expandIds)
  const focusNode = useGraphStore((s) => s.focusNode)
  const exitFocus = useGraphStore((s) => s.exitFocus)
  const addMention = useChatStore((s) => s.addMention)

  const node = selectedNodeId ? model.nodeById.get(selectedNodeId) ?? null : null
  const edge = selectedEdgeId ? model.edgeById.get(selectedEdgeId) ?? null : null

  const neighboursByType = useMemo(() => {
    if (!node) return []
    const grouped = new Map<string, Array<{ id: string; name: string }>>()
    for (const neighbourId of model.adjacency.get(node.id) ?? []) {
      const neighbour = model.nodeById.get(neighbourId)
      if (!neighbour) continue
      const entry = { id: neighbour.id, name: neighbour.name }
      const bucket = grouped.get(neighbour.label)
      if (bucket) bucket.push(entry)
      else grouped.set(neighbour.label, [entry])
    }
    return [...grouped.entries()].sort((a, b) => b[1].length - a[1].length)
  }, [node, model])

  const nodeRows = useMemo(
    () => (node ? orderedPropertyRows(node.properties, NODE_PROPERTY_ORDER) : []),
    [node],
  )
  const edgeRows = useMemo(
    () => (edge ? orderedPropertyRows(edge.properties, EDGE_PROPERTY_ORDER) : []),
    [edge],
  )

  if (!node && !edge) return null

  return (
    <motion.aside
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
      className="flex h-full min-h-0 w-full flex-col bg-[var(--surface)]"
      aria-label={node ? 'Node details' : 'Relationship details'}
    >
      <div className="flex items-start justify-between gap-2 border-b border-[var(--border)] p-3.5">
        {node ? (
          <div className="min-w-0">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-fg-subtle">
              Node details
            </div>
            <span
              className="mt-1.5 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
              style={{ backgroundColor: typeFill(node.label), color: NODE_INK }}
            >
              {typeDisplay(node.label)}
            </span>
            <h2 className="mt-1.5 break-words text-sm font-semibold text-fg">{node.name}</h2>
          </div>
        ) : edge ? (
          <div className="min-w-0">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-fg-subtle">
              Relationship details
            </div>
            <span className="mt-1.5 inline-flex items-center rounded-full bg-[var(--surface-3)] px-2 py-0.5 font-mono text-[10px] font-semibold text-fg-muted">
              {edge.relationship}
            </span>
            <h2 className="mt-1.5 flex flex-wrap items-center gap-1 text-xs text-fg">
              <span className="font-medium">{edge.sourceName}</span>
              <ArrowRight className="h-3 w-3 text-fg-subtle" />
              <span className="font-medium">{edge.targetName}</span>
            </h2>
            <p className="mt-0.5 text-[10px] text-fg-subtle">
              {humanizeRelationship(edge.relationship)}
            </p>
          </div>
        ) : null}

        <Button
          variant="ghost"
          size="icon"
          aria-label="Close inspector"
          onClick={() => {
            selectNode(null)
            selectEdge(null)
          }}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-4 p-3.5">
          {node ? (
            <>
              {nodeRows.length > 0 ? (
                <Section title="Properties">
                  <PropertyTable rows={nodeRows} />
                </Section>
              ) : node.description ? (
                <Section title="Description">
                  <p className="evidence-quote">{node.description}</p>
                </Section>
              ) : null}

              <Section title={`Connected entities (${node.degree})`}>
                <div className="space-y-2">
                  {neighboursByType.map(([label, neighbours]) => (
                    <div key={label}>
                      <div className="mb-1 flex items-center gap-1.5 text-2xs text-fg-muted">
                        <span
                          className="h-2 w-2 rounded-full"
                          style={{ backgroundColor: typeFill(label) }}
                          aria-hidden
                        />
                        {typeDisplay(label)}
                        <span className="text-fg-subtle">({neighbours.length})</span>
                      </div>
                      <div className="space-y-0.5">
                        {neighbours.map((neighbour) => (
                          <button
                            key={neighbour.id}
                            type="button"
                            onClick={() => {
                              expandIds([neighbour.id])
                              selectNode(neighbour.id)
                              canvas()?.centerOn(neighbour.id)
                            }}
                            className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-xs text-fg-muted hover:bg-[var(--surface-3)] hover:text-fg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)]"
                          >
                            <span className="min-w-0 flex-1 truncate">{neighbour.name}</span>
                            {!visible.has(neighbour.id) ? (
                              <span className="shrink-0 text-[10px] text-fg-subtle">hidden</span>
                            ) : null}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </Section>

              {node.sourceFiles.length > 0 ? (
                <Section title="Sources">
                  <div className="space-y-1">
                    {node.sourceFiles.map((file) => (
                      <CitationRow key={file} file={file} />
                    ))}
                  </div>
                </Section>
              ) : null}
            </>
          ) : edge ? (
            <>
              {edgeRows.length > 0 ? (
                <Section title="Properties">
                  <PropertyTable rows={edgeRows} />
                </Section>
              ) : edge.description ? (
                <Section title="Evidence">
                  <blockquote className="evidence-quote">{edge.description}</blockquote>
                </Section>
              ) : null}

              {edge.sourceFiles.length > 0 ? (
                <Section title="Sources">
                  <div className="space-y-1">
                    {edge.sourceFiles.map((file, index) => (
                      <CitationRow
                        key={`${file}-${index}`}
                        file={file}
                        pageLabel={edge.pageRanges[index] ? `pp. ${edge.pageRanges[index]}` : undefined}
                        chunkId={edge.chunkIds[index]}
                      />
                    ))}
                  </div>
                </Section>
              ) : null}
            </>
          ) : null}
        </div>
      </ScrollArea>

      {node ? (
        <div className="flex flex-wrap gap-1.5 border-t border-[var(--border)] p-2.5">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              const cy = canvas()?.cy()
              focusNode(node.id, cy ? { zoom: cy.zoom(), pan: { ...cy.pan() } } : null)
            }}
          >
            <Crosshair className="h-3 w-3" />
            Focus node
          </Button>
          <Button variant="secondary" size="sm" onClick={() => expandNode(node.id, 1)}>
            <Plus className="h-3 w-3" />
            Expand
          </Button>
          {nodeFocus ? (
            <Button variant="ghost" size="sm" onClick={exitFocus}>
              Exit focus
            </Button>
          ) : null}
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              addMention(node.name)
              navigate('/assistant')
            }}
          >
            <MessagesSquare className="h-3 w-3" />
            Ask Assistant
          </Button>
        </div>
      ) : null}
    </motion.aside>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-fg-subtle">
        {title}
      </h3>
      {children}
    </section>
  )
}

function PropertyTable({ rows }: { rows: Array<{ key: string; value: string }> }) {
  return (
    <div className="overflow-hidden rounded-md border border-[var(--border)]">
      <table className="w-full border-collapse text-left text-2xs">
        <thead>
          <tr className="bg-[var(--surface-2)] text-fg-subtle">
            <th className="px-2 py-1.5 font-semibold">Key</th>
            <th className="px-2 py-1.5 font-semibold">Value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-t border-[var(--border)] align-top">
              <td className="whitespace-nowrap px-2 py-1.5 font-mono text-fg-muted">{row.key}</td>
              <td className="break-words px-2 py-1.5 text-fg">{row.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function orderedPropertyRows(
  properties: Record<string, unknown>,
  preferred: string[],
): Array<{ key: string; value: string }> {
  const keys = Object.keys(properties)
  if (!keys.length) return []
  const preferredSet = new Set(preferred)
  const ordered = [
    ...preferred.filter((key) => key in properties),
    ...keys.filter((key) => !preferredSet.has(key)).sort((a, b) => a.localeCompare(b)),
  ]
  return ordered
    .map((key) => ({ key, value: formatPropertyValue(properties[key]) }))
    .filter((row) => row.value.length > 0)
}

function formatPropertyValue(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}
