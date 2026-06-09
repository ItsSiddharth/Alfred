/**
 * Pipeline progress strip — always visible at the top of the main panel.
 *
 * Driven entirely by the Zustand progress state, which is fed by WS `progress`
 * events. Shows: Stage N / substage name / tqdm-style live bar / label.
 * Also shows an Ollama health dot (polled every 20s).
 * The Force Reset button is always visible — it rolls back to the last checkpoint
 * after an explicit confirmation dialog.
 */
import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { RotateCcw } from 'lucide-react'
import { modelsApi, projectsApi } from '../../api/client'
import { useStore } from '../../store'

const STAGE_NAMES: Record<number, string> = {
  1: 'Hypothesis',
  2: 'Setup',
  3: 'Run',
}

const STATUS_COLORS: Record<string, string> = {
  running: 'var(--running)',
  waiting: 'var(--warn)',
  error: 'var(--danger)',
  done: 'var(--success)',
  idle: 'var(--border-strong)',
}

// ---------------------------------------------------------------------------
// ForceResetButton — always rendered; shows a confirmation dialog before acting
// ---------------------------------------------------------------------------

function ForceResetButton() {
  const activeProjectId = useStore((s) => s.activeProjectId)
  const setProgress = useStore((s) => s.setProgress)
  const setApprovalRequest = useStore((s) => s.setApprovalRequest)
  const setStreamingMsgId = useStore((s) => s.setStreamingMsgId)
  const [confirming, setConfirming] = useState(false)

  const mutation = useMutation({
    mutationFn: () => projectsApi.forceReset(activeProjectId!),
    onSuccess: (data) => {
      const sub = data.restored_to?.substage?.replace(/_/g, ' ') ?? 'checkpoint'
      setProgress({ status: 'idle', label: `Reset to: ${sub}`, substage: data.restored_to?.substage ?? 'idle' })
      setApprovalRequest(null)
      setStreamingMsgId(null)
      setConfirming(false)
    },
    onError: () => {
      setConfirming(false)
    },
  })

  if (!activeProjectId) return null

  if (confirming) {
    return (
      <div
        className="flex items-center gap-2 px-2 py-1 rounded border text-xs font-mono"
        style={{
          backgroundColor: 'rgba(239,68,68,0.10)',
          borderColor: 'rgba(239,68,68,0.4)',
        }}
      >
        <span style={{ color: 'var(--danger)' }}>Roll back to last checkpoint?</span>
        <button
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
          className="px-2 py-0.5 rounded text-xs font-mono transition-colors disabled:opacity-50"
          style={{
            backgroundColor: 'rgba(239,68,68,0.20)',
            color: 'var(--danger)',
            border: '1px solid rgba(239,68,68,0.4)',
          }}
        >
          {mutation.isPending ? 'Resetting…' : 'Confirm reset'}
        </button>
        <button
          onClick={() => setConfirming(false)}
          className="px-2 py-0.5 rounded text-xs font-mono transition-colors"
          style={{ color: 'var(--text-tertiary)', border: '1px solid var(--border)' }}
        >
          Cancel
        </button>
      </div>
    )
  }

  return (
    <button
      onClick={() => setConfirming(true)}
      className="shrink-0 flex items-center gap-1 px-2 py-0.5 rounded border text-xs font-mono transition-colors"
      title="Force reset — roll back to last checkpoint. Use when the agent is stuck."
      style={{
        color: 'var(--text-tertiary)',
        borderColor: 'var(--border)',
        backgroundColor: 'transparent',
      }}
    >
      <RotateCcw size={10} />
      Reset
    </button>
  )
}

export function ProgressStrip() {
  const progress = useStore((s) => s.progress)
  const { stage, substage, label, current, total, status } = progress

  const { data: health } = useQuery({
    queryKey: ['ollama-health-strip'],
    queryFn: modelsApi.getHealth,
    refetchInterval: 20_000,
    staleTime: 15_000,
  })
  const ollamaDown = health != null && !health.available

  const isIdle = status === 'idle'
  const pct = total > 0 ? Math.round((current / total) * 100) : 0
  const barColor = STATUS_COLORS[status] ?? STATUS_COLORS.idle
  const stageName = STAGE_NAMES[stage] ?? `Stage ${stage}`

  return (
    <div
      className="flex items-center gap-3 px-4 py-2 border-b border-border text-sm font-mono select-none"
      style={{ backgroundColor: 'var(--bg-inset)', minHeight: '38px' }}
    >
      {/* Stage pill */}
      <span
        className="shrink-0 px-2 py-0.5 rounded text-sm font-medium"
        style={{
          backgroundColor: isIdle ? 'var(--bg-elevated)' : `${barColor}22`,
          color: isIdle ? 'var(--text-tertiary)' : barColor,
          border: `1px solid ${isIdle ? 'var(--border)' : barColor}`,
        }}
      >
        {stageName}
      </span>

      {/* Substage name */}
      <span
        className="shrink-0 text-sm"
        style={{ color: isIdle ? 'var(--text-tertiary)' : 'var(--text-secondary)' }}
      >
        {isIdle ? 'idle' : substage}
      </span>

      {/* tqdm-style bar */}
      {!isIdle && total > 0 && (
        <>
          <div
            className="relative flex-1 h-1.5 rounded-full overflow-hidden"
            style={{ backgroundColor: 'var(--bg-elevated)' }}
          >
            <div
              className="absolute inset-y-0 left-0 rounded-full transition-all duration-300"
              style={{ width: `${pct}%`, backgroundColor: barColor }}
            />
          </div>

          <span className="shrink-0 tabular-nums" style={{ color: 'var(--text-tertiary)' }}>
            {current}/{total}
          </span>
        </>
      )}

      {/* Running spinner dot */}
      {status === 'running' && (
        <span
          className="shrink-0 w-1.5 h-1.5 rounded-full pulse-dot"
          style={{ backgroundColor: 'var(--running)' }}
        />
      )}

      {/* Label */}
      <span
        className="flex-1 truncate text-sm"
        style={{ color: isIdle ? 'var(--text-tertiary)' : 'var(--text-secondary)' }}
      >
        {isIdle ? 'No active pipeline' : label}
      </span>

      {/* Ollama health dot */}
      {ollamaDown && (
        <span
          className="shrink-0 flex items-center gap-1 text-xs font-mono px-2 py-0.5 rounded border"
          title="Ollama is not reachable — models cannot run"
          style={{
            color: 'var(--danger)',
            borderColor: 'rgba(239,68,68,0.3)',
            backgroundColor: 'rgba(239,68,68,0.07)',
          }}
        >
          <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: 'var(--danger)' }} />
          Ollama offline
        </span>
      )}

      {/* Force reset — always visible */}
      <ForceResetButton />
    </div>
  )
}