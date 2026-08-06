/**
 * Shared HTTP client for the CrateIQ backend API.
 *
 * All requests go to /api/* which Vite proxies to http://localhost:8000/api/*
 * during development. In production, point BASE_URL at the real backend.
 */

const BASE_URL = '/api'

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, detail: unknown) {
    const msg = extractMessage(detail)
    super(`API ${status}: ${msg}`)
    this.status = status
    this.detail = detail
    this.name = 'ApiError'
  }

  /** User-facing message, suitable for display in an ErrorBanner. */
  get displayMessage(): string {
    return extractMessage(this.detail)
  }
}

function extractMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI validation error array: [{msg: '...', loc: [...], ...}]
    return detail
      .map((d) => (typeof d === 'object' && d !== null && 'msg' in d ? String((d as { msg: unknown }).msg) : JSON.stringify(d)))
      .join(' | ')
  }
  if (typeof detail === 'object' && detail !== null && 'detail' in detail) {
    return extractMessage((detail as { detail: unknown }).detail)
  }
  return JSON.stringify(detail)
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${BASE_URL}${path}`
  const res = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  })

  if (!res.ok) {
    let detail: unknown = res.statusText
    try {
      detail = await res.json()
    } catch {
      // non-JSON error body — leave detail as statusText
    }
    throw new ApiError(res.status, detail)
  }

  // 204 No Content → return undefined
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

// ---------------------------------------------------------------------------
// Convenience methods
// ---------------------------------------------------------------------------

export const apiFetch = {
  /** `signal` lets callers abort in-flight reads (e.g. on track change). */
  get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return request<T>(path, signal ? { signal } : {})
  },

  post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
    return request<T>(path, {
      method: 'POST',
      body: JSON.stringify(body),
      ...(signal ? { signal } : {}),
    })
  },

  patch<T>(path: string, body: unknown): Promise<T> {
    return request<T>(path, {
      method: 'PATCH',
      body: JSON.stringify(body),
    })
  },

  delete<T>(path: string, signal?: AbortSignal): Promise<T> {
    return request<T>(path, { method: 'DELETE', ...(signal ? { signal } : {}) })
  },

  /** Fetch plain-text response (used for job logs). */
  async text(path: string): Promise<string> {
    const url = `${BASE_URL}${path}`
    const res = await fetch(url)
    if (!res.ok) {
      throw new ApiError(res.status, res.statusText)
    }
    return res.text()
  },
}
