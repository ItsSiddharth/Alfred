/**
 * api/client.ts — Typed REST client for the ALFRED backend.
 *
 * All fetch calls go to /api/* (proxied to :8000 by Vite in dev).
 * Throws a plain Error with a human-readable message on non-2xx responses.
 */

// ---------------------------------------------------------------------------
// Base fetch helper
// ---------------------------------------------------------------------------

async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
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
      /* ignore parse error */
    }
    throw new Error(detail)
  }
  // 204 No Content — return empty object
  if (res.status === 204) return {} as T
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Config API
// ---------------------------------------------------------------------------

export interface ConfigStatus {
  status: 'needs_setup' | 'configured'
  workspace_path?: string
  default_model?: string
  auto_approve_default?: boolean
  telemetry_opt_in?: boolean
  dataset_cache_path?: string
  default_workspace?: string
}

export const configApi = {
  getStatus: () => apiFetch<ConfigStatus>('/api/config/status'),

  setup: (workspace_path: string) =>
    apiFetch<ConfigStatus>('/api/config/setup', {
      method: 'POST',
      body: JSON.stringify({ workspace_path }),
    }),

  patch: (fields: Partial<Omit<ConfigStatus, 'status'>>) =>
    apiFetch<{ status: string }>('/api/config/', {
      method: 'PATCH',
      body: JSON.stringify(fields),
    }),
}

// ---------------------------------------------------------------------------
// Projects API
// ---------------------------------------------------------------------------

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

export const projectsApi = {
  list: () => apiFetch<Project[]>('/api/projects/'),

  get: (id: number) => apiFetch<Project>(`/api/projects/${id}`),

  create: (data: { name: string }) =>
    apiFetch<Project>('/api/projects/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  update: (
    id: number,
    data: Partial<Pick<Project, 'name' | 'conda_env' | 'experiment_folder' | 'auto_approve'>>
  ) =>
    apiFetch<Project>(`/api/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  delete: (id: number) =>
    apiFetch<void>(`/api/projects/${id}`, { method: 'DELETE' }),
}

// ---------------------------------------------------------------------------
// Models API
// ---------------------------------------------------------------------------

export interface HardwareInfo {
  backend: 'cuda' | 'metal' | 'cpu'
  gpu_name: string
  total_vram_mb: number
  free_vram_mb: number
  total_vram_gb: number
  free_vram_gb: number
  total_ram_mb: number
  total_ram_gb: number
  cpu_count: number
}

export interface OllamaHealth {
  available: boolean
  models?: string[]
  guidance?: string
}

export interface LocalModel {
  name: string
  size: number // bytes
  modified_at: string
  digest: string
  details?: {
    format?: string
    family?: string
    parameter_size?: string
    quantization_level?: string
  }
}

export type VramFit = 'fits' | 'tight' | 'too_large'

export interface CatalogModel {
  ollama_tag: string
  display_name: string
  family: string
  params_b: number
  quant_bits: number
  context_k: number
  description: string
  strengths: string[]
  required_vram_mb: number
  required_vram_gb: number
  fit: VramFit
}

export interface RecommendedResponse {
  hardware: HardwareInfo
  models: CatalogModel[]
}

export interface LocalModelsResponse {
  models: LocalModel[]
}

export const modelsApi = {
  getHardware: () => apiFetch<HardwareInfo>('/api/models/hardware'),

  getHealth: () => apiFetch<OllamaHealth>('/api/models/health'),

  getLocal: () => apiFetch<LocalModelsResponse>('/api/models/local'),

  getRecommended: () => apiFetch<RecommendedResponse>('/api/models/recommended'),

  pull: (model_name: string, project_id = 'global') =>
    apiFetch<{ status: string; model: string }>('/api/models/pull', {
      method: 'POST',
      body: JSON.stringify({ model_name, project_id }),
    }),

  delete: (model_name: string) =>
    apiFetch<{ status: string; model: string }>(
      `/api/models/${encodeURIComponent(model_name)}`,
      { method: 'DELETE' }
    ),
}