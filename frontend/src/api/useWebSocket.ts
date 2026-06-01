/**
 * useWebSocket — connects to /ws/project/{projectId} and dispatches all
 * canonical WS event types into the Zustand store.
 *
 * Re-connects automatically on disconnect with exponential back-off.
 * Returns the WebSocket instance so callers can send messages if needed.
 */

import { useCallback, useEffect, useRef } from 'react'
import { useStore } from '../store'

// Canonical payload shapes matching C7
interface WsEnvelope {
  type: string
  ts: string
  payload: Record<string, unknown>
}

interface ProgressPayload {
  stage: number
  substage: string
  label: string
  current: number
  total: number
  status: 'running' | 'waiting' | 'error' | 'done' | 'idle'
}

interface TokenPayload {
  token: string
  message_id: string
}

const RECONNECT_BASE_MS = 1000
const RECONNECT_MAX_MS = 16000

export function useWebSocket(projectId: number | null) {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectDelay = useRef(RECONNECT_BASE_MS)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const unmounted = useRef(false)

  const { setProgress, appendToken, finaliseStream } = useStore()

  const dispatch = useCallback(
    (envelope: WsEnvelope) => {
      const { type, payload } = envelope

      switch (type) {
        case 'progress': {
          const p = payload as unknown as ProgressPayload
          setProgress({
            stage: p.stage,
            substage: p.substage,
            label: p.label,
            current: p.current,
            total: p.total,
            status: p.status ?? 'running',
          })
          break
        }

        case 'token': {
          const t = payload as unknown as TokenPayload
          appendToken(t.message_id || 'stream', t.token)
          break
        }

        case 'done': {
          setProgress({ status: 'done' })
          // Finalise any open streaming message
          finaliseStream('stream')
          break
        }

        case 'error': {
          setProgress({ status: 'error' })
          console.warn('[WS error]', payload)
          break
        }

        case 'state_change':
        case 'log':
        case 'plan':
        case 'approval_request':
        case 'tool_call':
        case 'result':
        case 'plot':
          // Handled by higher-level components in later stages.
          // Log for observability during Stage 0.
          console.debug('[WS]', type, payload)
          break

        default:
          console.debug('[WS unknown]', type, payload)
      }
    },
    [setProgress, appendToken, finaliseStream],
  )

  const connect = useCallback(() => {
    if (projectId === null || unmounted.current) return

    const url = `ws://${window.location.hostname}:8000/ws/project/${projectId}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      reconnectDelay.current = RECONNECT_BASE_MS
      console.info(`[WS] connected project=${projectId}`)
    }

    ws.onmessage = (ev: MessageEvent<string>) => {
      try {
        const envelope = JSON.parse(ev.data) as WsEnvelope
        dispatch(envelope)
      } catch (err) {
        console.warn('[WS] failed to parse message', err)
      }
    }

    ws.onclose = () => {
      if (unmounted.current) return
      console.info(`[WS] disconnected — reconnecting in ${reconnectDelay.current}ms`)
      reconnectTimer.current = setTimeout(() => {
        reconnectDelay.current = Math.min(reconnectDelay.current * 2, RECONNECT_MAX_MS)
        connect()
      }, reconnectDelay.current)
    }

    ws.onerror = (ev) => {
      console.warn('[WS] error', ev)
      ws.close()
    }
  }, [projectId, dispatch])

  useEffect(() => {
    unmounted.current = false
    if (projectId !== null) connect()

    return () => {
      unmounted.current = true
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      if (wsRef.current) {
        wsRef.current.onclose = null // prevent reconnect on intentional close
        wsRef.current.close()
      }
    }
  }, [projectId, connect])

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data))
    }
  }, [])

  return { send }
}