import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import cytoscape, { type Core, type EdgeSingular, type NodeSingular } from 'cytoscape'
import fcose from 'cytoscape-fcose'
import { useGraphStore } from '@/state/graphStore'
import { buildStylesheet, DARK_TOKENS, LIGHT_TOKENS } from '@/lib/cytoscapeStyle'
import { buildAggregateEdges, buildElements, captionFor } from '@/lib/elements'
import { buildLayout } from '@/lib/layout'
import { separateNodes } from '@/lib/declutter'
import { LOD_SPECS, lodForZoom, type Lod } from '@/lib/lod'
import { hiddenNeighbourCount } from '@/lib/adjacency'
import { registerCanvasApi } from '@/lib/canvasApi'
import {
  applyFisheye,
  DEFAULT_FISHEYE_RADIUS,
  FISHEYE_DISTORTION,
  MAX_FISHEYE_RADIUS,
  MIN_FISHEYE_RADIUS,
} from '@/lib/fisheye'
import { useReducedMotion } from '@/hooks/useReducedMotion'
import type { Theme } from '@/hooks/useTheme'
import { NodeBadgeLayer } from './NodeBadgeLayer'
import { Minimap } from './Minimap'
import { ContextMenu, type ContextMenuState } from './ContextMenu'

cytoscape.use(fcose)

/**
 * Collect elements by id into one collection.
 *
 * `collection.merge()` returns a NEW collection rather than mutating the receiver, so
 * accumulating into a `cy.collection()` in a loop silently yields an empty set. This
 * builds the array first and hands it to Cytoscape in one go.
 */
function collect(cy: Core, ids: Iterable<string>) {
  const found: cytoscape.CollectionArgument[] = []
  for (const id of ids) {
    const element = cy.getElementById(id)
    if (element.nonempty()) found.push(element)
  }
  return cy.collection(found as never)
}

/**
 * Fit, then compensate for the overlay chrome — and never below a readable zoom.
 *
 * Cytoscape's `fit` centres content in the whole container, but the toolbar and the
 * banners float on top of it, so a naive fit parks nodes underneath them. The zoom
 * clamp matters more: fitting a few hundred nodes into a viewport yields a zoom around
 * 0.15, where every caption is a smudge. Below MIN_READABLE_ZOOM we keep the centre the
 * fit chose and hold the zoom at a level a human can actually read, letting the graph
 * overflow the viewport — panning is cheap, squinting is not.
 */
const CHROME = { left: 0, right: 0, bottom: 0 }

/** Node captions stop being legible below this; the camera is never framed under it. */
export const MIN_READABLE_ZOOM = 0.9
/** And a two-node result should not be blown up to fill the screen either. */
const MAX_FIT_ZOOM = 1.6

function fitInto(
  cy: Core,
  eles: cytoscape.CollectionArgument,
  padding: number,
  options: { banner?: boolean; duration?: number } = {},
) {
  const top = options.banner ? 64 : 0
  // The pan is what clears the chrome; padding only keeps content off the edges. Capping
  // it stops a small subgraph from being fitted so loosely that it reads as a dot cluster.
  const pad = Math.min(padding, Math.min(cy.width(), cy.height()) * 0.18)
  const panX = -(CHROME.right - CHROME.left) / 2
  const panY = (top - CHROME.bottom) / 2
  const duration = options.duration ?? 0
  const before = { zoom: cy.zoom(), pan: { ...cy.pan() } }

  cy.fit(eles, pad)
  const fitted = cy.zoom()
  const clamped = Math.min(MAX_FIT_ZOOM, Math.max(MIN_READABLE_ZOOM, fitted))
  if (clamped !== fitted) {
    cy.zoom({ level: clamped, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } })
    if (clamped > fitted) {
      // Held above the fit: the graph is now wider than the viewport, and the centre of
      // its bounding box can easily be empty space between two clusters. Anchor on the
      // busiest node instead, so the first thing on screen is the part worth reading.
      const anchor = busiestNode(eles)
      if (anchor) cy.center(anchor)
    }
  }
  const target = { zoom: cy.zoom(), pan: { x: cy.pan().x + panX, y: cy.pan().y + panY } }

  if (duration > 0) {
    // Rewind and animate to the resolved camera rather than animating a raw fit.
    cy.viewport(before)
    cy.animate({ zoom: target.zoom, pan: target.pan }, { duration, easing: 'ease-out' })
    return
  }
  cy.panBy({ x: panX, y: panY })
}

/** Highest-degree node in a collection — the natural anchor for an overflowing view. */
function busiestNode(eles: cytoscape.CollectionArgument): cytoscape.NodeSingular | null {
  let best: cytoscape.NodeSingular | null = null
  let bestDegree = -1
  ;(eles as cytoscape.CollectionReturnValue).nodes().forEach((node) => {
    const degree = Number(node.data('degree') ?? 0)
    if (degree > bestDegree) {
      bestDegree = degree
      best = node
    }
  })
  return best
}

/**
 * Everything the expansion set puts on the map — which is *not* the same as `:visible`.
 *
 * Cytoscape counts an element as visible only when it is displayed AND opaque, so
 * `:visible` silently drops nodes mid-entrance (opacity 0 until their stagger fires) and
 * nodes the current LOD gates away by tier. Laying out that set leaves those nodes
 * stacked wherever they were spawned, and they surface overlapping the moment they fade
 * in or the zoom crosses a level. Layout and overlap removal therefore work on this set;
 * only the camera works on `:visible`, since framing should follow what is drawn.
 */
function layoutEles(cy: Core) {
  return cy.elements().filter(
    (ele) =>
      !ele.hasClass('collapsed-hidden') && !ele.hasClass('type-filtered') && !ele.hasClass('aggregate'),
  )
}

const ENTRANCE_STAGGER_MS = 30

export function GraphCanvas({
  theme,
  onArranging,
}: {
  theme: Theme
  onArranging: (arranging: boolean) => void
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cyRef = useRef<Core | null>(null)
  const pendingInitialLayout = useRef(false)
  const reducedMotion = useReducedMotion()

  const model = useGraphStore((s) => s.model)
  const visible = useGraphStore((s) => s.visible)
  const entering = useGraphStore((s) => s.entering)
  const clearEntering = useGraphStore((s) => s.clearEntering)
  const hiddenTypes = useGraphStore((s) => s.hiddenTypes)
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId)
  const multiSelection = useGraphStore((s) => s.multiSelection)
  const nodeFocus = useGraphStore((s) => s.nodeFocus)
  const evidence = useGraphStore((s) => s.evidence)
  const layoutName = useGraphStore((s) => s.layout)
  const lod = useGraphStore((s) => s.lod)
  const density = useGraphStore((s) => s.density)
  const hoveredNodeId = useGraphStore((s) => s.hoveredNodeId)

  const selectNode = useGraphStore((s) => s.selectNode)
  const selectEdge = useGraphStore((s) => s.selectEdge)
  const expandNode = useGraphStore((s) => s.expandNode)
  const toggleFocus = useGraphStore((s) => s.toggleFocus)
  const setLod = useGraphStore((s) => s.setLod)

  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null)
  const [viewportVersion, setViewportVersion] = useState(0)

  const tokens = theme === 'dark' ? DARK_TOKENS : LIGHT_TOKENS
  const isDark = theme === 'dark'

  /* ---------------- Instance: created once, never re-created on state change. ---------------- */
  useEffect(() => {
    if (!containerRef.current) return
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: buildStylesheet(isDark ? DARK_TOKENS : LIGHT_TOKENS, 2),
      minZoom: 0.2,
      maxZoom: 4,
      wheelSensitivity: 0.22,
      textureOnViewport: true,
      hideEdgesOnViewport: true,
      motionBlur: true,
      pixelRatio: Math.min(1.5, window.devicePixelRatio || 1),
      selectionType: 'additive',
      boxSelectionEnabled: false,
    })
    cyRef.current = cy
    return () => {
      registerCanvasApi(null)
      cy.destroy()
      cyRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ---------------- Elements: rebuilt only when the graph itself changes. ---------------- */
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.elements().remove()
    if (model.nodes.length === 0) return

    cy.add(buildElements(model))
    cy.nodes().addClass('collapsed-hidden')
    cy.edges().addClass('collapsed-hidden')
    // The visibility effect runs later in this same commit and lays out what is actually
    // on screen; laying out here would place nodes that are about to be hidden.
    pendingInitialLayout.current = true
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model])

  /* ---------------- Style: re-derived on theme or LOD change only. ---------------- */
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.style(buildStylesheet(tokens, lod))

    const spec = LOD_SPECS[lod]
    cy.batch(() => {
      cy.nodes().forEach((node) => {
        const domain = model.nodeById.get(node.id())
        if (!domain) return
        node.data('caption', captionFor(domain.name, spec.captionChars))
      })
    })
  }, [tokens, lod, model])

  /**
   * Seeds the camera once the graph is visible. Refuses to run while the container has no
   * size — a zero-size viewport makes both the layout and the fit meaningless, and that
   * happens routinely (mount before layout, a hidden tab, a collapsed pane).
   */
  const runInitialLayout = useCallback(() => {
    const cy = cyRef.current
    if (!cy || !pendingInitialLayout.current) return
    if (cy.width() === 0 || cy.height() === 0) return

    const seeded = layoutEles(cy)
    if (seeded.empty()) return
    pendingInitialLayout.current = false

    onArranging(true)
    seeded
      .layout(
        buildLayout({
          name: 'fcose',
          visibleNodes: seeded.nodes().length,
          animate: false,
          onStop: () => {
            separateNodes(cy, layoutEles(cy).nodes())
            onArranging(false)
            fitInto(cy, cy.elements(':visible'), 80)
          },
        }),
      )
      .run()
  }, [onArranging])

  /* ---------------- Visibility: the store's derived set, applied verbatim. ---------------- */
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return

    cy.batch(() => {
      cy.nodes().forEach((node) => {
        const id = node.id()
        node.toggleClass('collapsed-hidden', !visible.has(id))
        node.toggleClass('type-filtered', hiddenTypes.has(String(node.data('label'))))
      })
      cy.edges('[!weight]').forEach((edge) => {
        const shown =
          visible.has(edge.data('source') as string) && visible.has(edge.data('target') as string)
        edge.toggleClass('collapsed-hidden', !shown)
      })
      // Ring badge only where an expansion would actually reveal something. In focus mode
      // the badge would be a lie — the neighbourhood is the whole point — so it is dropped.
      cy.nodes(':visible').forEach((node) => {
        const hidden = nodeFocus ? 0 : hiddenNeighbourCount(model, node.id(), visible)
        node.toggleClass('has-hidden', hidden > 0)
      })
    })

    runInitialLayout()
  }, [visible, hiddenTypes, model, nodeFocus, runInitialLayout])

  /* ---------------- Node Focus: relayout and frame the isolated neighbourhood. ---------------- */
  // Keyed on node *and* depth: widening the radius reveals nodes that have never been
  // laid out, so they would otherwise all sit stacked at the origin.
  const previousFocusKey = useRef<string | null>(null)
  useEffect(() => {
    const cy = cyRef.current
    if (!cy || model.nodes.length === 0) return
    const key = nodeFocus ? `${nodeFocus.nodeId}@${nodeFocus.depth}` : null
    if (previousFocusKey.current === key) return
    previousFocusKey.current = key

    const eles = layoutEles(cy)
    if (eles.empty()) return

    if (!nodeFocus) {
      // Leaving focus: restore the camera captured on the way in, if we still have it.
      const camera = useGraphStore.getState().preFocusCamera
      if (camera) {
        cy.animate({ zoom: camera.zoom, pan: camera.pan }, { duration: reducedMotion ? 0 : 350 })
      } else {
        fitInto(cy, cy.elements(':visible'), 70, { duration: reducedMotion ? 0 : 350 })
      }
      return
    }

    onArranging(true)
    eles
      .layout(
        buildLayout({
          name: layoutName,
          visibleNodes: eles.nodes().length,
          animate: !reducedMotion,
          onStop: () => {
            separateNodes(cy, layoutEles(cy).nodes())
            onArranging(false)
            fitInto(cy, cy.elements(':visible'), 90, { duration: reducedMotion ? 0 : 400 })
          },
        }),
      )
      .run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeFocus, model])

  /* ---------------- Expansion entrance: new nodes spawn at their parent. ---------------- */
  useEffect(() => {
    const cy = cyRef.current
    if (!cy || entering.length === 0) return

    const anchorId = useGraphStore.getState().selectedNodeId
    const anchor = anchorId ? cy.getElementById(anchorId) : null
    const origin = anchor && anchor.nonempty() ? anchor.position() : { x: 0, y: 0 }

    const newNodes = collect(cy, entering)
    // Spawn on a ring around the parent rather than in a jitter blob: the eye still reads
    // "these came from here", but they start separated instead of stacked, which matters
    // when the camera flies to them before the layout has finished settling.
    const ringRadius = 40 + Math.min(newNodes.length, 24) * 6
    cy.batch(() => {
      newNodes.forEach((node, index) => {
        const angle = (index / Math.max(1, newNodes.length)) * Math.PI * 2
        node.position({
          x: origin.x + Math.cos(angle) * ringRadius,
          y: origin.y + Math.sin(angle) * ringRadius,
        })
        node.addClass('entering')
      })
    })

    // Pin everything already placed: the existing map must not reshuffle under the user.
    const fixedNodeConstraint: Array<{ nodeId: string; position: { x: number; y: number } }> = []
    layoutEles(cy)
      .nodes()
      .difference(newNodes)
      .forEach((node) => {
        fixedNodeConstraint.push({
          nodeId: node.id(),
          position: { x: node.position('x'), y: node.position('y') },
        })
      })

    onArranging(true)
    layoutEles(cy)
      .layout(
        buildLayout({
          name: 'fcose',
          visibleNodes: layoutEles(cy).nodes().length,
          animate: !reducedMotion,
          incremental: true,
          fixedNodeConstraint,
          onStop: () => {
            separateNodes(cy, layoutEles(cy).nodes(), {
              frozen: new Set(fixedNodeConstraint.map((entry) => entry.nodeId)),
            })
            onArranging(false)
          },
        }),
      )
      .run()

    const timers: number[] = []
    entering.forEach((id, index) => {
      const delay = reducedMotion ? 0 : index * ENTRANCE_STAGGER_MS
      timers.push(window.setTimeout(() => cy.getElementById(id).removeClass('entering'), delay))
    })
    const done = window.setTimeout(
      () => clearEntering(),
      reducedMotion ? 0 : entering.length * ENTRANCE_STAGGER_MS + 60,
    )

    return () => {
      for (const t of timers) window.clearTimeout(t)
      window.clearTimeout(done)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entering])

  /* ---------------- L0 edge aggregation. ---------------- */
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.remove('edge.aggregate')
    if (lod !== 0 || model.nodes.length === 0) return
    cy.add(buildAggregateEdges(model, visible, LOD_SPECS[0].maxTier))
  }, [lod, model, visible])

  /* ---------------- Selection dimming (not focus — focus removes, it does not fade). ---------------- */
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.batch(() => {
      cy.elements().removeClass('dimmed')
      if (!selectedNodeId || evidence) return
      const selected = cy.getElementById(selectedNodeId)
      if (selected.empty()) return
      const keep = selected.closedNeighborhood()
      cy.nodes(':visible').difference(keep).addClass('dimmed')
      cy.edges(':visible').difference(selected.connectedEdges()).addClass('dimmed')
    })
  }, [selectedNodeId, evidence, visible])

  /* ---------------- Evidence Focus: halo the cited subgraph, mute the rest. ---------------- */
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return

    cy.batch(() => {
      cy.elements().removeClass('evidence evidence-muted')
      if (!evidence) return
      const citedNodes = collect(cy, evidence.nodeIds)
      const citedEdges = collect(cy, evidence.edgeKeys)
      citedNodes.addClass('evidence')
      citedEdges.addClass('evidence')
      cy.nodes(':visible').difference(citedNodes).addClass('evidence-muted')
      cy.edges(':visible').difference(citedEdges).addClass('evidence-muted')
    })

  }, [evidence])

  /**
   * The evidence camera flight is deliberately separate from the highlighting above: it
   * waits until no expansion entrance is in flight, so it frames settled positions rather
   * than a pile of nodes still being pushed apart by the incremental layout.
   */
  const hadEvidence = useRef(false)
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return

    if (!evidence) {
      // Leaving evidence view: the camera is still framing a subgraph that is no longer
      // highlighted, usually zoomed deep into it. Return to the graph the user has.
      if (hadEvidence.current) {
        hadEvidence.current = false
        const eles = cy.elements(':visible')
        if (eles.nonempty()) fitInto(cy, eles, 70, { duration: reducedMotion ? 0 : 420 })
      }
      return
    }

    hadEvidence.current = true
    if (evidence.nodeIds.length === 0 || entering.length > 0) return

    const targets = collect(cy, evidence.nodeIds)
    if (targets.nonempty()) {
      fitInto(cy, targets, 120, { banner: true, duration: reducedMotion ? 0 : 600 })
    }
  }, [evidence, entering, reducedMotion])

  /* ---------------- Hover pulse from chat chips and lineage nodes. ---------------- */
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.nodes('.pulse').removeClass('pulse')
    if (hoveredNodeId) {
      const node = cy.getElementById(hoveredNodeId)
      if (node.nonempty()) node.addClass('pulse')
    }
  }, [hoveredNodeId])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.batch(() => {
      cy.nodes(':selected').unselect()
      for (const id of multiSelection) {
        const node = cy.getElementById(id)
        if (node.nonempty()) node.select()
      }
    })
  }, [multiSelection])

  /* ---------------- Layout switching. ---------------- */
  const runLayout = useCallback(() => {
    const cy = cyRef.current
    if (!cy) return
    const eles = layoutEles(cy)
    if (eles.empty()) return
    onArranging(true)
    eles
      .layout(
        buildLayout({
          name: layoutName,
          visibleNodes: eles.nodes().length,
          animate: !reducedMotion,
          onStop: () => {
            separateNodes(cy, layoutEles(cy).nodes())
            onArranging(false)
            fitInto(cy, cy.elements(':visible'), 60, { duration: reducedMotion ? 0 : 300 })
          },
        }),
      )
      .run()
  }, [layoutName, reducedMotion, onArranging])

  const firstLayoutRun = useRef(true)
  useEffect(() => {
    if (firstLayoutRun.current) {
      firstLayoutRun.current = false
      return
    }
    runLayout()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutName])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    const factor = density === 'compact' ? 0.78 : 1
    cy.batch(() => {
      cy.nodes().forEach((node) => {
        const base = Number(node.data('size'))
        if (!Number.isFinite(base)) return
        node.style('width', base * factor)
        node.style('height', base * factor)
      })
    })
    // Growing the discs can push a pair into contact, so re-clear the gaps.
    separateNodes(cy, layoutEles(cy).nodes())
  }, [density, model])

  /* ---------------- Interaction. ---------------- */
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return

    let lastTap = 0
    let lastTapId = ''

    const onNodeTap = (event: cytoscape.EventObject) => {
      const node = event.target as NodeSingular
      const id = node.id()
      const original = event.originalEvent as MouseEvent | undefined
      const now = Date.now()

      if (original?.altKey) {
        selectNode(id)
        expandNode(id, 2)
        return
      }
      if (original?.shiftKey) {
        selectNode(id, true)
        return
      }

      // Cytoscape's dbltap is unreliable across trackpads, so double-tap is detected here.
      if (now - lastTap < 320 && lastTapId === id) {
        lastTap = 0
        // Double-click is Focus: isolate this node's neighbourhood, or leave focus if it
        // is already the focused node. Expand stays a separate, additive action.
        toggleFocus(id, { zoom: cy.zoom(), pan: { ...cy.pan() } })
        return
      }
      lastTap = now
      lastTapId = id
      selectNode(id)
    }

    const onEdgeTap = (event: cytoscape.EventObject) => {
      const edge = event.target as EdgeSingular
      if (edge.hasClass('aggregate')) {
        cy.animate({ fit: { eles: edge.connectedNodes(), padding: 90 }, zoom: 1 }, { duration: 420 })
        return
      }
      selectEdge(edge.id())
    }

    const onBackgroundTap = (event: cytoscape.EventObject) => {
      if (event.target === cy) {
        selectNode(null)
        setContextMenu(null)
      }
    }

    const onNodeContext = (event: cytoscape.EventObject) => {
      event.preventDefault?.()
      const node = event.target as NodeSingular
      const rendered = node.renderedPosition()
      setContextMenu({ nodeId: node.id(), x: rendered.x, y: rendered.y })
    }

    const over = (event: cytoscape.EventObject) => event.target.addClass('hovered')
    const out = (event: cytoscape.EventObject) => event.target.removeClass('hovered')

    cy.on('tap', 'node', onNodeTap)
    cy.on('tap', 'edge', onEdgeTap)
    cy.on('tap', onBackgroundTap)
    cy.on('cxttap', 'node', onNodeContext)
    cy.on('mouseover', 'node', over)
    cy.on('mouseout', 'node', out)
    cy.on('mouseover', 'edge', over)
    cy.on('mouseout', 'edge', out)

    return () => {
      cy.removeListener('tap', 'node', onNodeTap)
      cy.removeListener('tap', 'edge', onEdgeTap)
      cy.removeListener('tap', onBackgroundTap)
      cy.removeListener('cxttap', 'node', onNodeContext)
      cy.removeListener('mouseover', 'node', over)
      cy.removeListener('mouseout', 'node', out)
      cy.removeListener('mouseover', 'edge', over)
      cy.removeListener('mouseout', 'edge', out)
    }
  }, [selectNode, selectEdge, expandNode, toggleFocus])

  /* ---------------- Zoom -> LOD. ---------------- */
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    let frame = 0
    const onViewport = () => {
      // Resolved synchronously: hysteresis means it rarely changes, and deferring it to
      // rAF would leave the level stale for as long as frames are throttled.
      const current = useGraphStore.getState().lod
      const next = lodForZoom(cy.zoom(), current)
      if (next !== current) setLod(next as Lod)

      if (frame) return
      frame = window.requestAnimationFrame(() => {
        frame = 0
        setViewportVersion((v) => v + 1)
      })
    }
    cy.on('zoom pan', onViewport)
    return () => {
      cy.removeListener('zoom pan', onViewport)
      if (frame) window.cancelAnimationFrame(frame)
    }
  }, [setLod])

  /* ---------------- Fisheye lens (hold Space). ---------------- */
  const fisheyeRef = useRef({
    active: false,
    radius: DEFAULT_FISHEYE_RADIUS,
    saved: new Map<string, { x: number; y: number }>(),
  })

  useEffect(() => {
    if (reducedMotion) return
    const cy = cyRef.current
    const container = containerRef.current
    if (!cy || !container) return

    let frame = 0
    let pointer: { x: number; y: number } | null = null

    const restore = () => {
      const state = fisheyeRef.current
      if (!state.active) return
      state.active = false
      cy.batch(() => {
        for (const [id, position] of state.saved) {
          const node = cy.getElementById(id)
          if (node.nonempty()) node.position(position)
        }
      })
      state.saved.clear()
    }

    const apply = () => {
      frame = 0
      const state = fisheyeRef.current
      if (!state.active || !pointer) return
      const pan = cy.pan()
      const zoom = cy.zoom()
      const focusX = (pointer.x - pan.x) / zoom
      const focusY = (pointer.y - pan.y) / zoom

      cy.batch(() => {
        for (const [id, position] of state.saved) {
          const node = cy.getElementById(id)
          if (node.empty()) continue
          const lensed = applyFisheye(position.x, position.y, {
            focusX,
            focusY,
            radius: state.radius / zoom,
            distortion: FISHEYE_DISTORTION,
          })
          node.position({ x: lensed.x, y: lensed.y })
        }
      })
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code !== 'Space' || event.repeat) return
      const target = event.target
      if (target instanceof HTMLElement && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
      event.preventDefault()
      const state = fisheyeRef.current
      if (state.active) return
      state.active = true
      state.saved.clear()
      cy.nodes(':visible').forEach((node) => {
        state.saved.set(node.id(), { x: node.position('x'), y: node.position('y') })
      })
      container.style.cursor = 'zoom-in'
      if (!frame) frame = window.requestAnimationFrame(apply)
    }

    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code !== 'Space') return
      container.style.cursor = ''
      restore()
    }

    const onPointerMove = (event: PointerEvent) => {
      const rect = container.getBoundingClientRect()
      pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top }
      if (fisheyeRef.current.active && !frame) frame = window.requestAnimationFrame(apply)
    }

    const onWheel = (event: WheelEvent) => {
      const state = fisheyeRef.current
      if (!state.active) return
      event.preventDefault()
      event.stopPropagation()
      state.radius = Math.max(
        MIN_FISHEYE_RADIUS,
        Math.min(MAX_FISHEYE_RADIUS, state.radius - event.deltaY * 0.6),
      )
      if (!frame) frame = window.requestAnimationFrame(apply)
    }

    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', restore)
    container.addEventListener('pointermove', onPointerMove)
    container.addEventListener('wheel', onWheel, { passive: false, capture: true })

    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', restore)
      container.removeEventListener('pointermove', onPointerMove)
      container.removeEventListener('wheel', onWheel, { capture: true })
      if (frame) window.cancelAnimationFrame(frame)
      restore()
    }
  }, [reducedMotion])

  /* ---------------- Imperative camera API. ---------------- */
  useEffect(() => {
    registerCanvasApi({
      cy: () => cyRef.current,
      fit: () => {
        const cy = cyRef.current
        if (!cy) return
        const eles = cy.elements(':visible')
        if (eles.nonempty()) fitInto(cy, eles, 60, { duration: reducedMotion ? 0 : 260 })
      },
      zoomBy: (factor) => {
        const cy = cyRef.current
        if (!cy) return
        cy.animate(
          { zoom: { level: cy.zoom() * factor, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } } },
          { duration: reducedMotion ? 0 : 160 },
        )
      },
      centerOn: (nodeId, zoom) => {
        const cy = cyRef.current
        if (!cy) return
        const node = cy.getElementById(nodeId)
        if (node.empty()) return
        cy.animate(
          { center: { eles: node }, zoom: zoom ?? Math.max(cy.zoom(), 1.2) },
          { duration: reducedMotion ? 0 : 420, easing: 'ease-out' },
        )
      },
      fitTo: (nodeIds, padding = 100) => {
        const cy = cyRef.current
        if (!cy) return
        const eles = collect(cy, nodeIds)
        if (eles.nonempty()) fitInto(cy, eles, padding, { duration: reducedMotion ? 0 : 600 })
      },
      pulse: (nodeId) => {
        const cy = cyRef.current
        if (!cy) return
        cy.nodes('.pulse').removeClass('pulse')
        if (!nodeId) return
        const node = cy.getElementById(nodeId)
        if (node.nonempty()) node.addClass('pulse')
      },
      relayout: runLayout,
    })
  }, [runLayout, reducedMotion])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let previous = { width: 0, height: 0 }
    const observer = new ResizeObserver(() => {
      const cy = cyRef.current
      if (!cy) return
      cy.resize()
      const width = cy.width()
      const height = cy.height()
      const hasSize = width > 0 && height > 0
      if (hasSize && previous.width === 0) {
        // First real size: seed the camera now that fitting can mean something.
        runInitialLayout()
        if (!pendingInitialLayout.current) fitInto(cy, cy.elements(':visible'), 80)
      } else if (hasSize) {
        // Cytoscape keeps the pan across a resize, which slides the graph off toward one
        // corner whenever the viewport changes — the rail mounting, or a window resize.
        // Shifting the pan by half the delta keeps whatever the user was looking at
        // in the middle of the canvas.
        cy.panBy({ x: (width - previous.width) / 2, y: (height - previous.height) / 2 })
      }
      previous = { width, height }
      setViewportVersion((v) => v + 1)
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [runInitialLayout])

  const hasGraph = model.nodes.length > 0
  const contextNode = useMemo(
    () => (contextMenu ? model.nodeById.get(contextMenu.nodeId) ?? null : null),
    [contextMenu, model],
  )

  return (
    <div className="relative h-full w-full">
      <div
        ref={containerRef}
        role="application"
        aria-label="Knowledge graph canvas"
        aria-describedby="graph-a11y-summary"
        tabIndex={0}
        className="h-full w-full bg-[var(--canvas-bg)] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--accent)]"
      />

      {hasGraph ? (
        <>
          <NodeBadgeLayer cy={cyRef.current} version={viewportVersion} lod={lod} />
          <Minimap cy={cyRef.current} version={viewportVersion} theme={theme} />
        </>
      ) : null}

      {contextMenu && contextNode ? (
        <ContextMenu state={contextMenu} node={contextNode} onClose={() => setContextMenu(null)} />
      ) : null}
    </div>
  )
}
