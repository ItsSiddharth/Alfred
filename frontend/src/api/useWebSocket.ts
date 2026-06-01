/**
 * api/useWebSocket.ts — WebSocket hook for a single project connection.
 *
 * Connects to ws://localhost:8000/ws/project/{projectId} when projectId is set.
 * Parses incoming JSON envelopes and routes them to the Zustand store.
 *
 * Outbound messages are dispatched via the custom DOM event `alfred:send`.
 * ChatBar fires: window.dispatchEvent(new CustomEvent('alfred:send', { detail: {...} }))
 * This hook forwards that detail object as JSON to the WebSocket.
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

  const { setProgress, appendToken, finaliseStream, resetProgress } = useStore()
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
          case 'progress':
            setProgress({
              stage: (payload.stage as number) ?? 1,
              substage: (payload.substage as string) ?? '',
              label: (payload.label as string) ?? '',
              current: (payload.current as number) ?? 0,
              total: (payload.total as number) ?? 0,
              status: (payload.status as 'running' | 'waiting' | 'error' | 'done' | 'idle') ?? 'running',
            })
            break

          case 'token': {
            const messageId = (payload.message_id as string) || 'stream'
            const token = (payload.token as string) || ''
            if (token) appendToken(messageId, token)
            break
          }

          case 'done': {
            // Finalise all active streaming messages.
            const streams = useStore.getState().streamingMessages
            Object.keys(streams).forEach((id) => finaliseStream(id))
            setProgress({ status: 'done', label: (payload.summary as string) || 'Done' })
            break
          }

          case 'error':
            console.error('[WS] Error event:', payload)
            setProgress({ status: 'error', label: (payload.message as string) || 'Error' })
            break

          case 'result':
            // Handle model_pulled result — refresh local model list.
            if ((payload as { kind?: string }).kind === 'model_pulled') {
              queryClient.invalidateQueries({ queryKey: ['local-models'] })
              queryClient.invalidateQueries({ queryKey: ['ollama-health'] })
              // Remove from pulling set.
              const model = payload.model as string
              if (model) useStore.getState().removePullingModel(model)
            }
            break

          case 'state_change':
            // Stage 2 will route this properly.
            console.info('[WS] state_change:', payload)
            break

          case 'log':
          case 'plan':
          case 'approval_request':
          case 'tool_call':
          case 'plot':
            // Handled in later stages.
            console.info(`[WS] ${type}:`, payload)
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
        // Unexpected close — retry with exponential backoff.
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
        // onclose will fire next and handle the retry.
      }
    }

    connect()

    // Forward alfred:send DOM events to the WebSocket.
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