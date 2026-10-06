import { useEffect, useRef } from 'react'
import type { ChatMessage, ThinkingPhase } from '@/state/chatStore'
import { MessageBubble } from './MessageBubble'
import { ThinkingState } from './ThinkingState'
import { ScrollArea } from '@/components/ui/scroll-area'

const STARTERS = [
  'Which standards apply to the power supply?',
  'What components are supplied by third parties?',
  'Which clauses does the certification depend on?',
  'Show the lineage from the product to the applicable test.',
]

export function MessageList({
  messages,
  pending,
  phase,
  hasGraph,
  onStarter,
}: {
  messages: ChatMessage[]
  pending: boolean
  phase: ThinkingPhase
  hasGraph: boolean
  onStarter: (question: string) => void
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, pending])

  if (messages.length === 0) {
    return (
      <ScrollArea className="flex-1">
        <div className="mx-auto w-full max-w-3xl px-6 py-10">
          <p className="text-sm leading-relaxed text-fg-muted">
            Ask about the graph and its source documents. Every answer cites the pages it came
            from, and relationship questions are drawn as lineage you can open in the graph.
          </p>
          <div className="mt-5 grid gap-2 sm:grid-cols-2">
            {STARTERS.map((starter) => (
              <button
                key={starter}
                type="button"
                disabled={!hasGraph}
                onClick={() => onStarter(starter)}
                className="rounded-lg border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2.5 text-left text-xs text-fg-muted transition-colors hover:border-[var(--accent)] hover:text-fg disabled:opacity-45 disabled:hover:border-[var(--border)]"
              >
                {starter}
              </button>
            ))}
          </div>
          {!hasGraph ? (
            <p className="mt-4 text-2xs leading-relaxed text-fg-subtle">
              Build or select a knowledge graph first — the assistant answers against a
              specific graph and its documents.
            </p>
          ) : null}
        </div>
      </ScrollArea>
    )
  }

  return (
    <ScrollArea className="flex-1">
      <div
        className="mx-auto w-full max-w-3xl space-y-7 px-6 py-8"
        aria-live="polite"
        aria-relevant="additions"
      >
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
        {pending ? <ThinkingState phase={phase} /> : null}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  )
}
