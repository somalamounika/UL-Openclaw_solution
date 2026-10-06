import { motion } from 'framer-motion'
import { ChatPanel } from '@/components/assistant/ChatPanel'
import { useProcessingStore } from '@/state/processingStore'

/** Page 2. The chat gets the whole viewport — never a narrow rail beside the graph. */
export function AssistantPage() {
  const documentId = useProcessingStore((s) => s.result?.document_id ?? null)

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2 }}
      className="h-full min-h-0"
    >
      <ChatPanel documentId={documentId} />
    </motion.div>
  )
}
