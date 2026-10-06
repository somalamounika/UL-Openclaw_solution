import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import { typeFill, humanizeRelationship } from '@/lib/ontology'
import { layoutLineage } from '@/lib/lineageLayout'
import { DARK_TOKENS, LIGHT_TOKENS } from '@/lib/cytoscapeStyle'
import type { ChatEvidenceNode, ChatEvidenceRelationship } from '@/types/api'

/**
 * Cytoscape paints to a canvas, so it cannot read CSS custom properties — the palette
 * has to be handed over as concrete colours. Track the `dark` class that useTheme puts
 * on <html> so the panel restyles when the theme is toggled underneath it.
 */
function useIsDarkTheme(): boolean {
  const subscribe = useCallback((onStoreChange: () => void) => {
    const observer = new MutationObserver(onStoreChange)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])
  return useSyncExternalStore(
    subscribe,
    () => document.documentElement.classList.contains('dark'),
    () => true,
  )
}

function buildElements(
  nodes: ChatEvidenceNode[],
  relationships: ChatEvidenceRelationship[],
): ElementDefinition[] {
  const elements: ElementDefinition[] = []
  const seen = new Set<string>()
  // Positions only; node and edge identity is untouched.
  const positions = layoutLineage(nodes, relationships)
  const positionOf = (id: string) => positions.get(id) ?? { x: 0, y: 0 }

  for (const node of nodes) {
    if (!node.id || seen.has(node.id)) continue
    seen.add(node.id)
    elements.push({
      group: 'nodes',
      data: {
        id: node.id,
        name: node.name,
        label: node.type,
        color: typeFill(node.type),
      },
      position: positionOf(node.id),
    })
  }

  for (const rel of relationships) {
    if (!seen.has(rel.source)) {
      seen.add(rel.source)
      elements.push({
        group: 'nodes',
        data: {
          id: rel.source,
          name: rel.source_name,
          label: rel.source_type,
          color: typeFill(rel.source_type),
        },
        position: positionOf(rel.source),
      })
    }
    if (!seen.has(rel.target)) {
      seen.add(rel.target)
      elements.push({
        group: 'nodes',
        data: {
          id: rel.target,
          name: rel.target_name,
          label: rel.target_type,
          color: typeFill(rel.target_type),
        },
        position: positionOf(rel.target),
      })
    }
    elements.push({
      group: 'edges',
      data: {
        id: `${rel.source}-${rel.relationship}-${rel.target}`,
        source: rel.source,
        target: rel.target,
        caption: humanizeRelationship(rel.relationship),
      },
    })
  }

  return elements
}

/**
 * Compact Cytoscape view of chat ``graph_evidence``. Renders in-panel — no navigation
 * to the Knowledge Graph page.
 */
export function LineageGraphCanvas({
  nodes,
  relationships,
}: {
  nodes: ChatEvidenceNode[]
  relationships: ChatEvidenceRelationship[]
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cyRef = useRef<Core | null>(null)
  const elements = useMemo(() => buildElements(nodes, relationships), [nodes, relationships])
  const isDark = useIsDarkTheme()
  const theme = isDark ? DARK_TOKENS : LIGHT_TOKENS

  useEffect(() => {
    if (!containerRef.current) return

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': 'data(color)',
            label: 'data(name)',
            color: theme.fg,
            'font-family': 'Inter, "Open Sans", ui-sans-serif, system-ui, sans-serif',
            'font-size': 10,
            'font-weight': 500,
            'text-wrap': 'wrap',
            'text-max-width': '96px',
            'text-valign': 'bottom',
            'text-margin-y': 7,
            // Halo in the canvas colour: captions stay legible where they cross an edge.
            'text-outline-color': theme.canvasBg,
            'text-outline-width': 2.5,
            'text-outline-opacity': 1,
            width: 28,
            height: 28,
            'border-width': 1.5,
            'border-color': theme.outline,
            'z-index': 10,
          },
        },
        {
          selector: 'edge',
          style: {
            width: 1.5,
            'line-color': theme.edgeLine,
            'target-arrow-color': theme.edgeLine,
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            label: 'data(caption)',
            'font-family': 'Inter, "Open Sans", ui-sans-serif, system-ui, sans-serif',
            'font-size': 8.5,
            color: theme.edgeLabel,
            'text-rotation': 'autorotate',
            // Painted on the canvas ground so the line does not strike through the type.
            'text-background-color': theme.canvasBg,
            'text-background-opacity': 1,
            'text-background-padding': '2px',
            'text-background-shape': 'roundrectangle',
            'text-margin-y': -8,
            'z-index': 1,
          },
        },
      ],
      // Preset: positions are precomputed, so the same graph always renders
      // identically instead of depending on a force simulation.
      layout: { name: 'preset', padding: 28, fit: true, animate: false },
      userZoomingEnabled: true,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
    })
    cyRef.current = cy
    cy.fit(undefined, 28)

    return () => {
      cy.destroy()
      cyRef.current = null
    }
  }, [elements, theme])

  if (elements.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-center text-xs text-fg-muted">
        No lineage edges were found for this answer.
      </div>
    )
  }

  // Must stay --canvas-bg: edge labels are painted in that exact colour, so any
  // mismatch shows up as a visible plate behind every relationship name.
  return <div ref={containerRef} className="h-full w-full bg-[var(--canvas-bg)]" />
}
