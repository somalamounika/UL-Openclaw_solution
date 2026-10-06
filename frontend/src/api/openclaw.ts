import { apiFetch, apiJson } from './client'
import type { OpenClawBuildGraphResponse, OpenClawRunsResponse } from '@/types/openclaw'

export function fetchOpenClawRuns(): Promise<OpenClawRunsResponse> {
  return apiFetch<OpenClawRunsResponse>('/api/openclaw/runs')
}

export function buildGraphFromOpenClawRun(
  metadataBlob: string,
  signal?: AbortSignal,
): Promise<OpenClawBuildGraphResponse> {
  return apiJson<OpenClawBuildGraphResponse>(
    '/api/openclaw/runs/build-graph',
    { metadata_blob: metadataBlob },
    { signal },
  )
}
