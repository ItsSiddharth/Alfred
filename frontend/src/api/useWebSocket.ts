/**
 * api/useWebSocket.ts — Stage 4 (patched).
 *
 * Key fixes vs original:
 *
 * F1 — Double-render eliminated:
 *   The old code both appended to streamingMessages AND patched persistedMessages
 *   for the same token. On `done`, streamingMessages was marked isStreaming=false
 *   but NOT removed, so ChatThread rendered both. Now:
 *   - Tokens go ONLY to patchPersistedMessage (updates the DB-row placeholder)
 *     when we have a streamingMsgId.
 *   - appendToken (streamingMessages buffer) is used ONLY as a fallback when
 *     no streamingMsgId exists (e.g. legacy/test streams without msg_start).
 *   - finaliseStream() now removes the entry from streamingMessages entirely
 *     (change in store/index.ts), so no stale entry remains to double-render.
 *
 * F2 — Race condition mitigated:
 *   msg_start is now processed before the streaming coroutine can possibly
 *   emit tokens (backend awaits the WS send). The frontend stores the
 *   streamingMsgId immediately. If a token somehow arrives before msg_start
 *   (shouldn't happen but network reordering could theoretically cause it),
 *   we fall back to the streamingMessages buffer which gets cleaned up on done.
 *
 * Show Work improvements:
 *   The raw memory block, memory token count, and model name from
 *   metadata_json are now surfaced in the tool_call / log events when
 *   showWorkMode is active, giving the user visibility into what went
 *   into the LLM.
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
    setStreamingMsgId,
    addToolCall,
    addPlot,
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

        const { type, payload, ts = new Date().toISOString() } = envelope

        switch (type) {

          // ── msg_start — backend created the DB row, here is its id ──────
          case 'msg_start': {
            const msgId = payload.msg_id as number | undefined
            if (msgId != null) {
              // Store which DB row the tokens should patch
              setStreamingMsgId(msgId)
              // Add an empty placeholder to the persisted list so the row
              // renders immediately and tokens patch it in place.
              // appendPersistedMessage guards against duplicate IDs.
              appendPersistedMessage({
                id: msgId,
                project_id: projectId as number,
                role: 'assistant',
                content: '',
                created_at: new Date().toISOString(),
                kind: 'chat',
                metadata_json: '{}',
              })
            }
            break
          }

          // ── Progress ──────────────────────────────────────────────────────
          case 'progress':
            setProgress({
              stage: (payload.stage as number) ?? 1,
              substage: (payload.substage as string) ?? '',
              label: (payload.label as string) ?? '',
              current: (payload.current as number) ?? 0,
              total: (payload.total as number) ?? 0,
              status:
                (payload.status as 'running' | 'waiting' | 'error' | 'done' | 'idle') ??
                'running',
            })
            break

          // ── State change ──────────────────────────────────────────────────
          case 'state_change':
            setProgress({
              stage: (payload.stage as number) ?? undefined,
              substage: (payload.substage as string) ?? '',
              label: (payload.label as string) ?? '',
              status: 'running',
            })
            break

          // ── Token streaming ───────────────────────────────────────────────
          case 'token': {
            const token = (payload.token as string) || ''
            if (!token) break

            const kind = (payload.kind as string) || 'chat'

            if (kind === 'thinking') {
              // Thinking tokens always go to the log/thinking buffer
              const messageId = (payload.message_id as string) || 'thinking-stream'
              appendLogToken(messageId, token, 'thinking', 'thinking')
              break
            }

            // Regular assistant tokens: append to the persisted DB-row placeholder.
            // Single setState call — avoids the previous double-update bug where
            // patchPersistedMessage(id, '') reset content to '' then setState
            // appended only the new token, making each token replace the last.
            const streamId = useStore.getState().streamingMsgId
            if (streamId != null) {
              useStore.setState((state) => ({
                persistedMessages: state.persistedMessages.map((m) =>
                  m.id === streamId ? { ...m, content: m.content + token } : m
                ),
              }))
            } else {
              // Fallback: no DB row yet (msg_start hasn't arrived or no row)
              const messageId =
                (payload.message_id as string) || `stream-${projectId}`
              appendToken(messageId, token)
            }
            break
          }

          // ── Done ──────────────────────────────────────────────────────────
          case 'done': {
            const doneMsgId = useStore.getState().streamingMsgId

            // Clear the streaming row tracker
            setStreamingMsgId(null)

            // Remove all streaming buffer entries
            const streams = useStore.getState().streamingMessages
            Object.keys(streams).forEach((id) => finaliseStream(id))

            // Mark thinking/log tabs as no longer streaming
            const logs = useStore.getState().logEntries
            Object.keys(logs).forEach((id) => finaliseLog(id))

            setProgress({
              status: 'done',
              label: (payload.summary as string) || 'Done',
            })

            // Fix 2 — Show Work: fetch the completed message row from REST so
            // metadata_json (model, memory_tokens, memory_block) is available
            // to ShowWorkMeta. The msg_start placeholder always has '{}', so
            // without this fetch Show Work has nothing to display.
            if (doneMsgId != null && projectId != null) {
              fetch(`/api/projects/${projectId}/messages/${doneMsgId}`)
                .then((r) => r.ok ? r.json() : null)
                .then((msg) => {
                  if (!msg) return
                  useStore.setState((state) => ({
                    persistedMessages: state.persistedMessages.map((m) =>
                      m.id === doneMsgId
                        ? { ...m, metadata_json: msg.metadata_json ?? m.metadata_json }
                        : m
                    ),
                  }))
                })
                .catch(() => { /* metadata fetch is best-effort */ })
            }
            break
          }

          // ── Approval request ──────────────────────────────────────────────
          case 'approval_request':
            setApprovalRequest({
              stage: (payload.stage as number) ?? 1,
              substage: (payload.substage as string) ?? '',
              plan: (payload.plan as Record<string, unknown>) ?? {},
              auto_approve: (payload.auto_approve as boolean) ?? false,
              experiment_id: (payload.experiment_id as number) ?? undefined,
            })
            break

          // ── Log / thinking ────────────────────────────────────────────────
          case 'log': {
            const msgId =
              (payload.message_id as string) || `log-${Date.now()}`
            const content =
              (payload.message as string) || (payload.content as string) || ''
            const phase = (payload.phase as string) || 'log'
            const logKind =
              (payload.kind as string) === 'thinking' ? 'thinking' : 'log'
            if (content) {
              appendLogToken(msgId, content, phase, logKind as 'thinking' | 'log')
            }
            break
          }

          // ── Tool call ─────────────────────────────────────────────────────
          case 'tool_call': {
            addToolCall({
              tool_name: (payload.tool_name as string) ?? 'unknown',
              input: payload.input as Record<string, unknown> | undefined,
              reason: payload.reason as string | undefined,
              status:
                (payload.status as 'running' | 'done' | 'error') ?? 'done',
              sources: payload.sources as string[] | undefined,
              error: payload.error as string | null | undefined,
              result_count: payload.result_count as number | undefined,
              ts,
            })
            break
          }

          // ── Error ─────────────────────────────────────────────────────────
          case 'error':
            console.error('[WS] Error event:', payload)
            setProgress({
              status: 'error',
              label: (payload.message as string) || 'Error',
            })
            break

          // ── Plot ─────────────────────────────────────────────────────────
          case 'plot': {
            addPlot({
              filename: payload.filename as string,
              base64_png: payload.base64_png as string,
              ascii_art: payload.ascii_art as string,
              experiment_id: payload.experiment_id as number,
              ts,
            })
            break
          }

          // ── Result (e.g. model pulled) ────────────────────────────────────
          case 'result':
            if ((payload as { kind?: string }).kind === 'model_pulled') {
              queryClient.invalidateQueries({ queryKey: ['local-models'] })
              queryClient.invalidateQueries({ queryKey: ['ollama-health'] })
              const model = payload.model as string
              if (model) useStore.getState().removePullingModel(model)
            }
            break

          default:
            console.debug('[WS] Unknown event type:', type, payload)
        }
      }

      ws.onclose = (evt) => {
        wsRef.current = null
        if (cancelled) return
        if (evt.wasClean) return
        if (retryCountRef.current < MAX_RETRIES) {
          const delay = Math.min(1000 * 2 ** retryCountRef.current, 15000)
          retryCountRef.current += 1
          console.warn(
            `[WS] Disconnected — retry ${retryCountRef.current} in ${delay}ms`
          )
          retryTimerRef.current = setTimeout(connect, delay)
        }
      }

      ws.onerror = () => {
        // onclose fires after onerror — let that handle reconnect
      }
    }

    connect()

    // Allow ChatBar / other components to dispatch messages via custom event
    function handleSendEvent(e: Event) {
      const detail = (e as CustomEvent).detail
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(detail))
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