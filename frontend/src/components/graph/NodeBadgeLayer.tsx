import { useMemo } from 'react'
import type { Core } from 'cytoscape'
import { useGraphStore } from '@/state/graphStore'
import { hiddenNeighbourCount } from '@/lib/adjacency'
import { canvas } from '@/lib/canvasApi'
import { LOD_SPECS, type Lod } from '@/lib/lod'
import type { GraphModel } from '@/types/graph'

/**
 * The `+n` ring badges. HTML rather than Cytoscape labels because they must be
 * clickable and readable at any zoom. Only badges for on-screen nodes are rendered,
 * and the count is capped, so this stays cheap even at the 2000-node ceiling.
 */

const MAX_BADGES = 160

interface Props {
  cy: Core | null
  /** Bumped on every viewport change so positions re-read. */
  version: number
  lod: Lod
}

/**
 * At L0, tiers 4+ are gated off by the stylesheet. Each hidden node is attributed to
 * its nearest visible tier 1–3 ancestor so the overview shows where the mass sits.
 */
function collapsedCountsByAncestor(model: GraphModel, visible: ReadonlySet<string>, maxTier: number) {
  const counts = new Map<string, number>()
  const owner = new Map<string, string>()
  let frontier: string[] = []

  for (const node of model.nodes) {
    if (visible.has(node.id) && node.tier <= maxTier) {
      owner.set(node.id, node.id)
      frontier.push(node.id)
    }
  }

  while (frontier.length) {
    const next: string[] = []
    for (const id of frontier) {
      const root = owner.get(id)!
      for (const neighbourId of model.adjacency.get(id) ?? []) {
        if (owner.has(neighbourId)) continue
        const neighbour = model.nodeById.get(neighbourId)
        if (!neighbour || neighbour.tier <= maxTier) continue
        owner.set(neighbourId, root)
        counts.set(root, (counts.get(root) ?? 0) + 1)
        next.push(neighbourId)
      }
    }
    frontier = next
  }

  return { counts, owner }
}

export function NodeBadgeLayer({ cy, version, lod }: Props) {
  const model = useGraphStore((s) => s.model)
  const visible = useGraphStore((s) => s.visible)
  const expandNode = useGraphStore((s) => s.expandNode)
  const expandIds = useGraphStore((s) => s.expandIds)

  const overview = lod === 0
  const collapsed = useMemo(
    () => (overview ? collapsedCountsByAncestor(model, visible, LOD_SPECS[0].maxTier) : null),
    [overview, model, visible],
  )

  const badges = useMemo(() => {
    if (!cy) return []
    const width = cy.width()
    const height = cy.height()
    const items: Array<{ id: string; x: number; y: number; count: number; radius: number }> = []

    cy.nodes(':visible').forEach((node) => {
      if (items.length >= MAX_BADGES) return
      const count = overview
        ? (collapsed?.counts.get(node.id()) ?? 0)
        : hiddenNeighbourCount(model, node.id(), visible)
      if (count <= 0) return

      const position = node.renderedPosition()
      // Cull off-screen badges — there is no point paying DOM cost for them.
      if (position.x < -40 || position.y < -40 || position.x > width + 40 || position.y > height + 40) return
      items.push({
        id: node.id(),
        x: position.x,
        y: position.y,
        count,
        radius: (node.renderedOuterWidth() || 0) / 2,
      })
    })
    return items
    // `version` is the viewport tick: positions must be re-read after pan/zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cy, version, model, visible, overview, collapsed])

  if (!cy || badges.length === 0) return null

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {badges.map((badge) => (
        <button
          key={badge.id}
          type="button"
          className="pointer-events-auto absolute -translate-x-1/2 -translate-y-1/2 rounded-full border border-[var(--border-strong)] bg-[var(--surface)] px-1.5 py-px text-[10px] font-semibold tabular-nums text-fg-muted shadow-sm transition-colors hover:border-[var(--accent)] hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          style={{ left: badge.x + badge.radius * 0.72, top: badge.y - badge.radius * 0.72 }}
          title={
            overview
              ? `${badge.count} deeper nodes collapsed — click to zoom to this subtree`
              : `${badge.count} hidden neighbours — click to expand`
          }
          onClick={(event) => {
            event.stopPropagation()
            if (overview && collapsed) {
              // A collapsed count is navigable, not decorative: zoom to fit its subtree.
              const subtree = [badge.id]
              for (const [nodeId, root] of collapsed.owner) {
                if (root === badge.id) subtree.push(nodeId)
              }
              expandIds(subtree)
              canvas()?.fitTo(subtree, 80)
            } else {
              expandNode(badge.id, 1)
            }
          }}
        >
          +{badge.count}
        </button>
      ))}
    </div>
  )
}
