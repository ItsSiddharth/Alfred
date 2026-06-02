/**
 * api/useWebSocket.ts — WebSocket hook for a single project connection.
 *
 * Stage 2 additions:
 *  - Routes `state_change` events to progress strip
 *  - Routes `approval_request` events to store.setApprovalRequest
 *  - Routes `log` / `thinking` events to store.appendLogToken
 *  - Routes `plan` events into the persisted message stream
 *
 * Auto-reconnects on unexpected close (up to 5 attempts, exponential backoff).
 */

import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useStore } from '../store'

const WS_BASE = 'ws://localhost:8000'
const MAX_RETRIES = 5

export function useWebSocket(projectId: number | null) {
  const wsRef = useRef<WebSocket | null>(null)
  const retryCountRef = useRef(0)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const {
    setProgress,
    appendToken,
    finaliseStream,
    resetProgress,
    setApprovalRequest,
    appendLogToken,
    finaliseLog,
    appendPersistedMessage,
  } = useStore()
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!projectId) return

    let cancelled = false

    function connect() {
      if (cancelled) return

      const url = `${WS_BASE}/ws/project/${projectId}`
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        retryCountRef.current = 0
        console.info(`[WS] Connected: project ${projectId}`)
      }

      ws.onmessage = (evt) => {
        let envelope: { type: string; ts: string; payload: Record<string, unknown> }
        try {
          envelope = JSON.parse(evt.data)
        } catch {
          return
        }

        const { type, payload } = envelope

        switch (type) {
          // ── Progress ────────────────────────────────────────────────────
          case 'progress':
            setProgress({
              stage: (payload.stage as number) ?? 1,
              substage: (payload.substage as string) ?? '',
              label: (payload.label as string) ?? '',
              current: (payload.current as number) ?? 0,
              total: (payload.total as number) ?? 0,
              status:
                (payload.status as
                  | 'running'
                  | 'waiting'
                  | 'error'
                  | 'done'
                  | 'idle') ?? 'running',
            })
            break

          // ── State change — update progress strip substage label ─────────
          case 'state_change':
            setProgress({
              stage: (payload.stage as number) ?? undefined,
              substage: (payload.substage as string) ?? '',
              label: (payload.label as string) ?? '',
              status: 'running',
            })
            break

          // ── Token streaming ─────────────────────────────────────────────
          case 'token': {
            const messageId = (payload.message_id as string) || 'stream'
            const token = (payload.token as string) || ''
            const kind = (payload.kind as string) || 'chat'
            if (token) {
              if (kind === 'thinking') {
                appendLogToken(messageId, token, 'thinking', 'thinking')
              } else {
                appendToken(messageId, token)
              }
            }
            break
          }

          // ── Done ────────────────────────────────────────────────────────
          case 'done': {
            const streams = useStore.getState().streamingMessages
            Object.keys(streams).forEach((id) => finaliseStream(id))
            const logs = useStore.getState().logEntries
            Object.keys(logs).forEach((id) => finaliseLog(id))
            setProgress({
              status: 'done',
              label: (payload.summary as string) || 'Done',
            })
            break
          }

          // ── Approval request ────────────────────────────────────────────
          case 'approval_request':
            setApprovalRequest({
              stage: (payload.stage as number) ?? 1,
              substage: (payload.substage as string) ?? '',
              plan: (payload.plan as Record<string, unknown>) ?? {},
              auto_approve: (payload.auto_approve as boolean) ?? false,
              experiment_id: (payload.experiment_id as number) ?? undefined,
            })
            break

          // ── Log / thinking events ───────────────────────────────────────
          case 'log': {
            const msgId = (payload.message_id as string) || `log-${Date.now()}`
            const content = (payload.message as string) || (payload.content as string) || ''
            const phase = (payload.phase as string) || 'log'
            const logKind = (payload.kind as string) === 'thinking' ? 'thinking' : 'log'
            if (content) appendLogToken(msgId, content, phase, logKind as 'thinking' | 'log')
            break
          }

          // ── Plan card ───────────────────────────────────────────────────
          case 'plan': {
            // Plans are surfaced via the approval_request flow; also log them.
            console.info('[WS] plan:', payload)
            break
          }

          // ── Error ───────────────────────────────────────────────────────
          case 'error':
            console.error('[WS] Error event:', payload)
            setProgress({
              status: 'error',
              label: (payload.message as string) || 'Error',
            })
            break

          // ── Result ─────────────────────────────────────────────────────
          case 'result':
            if ((payload as { kind?: string }).kind === 'model_pulled') {
              queryClient.invalidateQueries({ queryKey: ['local-models'] })
              queryClient.invalidateQueries({ queryKey: ['ollama-health'] })
              const model = payload.model as string
              if (model) useStore.getState().removePullingModel(model)
            }
            break

          // ── Tool call (Stage 4+) ────────────────────────────────────────
          case 'tool_call': {
            const tcId = (payload.tool_call_id as string) || `tc-${Date.now()}`
            const summary = `[Tool: ${payload.tool_name}] ${payload.query ?? ''}`
            appendLogToken(tcId, summary, 'tool', 'tool_call')
            break
          }

          // ── Plot (Stage 7+) ─────────────────────────────────────────────
          case 'plot':
            console.info('[WS] plot received:', payload)
            break

          default:
            console.debug('[WS] Unknown event type:', type, payload)
        }
      }

      ws.onclose = (evt) => {
        wsRef.current = null
        if (cancelled) return
        if (evt.wasClean) {
          console.info('[WS] Closed cleanly')
          return
        }
        if (retryCountRef.current < MAX_RETRIES) {
          const delay = Math.min(1000 * 2 ** retryCountRef.current, 15000)
          retryCountRef.current += 1
          console.warn(`[WS] Disconnected — retry ${retryCountRef.current} in ${delay}ms`)
          retryTimerRef.current = setTimeout(connect, delay)
        } else {
          console.error('[WS] Max retries reached — giving up.')
        }
      }

      ws.onerror = (evt) => {
        console.warn('[WS] Socket error:', evt)
      }
    }

    connect()

    function handleSendEvent(e: Event) {
      const detail = (e as CustomEvent).detail
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(detail))
      } else {
        console.warn('[WS] Cannot send — socket not open', wsRef.current?.readyState)
      }
    }
    window.addEventListener('alfred:send', handleSendEvent)

    return () => {
      cancelled = true
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
      window.removeEventListener('alfred:send', handleSendEvent)
      if (wsRef.current) {
        wsRef.current.close(1000, 'project changed')
        wsRef.current = null
      }
      resetProgress()
    }
  }, [projectId]) // eslint-disable-line react-hooks/exhaustive-deps
}