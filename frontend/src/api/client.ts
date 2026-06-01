/**
 * Typed REST API client.
 *
 * All calls go to the FastAPI backend via the Vite proxy (/api/*).
 * Throws ApiError on non-2xx responses so callers get a structured object.
 */

const BASE = '/api'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`API ${status}: ${detail}`)
    this.name = 'ApiError'
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      // ignore parse errors
    }
    throw new ApiError(res.status, detail)
  }
  // 204 No Content — return undefined cast to T
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ── Config ────────────────────────────────────────────────────────────────

export interface ConfigStatus {
  status: 'needs_setup' | 'configured'
  default_workspace?: string
  workspace_path?: string
  default_model?: string
  auto_approve_default?: boolean
}

export const configApi = {
  getStatus: () => request<ConfigStatus>('/config/status'),
  setup: (workspace_path: string) =>
    request<ConfigStatus>('/config/setup', {
      method: 'POST',
      body: JSON.stringify({ workspace_path }),
    }),
  patch: (fields: Partial<Omit<ConfigStatus, 'status'>>) =>
    request<{ status: string }>('/config/', {
      method: 'PATCH',
      body: JSON.stringify(fields),
    }),
}

// ── Projects ──────────────────────────────────────────────────────────────

export interface Project {
  id: number
  name: string
  created_at: string
  updated_at: string
  workspace_path: string
  conda_env: string
  experiment_folder: string
  current_stage: 'hypothesis' | 'setup' | 'run'
  auto_approve: boolean
  status: string
}

export interface ProjectCreate {
  name: string
  workspace_path?: string
  conda_env?: string
  experiment_folder?: string
}

export const projectsApi = {
  list: () => request<Project[]>('/projects/'),
  get: (id: number) => request<Project>(`/projects/${id}`),
  create: (data: ProjectCreate) =>
    request<Project>('/projects/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (id: number, data: Partial<Project>) =>
    request<Project>(`/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  delete: (id: number) =>
    request<void>(`/projects/${id}`, { method: 'DELETE' }),
}

// ── Health ────────────────────────────────────────────────────────────────

export const healthApi = {
  check: () => request<{ status: string; configured: boolean }>('/health'),
}