import { apiFetch } from './client'
import type { DeleteGraphResponse, GraphListResponse, GraphResponse, HealthResponse } from '@/types/api'

/** GET /api/ul/graphs — newest first. */
export async function listGraphs(signal?: AbortSignal): Promise<GraphListResponse> {
  return apiFetch<GraphListResponse>('/api/ul/graphs', { signal })
}

/**
 * GET /api/ul/graph — the whole canonical UL graph in one shot (server caps: 2000 nodes / 4000 edges,
 * no pagination). Omitting graph_id returns the global graph. Document ids do not isolate entities.
 */
export async function fetchGraph(graphId: string | null, signal?: AbortSignal): Promise<GraphResponse> {
  const query = graphId ? `?graph_id=${encodeURIComponent(graphId)}` : ''
  return apiFetch<GraphResponse>(`/api/ul/graph${query}`, { signal })
}

/** DELETE /api/ul/graphs/{graph_id} — deletes only that graph. */
export async function deleteGraph(graphId: string, signal?: AbortSignal): Promise<DeleteGraphResponse> {
  return apiFetch<DeleteGraphResponse>(`/api/ul/graphs/${encodeURIComponent(graphId)}`, {
    method: 'DELETE',
    signal,
  })
}

export async function checkHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiFetch<HealthResponse>('/health', { signal })
}
