/**
 * Global Zustand store.
 *
 * Holds all transient UI state:
 *  - active project + sidebar panel selection
 *  - pipeline progress strip data (fed by WS progress events)
 *  - streaming token accumulator (fed by WS token events)
 *  - config/setup status
 *
 * Persistent data (projects, messages) lives in the DB and is fetched via
 * TanStack Query — not stored here.
 */

import { create } from 'zustand'

// ── Types ─────────────────────────────────────────────────────────────────

export type SidebarPanel = 'memory' | 'tools' | 'find-models' | null

export interface ProgressState {
  stage: number          // 1 | 2 | 3
  substage: string       // e.g. "snowballing"
  label: string          // e.g. "Expanding citations — paper 4/10"
  current: number
  total: number
  status: 'running' | 'waiting' | 'error' | 'done' | 'idle'
}

export interface StreamingMessage {
  messageId: string
  content: string
  isStreaming: boolean
}

export interface AlfredStore {
  // ── Setup ──────────────────────────────────────────────────────────────
  configStatus: 'unknown' | 'needs_setup' | 'configured'
  setConfigStatus: (s: AlfredStore['configStatus']) => void

  // ── Active project ─────────────────────────────────────────────────────
  activeProjectId: number | null
  setActiveProjectId: (id: number | null) => void

  // ── Sidebar ────────────────────────────────────────────────────────────
  sidebarPanel: SidebarPanel
  setSidebarPanel: (panel: SidebarPanel) => void

  // ── Selected model ─────────────────────────────────────────────────────
  selectedModel: string
  setSelectedModel: (model: string) => void

  // ── Progress strip ─────────────────────────────────────────────────────
  progress: ProgressState
  setProgress: (p: Partial<ProgressState>) => void
  resetProgress: () => void

  // ── Streaming tokens ───────────────────────────────────────────────────
  streamingMessages: Record<string, StreamingMessage>
  appendToken: (messageId: string, token: string) => void
  finaliseStream: (messageId: string) => void
  clearStreams: () => void

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

export const useStore = create<AlfredStore>((set) => ({
  // Setup
  configStatus: 'unknown',
  setConfigStatus: (s) => set({ configStatus: s }),

  // Active project
  activeProjectId: null,
  setActiveProjectId: (id) => set({ activeProjectId: id }),

  // Sidebar
  sidebarPanel: null,
  setSidebarPanel: (panel) =>
    set((state) => ({
      sidebarPanel: state.sidebarPanel === panel ? null : panel,
    })),

  // Model
  selectedModel: '',
  setSelectedModel: (model) => set({ selectedModel: model }),

  // Progress
  progress: defaultProgress,
  setProgress: (p) =>
    set((state) => ({ progress: { ...state.progress, ...p } })),
  resetProgress: () => set({ progress: defaultProgress }),

  // Streaming
  streamingMessages: {},
  appendToken: (messageId, token) =>
    set((state) => {
      const existing = state.streamingMessages[messageId]
      return {
        streamingMessages: {
          ...state.streamingMessages,
          [messageId]: {
            messageId,
            content: (existing?.content ?? '') + token,
            isStreaming: true,
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

  // Show work
  showWorkMode: false,
  toggleShowWork: () => set((state) => ({ showWorkMode: !state.showWorkMode })),
}))