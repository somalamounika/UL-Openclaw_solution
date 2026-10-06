import { apiJson } from './client'
import type { ChatRequest, ChatResponse } from '@/types/api'

/** POST /api/ul/chat — JSON in, JSON out. Not streamed; there are no tokens to render. */
export async function askQuestion(request: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  return apiJson<ChatResponse>('/api/ul/chat', request, { signal })
}
