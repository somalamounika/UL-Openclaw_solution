import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteGraph, fetchGraph, listGraphs, checkHealth } from '@/api/graph'
import { buildGraphModel } from '@/lib/graphAdapter'
import { useGraphStore } from '@/state/graphStore'
import type { GraphModel } from '@/types/graph'

export type BackendStatus = 'checking' | 'online' | 'offline'

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    // Deliberately unsignalled: this is a liveness probe, and a cancelled probe
    // reverts the query to `pending` instead of settling, which would strand the UI
    // on "checking" whenever the tree remounts.
    queryFn: () => checkHealth(),
    refetchInterval: 30_000,
    retry: 1,
    staleTime: 10_000,
  })
}

/**
 * One answer to "is the backend up?", shared by the status bar and the canvas empty
 * state so they can never contradict each other.
 *
 * A query that has failed at least once but is mid-retry is reported as 'checking',
 * not 'offline' — `isError` alone is false during retry backoff, which would otherwise
 * make the two surfaces disagree for a second at startup.
 */
export function useBackendStatus(): BackendStatus {
  const health = useHealth()
  if (health.isSuccess && health.data?.status === 'ok') return 'online'
  if (health.isError) return 'offline'
  // Settled with no result and at least one recorded failure: the probe is done and
  // it did not succeed, whatever intermediate state the query is reporting.
  if (health.failureCount > 0 && health.fetchStatus === 'idle') return 'offline'
  return 'checking'
}

export function useGraphList() {
  return useQuery({
    queryKey: ['graphs'],
    queryFn: ({ signal }) => listGraphs(signal),
    staleTime: 5_000,
  })
}

/**
 * Loads one graph and hands the adapted model to the store. The model — adjacency,
 * degree and tier maps included — is built once here and memoised by React Query;
 * nothing downstream recomputes it.
 */
export function useGraph(graphId: string | null) {
  const setModel = useGraphStore((s) => s.setModel)
  const clearModel = useGraphStore((s) => s.clearModel)

  const query = useQuery({
    queryKey: ['graph', graphId],
    queryFn: async ({ signal }): Promise<GraphModel> => buildGraphModel(await fetchGraph(graphId, signal)),
    enabled: graphId !== null,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
  })

  const model = query.data

  useEffect(() => {
    if (!model) {
      if (graphId === null) clearModel()
      return
    }

    // setModel resets every piece of per-graph UI state — expansion, focus, evidence,
    // selection. A background refetch hands back a fresh object for the *same* graph, so
    // reseeding on object identity alone would silently wipe the user's work mid-session.
    // Only re-seed when the graph actually changed.
    const current = useGraphStore.getState().model
    const sameGraph =
      current.graphId === model.graphId &&
      current.nodes.length === model.nodes.length &&
      current.edges.length === model.edges.length
    if (sameGraph) return

    setModel(model)
  }, [model, graphId, setModel, clearModel])

  return query
}

export function useDeleteGraph() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (graphId: string) => deleteGraph(graphId),
    onSuccess: (_data, graphId) => {
      queryClient.invalidateQueries({ queryKey: ['graphs'] })
      queryClient.removeQueries({ queryKey: ['graph', graphId] })
    },
  })
}
