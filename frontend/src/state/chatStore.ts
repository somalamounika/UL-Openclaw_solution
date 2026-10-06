import { create } from 'zustand'
import { askQuestion } from '@/api/chat'
import { ApiError } from '@/api/client'
import { CHAT_NO_RESULTS_ANSWER, type ChatResponse } from '@/types/api'

/**
 * Chat lives in a store rather than in the page, so navigating to the graph and back
 * preserves the whole conversation — the two pages are one product.
 *
 * The endpoint is not streamed, so there are no tokens to render: the waiting state
 * names the phases the backend actually runs through instead of faking a typewriter.
 */
export type ThinkingPhase = 'graph' | 'documents' | 'composing'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  createdAt: number
  /** The question this answer replies to — kept on the message so evidence survives navigation. */
  question?: string
  response?: ChatResponse
  isNoResults?: boolean
  error?: string
}

export interface LineagePanelState {
  question: string
  response: ChatResponse
}

interface ChatState {
  messages: ChatMessage[]
  pending: boolean
  phase: ThinkingPhase
  /** Text queued into the composer by "Ask Assistant" on the graph page. */
  draft: string
  mentions: string[]
  /** Right-rail lineage focus for the current assistant turn. */
  lineagePanel: LineagePanelState | null

  send: (question: string, graphId: string | null, documentId: string | null) => Promise<void>
  clear: () => void
  setDraft: (draft: string) => void
  addMention: (name: string) => void
  removeMention: (name: string) => void
  clearMentions: () => void
  openLineagePanel: (question: string, response: ChatResponse) => void
  closeLineagePanel: () => void
}

let counter = 0
const nextId = () => `msg-${Date.now()}-${counter++}`
let timers: number[] = []

const clearTimers = () => {
  for (const id of timers) window.clearTimeout(id)
  timers = []
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  pending: false,
  phase: 'graph',
  draft: '',
  mentions: [],
  lineagePanel: null,

  send: async (question, graphId, documentId) => {
    const trimmed = question.trim()
    if (!trimmed || get().pending) return

    set((state) => ({
      messages: [...state.messages, { id: nextId(), role: 'user', text: trimmed, createdAt: Date.now() }],
      pending: true,
      phase: 'graph',
      lineagePanel: null,
    }))

    // Phase captions advance on a timer because the endpoint reports no progress of its
    // own. They follow the backend's real order — graph retrieval, then chunk retrieval,
    // then answer composition — and the timer drives nothing else.
    clearTimers()
    timers.push(window.setTimeout(() => set({ phase: 'documents' }), 1200))
    timers.push(window.setTimeout(() => set({ phase: 'composing' }), 3200))

    try {
      const response = await askQuestion({
        question: trimmed,
        graph_id: graphId,
        document_id: documentId,
      })
      set((state) => ({
        messages: [
          ...state.messages,
          {
            id: nextId(),
            role: 'assistant',
            text: response.answer,
            createdAt: Date.now(),
            question: trimmed,
            response,
            isNoResults: response.answer.trim() === CHAT_NO_RESULTS_ANSWER,
          },
        ],
        // Lineage stays closed until the user clicks "View lineage".
        lineagePanel: null,
      }))
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.isNetworkError
            ? 'Could not reach the assistant. Is the backend running?'
            : error.detail
          : 'The assistant is temporarily unavailable.'
      set((state) => ({
        messages: [
          ...state.messages,
          { id: nextId(), role: 'assistant', text: '', createdAt: Date.now(), question: trimmed, error: message },
        ],
      }))
    } finally {
      clearTimers()
      set({ pending: false })
    }
  },

  clear: () => {
    clearTimers()
    set({ messages: [], pending: false, draft: '', mentions: [], lineagePanel: null })
  },

  setDraft: (draft) => set({ draft }),
  addMention: (name) =>
    set((state) => (state.mentions.includes(name) ? {} : { mentions: [...state.mentions, name] })),
  removeMention: (name) => set((state) => ({ mentions: state.mentions.filter((m) => m !== name) })),
  clearMentions: () => set({ mentions: [] }),
  openLineagePanel: (question, response) => set({ lineagePanel: { question, response } }),
  closeLineagePanel: () => set({ lineagePanel: null }),
}))
