import { useCallback, useRef, useState } from 'react'
import { FilePlus2, Upload } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { RejectedFile } from '@/state/processingStore'

/**
 * The accepted set mirrors SUPPORTED_UPLOAD_EXTENSIONS in
 * backend/ul/document_extractor.py: PDF, DOCX and email. Anything else is rejected here
 * with the reason on the chip rather than round-tripping to the server to be told 400.
 */
const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.eml', '.msg']
export function UploadDropzone({
  onFiles,
  onReject,
  disabled,
}: {
  onFiles: (files: File[]) => void
  onReject: (rejected: RejectedFile[]) => void
  disabled?: boolean
}) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)

  const partition = useCallback(
    (files: File[]) => {
      const accepted: File[] = []
      const rejected: RejectedFile[] = []
      for (const file of files) {
        const name = file.name.toLowerCase()
        if (!ACCEPTED_EXTENSIONS.some((extension) => name.endsWith(extension))) {
          rejected.push({
            name: file.name,
            reason: 'Unsupported file type — PDF, DOCX and email (.eml, .msg) only.',
          })
        } else if (file.size === 0) {
          rejected.push({ name: file.name, reason: 'File is empty.' })
        } else {
          accepted.push(file)
        }
      }
      if (accepted.length) onFiles(accepted)
      if (rejected.length) onReject(rejected)
    },
    [onFiles, onReject],
  )

  return (
    <div
      className={cn(
        'relative rounded-lg border border-dashed p-4 text-center transition-colors',
        dragging ? 'border-[var(--accent)] bg-[var(--accent-soft)]' : 'border-[var(--border-strong)] bg-[var(--surface-2)]',
        disabled && 'pointer-events-none opacity-50',
      )}
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        partition([...event.dataTransfer.files])
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.docx,.eml,.msg,application/pdf"
        multiple
        className="sr-only"
        onChange={(event) => {
          partition([...(event.target.files ?? [])])
          event.target.value = ''
        }}
        id="document-input"
      />
      <label
        htmlFor="document-input"
        className="flex cursor-pointer flex-col items-center gap-1.5 focus-within:outline-none"
      >
        {dragging ? (
          <FilePlus2 className="h-5 w-5 text-accent" />
        ) : (
          <Upload className="h-5 w-5 text-fg-subtle" />
        )}
        <span className="text-xs font-medium text-fg">Drop documents here</span>
        <span className="text-2xs text-fg-subtle">
          or click to browse · PDF, DOCX, Email (.eml, .msg) · Multiple files supported
        </span>
      </label>
    </div>
  )
}
