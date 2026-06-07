/**
 * Pipeline progress strip — always visible at the top of the main panel.
 *
 * Driven entirely by the Zustand progress state, which is fed by WS `progress`
 * events. Shows: Stage N / substage name / tqdm-style live bar / label.
 * Also shows an Ollama health dot (polled every 20s).
 */
import { useQuery } from '@tanstack/react-query'
import { modelsApi } from '../../api/client'
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
    </div>
  )
}