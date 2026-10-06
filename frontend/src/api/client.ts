/**
 * Single transport layer. Vite proxies /api -> http://127.0.0.1:8000 in dev;
 * VITE_API_BASE_URL only matters when the built bundle is served from another origin.
 */

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  /** True when the request never reached the server (backend down, DNS, CORS). */
  readonly isNetworkError: boolean

  constructor(message: string, status: number, detail: string, isNetworkError = false) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.isNetworkError = isNetworkError
  }
}

function url(path: string): string {
  return `${BASE}${path}`
}

/** FastAPI reports errors as {detail: string} or {detail: [{msg}, ...]} for validation. */
async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json()
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) => (typeof d === 'object' && d && 'msg' in d ? String((d as { msg: unknown }).msg) : null))
        .filter((m): m is string => Boolean(m))
      if (msgs.length) return msgs.join('; ')
    }
    return response.statusText || `Request failed with ${response.status}`
  } catch {
    return response.statusText || `Request failed with ${response.status}`
  }
}

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await readError(response)
    throw new ApiError(detail, response.status, detail)
  }
  return (await response.json()) as T
}

function networkError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  if (error instanceof DOMException && error.name === 'AbortError') {
    throw error
  }
  return new ApiError(
    'Could not reach the UL Atlas backend.',
    0,
    error instanceof Error ? error.message : String(error),
    true,
  )
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(url(path), {
      ...init,
      headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    })
  } catch (error) {
    throw networkError(error)
  }
  return handle<T>(response)
}

export async function apiJson<T>(path: string, body: unknown, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(url(path), {
      method: 'POST',
      ...init,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...(init?.headers ?? {}) },
      body: JSON.stringify(body),
    })
  } catch (error) {
    throw networkError(error)
  }
  return handle<T>(response)
}

/** Multipart upload. Never set Content-Type by hand — the boundary must be generated. */
export async function apiUpload<T>(path: string, form: FormData, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(url(path), {
      method: 'POST',
      ...init,
      body: form,
      headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    })
  } catch (error) {
    throw networkError(error)
  }
  return handle<T>(response)
}
