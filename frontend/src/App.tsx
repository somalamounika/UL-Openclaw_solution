import { useEffect, useMemo } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { AppHeader } from '@/components/navigation/AppHeader'
import { ShortcutSheet } from '@/components/graph/ShortcutSheet'
import { UploadPage } from '@/pages/UploadPage'
import { AssistantPage } from '@/pages/AssistantPage'
import { KnowledgeGraphPage } from '@/pages/KnowledgeGraphPage'
import { OpenClawPage } from '@/pages/OpenClawPage'
import { useGraphStore } from '@/state/graphStore'
import { useGraph, useGraphList } from '@/hooks/useGraphData'
import { useProcessingProgressClock } from '@/hooks/useProcessingProgress'
import { useKeyboard } from '@/hooks/useKeyboard'
import { useTheme } from '@/hooks/useTheme'
import { canvas } from '@/lib/canvasApi'

export default function App() {
  const { theme, toggleTheme } = useTheme()
  const navigate = useNavigate()
  const location = useLocation()

  // One clock for the whole app; every progress surface reads the same store.
  useProcessingProgressClock()

  const graphId = useGraphStore((s) => s.graphId)
  const setGraphId = useGraphStore((s) => s.setGraphId)
  const hasGraph = useGraphStore((s) => s.model.nodes.length > 0)

  const graphList = useGraphList()
  // Loaded once at the app level so the graph survives navigation between pages.
  useGraph(graphId)

  // Land a returning user on their most recent graph.
  useEffect(() => {
    if (graphId !== null) return
    const newest = graphList.data?.graphs[0]
    if (newest) setGraphId(newest.graph_id)
  }, [graphList.data, graphId, setGraphId])

  const onGraphPage = location.pathname === '/graph'

  const shortcuts = useMemo(
    () => ({
      '?': () => useGraphStore.getState().setShortcutsOpen(true),
      '/': (event: KeyboardEvent) => {
        if (!onGraphPage) return
        event.preventDefault()
        useGraphStore.getState().setSearchOpen(true)
      },
      '0': () => onGraphPage && canvas()?.fit(),
      '+': () => onGraphPage && canvas()?.zoomBy(1.3),
      '=': () => onGraphPage && canvas()?.zoomBy(1.3),
      '-': () => onGraphPage && canvas()?.zoomBy(1 / 1.3),
      f: () => {
        if (!onGraphPage) return
        const state = useGraphStore.getState()
        const cy = canvas()?.cy()
        if (state.selectedNodeId) {
          state.focusNode(state.selectedNodeId, cy ? { zoom: cy.zoom(), pan: { ...cy.pan() } } : null)
        }
      },
      e: () => {
        if (!onGraphPage) return
        const state = useGraphStore.getState()
        if (state.selectedNodeId) state.expandNode(state.selectedNodeId, 1)
      },
      c: () => {
        if (!onGraphPage) return
        const state = useGraphStore.getState()
        if (state.selectedNodeId) state.collapseNode(state.selectedNodeId)
      },
      Escape: () => {
        const state = useGraphStore.getState()
        // Esc unwinds one mode at a time: evidence, then node focus, then selection.
        if (state.evidence) state.setEvidence(null)
        else if (state.nodeFocus) state.exitFocus()
        else state.selectNode(null)
      },
    }),
    [onGraphPage],
  )
  useKeyboard(shortcuts)

  // The Assistant and Graph pages are meaningless without a graph, so send the user back
  // rather than showing an empty shell. Wait for the graph list to resolve first —
  // redirecting on the initial null would bounce anyone who deep-links or refreshes
  // on /graph before their graph has loaded.
  useEffect(() => {
    if (hasGraph || graphId) return
    if (!graphList.isSuccess) return
    if (graphList.data.graphs.length > 0) return
    if (location.pathname !== '/' && location.pathname !== '/openclaw') navigate('/', { replace: true })
  }, [hasGraph, graphId, graphList.isSuccess, graphList.data, location.pathname, navigate])

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-[var(--bg)]">
      <AppHeader
        graphId={graphId}
        hasGraph={hasGraph || Boolean(graphId)}
        theme={theme}
        onToggleTheme={toggleTheme}
      />

      <div className="min-h-0 flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={<div className="h-full overflow-y-auto"><UploadPage /></div>} />
          <Route path="/openclaw" element={<div className="h-full overflow-y-auto"><OpenClawPage /></div>} />
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="/graph" element={<KnowledgeGraphPage theme={theme} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>

      <ShortcutSheet />
    </div>
  )
}
