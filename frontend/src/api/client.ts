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

export type ProjectStage = 'hypothesis' | 'setup' | 'run'

export interface Project {
  id: number
  name: string
  created_at: string
  updated_at: string
  workspace_path: string
  conda_env: string
  experiment_folder: string
  current_stage: ProjectStage
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

  setAutoApprove: (id: number, auto_approve: boolean) =>
    apiFetch<{ status: string; auto_approve: boolean }>(
      `/api/projects/${id}/auto_approve`,
      { method: 'POST', body: JSON.stringify({ auto_approve }) }
    ),
}

// ---------------------------------------------------------------------------
// Messages API
// ---------------------------------------------------------------------------

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool'
export type MessageKind = 'chat' | 'plan' | 'result' | 'error' | 'thinking'

export interface Message {
  id: number
  project_id: number
  role: MessageRole
  content: string
  created_at: string
  kind: MessageKind
  metadata_json: string
}

export const messagesApi = {
  list: (projectId: number, limit = 200, offset = 0) =>
    apiFetch<Message[]>(
      `/api/projects/${projectId}/messages/?limit=${limit}&offset=${offset}`
    ),

  create: (
    projectId: number,
    data: { role: MessageRole; content: string; kind?: MessageKind; metadata_json?: string }
  ) =>
    apiFetch<Message>(`/api/projects/${projectId}/messages/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
}

// ---------------------------------------------------------------------------
// Experiments API
// ---------------------------------------------------------------------------

export type ExperimentStatus = 'planned' | 'running' | 'done' | 'failed'
export type VersionMode = 'modify' | 'branch'

export interface Experiment {
  id: number
  project_id: number
  iteration: number
  git_commit: string
  code_path: string
  dataset_hash: string
  conda_snapshot_path: string
  seed: number
  status: ExperimentStatus
  started_at: string | null
  finished_at: string | null
  runtime_seconds: number | null
  version_mode: VersionMode
  plan_json: string
}

export const experimentsApi = {
  list: (projectId: number) =>
    apiFetch<Experiment[]>(`/api/projects/${projectId}/experiments`),

  create: (
    projectId: number,
    data: { iteration?: number; seed?: number; plan_json?: string; version_mode?: VersionMode }
  ) =>
    apiFetch<Experiment>(`/api/projects/${projectId}/experiments`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  approve: (projectId: number, expId: number, editedPlan?: Record<string, unknown>) =>
    apiFetch<{ status: string; experiment_id: number }>(
      `/api/projects/${projectId}/experiments/${expId}/approve`,
      { method: 'POST', body: JSON.stringify({ edited_plan: editedPlan ?? null }) }
    ),

  reject: (projectId: number, expId: number, feedback: string) =>
    apiFetch<{ status: string; experiment_id: number; feedback: string }>(
      `/api/projects/${projectId}/experiments/${expId}/reject`,
      { method: 'POST', body: JSON.stringify({ feedback }) }
    ),

  update: (projectId: number, expId: number, data: Partial<Experiment>) =>
    apiFetch<Experiment>(`/api/projects/${projectId}/experiments/${expId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
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
  size: number
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

// ---------------------------------------------------------------------------
// Hypothesis API (Stage 5)
// ---------------------------------------------------------------------------

export interface HypothesisScore {
  id: number
  project_id: number
  kind: 'novelty' | 'gap' | 'publishability'
  value: number
  rationale: string
  citations: Array<{ title: string; year?: number; venue?: string; url?: string }>
  created_at: string
}

export const hypothesisApi = {
  getScores: (projectId: number) =>
    apiFetch<HypothesisScore[]>(`/api/projects/${projectId}/hypothesis/scores`),

  start: (projectId: number, hypothesis: string, model: string, feedback = '') =>
    apiFetch<{ status: string; project_id: number }>(
      `/api/projects/${projectId}/hypothesis/start`,
      {
        method: 'POST',
        body: JSON.stringify({ hypothesis, model, feedback }),
      }
    ),
}

// ---------------------------------------------------------------------------
// Models API
// ---------------------------------------------------------------------------

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