import { apiUpload } from './client'
import type { UploadResponse } from '@/types/api'

/** POST /api/ul/upload — PDF only; the server rejects anything else with a 400. */
export async function uploadDocuments(files: File[], signal?: AbortSignal): Promise<UploadResponse> {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  return apiUpload<UploadResponse>('/api/ul/upload', form, { signal })
}
