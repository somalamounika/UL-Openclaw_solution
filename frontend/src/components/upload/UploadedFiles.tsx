import { AlertTriangle, CheckCircle2, Clock, FileText, X } from 'lucide-react'
import { formatBytes, cn } from '@/lib/utils'
import type { FileState, TrackedFile } from '@/state/processingStore'

const STATE_META: Record<FileState, { icon: typeof Clock; className: string; label: string }> = {
  queued: { icon: Clock, className: 'text-fg-subtle', label: 'Queued' },
  uploaded: { icon: CheckCircle2, className: 'text-accent', label: 'Uploaded' },
  processed: { icon: CheckCircle2, className: 'text-[var(--success)]', label: 'Processed' },
  failed: { icon: AlertTriangle, className: 'text-[var(--danger)]', label: 'Failed' },
}

export function UploadedFiles({
  files,
  onRemove,
  disabled,
}: {
  files: TrackedFile[]
  onRemove: (id: string) => void
  disabled?: boolean
}) {
  if (files.length === 0) return null

  return (
    <ul className="space-y-1.5">
      {files.map((tracked) => {
        const meta = STATE_META[tracked.state]
        const Icon = meta.icon
        return (
          <li key={tracked.id} className="card p-2">
            <div className="flex items-start gap-2">
              <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fg-subtle" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium text-fg">{tracked.file.name}</div>
                <div className="mt-0.5 flex items-center gap-1.5 text-2xs text-fg-subtle">
                  <span>{formatBytes(tracked.file.size)}</span>
                  <span>·</span>
                  <span className={cn('inline-flex items-center gap-1', meta.className)}>
                    <Icon className="h-2.5 w-2.5" />
                    {meta.label}
                  </span>
                </div>
              </div>
              {!disabled ? (
                <button
                  type="button"
                  onClick={() => onRemove(tracked.id)}
                  aria-label={`Remove ${tracked.file.name}`}
                  className="rounded p-0.5 text-fg-subtle hover:bg-[var(--surface-3)] hover:text-fg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)]"
                >
                  <X className="h-3 w-3" />
                </button>
              ) : null}
            </div>
            {tracked.warning ? (
              <p className="mt-1.5 rounded border border-[var(--danger)]/30 bg-[var(--danger)]/10 px-1.5 py-1 text-2xs leading-snug text-[var(--danger)]">
                {tracked.warning}
              </p>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}
