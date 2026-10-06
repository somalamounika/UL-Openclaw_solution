import type { ProcessResponse } from '@/types/api'

export interface OpenClawAttachment {
  original_filename?: string
  blob_path?: string
  size_bytes?: number
  content_type?: string
}

export interface OpenClawIntentAnalysis {
  primary_intent?: string
  intent_confidence?: number
  intent_rationale?: string
  secondary_intents?: string[]
}

export interface OpenClawRunRow {
  id: string
  metadata_blob: string
  department_blob_prefix: string
  date_folder: string
  email_folder: string
  department_folder: string
  client_name: string
  message_id: string | null
  category: string
  doc_count: number
  intent: string
  recipient: string | null
  subject: string | null
  body_snippet: string
  executive_summary: string[]
  recommended_action: string
  intent_analysis: OpenClawIntentAnalysis
  attachments: OpenClawAttachment[]
  archived_at: string | null
  email_overview: Record<string, unknown> | null
}

export interface OpenClawRunsResponse {
  status: string
  count: number
  runs: OpenClawRunRow[]
}

export type OpenClawBuildGraphResponse = ProcessResponse
