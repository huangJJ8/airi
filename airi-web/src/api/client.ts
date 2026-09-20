import axios from 'axios'

/** Normalized API error. Airi governance rejections (400/409/422) keep their codes. */

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null

  constructor(status: number, code: string, message: string, requestId: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
  }
}

interface ErrorBody {
  error?: { code?: string; message?: string; request_id?: string }
  detail?: unknown
}

export function normalizeApiError(status: number, body: unknown): ApiError {
  const payload = (body ?? {}) as ErrorBody
  if (payload.error && typeof payload.error.code === 'string') {
    return new ApiError(
      status,
      payload.error.code,
      payload.error.message ?? 'Request failed',
      payload.error.request_id ?? null,
    )
  }
  // FastAPI validation errors (422) bypass the AIRI error envelope.
  if (Array.isArray(payload.detail)) {
    const first = payload.detail[0] as { msg?: string; loc?: unknown[] } | undefined
    const where = first?.loc && Array.isArray(first.loc) ? first.loc.join('.') : ''
    return new ApiError(
      status,
      'validation_error',
      first?.msg ? `${where}: ${first.msg}` : 'Validation failed',
    )
  }
  return new ApiError(status, 'http_error', 'Request failed')
}

/** Short, governance-aware description used by error alerts in the UI. */
export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return `Governance check refused the action (${error.code}): ${error.message}`
    }
    return `[${error.status} ${error.code}] ${error.message}`
  }
  if (error instanceof Error) {
    return error.message
  }
  return 'Unknown error'
}

const baseURL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export const http = axios.create({
  baseURL,
  timeout: 120_000,
})

/**
 * Single response interceptor: every AIRI governance rejection keeps its
 * backend `error.code` / `error.message` instead of collapsing into
 * "Request failed".
 */
http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      return Promise.reject(normalizeApiError(error.response.status, error.response.data))
    }
    return Promise.reject(new Error(error.message ?? 'Network error'))
  },
)
