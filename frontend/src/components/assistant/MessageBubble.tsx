import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AlertTriangle, SearchX } from 'lucide-react'
import type { ChatMessage } from '@/state/chatStore'
import { DocumentEvidence } from './DocumentEvidence'
import { ShowOnGraphButton } from './ShowOnGraphButton'

export function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] rounded-xl rounded-br-sm bg-[var(--accent-soft)] px-3.5 py-2.5 text-sm text-fg">
          {message.text}
        </div>
      </div>
    )
  }

  if (message.error) {
    return (
      <div className="flex items-start gap-2 rounded-xl border border-[var(--danger)]/40 bg-[var(--danger)]/10 p-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--danger)]" />
        <p className="text-xs leading-relaxed text-fg-muted">{message.error}</p>
      </div>
    )
  }

  if (message.isNoResults) {
    return (
      <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-2)] p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-fg">
          <SearchX className="h-4 w-4 text-fg-subtle" />
          Nothing matched in this graph
        </div>
        <p className="mt-1.5 text-xs leading-relaxed text-fg-muted">
          Neither the knowledge graph nor its indexed documents contained anything relevant.
          Try naming a specific product, component or standard, or switch to a different graph.
        </p>
      </div>
    )
  }

  const question = message.question ?? ''
  const response = message.response
  const hasLineage =
    !!response &&
    (response.graph_evidence.relationships.length > 0 || response.graph_evidence.nodes.length > 0)

  return (
    <div className="space-y-3.5">
      <div className="space-y-2">
        <div className="text-2xs font-semibold uppercase tracking-wide text-fg-subtle">Answer</div>
        <div className="markdown-body text-sm">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
        </div>
      </div>

      {response ? (
        <div className="space-y-3.5">
          {hasLineage ? <ShowOnGraphButton question={question} response={response} /> : null}

          <DocumentEvidence sources={response.sources} />
        </div>
      ) : null}
    </div>
  )
}
