import { Network } from 'lucide-react'
import { useChatStore } from '@/state/chatStore'
import { Button } from '@/components/ui/button'
import type { ChatResponse } from '@/types/api'

/**
 * Opens the left lineage popup using lineage already returned on the chat response.
 * Does not call Neo4j again — Neo4j was fetched in parallel during /chat.
 */
export function ShowOnGraphButton({
  question,
  response,
}: {
  question: string
  response: ChatResponse
}) {
  const openLineagePanel = useChatStore((s) => s.openLineagePanel)

  const cited = response.graph_evidence.nodes
  if (cited.length === 0 && response.graph_evidence.relationships.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="primary" size="sm" onClick={() => openLineagePanel(question, response)}>
        <Network className="h-3 w-3" />
        View lineage
      </Button>
    </div>
  )
}
