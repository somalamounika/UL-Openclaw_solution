import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { GitBranch, Network } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { ChatEvidenceNode, ChatResponse } from '@/types/api'
import { buildLineageChains, unchainedNodes } from '@/lib/lineage'
import { useGraphStore } from '@/state/graphStore'
import { useChatStore } from '@/state/chatStore'
import { LineageNode } from './LineageNode'
import { LineageEdge } from './LineageEdge'
import { Button } from '@/components/ui/button'
import { typeFill } from '@/lib/ontology'

/** Chains beyond this are collapsed behind a "+N more" control. */
const VISIBLE_CHAINS = 2
/** Hops beyond this inside one chain collapse too — a 20-hop column is unreadable. */
const VISIBLE_STEPS = 5

/**
 * Renders the lineage the assistant actually cited: nodes and hops come straight from
 * `graph_evidence`, in the backend's own direction, with the backend's own relationship
 * names. Nothing here infers a link the answer did not contain.
 */
export function LineageView({
  question,
  response,
  compact = false,
}: {
  question: string
  response: ChatResponse
  /** When true, omit the rail CTA (the parent panel already owns navigation). */
  compact?: boolean
}) {
  const navigate = useNavigate()
  const applyChatEvidence = useGraphStore((s) => s.applyChatEvidence)
  const selectNode = useGraphStore((s) => s.selectNode)
  const expandIds = useGraphStore((s) => s.expandIds)
  const addMention = useChatStore((s) => s.addMention)
  const openLineagePanel = useChatStore((s) => s.openLineagePanel)

  const [expandedAll, setExpandedAll] = useState(false)

  const chains = useMemo(() => buildLineageChains(response, compact ? 8 : 6), [response, compact])
  const extras = useMemo(() => unchainedNodes(response, chains), [response, chains])

  if (chains.length === 0) return null

  const shown = expandedAll ? chains : chains.slice(0, compact ? 4 : VISIBLE_CHAINS)
  const hiddenChains = chains.length - shown.length

  const onAsk = (node: ChatEvidenceNode) => {
    addMention(node.name)
  }
  const onShowInGraph = (node: ChatEvidenceNode) => {
    applyChatEvidence(question, response)
    expandIds([node.id])
    selectNode(node.id)
    navigate('/graph')
  }
  const onViewDetails = (node: ChatEvidenceNode) => {
    expandIds([node.id])
    selectNode(node.id)
    navigate('/graph')
  }

  return (
    <motion.section
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
      className={
        compact
          ? 'space-y-3'
          : 'rounded-xl border border-[var(--border)] bg-[var(--surface-2)] p-3.5'
      }
    >
      {!compact ? (
        <div className="mb-3 flex items-center gap-1.5">
          <GitBranch className="h-3.5 w-3.5 text-accent" />
          <h4 className="text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
            Supporting lineage
          </h4>
        </div>
      ) : null}

      {/* Vertical chains read like Product → Model → Standard on the right rail. */}
      <div className={compact ? 'flex flex-col gap-5' : 'flex gap-6 overflow-x-auto pb-1'}>
        {shown.map((chain, chainIndex) => {
          const collapsed = !expandedAll && chain.steps.length > VISIBLE_STEPS
          const steps = collapsed ? chain.steps.slice(0, VISIBLE_STEPS) : chain.steps
          const hiddenHops = chain.steps.length - steps.length

          return (
            <div key={chainIndex} className={compact ? 'w-full' : 'min-w-[11rem] shrink-0'}>
              <LineageNode
                node={chain.nodes[0]}
                onAsk={onAsk}
                onShowInGraph={onShowInGraph}
                onViewDetails={onViewDetails}
              />
              {steps.map((step, stepIndex) => (
                <div key={`${step.relationship}-${stepIndex}`}>
                  <LineageEdge relationship={step.relationship} description={step.description} />
                  <LineageNode
                    node={step.to}
                    onAsk={onAsk}
                    onShowInGraph={onShowInGraph}
                    onViewDetails={onViewDetails}
                  />
                </div>
              ))}
              {hiddenHops > 0 ? (
                <button
                  type="button"
                  onClick={() => setExpandedAll(true)}
                  className="ml-4 mt-1.5 rounded border border-dashed border-[var(--border-strong)] px-2 py-1 text-[10px] text-fg-muted hover:border-[var(--accent)] hover:text-accent"
                >
                  +{hiddenHops} more hop{hiddenHops === 1 ? '' : 's'}
                </button>
              ) : null}
            </div>
          )
        })}
      </div>

      {hiddenChains > 0 ? (
        <button
          type="button"
          onClick={() => setExpandedAll(true)}
          className="mt-2 rounded border border-dashed border-[var(--border-strong)] px-2 py-1 text-[10px] text-fg-muted hover:border-[var(--accent)] hover:text-accent"
        >
          +{hiddenChains} more lineage path{hiddenChains === 1 ? '' : 's'}
        </button>
      ) : null}

      {extras.length > 0 && extras.length <= 6 ? (
        <div className="mt-3 border-t border-[var(--border)] pt-2.5">
          <div className="mb-1.5 text-[10px] uppercase tracking-wide text-fg-subtle">
            Also supporting
          </div>
          <div className="flex flex-wrap gap-1">
            {extras.map((node) => (
              <span
                key={node.id}
                className="inline-flex items-center gap-1 rounded border border-[var(--border)] bg-[var(--surface-3)] px-1.5 py-0.5 text-2xs text-fg"
              >
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ backgroundColor: typeFill(node.type) }}
                  aria-hidden
                />
                {node.name}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {!compact ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {!expandedAll && (hiddenChains > 0 || chains.some((c) => c.steps.length > VISIBLE_STEPS)) ? (
            <Button variant="secondary" size="sm" onClick={() => setExpandedAll(true)}>
              <GitBranch className="h-3 w-3" />
              Show full lineage
            </Button>
          ) : null}
          <Button
            variant="primary"
            size="sm"
            onClick={() => openLineagePanel(question, response)}
          >
            <Network className="h-3 w-3" />
            View lineage
          </Button>
        </div>
      ) : null}
    </motion.section>
  )
}
