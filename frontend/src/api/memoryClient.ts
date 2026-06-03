/**
 * api/memoryClient.ts — Typed REST client for ALFRED's memory engine.
 *
 * Mirrors the backend api/memory_router.py endpoints exactly.
 */

// ---------------------------------------------------------------------------
// Base fetch (re-used pattern from client.ts)
// ---------------------------------------------------------------------------

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      detail = body.detail ?? JSON.stringify(body)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  if (res.status === 204) return {} as T
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type MemoryType = 'mistake' | 'preference' | 'fact' | 'dataset_ref'
export type MemorySource = 'user' | 'agent'

export interface MemoryItem {
  id: number
  project_id: number | null
  type: MemoryType
  content: string
  tags: string
  created_at: string
  active: boolean
  source: MemorySource
}

export interface CompiledMemory {
  markdown: string
  token_estimate: number
  item_count: number
  is_stale: boolean
}

export interface MemoryItemCreate {
  type: MemoryType
  content: string
  tags?: string
  source?: MemorySource
}

export interface MemoryItemUpdate {
  content?: string
  tags?: string
  active?: boolean
}

// ---------------------------------------------------------------------------
// Project-scoped memory API
// ---------------------------------------------------------------------------

export const memoryApi = {
  listItems: (
    projectId: number,
    opts: { type?: MemoryType; active_only?: boolean; include_global?: boolean } = {}
  ): Promise<MemoryItem[]> => {
    const params = new URLSearchParams()
    if (opts.type) params.set('type', opts.type)
    if (opts.active_only !== undefined) params.set('active_only', String(opts.active_only))
    if (opts.include_global !== undefined) params.set('include_global', String(opts.include_global))
    const qs = params.toString() ? `?${params}` : ''
    return apiFetch<MemoryItem[]>(`/api/projects/${projectId}/memory/items${qs}`)
  },

  createItem: (projectId: number, data: MemoryItemCreate): Promise<MemoryItem> =>
    apiFetch<MemoryItem>(`/api/projects/${projectId}/memory/items`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  updateItem: (projectId: number, itemId: number, data: MemoryItemUpdate): Promise<MemoryItem> =>
    apiFetch<MemoryItem>(`/api/projects/${projectId}/memory/items/${itemId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  deleteItem: (projectId: number, itemId: number): Promise<void> =>
    apiFetch<void>(`/api/projects/${projectId}/memory/items/${itemId}`, {
      method: 'DELETE',
    }),

  getCompiled: (projectId: number): Promise<CompiledMemory> =>
    apiFetch<CompiledMemory>(`/api/projects/${projectId}/memory/compiled`),

  compile: (projectId: number, model?: string): Promise<CompiledMemory> =>
    apiFetch<CompiledMemory>(`/api/projects/${projectId}/memory/compile`, {
      method: 'POST',
      body: JSON.stringify({ model: model ?? '' }),
    }),
}

// ---------------------------------------------------------------------------
// Global memory API
// ---------------------------------------------------------------------------

export const globalMemoryApi = {
  listItems: (opts: { type?: MemoryType; active_only?: boolean } = {}): Promise<MemoryItem[]> => {
    const params = new URLSearchParams()
    if (opts.type) params.set('type', opts.type)
    if (opts.active_only !== undefined) params.set('active_only', String(opts.active_only))
    const qs = params.toString() ? `?${params}` : ''
    return apiFetch<MemoryItem[]>(`/api/memory/global/items${qs}`)
  },

  createItem: (data: MemoryItemCreate): Promise<MemoryItem> =>
    apiFetch<MemoryItem>('/api/memory/global/items', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
}