import { create } from 'zustand'
import type { EvidenceSelection, GraphModel } from '@/types/graph'
import type { ChatResponse } from '@/types/api'
import { EMPTY_GRAPH } from '@/lib/graphAdapter'
import { collapsibleDescendants, expandFrom } from '@/lib/adjacency'
import { focusNeighborhood, type CameraState, type FocusDepth, type NodeFocus } from '@/lib/focus'
import type { LayoutName } from '@/lib/layout'
import type { Lod } from '@/lib/lod'

export type Density = 'comfortable' | 'compact'

export interface FocusFrame {
  nodeId: string
  name: string
}

interface GraphState {
  /** The graph_id every page reads. The live graph is the global UL canonical graph. */
  graphId: string | null
  model: GraphModel

  /** Additive expansion set — what Expand reveals. */
  expansion: Set<string>
  /** Nodes the user explicitly expanded. */
  expanded: Set<string>
  /** Seed anchors (tier 1–2), always present in the expansion set. */
  anchors: Set<string>
  /** What the canvas actually draws. Derived from expansion + node focus. */
  visible: Set<string>
  entering: string[]

  /** Node Focus Mode: isolation. Null when browsing the full graph. */
  nodeFocus: NodeFocus | null
  /** Breadcrumb of focused nodes, oldest first. */
  focusTrail: FocusFrame[]
  /** Expansion set + camera captured on entering focus, restored on exit. */
  preFocusExpansion: Set<string> | null
  preFocusCamera: CameraState | null

  /** Evidence Focus Mode: chat provenance. Kept strictly separate from node focus. */
  evidence: EvidenceSelection | null

  selectedNodeId: string | null
  selectedEdgeId: string | null
  multiSelection: Set<string>
  hoveredNodeId: string | null

  hiddenTypes: Set<string>
  layout: LayoutName
  lod: Lod
  density: Density
  searchOpen: boolean
  shortcutsOpen: boolean
  /** Node name handed to the Assistant composer by "Ask Assistant". */
  pendingMention: string | null

  setGraphId: (graphId: string | null) => void
  setModel: (model: GraphModel) => void
  clearModel: () => void

  selectNode: (id: string | null, additive?: boolean) => void
  selectEdge: (id: string | null) => void
  setHovered: (id: string | null) => void

  expandNode: (id: string, hops?: number) => void
  expandIds: (ids: string[]) => void
  collapseNode: (id: string) => void
  expandAll: () => void
  collapseAll: () => void

  /** Enter (or re-target) Node Focus Mode. */
  focusNode: (id: string, camera?: CameraState | null) => void
  /** Double-click on the already-focused node, Esc, or "Show Full Graph". */
  exitFocus: () => void
  /** Toggle behaviour for a double-click: focus, or exit if already focused. */
  toggleFocus: (id: string, camera?: CameraState | null) => void
  setFocusDepth: (depth: FocusDepth) => void
  focusToCrumb: (index: number) => void

  setEvidence: (evidence: EvidenceSelection | null) => void
  applyChatEvidence: (question: string, response: ChatResponse) => EvidenceSelection

  toggleType: (label: string) => void
  showAllTypes: () => void
  setLayout: (layout: LayoutName) => void
  setLod: (lod: Lod) => void
  setDensity: (density: Density) => void
  setSearchOpen: (open: boolean) => void
  setShortcutsOpen: (open: boolean) => void
  setPendingMention: (mention: string | null) => void
  clearEntering: () => void
}

/**
 * Initial render is not the whole graph: seed tier 1–2, plus isolated nodes which no
 * expansion could ever reach.
 */
function seedExpansion(model: GraphModel): { expansion: Set<string>; anchors: Set<string> } {
  const anchors = new Set<string>()
  for (const node of model.nodes) {
    if (node.tier <= 2) anchors.add(node.id)
  }
  if (anchors.size === 0) {
    const minTier = model.nodes.reduce((min, n) => Math.min(min, n.tier), 9)
    for (const node of model.nodes) {
      if (node.tier === minTier) anchors.add(node.id)
    }
  }
  const expansion = new Set(anchors)
  for (const node of model.nodes) {
    if (node.degree === 0) expansion.add(node.id)
  }
  return { expansion, anchors }
}

/**
 * The single rule for what the canvas draws.
 *
 * In Node Focus Mode the neighbourhood *replaces* the expansion set — unrelated nodes
 * are genuinely gone from the visible set, not merely faded.
 */
function deriveVisible(model: GraphModel, expansion: Set<string>, nodeFocus: NodeFocus | null): Set<string> {
  if (!nodeFocus) return new Set(expansion)
  return focusNeighborhood(model, nodeFocus)
}

export const useGraphStore = create<GraphState>((set, get) => ({
  graphId: null,
  model: EMPTY_GRAPH,
  expansion: new Set(),
  expanded: new Set(),
  anchors: new Set(),
  visible: new Set(),
  entering: [],
  nodeFocus: null,
  focusTrail: [],
  preFocusExpansion: null,
  preFocusCamera: null,
  evidence: null,
  selectedNodeId: null,
  selectedEdgeId: null,
  multiSelection: new Set(),
  hoveredNodeId: null,
  hiddenTypes: new Set(),
  layout: 'fcose',
  lod: 2,
  density: 'comfortable',
  searchOpen: false,
  shortcutsOpen: false,
  pendingMention: null,

  setGraphId: (graphId) => set({ graphId }),

  setModel: (model) => {
    const { expansion, anchors } = seedExpansion(model)
    // Every per-graph value resets: a node from another graph_id must never survive.
    set({
      model,
      expansion,
      anchors,
      visible: new Set(expansion),
      expanded: new Set(),
      entering: [],
      nodeFocus: null,
      focusTrail: [],
      preFocusExpansion: null,
      preFocusCamera: null,
      evidence: null,
      selectedNodeId: null,
      selectedEdgeId: null,
      multiSelection: new Set(),
      hoveredNodeId: null,
      hiddenTypes: new Set(),
      pendingMention: null,
    })
  },

  clearModel: () =>
    set({
      model: EMPTY_GRAPH,
      expansion: new Set(),
      anchors: new Set(),
      visible: new Set(),
      expanded: new Set(),
      entering: [],
      nodeFocus: null,
      focusTrail: [],
      preFocusExpansion: null,
      preFocusCamera: null,
      evidence: null,
      selectedNodeId: null,
      selectedEdgeId: null,
      multiSelection: new Set(),
      hiddenTypes: new Set(),
    }),

  selectNode: (id, additive = false) =>
    set((state) => {
      if (id === null) {
        return { selectedNodeId: null, selectedEdgeId: null, multiSelection: new Set<string>() }
      }
      const multi = new Set(additive ? state.multiSelection : [])
      multi.add(id)
      return { selectedNodeId: id, selectedEdgeId: null, multiSelection: multi }
    }),

  selectEdge: (id) => set({ selectedEdgeId: id, selectedNodeId: null, multiSelection: new Set() }),
  setHovered: (id) => set({ hoveredNodeId: id }),

  /* ---------------- Expand: additive ---------------- */

  expandNode: (id, hops = 1) =>
    set((state) => {
      const revealed = expandFrom(state.model, id, hops).filter((n) => !state.expansion.has(n))
      const expanded = new Set(state.expanded).add(id)
      if (revealed.length === 0) return { expanded }
      const expansion = new Set(state.expansion)
      for (const nodeId of revealed) expansion.add(nodeId)
      return {
        expansion,
        expanded,
        entering: revealed,
        // Expanding while focused widens the focus rather than silently doing nothing.
        visible: deriveVisible(state.model, expansion, state.nodeFocus),
      }
    }),

  expandIds: (ids) =>
    set((state) => {
      const revealed = ids.filter((id) => state.model.nodeById.has(id) && !state.expansion.has(id))
      if (revealed.length === 0) return {}
      const expansion = new Set(state.expansion)
      for (const id of revealed) expansion.add(id)
      return {
        expansion,
        entering: revealed,
        visible: deriveVisible(state.model, expansion, state.nodeFocus),
      }
    }),

  collapseNode: (id) =>
    set((state) => {
      const doomed = collapsibleDescendants(state.model, id, state.expansion, state.anchors)
      if (doomed.length === 0) return {}
      const expansion = new Set(state.expansion)
      const expanded = new Set(state.expanded)
      for (const nodeId of doomed) {
        expansion.delete(nodeId)
        expanded.delete(nodeId)
      }
      expanded.delete(id)
      return {
        expansion,
        expanded,
        entering: [],
        visible: deriveVisible(state.model, expansion, state.nodeFocus),
      }
    }),

  expandAll: () =>
    set((state) => {
      const all = new Set(state.model.nodes.map((n) => n.id))
      const entering = state.model.nodes.filter((n) => !state.expansion.has(n.id)).map((n) => n.id)
      return {
        expansion: all,
        expanded: new Set(all),
        entering,
        visible: deriveVisible(state.model, all, state.nodeFocus),
      }
    }),

  collapseAll: () =>
    set((state) => {
      const expansion = new Set(state.anchors)
      return {
        expansion,
        expanded: new Set(),
        entering: [],
        selectedNodeId: null,
        selectedEdgeId: null,
        visible: deriveVisible(state.model, expansion, state.nodeFocus),
      }
    }),

  /* ---------------- Focus: isolation ---------------- */

  focusNode: (id, camera = null) =>
    set((state) => {
      const node = state.model.nodeById.get(id)
      if (!node) return {}

      const entering = state.nodeFocus === null
      const nodeFocus: NodeFocus = { nodeId: id, depth: state.nodeFocus?.depth ?? 1 }
      const trail = state.focusTrail.some((frame) => frame.nodeId === id)
        ? state.focusTrail
        : [...state.focusTrail, { nodeId: id, name: node.name }]

      return {
        nodeFocus,
        focusTrail: trail,
        // Snapshot only on the way in, so focusing deeper keeps the original restore point.
        preFocusExpansion: entering ? new Set(state.expansion) : state.preFocusExpansion,
        preFocusCamera: entering ? camera : state.preFocusCamera,
        selectedNodeId: id,
        selectedEdgeId: null,
        // Node focus and evidence focus are separate modes; entering one leaves the other.
        evidence: null,
        entering: [],
        visible: deriveVisible(state.model, state.expansion, nodeFocus),
      }
    }),

  exitFocus: () =>
    set((state) => {
      if (!state.nodeFocus) return {}
      const expansion = state.preFocusExpansion ?? state.expansion
      return {
        nodeFocus: null,
        focusTrail: [],
        expansion,
        preFocusExpansion: null,
        entering: [],
        visible: deriveVisible(state.model, expansion, null),
      }
    }),

  toggleFocus: (id, camera = null) => {
    const state = get()
    if (state.nodeFocus?.nodeId === id) state.exitFocus()
    else state.focusNode(id, camera)
  },

  setFocusDepth: (depth) =>
    set((state) => {
      if (!state.nodeFocus) return {}
      const nodeFocus: NodeFocus = { ...state.nodeFocus, depth }
      return { nodeFocus, visible: deriveVisible(state.model, state.expansion, nodeFocus) }
    }),

  focusToCrumb: (index) =>
    set((state) => {
      const frame = state.focusTrail[index]
      if (!frame) return {}
      const focusTrail = state.focusTrail.slice(0, index + 1)
      const nodeFocus: NodeFocus = { nodeId: frame.nodeId, depth: state.nodeFocus?.depth ?? 1 }
      return {
        focusTrail,
        nodeFocus,
        selectedNodeId: frame.nodeId,
        visible: deriveVisible(state.model, state.expansion, nodeFocus),
      }
    }),

  /* ---------------- Evidence focus ---------------- */

  setEvidence: (evidence) =>
    set((state) => (evidence ? { evidence } : { evidence: null, ...(state.nodeFocus ? {} : {}) })),

  /**
   * Joins chat evidence to canvas nodes on the shared `<Label>:<name>` id, reveals any
   * cited node that is hidden, and records which cited ids are absent from this graph so
   * the UI can say "4 of 5" rather than quietly dropping one.
   */
  applyChatEvidence: (question, response) => {
    const state = get()
    const { model } = state
    const nodeIds: string[] = []
    const missingNodeIds: string[] = []
    const nodeEvidence = new Map<string, string>()

    for (const node of response.graph_evidence.nodes) {
      if (model.nodeById.has(node.id)) {
        nodeIds.push(node.id)
        if (node.description) nodeEvidence.set(node.id, node.description)
      } else {
        missingNodeIds.push(node.id)
      }
    }

    const edgeKeys: string[] = []
    for (const rel of response.graph_evidence.relationships) {
      const id = `${rel.source}-[${rel.relationship}]->${rel.target}`
      if (model.edgeById.has(id)) {
        edgeKeys.push(id)
        if (rel.description) {
          if (!nodeEvidence.has(rel.source)) nodeEvidence.set(rel.source, rel.description)
          if (!nodeEvidence.has(rel.target)) nodeEvidence.set(rel.target, rel.description)
        }
      }
    }

    const evidence: EvidenceSelection = { question, nodeIds, missingNodeIds, edgeKeys, nodeEvidence }

    const expansion = new Set(state.expansion)
    for (const id of nodeIds) expansion.add(id)

    set({
      evidence,
      expansion,
      // Evidence focus replaces node focus outright — they are different questions.
      nodeFocus: null,
      focusTrail: [],
      preFocusExpansion: null,
      selectedNodeId: null,
      selectedEdgeId: null,
      hiddenTypes: new Set(),
      visible: deriveVisible(model, expansion, null),
    })
    return evidence
  },

  toggleType: (label) =>
    set((state) => {
      const hiddenTypes = new Set(state.hiddenTypes)
      if (hiddenTypes.has(label)) hiddenTypes.delete(label)
      else hiddenTypes.add(label)
      return { hiddenTypes }
    }),

  showAllTypes: () => set({ hiddenTypes: new Set() }),
  setLayout: (layout) => set({ layout }),
  setLod: (lod) => set({ lod }),
  setDensity: (density) => set({ density }),
  setSearchOpen: (searchOpen) => set({ searchOpen }),
  setShortcutsOpen: (shortcutsOpen) => set({ shortcutsOpen }),
  setPendingMention: (pendingMention) => set({ pendingMention }),
  clearEntering: () => set({ entering: [] }),
}))
