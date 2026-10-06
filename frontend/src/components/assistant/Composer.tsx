import { useEffect, useRef } from 'react'
import { CornerDownLeft, X } from 'lucide-react'
import { useChatStore } from '@/state/chatStore'
import { Button } from '@/components/ui/button'

/**
 * Enter sends, Shift+Enter inserts a newline. Mention chips arrive from the graph page's
 * "Ask Assistant" action, so a question can be anchored to exactly what was clicked.
 */
export function Composer({
  disabled,
  disabledHint,
  pending,
  onSend,
}: {
  disabled: boolean
  disabledHint?: string
  pending: boolean
  onSend: (question: string) => void
}) {
  const value = useChatStore((s) => s.draft)
  const setDraft = useChatStore((s) => s.setDraft)
  const mentions = useChatStore((s) => s.mentions)
  const removeMention = useChatStore((s) => s.removeMention)
  const clearMentions = useChatStore((s) => s.clearMentions)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    if (mentions.length > 0) textareaRef.current?.focus()
  }, [mentions.length])

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`
  }, [value])

  const submit = () => {
    const question = [...mentions.map((m) => `@${m}`), value.trim()].filter(Boolean).join(' ')
    if (!question || disabled || pending) return
    onSend(question)
    setDraft('')
    clearMentions()
  }

  return (
    <div className="border-t border-[var(--border)] bg-[var(--surface)] px-6 py-3">
      <div className="mx-auto w-full max-w-3xl">
        {mentions.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1">
            {mentions.map((mention) => (
              <span
                key={mention}
                className="inline-flex items-center gap-1 rounded bg-[var(--accent-soft)] px-2 py-0.5 text-2xs text-fg"
              >
                @{mention}
                <button
                  type="button"
                  aria-label={`Remove ${mention}`}
                  onClick={() => removeMention(mention)}
                  className="text-fg-subtle hover:text-fg"
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              </span>
            ))}
          </div>
        ) : null}

        <div className="relative">
          <textarea
            ref={textareaRef}
            rows={1}
            value={value}
            disabled={disabled}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                submit()
              }
            }}
            placeholder={
              disabled
                ? (disabledHint ?? 'Unavailable')
                : 'Ask anything about your compliance knowledge graph…'
            }
            aria-label="Ask the assistant"
            className="w-full resize-none rounded-xl border border-[var(--border)] bg-[var(--surface-3)] py-3 pl-3.5 pr-12 text-sm text-fg placeholder:text-fg-subtle focus-visible:border-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-60"
          />
          <Button
            variant="primary"
            size="icon"
            className="absolute bottom-2.5 right-2.5"
            disabled={disabled || pending || (!value.trim() && mentions.length === 0)}
            onClick={submit}
            aria-label="Send question"
          >
            <CornerDownLeft className="h-3.5 w-3.5" />
          </Button>
        </div>

        <p className="mt-1.5 text-2xs text-fg-subtle">
          {disabled && disabledHint ? disabledHint : 'Enter to send · Shift+Enter for a new line'}
        </p>
      </div>
    </div>
  )
}
