/**
 * Global Zustand store — Stage 2 edition.
 *
 * New in Stage 2:
 *  - persistedMessages: Message[] loaded from DB on project open
 *  - approvalRequest: the pending plan card data (from WS approval_request event)
 *  - showWorkMode: "show your work" toggle — expands thinking tabs inline
 *  - activeExperimentId: tracks the current experiment row
 *  - per-project model selection (model stored keyed by projectId)
 *
 * Streaming tokens still accumulate in streamingMessages; on WS `done` they
 * are finalised in place. The ChatThread renders both persisted + streaming.
 */

import { create } from 'zustand'

// ── Re-exported types ─────────────────────────────────────────────────────

export type SidebarPanel = 'memory' | 'tools' | 'find-models' | null

export interface ProgressState {
  stage: number
  substage: string
  label: string
  current: number
  total: number
  status: 'running' | 'waiting' | 'error' | 'done' | 'idle'
}

export interface StreamingMessage {
  messageId: string
  content: string
  isStreaming: boolean
  kind: MessageKind
}

// Mirrors backend MessageKind enum
export type MessageRole = 'user' | 'assistant' | 'system' | 'tool'
export type MessageKind = 'chat' | 'plan' | 'result' | 'error' | 'thinking'

export interface PersistedMessage {
  id: number
  project_id: number
  role: MessageRole
  content: string
  created_at: string
  kind: MessageKind
  metadata_json: string
}

export interface ApprovalRequest {
  stage: number
  substage: string
  plan: Record<string, unknown>
  auto_approve: boolean
  experiment_id?: number
}

export interface LogEntry {
  messageId: string
  content: string
  phase: string
  kind: 'thinking' | 'log' | 'tool_call'
  isStreaming: boolean
}

// ── Store interface ────────────────────────────────────────────────────────

export interface AlfredStore {
  // ── Setup ──────────────────────────────────────────────────────────────
  configStatus: 'unknown' | 'needs_setup' | 'configured'
  setConfigStatus: (s: AlfredStore['configStatus']) => void

  // ── Active project ─────────────────────────────────────────────────────
  activeProjectId: number | null
  setActiveProjectId: (id: number | null) => void

  // ── Active experiment ──────────────────────────────────────────────────
  activeExperimentId: number | null
  setActiveExperimentId: (id: number | null) => void

  // ── Sidebar ────────────────────────────────────────────────────────────
  sidebarPanel: SidebarPanel
  setSidebarPanel: (panel: SidebarPanel) => void

  // ── Selected model (per-project map + convenience getter) ──────────────
  selectedModel: string
  setSelectedModel: (model: string) => void
  projectModels: Record<number, string>
  setProjectModel: (projectId: number, model: string) => void

  // ── Pulling models ─────────────────────────────────────────────────────
  pullingModels: Set<string>
  addPullingModel: (tag: string) => void
  removePullingModel: (tag: string) => void

  // ── Progress strip ─────────────────────────────────────────────────────
  progress: ProgressState
  setProgress: (p: Partial<ProgressState>) => void
  resetProgress: () => void

  // ── Persisted messages (loaded from DB on project open) ────────────────
  persistedMessages: PersistedMessage[]
  setPersistedMessages: (msgs: PersistedMessage[]) => void
  appendPersistedMessage: (msg: PersistedMessage) => void

  // ── Streaming tokens ───────────────────────────────────────────────────
  streamingMessages: Record<string, StreamingMessage>
  appendToken: (messageId: string, token: string, kind?: MessageKind) => void
  finaliseStream: (messageId: string) => void
  clearStreams: () => void

  // ── Inline log / thinking entries ──────────────────────────────────────
  logEntries: Record<string, LogEntry>
  appendLogToken: (messageId: string, token: string, phase: string, kind: LogEntry['kind']) => void
  finaliseLog: (messageId: string) => void
  clearLogs: () => void

  // ── Approval ───────────────────────────────────────────────────────────
  approvalRequest: ApprovalRequest | null
  setApprovalRequest: (req: ApprovalRequest | null) => void

  // ── "Show your work" toggle ────────────────────────────────────────────
  showWorkMode: boolean
  toggleShowWork: () => void
}

// ── Default progress state ────────────────────────────────────────────────

const defaultProgress: ProgressState = {
  stage: 1,
  substage: 'idle',
  label: 'Ready',
  current: 0,
  total: 0,
  status: 'idle',
}

// ── Store ─────────────────────────────────────────────────────────────────

export const useStore = create<AlfredStore>((set, get) => ({
  // Setup
  configStatus: 'unknown',
  setConfigStatus: (s) => set({ configStatus: s }),

  // Active project
  activeProjectId: null,
  setActiveProjectId: (id) => {
    // When switching projects, restore the per-project model selection.
    const { projectModels } = get()
    const model = id !== null ? (projectModels[id] ?? '') : ''
    set({
      activeProjectId: id,
      selectedModel: model,
      persistedMessages: [],
      streamingMessages: {},
      logEntries: {},
      approvalRequest: null,
      activeExperimentId: null,
    })
  },

  // Active experiment
  activeExperimentId: null,
  setActiveExperimentId: (id) => set({ activeExperimentId: id }),

  // Sidebar
  sidebarPanel: null,
  setSidebarPanel: (panel) =>
    set((state) => ({
      sidebarPanel: state.sidebarPanel === panel ? null : panel,
    })),

  // Model (per-project)
  selectedModel: '',
  setSelectedModel: (model) => {
    const { activeProjectId } = get()
    set((state) => ({
      selectedModel: model,
      projectModels: activeProjectId !== null
        ? { ...state.projectModels, [activeProjectId]: model }
        : state.projectModels,
    }))
  },
  projectModels: {},
  setProjectModel: (projectId, model) =>
    set((state) => ({
      projectModels: { ...state.projectModels, [projectId]: model },
    })),

  // Pulling models
  pullingModels: new Set<string>(),
  addPullingModel: (tag) =>
    set((state) => ({
      pullingModels: new Set([...state.pullingModels, tag]),
    })),
  removePullingModel: (tag) =>
    set((state) => {
      const next = new Set(state.pullingModels)
      next.delete(tag)
      return { pullingModels: next }
    }),

  // Progress
  progress: defaultProgress,
  setProgress: (p) =>
    set((state) => ({ progress: { ...state.progress, ...p } })),
  resetProgress: () => set({ progress: defaultProgress }),

  // Persisted messages
  persistedMessages: [],
  setPersistedMessages: (msgs) => set({ persistedMessages: msgs }),
  appendPersistedMessage: (msg) =>
    set((state) => ({
      persistedMessages: [...state.persistedMessages, msg],
    })),

  // Streaming
  streamingMessages: {},
  appendToken: (messageId, token, kind = 'chat') =>
    set((state) => {
      const existing = state.streamingMessages[messageId]
      return {
        streamingMessages: {
          ...state.streamingMessages,
          [messageId]: {
            messageId,
            content: (existing?.content ?? '') + token,
            isStreaming: true,
            kind: existing?.kind ?? kind,
          },
        },
      }
    }),
  finaliseStream: (messageId) =>
    set((state) => {
      const existing = state.streamingMessages[messageId]
      if (!existing) return {}
      return {
        streamingMessages: {
          ...state.streamingMessages,
          [messageId]: { ...existing, isStreaming: false },
        },
      }
    }),
  clearStreams: () => set({ streamingMessages: {} }),

  // Log / thinking entries
  logEntries: {},
  appendLogToken: (messageId, token, phase, kind) =>
    set((state) => {
      const existing = state.logEntries[messageId]
      return {
        logEntries: {
          ...state.logEntries,
          [messageId]: {
            messageId,
            content: (existing?.content ?? '') + token,
            phase,
            kind,
            isStreaming: true,
          },
        },
      }
    }),
  finaliseLog: (messageId) =>
    set((state) => {
      const existing = state.logEntries[messageId]
      if (!existing) return {}
      return {
        logEntries: {
          ...state.logEntries,
          [messageId]: { ...existing, isStreaming: false },
        },
      }
    }),
  clearLogs: () => set({ logEntries: {} }),

  // Approval
  approvalRequest: null,
  setApprovalRequest: (req) => set({ approvalRequest: req }),

  // Show work
  showWorkMode: false,
  toggleShowWork: () => set((state) => ({ showWorkMode: !state.showWorkMode })),
}))