import { apiUpload } from './client'
import type { ProcessResponse } from '@/types/api'

interface ProcessArgs {
  documentId?: string | null
  files?: File[]
  /** Optional; omitted unless the caller sets a non-empty target. */
  targetProductName?: string | null
  signal?: AbortSignal
}

/**
 * POST /api/ul/process — blocking. The backend runs its full pipeline and only
 * responds once the graph has been written.
 *
 * Do not set Content-Type manually — the browser must generate the multipart boundary.
 */
export async function processDocuments({
  documentId,
  files,
  targetProductName,
  signal,
}: ProcessArgs): Promise<ProcessResponse> {
  const form = new FormData()

  const uploads = (files ?? []).filter((file) => Boolean(file?.name))
  if (uploads.length) {
    for (const file of uploads) {
      form.append('files', file, file.name)
    }
  } else if (documentId) {
    form.append('document_id', documentId)
  }

  const target = targetProductName?.trim()
  if (target) {
    form.append('target_product_name', target)
  }

  return apiUpload<ProcessResponse>('/api/ul/process', form, { signal })
}
