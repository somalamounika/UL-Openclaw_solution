import { useCallback, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Copy,
  Loader2,
  RefreshCw,
  Play,
} from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { buildGraphFromOpenClawRun, fetchOpenClawRuns } from '@/api/openclaw'
import { ApiError } from '@/api/client'
import type { OpenClawRunRow } from '@/types/openclaw'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { useGraphStore } from '@/state/graphStore'
import { useProcessingProgress, useProcessingProgressStore } from '@/hooks/useProcessingProgress'

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // ignore
  }
}

function categoryBadgeClass(category: string): string {
  const key = category.toLowerCase()
  if (key.includes('standard') || key.includes('regulation')) {
    return 'bg-blue-500/15 text-blue-700 dark:text-blue-300'
  }
  if (key.includes('test') || key.includes('lab')) {
    return 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
  }
  if (key.includes('certification')) {
    return 'bg-violet-500/15 text-violet-700 dark:text-violet-300'
  }
  if (key.includes('product') || key.includes('support')) {
    return 'bg-amber-500/15 text-amber-800 dark:text-amber-200'
  }
  return 'bg-[var(--surface-3)] text-fg-muted'
}

function RunRow({
  run,
  expanded,
  onToggle,
  onRun,
  running,
  activeBlob,
}: {
  run: OpenClawRunRow
  expanded: boolean
  onToggle: () => void
  onRun: (run: OpenClawRunRow) => void
  running: boolean
  activeBlob: string | null
}) {
  const isActive = running && activeBlob === run.metadata_blob
  const attachmentNames = run.attachments.map(
    (a) => a.original_filename || a.blob_path?.split('/').pop() || 'attachment',
  )

  return (
    <>
      <tr className="border-b border-[var(--border)] hover:bg-[var(--surface-2)]/60">
        <td className="px-3 py-2.5">
          <button
            type="button"
            className="flex items-center gap-1 text-left text-xs text-fg-muted hover:text-fg"
            onClick={onToggle}
            aria-expanded={expanded}
          >
            {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        </td>
        <td className="px-3 py-2.5 text-xs font-medium text-fg">{run.client_name}</td>
        <td className="px-3 py-2.5">
          <span className={cn('rounded px-2 py-0.5 text-2xs font-medium', categoryBadgeClass(run.category))}>
            {run.category}
          </span>
        </td>
        <td className="px-3 py-2.5 text-center text-xs tabular-nums text-fg-muted">{run.doc_count}</td>
        <td className="px-3 py-2.5 text-xs text-fg-muted">{run.intent}</td>
        <td className="max-w-[140px] truncate px-3 py-2.5 text-2xs text-fg-muted" title={run.recipient ?? ''}>
          {run.recipient ?? '—'}
        </td>
        <td className="px-3 py-2.5 text-right">
          <Button
            variant="primary"
            size="xs"
            disabled={running || run.doc_count === 0}
            onClick={() => onRun(run)}
          >
            {isActive ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
            Run
          </Button>
        </td>
      </tr>
      {expanded ? (
        <tr className="border-b border-[var(--border)] bg-[var(--surface-2)]/40">
          <td colSpan={7} className="px-4 py-3">
            <div className="grid gap-3 text-2xs text-fg-muted md:grid-cols-2">
              {run.message_id ? (
                <div className="md:col-span-2">
                  <div className="font-semibold uppercase tracking-wide text-fg-subtle">Message ID</div>
                  <button
                    type="button"
                    className="mt-1 inline-flex items-center gap-1 font-mono text-fg-muted hover:text-fg"
                    onClick={() => copyText(run.message_id!)}
                  >
                    {run.message_id}
                    <Copy className="h-3 w-3 opacity-60" />
                  </button>
                </div>
              ) : null}
              <div>
                <div className="font-semibold uppercase tracking-wide text-fg-subtle">Executive summary</div>
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {(run.executive_summary.length ? run.executive_summary : ['No summary recorded.']).map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="font-semibold uppercase tracking-wide text-fg-subtle">Recommended action</div>
                <p className="mt-1 leading-relaxed">{run.recommended_action || '—'}</p>
                <div className="mt-3 font-semibold uppercase tracking-wide text-fg-subtle">Attachments</div>
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {(attachmentNames.length ? attachmentNames : ['None']).map((name) => (
                    <li key={name}>{name}</li>
                  ))}
                </ul>
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  )
}

export function OpenClawPage() {
  const queryClient = useQueryClient()
  const setGraphId = useGraphStore((s) => s.setGraphId)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [buildError, setBuildError] = useState<string | null>(null)
  const [buildSuccess, setBuildSuccess] = useState<{ graphId: string } | null>(null)
  const [running, setRunning] = useState(false)
  const [activeBlob, setActiveBlob] = useState<string | null>(null)
  const [buildCaption, setBuildCaption] = useState('Downloading attachments…')

  const { phase, percent, caption } = useProcessingProgress()
  const progressRunning = phase === 'running'

  const runsQuery = useQuery({
    queryKey: ['openclaw-runs'],
    queryFn: fetchOpenClawRuns,
    staleTime: 30_000,
  })

  const runs = useMemo(() => runsQuery.data?.runs ?? [], [runsQuery.data])

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['openclaw-runs'] })
  }, [queryClient])

  const handleRun = useCallback(
    async (run: OpenClawRunRow) => {
      setBuildError(null)
      setBuildSuccess(null)
      setRunning(true)
      setActiveBlob(run.metadata_blob)
      useProcessingProgressStore.getState().start()
      setBuildCaption('Downloading attachments from Azure…')

      const captionTimer = window.setTimeout(() => setBuildCaption('Extracting entities…'), 8000)
      const captionTimer2 = window.setTimeout(() => setBuildCaption('Constructing knowledge graph…'), 25000)

      try {
        const result = await buildGraphFromOpenClawRun(run.metadata_blob)
        useProcessingProgressStore.getState().complete()
        if (result.graph_id) {
          setGraphId(result.graph_id)
          setBuildSuccess({ graphId: result.graph_id })
        }
      } catch (error) {
        useProcessingProgressStore.getState().fail()
        const message =
          error instanceof ApiError
            ? error.detail
            : error instanceof Error
              ? error.message
              : 'Graph build failed.'
        setBuildError(message)
      } finally {
        window.clearTimeout(captionTimer)
        window.clearTimeout(captionTimer2)
        setRunning(false)
        setActiveBlob(null)
      }
    },
    [setGraphId],
  )

  const showProgress = running || progressRunning

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
      className="mx-auto flex h-full w-full max-w-6xl flex-col px-6 py-8"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-fg">Overview</h1>
          <p className="mt-1 text-sm text-fg-muted">
            Triage runs archived from the email pipeline in Azure Blob Storage (
            <code className="text-fg">openclaw-ul</code>).
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={refresh} disabled={runsQuery.isFetching}>
          <RefreshCw className={cn('h-3.5 w-3.5', runsQuery.isFetching && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      {buildSuccess ? (
        <div className="mt-4 rounded-lg border border-[var(--success)]/40 bg-[var(--success)]/10 px-3 py-2.5 text-xs text-fg">
          Knowledge graph built successfully ({buildSuccess.graphId.slice(0, 8)}…).{' '}
          <Link to="/graph" className="font-semibold text-accent underline-offset-2 hover:underline">
            View in Knowledge Graph
          </Link>
        </div>
      ) : null}

      {buildError ? (
        <div className="mt-4 flex items-start gap-2 rounded-lg border border-[var(--danger)]/40 bg-[var(--danger)]/10 px-3 py-2.5 text-xs text-fg-muted">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--danger)]" />
          {buildError}
        </div>
      ) : null}

      {showProgress ? (
        <div className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] px-4 py-3">
          <div className="text-xs font-medium text-fg">{buildCaption || caption}</div>
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-3)]">
            <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${Math.max(2, percent)}%` }} />
          </div>
        </div>
      ) : null}

      {runsQuery.isError ? (
        <div className="mt-6 rounded-lg border border-[var(--danger)]/40 bg-[var(--danger)]/10 p-4 text-xs text-fg-muted">
          {(runsQuery.error as ApiError)?.detail ?? 'Could not load triage runs.'}
        </div>
      ) : null}

      <div className="mt-6 min-h-0 flex-1 overflow-auto rounded-lg border border-[var(--border)]">
        <table className="w-full min-w-[780px] border-collapse text-left">
          <thead className="sticky top-0 z-10 bg-[var(--surface)] text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
            <tr className="border-b border-[var(--border)]">
              <th className="w-8 px-3 py-2" aria-label="Expand" />
              <th className="px-3 py-2">Client name</th>
              <th className="px-3 py-2">Category</th>
              <th className="px-3 py-2 text-center">No. of docs</th>
              <th className="px-3 py-2">Intent</th>
              <th className="px-3 py-2">Responsible team</th>
              <th className="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {runsQuery.isLoading ? (
              <tr>
                <td colSpan={7} className="px-3 py-8 text-center text-sm text-fg-muted">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                  <span className="mt-2 block">Loading triage runs…</span>
                </td>
              </tr>
            ) : runs.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-8 text-center text-sm text-fg-muted">
                  No archived runs found in Azure Blob Storage.
                </td>
              </tr>
            ) : (
              runs.map((run) => (
                <RunRow
                  key={run.metadata_blob}
                  run={run}
                  expanded={expandedId === run.metadata_blob}
                  onToggle={() =>
                    setExpandedId((current) => (current === run.metadata_blob ? null : run.metadata_blob))
                  }
                  onRun={handleRun}
                  running={running}
                  activeBlob={activeBlob}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
    </motion.div>
  )
}
