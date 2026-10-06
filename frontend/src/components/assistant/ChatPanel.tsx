import { useCallback } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Eraser } from 'lucide-react'
import { useChatStore } from '@/state/chatStore'
import { useGraphStore } from '@/state/graphStore'
import { MessageList } from './MessageList'
import { Composer } from './Composer'
import { LineagePanel } from './LineagePanel'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export function ChatPanel({ documentId }: { documentId: string | null }) {
  const graphId = useGraphStore((s) => s.graphId)
  const hasGraph = useGraphStore((s) => s.model.nodes.length > 0)

  const messages = useChatStore((s) => s.messages)
  const pending = useChatStore((s) => s.pending)
  const phase = useChatStore((s) => s.phase)
  const lineagePanel = useChatStore((s) => s.lineagePanel)
  const send = useChatStore((s) => s.send)
  const clear = useChatStore((s) => s.clear)
  const closeLineagePanel = useChatStore((s) => s.closeLineagePanel)

  const onSend = useCallback(
    (question: string) => void send(question, graphId, documentId),
    [send, graphId, documentId],
  )

  const lineageOpen = Boolean(lineagePanel)

  return (
    <div className="relative flex h-full min-h-0 overflow-hidden">
      {/* AI Assistant — stays centered when closed; shifts left when lineage opens on the right */}
      <div
        className={cn(
          'flex h-full min-h-0 min-w-0 flex-1 flex-col transition-[margin] duration-300',
          lineageOpen ? 'lg:mr-[min(100%,28rem)]' : '',
        )}
      >
        <div className="mx-auto flex h-full min-h-0 w-full max-w-3xl flex-col">
          <div className="flex shrink-0 items-start justify-between gap-4 border-b border-[var(--border)] px-6 py-4">
            <div>
              <h1 className="text-base font-semibold tracking-tight text-fg">AI Assistant</h1>
              <p className="mt-0.5 text-xs text-fg-muted">
                Grounded in your compliance documents and knowledge graph
              </p>
            </div>
            {messages.length > 0 ? (
              <Button variant="ghost" size="sm" onClick={clear}>
                <Eraser className="h-3 w-3" />
                Clear
              </Button>
            ) : null}
          </div>

          <MessageList
            messages={messages}
            pending={pending}
            phase={phase}
            hasGraph={hasGraph}
            onStarter={onSend}
          />

          <Composer
            disabled={!graphId}
            disabledHint={!graphId ? 'Select or build a knowledge graph before asking a question.' : undefined}
            pending={pending}
            onSend={onSend}
          />
        </div>
      </div>

      {/* Right-side lineage popup — fills the empty space beside chat */}
      <AnimatePresence>
        {lineageOpen ? (
          <>
            <motion.button
              type="button"
              aria-label="Close lineage"
              className="absolute inset-0 z-30 bg-black/15 lg:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={closeLineagePanel}
            />
            <motion.aside
              className="absolute inset-y-0 right-0 z-40 flex w-[min(100%,24rem)] flex-col border-l border-[var(--border)] bg-[var(--surface)] shadow-xl sm:w-[28rem]"
              initial={{ x: '100%' }}
              animate={{ x: 0 }}
              exit={{ x: '100%' }}
              transition={{ type: 'spring', stiffness: 380, damping: 36 }}
            >
              <LineagePanel />
            </motion.aside>
          </>
        ) : null}
      </AnimatePresence>
    </div>
  )
}
