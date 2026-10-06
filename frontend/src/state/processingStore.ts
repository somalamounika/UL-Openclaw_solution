import { create } from 'zustand'
import { processDocuments } from '@/api/processing'
import { ApiError } from '@/api/client'
import type { ProcessResponse } from '@/types/api'
import { useProcessingProgressStore } from '@/hooks/useProcessingProgress'

/**
 * Owns the single blocking /process call plus the upload queue. It is a store, not page
 * state, so a user who navigates away mid-run and comes back still sees their run.
 *
 * The request cannot be cancelled server-side: "stop watching" aborts this browser's
 * fetch only, and the UI says exactly that.
 */
export type ProcessingStatus = 'idle' | 'running' | 'succeeded' | 'failed' | 'abandoned'

export type FileState = 'queued' | 'uploaded' | 'processed' | 'failed'

export interface TrackedFile {
  id: string
  file: File
  state: FileState
  /** Matched from the /process response's warnings[] when this file failed. */
  warning?: string
}

export interface RejectedFile {
  name: string
  reason: string
}

interface ProcessingState {
  files: TrackedFile[]
  rejected: RejectedFile[]
  status: ProcessingStatus
  result: ProcessResponse | null
  error: string | null

  addFiles: (files: File[]) => void
  rejectFiles: (rejected: RejectedFile[]) => void
  dismissRejected: (name: string) => void
  removeFile: (id: string) => void
  run: () => Promise<ProcessResponse | null>
  stopWatching: () => void
  reset: () => void
}

let fileCounter = 0
let controller: AbortController | null = null

export const useProcessingStore = create<ProcessingState>((set, get) => ({
  files: [],
  rejected: [],
  status: 'idle',
  result: null,
  error: null,

  addFiles: (incoming) =>
    set((state) => ({
      files: [
        ...state.files,
        ...incoming.map((file) => ({ id: `f-${fileCounter++}`, file, state: 'queued' as const })),
      ],
    })),

  rejectFiles: (rejected) => set({ rejected }),
  dismissRejected: (name) => set((state) => ({ rejected: state.rejected.filter((r) => r.name !== name) })),
  removeFile: (id) => set((state) => ({ files: state.files.filter((f) => f.id !== id) })),

  run: async () => {
    const { files } = get()
    if (files.length === 0) return null

    controller?.abort()
    const abort = new AbortController()
    controller = abort

    set({ status: 'running', result: null, error: null, rejected: [] })
    useProcessingProgressStore.getState().start()

    try {
      const result = await processDocuments({
        files: files.map((tracked) => tracked.file),
        // No target filter in the UI: every run graphs everything the documents contain.
        targetProductName: null,
        signal: abort.signal,
      })
      useProcessingProgressStore.getState().complete()
      set((state) => ({
        status: 'succeeded',
        result,
        // warnings[] names the files that failed; everything else processed.
        files: state.files.map((tracked) => {
          const warning = result.warnings.find((message) => message.includes(tracked.file.name))
          return warning
            ? { ...tracked, state: 'failed' as const, warning }
            : { ...tracked, state: 'processed' as const }
        }),
      }))
      return result
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        useProcessingProgressStore.getState().reset()
        set({ status: 'abandoned', result: null, error: null })
        return null
      }
      useProcessingProgressStore.getState().fail()
      const message =
        error instanceof ApiError
          ? error.isNetworkError
            ? 'Could not reach the backend. Is the FastAPI service running on port 8000?'
            : error.detail
          : error instanceof Error
            ? error.message
            : 'Processing failed.'
      set({ status: 'failed', result: null, error: message })
      return null
    } finally {
      controller = null
    }
  },

  /** Abandons this browser's wait only. The pipeline keeps running on the server. */
  stopWatching: () => controller?.abort(),

  reset: () => {
    useProcessingProgressStore.getState().reset()
    set({ status: 'idle', result: null, error: null, rejected: [] })
  },
}))
