import { useCallback } from 'react'
import { motion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Play, X } from 'lucide-react'
import { UploadDropzone } from '@/components/upload/UploadDropzone'
import { UploadedFiles } from '@/components/upload/UploadedFiles'
import { ProcessingPage } from '@/components/upload/ProcessingPage'
import { SavedGraphs } from '@/components/upload/SavedGraphs'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { useProcessingStore } from '@/state/processingStore'
import { useGraphStore } from '@/state/graphStore'

/**
 * Page 1. Deliberately spacious and single-purpose: get documents in, and build a graph.
 * The run itself takes over the whole page rather than shrinking into a rail.
 */
export function UploadPage() {
  const navigate = useNavigate()
  const files = useProcessingStore((s) => s.files)
  const rejected = useProcessingStore((s) => s.rejected)
  const status = useProcessingStore((s) => s.status)
  const result = useProcessingStore((s) => s.result)
  const error = useProcessingStore((s) => s.error)
  const addFiles = useProcessingStore((s) => s.addFiles)
  const rejectFiles = useProcessingStore((s) => s.rejectFiles)
  const dismissRejected = useProcessingStore((s) => s.dismissRejected)
  const removeFile = useProcessingStore((s) => s.removeFile)
  const run = useProcessingStore((s) => s.run)
  const reset = useProcessingStore((s) => s.reset)

  const setGraphId = useGraphStore((s) => s.setGraphId)

  /**
   * A finished run has one destination: the graph it just built. The completion screen
   * in between was a report on work the user did not ask to read.
   */
  const build = useCallback(async () => {
    const outcome = await run()
    if (!outcome?.graph_id) return
    setGraphId(outcome.graph_id)
    // Keep `result` so the assistant still receives document_id after this redirect.
    navigate('/graph')
  }, [run, setGraphId, navigate])

  if (status === 'running') return <ProcessingPage />

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
      className="mx-auto w-full max-w-2xl px-6 py-12"
    >
      <div className="text-center">
        <h1 className="text-xl font-semibold tracking-tight text-fg">
          Build your compliance knowledge graph
        </h1>
        <p className="mt-2 text-sm text-fg-muted">
          Upload product and supplier documentation. Every claim stays traceable to the page
          it came from.
        </p>
      </div>

      <div className="mt-9">
        <UploadDropzone onFiles={addFiles} onReject={rejectFiles} />
      </div>

      {rejected.length > 0 ? (
        <ul className="mt-3 space-y-1.5">
          {rejected.map((file) => (
            <li
              key={file.name}
              className="flex items-start gap-2 rounded-md border border-[var(--danger)]/40 bg-[var(--danger)]/10 px-2.5 py-2"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--danger)]" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium text-fg">{file.name}</div>
                <div className="text-2xs text-fg-muted">{file.reason}</div>
              </div>
              <button
                type="button"
                aria-label={`Dismiss ${file.name}`}
                onClick={() => dismissRejected(file.name)}
                className="text-fg-subtle hover:text-fg"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {files.length > 0 ? (
        <section className="mt-7">
          <h2 className="mb-2 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
            Uploaded documents
          </h2>
          <UploadedFiles files={files} onRemove={removeFile} />
        </section>
      ) : null}

      {status === 'succeeded' && result && !result.graph_id ? (
        <div className="mt-6 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] p-3">
          <p className="text-2xs leading-relaxed text-fg-muted">
            The documents were processed, but no graph came back. Try building again, or pick an
            existing graph below.
          </p>
          <Button variant="ghost" size="xs" className="mt-1.5" onClick={reset}>
            Dismiss
          </Button>
        </div>
      ) : null}

      {status === 'failed' && error ? (
        <div className="mt-6 rounded-lg border border-[var(--danger)]/40 bg-[var(--danger)]/10 p-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--danger)]">
            <AlertTriangle className="h-3.5 w-3.5" />
            Processing failed
          </div>
          <p className="mt-1 text-2xs leading-relaxed text-fg-muted">{error}</p>
        </div>
      ) : null}

      {status === 'abandoned' ? (
        <div className="mt-6 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] p-3">
          <p className="text-2xs leading-relaxed text-fg-muted">
            Stopped watching. The pipeline is still running on the server — when it finishes,
            the new graph appears under <span className="text-fg">Saved graphs</span> below.
          </p>
          <Button variant="ghost" size="xs" className="mt-1.5" onClick={reset}>
            Dismiss
          </Button>
        </div>
      ) : null}

      <div className="mt-8">
        <Button
          variant="primary"
          size="md"
          className="w-full justify-center"
          disabled={files.length === 0}
          onClick={build}
        >
          <Play className="h-3.5 w-3.5" />
          Build Knowledge Graph
        </Button>
        {files.length === 0 ? (
          <p className="mt-2 text-center text-2xs text-fg-subtle">
            Add at least one document first.
          </p>
        ) : null}
      </div>

      <Separator className="my-10" />

      <section>
        <h2 className="mb-2.5 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
          Saved graphs
        </h2>
        <SavedGraphs />
      </section>
    </motion.div>
  )
}
