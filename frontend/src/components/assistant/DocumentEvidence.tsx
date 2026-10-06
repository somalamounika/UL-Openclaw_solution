import { useState } from 'react'
import { ChevronRight, FileText, Library } from 'lucide-react'
import { cn } from '@/lib/utils'
import { isReferenceFile } from '@/lib/referenceDocs'

/**
 * One citation row. Deliberately shared with the inspector drawer so a citation looks
 * identical wherever it appears — chat answer or edge evidence.
 */
export function CitationRow({
  file,
  pageLabel,
  chunkId,
  className,
}: {
  file: string
  pageLabel?: string
  chunkId?: string
  className?: string
}) {
  // Catalogue files from backend/documents also feed graph extraction
  // (standards / certifications / tests) and chatbot retrieval.
  const isReference = isReferenceFile(file)

  return (
    <div
      className={cn(
        'flex items-center gap-2 rounded-md border px-2 py-1.5 text-2xs',
        isReference
          ? 'border-[var(--border)] bg-[var(--surface-3)]/60 text-fg-muted'
          : 'border-[var(--border)] bg-[var(--surface-3)] text-fg',
        className,
      )}
    >
      {isReference ? (
        <Library className="h-3 w-3 shrink-0 text-fg-subtle" />
      ) : (
        <FileText className="h-3 w-3 shrink-0 text-accent" />
      )}
      <span className="min-w-0 flex-1 truncate font-medium">{file}</span>
      {pageLabel ? <span className="shrink-0 tabular-nums text-fg-subtle">{pageLabel}</span> : null}
      {chunkId ? (
        <span className="shrink-0 rounded bg-[var(--surface)] px-1 py-px font-mono text-[10px] text-fg-subtle">
          {chunkId}
        </span>
      ) : null}
    </div>
  )
}

export function DocumentEvidence({
  sources,
}: {
  sources: Array<{ source_file: string; page_range: { start: number; end: number }; chunk_id: string }>
}) {
  const [open, setOpen] = useState(false)

  if (sources.length === 0) return null

  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        className="flex items-center gap-1 text-2xs font-semibold uppercase tracking-wide text-fg-subtle transition-colors hover:text-fg-muted"
      >
        <ChevronRight
          className={cn('h-3 w-3 shrink-0 transition-transform', open && 'rotate-90')}
          aria-hidden
        />
        Sources ({sources.length})
      </button>

      {open ? (
        <div className="space-y-1">
          {sources.map((source, index) => {
            const { start, end } = source.page_range || { start: 0, end: 0 }
            const startNum = Number(start)
            const endNum = Number(end)
            const hasPage = Number.isFinite(startNum) && startNum > 0
            const pageLabel = !hasPage
              ? undefined
              : startNum === endNum || !Number.isFinite(endNum) || endNum <= 0
                ? `p. ${startNum}`
                : `pp. ${startNum}–${endNum}`
            return (
              <CitationRow
                key={`${source.chunk_id}-${index}`}
                file={source.source_file}
                pageLabel={pageLabel}
              />
            )
          })}
        </div>
      ) : null}
    </div>
  )
}
