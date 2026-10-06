import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { GraphCanvas } from '@/components/graph/GraphCanvas'
import { GraphToolbar } from '@/components/graph/GraphToolbar'
import { ResultOverview } from '@/components/graph/ResultOverview'
import { GraphSearch } from '@/components/graph/GraphSearch'
import { NodeInspector } from '@/components/graph/NodeInspector'
import { EvidenceFocusMode } from '@/components/graph/EvidenceFocusMode'
import { GraphEmptyState, type EmptyKind } from '@/components/graph/GraphEmptyState'
import { GraphA11ySummary } from '@/components/graph/GraphA11ySummary'
import { useGraphStore } from '@/state/graphStore'
import { useBackendStatus, useGraph, useGraphList } from '@/hooks/useGraphData'
import type { Theme } from '@/hooks/useTheme'

/**
 * Page 3. The canvas takes the viewport minus one docked right rail — the rail is a real
 * column rather than a floating panel, so nothing the user needs to read ever sits on top
 * of the graph. The rail shows the Result Overview until something is selected, and the
 * inspector for that selection afterwards.
 */
export function KnowledgeGraphPage({ theme }: { theme: Theme }) {
  const [arranging, setArranging] = useState(false)

  const graphId = useGraphStore((s) => s.graphId)
  const model = useGraphStore((s) => s.model)
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId)
  const selectedEdgeId = useGraphStore((s) => s.selectedEdgeId)

  const backendStatus = useBackendStatus()
  const graphList = useGraphList()
  const graphQuery = useGraph(graphId)

  const emptyKind: EmptyKind = useMemo(() => {
    if (backendStatus === 'offline') return 'offline'
    if (graphList.isError) return 'store-unreachable'
    if (graphQuery.isLoading && graphId) return 'loading'
    if (!graphId) return graphList.isSuccess ? 'no-graphs' : 'loading'
    if (model.nodes.length === 0 && graphQuery.isSuccess) return 'empty-graph'
    return 'none'
  }, [backendStatus, graphList.isError, graphList.isSuccess, graphQuery.isLoading, graphQuery.isSuccess, graphId, model])

  const hasGraph = model.nodes.length > 0

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2 }}
      className="flex h-full min-h-0 flex-col"
    >
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <main className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
          <GraphA11ySummary />
          <GraphCanvas theme={theme} onArranging={setArranging} />

          {hasGraph ? (
            <>
              <GraphToolbar arranging={arranging} />
              <EvidenceFocusMode />
            </>
          ) : null}

          <GraphEmptyState kind={emptyKind} />
        </main>

        {hasGraph ? (
          <aside
            className="flex w-[20rem] shrink-0 flex-col border-l border-[var(--border)] xl:w-[22rem]"
            aria-label="Graph details"
          >
            {selectedNodeId || selectedEdgeId ? <NodeInspector /> : <ResultOverview />}
          </aside>
        ) : null}
      </div>

      <GraphSearch />
    </motion.div>
  )
}
