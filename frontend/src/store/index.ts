/**
 * Global Zustand store — Stage 4 (patched).
 *
 * Key fixes vs original Stage 4:
 *  - clearProjectState() now resets streamingMsgId (F3)
 *  - appendPersistedMessage() guards against duplicate IDs (F1 partial fix)
 *  - finaliseStream() removes the entry from streamingMessages entirely
 *    instead of just marking isStreaming=false, preventing double-render (F1)
 *  - setActiveProjectId clears streamingMsgId on project switch
 */

import { create } from 'zustand'

// ── Types ─────────────────────────────────────────────────────────────────

export type SidebarPanel = 'memory' | 'tools' | 'find-models' | 'dashboard' | null

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

export interface ToolCallEvent {
  tool_name: string
  input?: Record<string, unknown>
  reason?: string
  status: 'running' | 'done' | 'error'
  sources?: string[]
  error?: string | null
  result_count?: number
  ts: string
}

export interface RunLogEntry {
  id: string        // client-side uuid
  level: string     // INFO / DEBUG / ERROR / WARNING
  message: string
  phase: string     // preprocess / train / eval / error / fix
  ts: string
}

export interface TokenStats {
  sessionPrompt: number      // cumulative prompt tokens this session
  sessionCompletion: number  // cumulative completion tokens this session
  sessionTotal: number       // sessionPrompt + sessionCompletion
  lastPrompt: number         // last single-call prompt tokens
  lastCompletion: number     // last single-call completion tokens
}

export interface PlotEntry {
  filename: string
  base64_png: string
  ascii_art: string
  experiment_id: number
  ts: string
}

// ── Store interface ────────────────────────────────────────────────────────

export interface AlfredStore {
  // Setup
  configStatus: 'unknown' | 'needs_setup' | 'configured'
  setConfigStatus: (s: AlfredStore['configStatus']) => void

  // Active project
  activeProjectId: number | null
  setActiveProjectId: (id: number | null) => void

  // Active project stage — synced from the project list when a project is selected
  activeProjectStage: 'hypothesis' | 'setup' | 'run' | null
  setActiveProjectStage: (stage: 'hypothesis' | 'setup' | 'run' | null) => void

  // Active experiment
  activeExperimentId: number | null
  setActiveExperimentId: (id: number | null) => void

  // Sidebar
  sidebarPanel: SidebarPanel
  setSidebarPanel: (panel: SidebarPanel) => void

  // Selected model
  selectedModel: string
  setSelectedModel: (model: string) => void
  projectModels: Record<number, string>
  setProjectModel: (projectId: number, model: string) => void

  // Pulling models
  pullingModels: Set<string>
  addPullingModel: (tag: string) => void
  removePullingModel: (tag: string) => void

  // Per-model download progress (streamed via WS during pull)
  pullProgress: { model: string; completed: number; total: number } | null
  setPullProgress: (p: AlfredStore['pullProgress']) => void

  // Progress strip
  progress: ProgressState
  setProgress: (p: Partial<ProgressState>) => void
  resetProgress: () => void

  // Persisted messages (loaded from DB on project open)
  persistedMessages: PersistedMessage[]
  setPersistedMessages: (msgs: PersistedMessage[]) => void
  /**
   * Append a message to persistedMessages, guarding against duplicate IDs.
   * If a message with the same id already exists, it is updated in place.
   */
  appendPersistedMessage: (msg: PersistedMessage) => void
  /**
   * Patch a persisted message's content in place (used during streaming).
   * No-op if message with given id is not found.
   */
  patchPersistedMessage: (id: number, content: string) => void

  // Streaming token buffers (keyed by message_id string)
  // NOTE: these are ONLY used for messages that don't yet have a DB row id.
  // Once msg_start is received, tokens go into persistedMessages instead.
  streamingMessages: Record<string, StreamingMessage>
  appendToken: (messageId: string, token: string, kind?: MessageKind) => void
  /**
   * Remove the streaming entry (don't just mark done — removal prevents
   * the double-render bug where ChatThread renders both streamingMessages
   * and persistedMessages for the same content).
   */
  finaliseStream: (messageId: string) => void
  clearStreams: () => void

  // Which DB row is currently being streamed into
  streamingMsgId: number | null
  setStreamingMsgId: (id: number | null) => void

  // Inline log / thinking entries
  logEntries: Record<string, LogEntry>
  appendLogToken: (messageId: string, token: string, phase: string, kind: LogEntry['kind']) => void
  finaliseLog: (messageId: string) => void
  clearLogs: () => void

  // Approval gate
  approvalRequest: ApprovalRequest | null
  setApprovalRequest: (req: ApprovalRequest | null) => void

  // Show-your-work toggle
  showWorkMode: boolean
  toggleShowWork: () => void

  // Tool call events (live feed from WS)
  toolCalls: ToolCallEvent[]
  addToolCall: (event: ToolCallEvent) => void
  clearToolCalls: () => void

  // Run stage: live log lines streamed from experiment subprocess
  runLogs: RunLogEntry[]
  appendRunLog: (entry: Omit<RunLogEntry, 'id'>) => void
  clearRunLogs: () => void

  // Run stage: plots emitted during experiment execution
  activePlots: PlotEntry[]
  addPlot: (entry: PlotEntry) => void
  clearPlots: () => void

  // Token usage tracking (cumulative for the current session)
  tokenStats: TokenStats
  addTokenUsage: (prompt: number, completion: number) => void
  resetTokenStats: () => void

  // Project management helpers
  deleteProjectLocal: (id: number) => void
  /** Reset ALL ephemeral per-project state when switching projects. */
  clearProjectState: () => void
}

const defaultProgress: ProgressState = {
  stage: 1,
  substage: 'idle',
  label: 'Ready',
  current: 0,
  total: 0,
  status: 'idle',
}

export const useStore = create<AlfredStore>((set, get) => ({
  // ── Setup ────────────────────────────────────────────────────────────────
  configStatus: 'unknown',
  setConfigStatus: (s) => set({ configStatus: s }),

  // ── Active project ────────────────────────────────────────────────────────
  activeProjectId: null,
  setActiveProjectId: (id) => {
    const { projectModels } = get()
    const model = id !== null ? (projectModels[id] ?? '') : ''
    set({
      activeProjectId: id,
      selectedModel: model,
      // Clear ALL per-project ephemeral state on project switch
      persistedMessages: [],
      streamingMessages: {},
      logEntries: {},
      approvalRequest: null,
      activeExperimentId: null,
      streamingMsgId: null,   // F3 fix
      toolCalls: [],
      progress: defaultProgress,
      runLogs: [],
      activePlots: [],
      tokenStats: {
        sessionPrompt: 0,
        sessionCompletion: 0,
        sessionTotal: 0,
        lastPrompt: 0,
        lastCompletion: 0,
      },
    })
  },

  activeProjectStage: null,
  setActiveProjectStage: (stage) => set({ activeProjectStage: stage }),

  activeExperimentId: null,
  setActiveExperimentId: (id) => set({ activeExperimentId: id }),

  // ── Sidebar ────────────────────────────────────────────────────────────────
  sidebarPanel: null,
  setSidebarPanel: (panel) =>
    set((state) => ({ sidebarPanel: state.sidebarPanel === panel ? null : panel })),

  // ── Model selection ────────────────────────────────────────────────────────
  selectedModel: '',
  setSelectedModel: (model) => {
    const { activeProjectId } = get()
    set((state) => ({
      selectedModel: model,
      projectModels:
        activeProjectId !== null
          ? { ...state.projectModels, [activeProjectId]: model }
          : state.projectModels,
    }))
  },
  projectModels: {},
  setProjectModel: (projectId, model) =>
    set((state) => ({
      projectModels: { ...state.projectModels, [projectId]: model },
    })),

  // ── Pulling models ─────────────────────────────────────────────────────────
  pullingModels: new Set<string>(),
  addPullingModel: (tag) =>
    set((state) => ({ pullingModels: new Set([...state.pullingModels, tag]) })),
  removePullingModel: (tag) =>
    set((state) => {
      const n = new Set(state.pullingModels)
      n.delete(tag)
      return { pullingModels: n, pullProgress: null }
    }),

  // ── Pull progress ──────────────────────────────────────────────────────────
  pullProgress: null,
  setPullProgress: (p) => set({ pullProgress: p }),

  // ── Progress strip ─────────────────────────────────────────────────────────
  progress: defaultProgress,
  setProgress: (p) =>
    set((state) => ({ progress: { ...state.progress, ...p } })),
  resetProgress: () => set({ progress: defaultProgress }),

  // ── Persisted messages ─────────────────────────────────────────────────────
  persistedMessages: [],
  setPersistedMessages: (msgs) => set({ persistedMessages: msgs }),

  appendPersistedMessage: (msg) =>
    set((state) => {
      // Guard: if a message with this id already exists, update it in place
      // rather than appending a duplicate. This handles the case where the
      // REST load and the WS msg_start arrive for the same row.
      const existingIdx = state.persistedMessages.findIndex((m) => m.id === msg.id)
      if (existingIdx >= 0) {
        const updated = [...state.persistedMessages]
        updated[existingIdx] = { ...updated[existingIdx], ...msg }
        return { persistedMessages: updated }
      }
      return { persistedMessages: [...state.persistedMessages, msg] }
    }),

  patchPersistedMessage: (id, content) =>
    set((state) => ({
      persistedMessages: state.persistedMessages.map((m) =>
        m.id === id ? { ...m, content } : m
      ),
    })),

  // ── Streaming token buffers ────────────────────────────────────────────────
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

  // F1 fix: REMOVE the entry on finalise, don't just mark isStreaming=false.
  // This prevents ChatThread from rendering the streaming buffer alongside
  // the now-complete persistedMessage for the same content.
  finaliseStream: (messageId) =>
    set((state) => {
      const { [messageId]: _removed, ...rest } = state.streamingMessages
      return { streamingMessages: rest }
    }),

  clearStreams: () => set({ streamingMessages: {} }),

  // ── DB row being streamed ──────────────────────────────────────────────────
  streamingMsgId: null,
  setStreamingMsgId: (id) => set({ streamingMsgId: id }),

  // ── Log / thinking entries ─────────────────────────────────────────────────
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

  // ── Approval gate ──────────────────────────────────────────────────────────
  approvalRequest: null,
  setApprovalRequest: (req) => set({ approvalRequest: req }),

  // ── Show-your-work ─────────────────────────────────────────────────────────
  showWorkMode: false,
  toggleShowWork: () =>
    set((state) => ({ showWorkMode: !state.showWorkMode })),

  // ── Tool calls ─────────────────────────────────────────────────────────────
  toolCalls: [],
  addToolCall: (event) =>
    set((state) => ({
      toolCalls: [event, ...state.toolCalls].slice(0, 100),
    })),
  clearToolCalls: () => set({ toolCalls: [] }),

  // ── Run stage logs ─────────────────────────────────────────────────────────
  runLogs: [],
  appendRunLog: (entry) =>
    set((state) => ({
      runLogs: [
        ...state.runLogs,
        { ...entry, id: `${Date.now()}-${Math.random().toString(36).slice(2)}` },
      ].slice(-2000),   // cap at 2000 lines to avoid unbounded growth
    })),
  clearRunLogs: () => set({ runLogs: [] }),

  // ── Active plots ────────────────────────────────────────────────────────────
  activePlots: [],
  addPlot: (entry) =>
    set((state) => ({ activePlots: [...state.activePlots, entry] })),
  clearPlots: () => set({ activePlots: [] }),

  // ── Token stats ────────────────────────────────────────────────────────────
  tokenStats: {
    sessionPrompt: 0,
    sessionCompletion: 0,
    sessionTotal: 0,
    lastPrompt: 0,
    lastCompletion: 0,
  },
  addTokenUsage: (prompt, completion) =>
    set((state) => ({
      tokenStats: {
        sessionPrompt: state.tokenStats.sessionPrompt + prompt,
        sessionCompletion: state.tokenStats.sessionCompletion + completion,
        sessionTotal: state.tokenStats.sessionTotal + prompt + completion,
        lastPrompt: prompt,
        lastCompletion: completion,
      },
    })),
  resetTokenStats: () =>
    set({
      tokenStats: {
        sessionPrompt: 0,
        sessionCompletion: 0,
        sessionTotal: 0,
        lastPrompt: 0,
        lastCompletion: 0,
      },
    }),

  // ── Project management ─────────────────────────────────────────────────────
  deleteProjectLocal: (id) =>
    set((state) => ({
      activeProjectId: state.activeProjectId === id ? null : state.activeProjectId,
    })),

  clearProjectState: () =>
    set({
      persistedMessages: [],
      streamingMessages: {},
      logEntries: {},
      approvalRequest: null,
      streamingMsgId: null,   // F3 fix — was missing
      toolCalls: [],
      progress: defaultProgress,
      runLogs: [],
      activePlots: [],
    }),
}))