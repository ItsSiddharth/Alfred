/**
 * DashboardPanel — Stage 8: Comparison dashboard, compute budget, paper export.
 *
 * Shows:
 *  - Comparison table (one row per iteration: metrics, runtime, git commit)
 *  - Recharts line charts overlaying metric curves across iterations
 *  - Compute budget estimate (median of past runtimes)
 *  - Export button (Markdown + LaTeX research note)
 */

import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer,
} from 'recharts'
import { Download, RefreshCw, BarChart2, Clock, Table2, X } from 'lucide-react'
import { dashboardApi, type DashboardMetricCurve } from '../../api/client'
import { useStore } from '../../store'

// Distinct stroke colors for up to 8 iterations
const ITER_COLORS = [
  'var(--accent)',
  '#f59e0b',
  '#34d399',
  '#f87171',
  '#a78bfa',
  '#38bdf8',
  '#fb923c',
  '#e879f9',
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtRuntime(secs: number | null): string {
  if (secs == null) return '—'
  if (secs < 60) return `${secs.toFixed(0)}s`
  if (secs < 3600) return `${(secs / 60).toFixed(1)}m`
  return `${(secs / 3600).toFixed(2)}h`
}

function statusBadgeColor(status: string): string {
  if (status === 'done') return 'var(--running)'
  if (status === 'running') return 'var(--warn)'
  if (status === 'failed') return 'var(--danger)'
  return 'var(--text-tertiary)'
}

// ---------------------------------------------------------------------------
// ChartSection — one chart per metric name, all iterations overlaid
// ---------------------------------------------------------------------------

function buildChartData(
  curves: DashboardMetricCurve[],
  metricName: string,
): Array<Record<string, number>> {
  // Collect all step values for this metric
  const forMetric = curves.filter((c) => c.name === metricName)
  const allSteps = new Set<number>()
  for (const c of forMetric) {
    for (const p of c.points) allSteps.add(p.step)
  }
  const steps = Array.from(allSteps).sort((a, b) => a - b)

  return steps.map((step) => {
    const row: Record<string, number> = { step }
    for (const c of forMetric) {
      const pt = c.points.find((p) => p.step === step)
      if (pt != null) row[`iter ${c.iteration}`] = pt.value
    }
    return row
  })
}

function MetricChart({
  metricName,
  curves,
  iterIds,
}: {
  metricName: string
  curves: DashboardMetricCurve[]
  iterIds: number[]
}) {
  const data = buildChartData(curves, metricName)
  if (data.length === 0) return null

  // Single data point — show a value badge instead of a useless single-point chart
  if (data.length === 1) {
    const row = data[0]
    return (
      <div className="mb-4">
        <div className="text-xs font-mono mb-1.5" style={{ color: 'var(--text-secondary)' }}>
          {metricName}
        </div>
        <div className="flex flex-wrap gap-2">
          {iterIds.map((iter, i) => {
            const val = row[`iter ${iter}`]
            if (val == null) return null
            const color = ITER_COLORS[i % ITER_COLORS.length]
            return (
              <div
                key={iter}
                className="flex items-center gap-2 px-3 py-1.5 rounded text-xs font-mono"
                style={{
                  backgroundColor: 'var(--bg-inset)',
                  border: `1px solid ${color}40`,
                }}
              >
                <span style={{ color: 'var(--text-tertiary)' }}>iter {iter}</span>
                <span style={{ color, fontWeight: 600, fontSize: 13 }}>
                  {val % 1 === 0 ? val : val.toFixed(4)}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div className="mb-4">
      <div className="text-xs font-mono mb-1.5" style={{ color: 'var(--text-secondary)' }}>
        {metricName}
      </div>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis
            dataKey="step"
            tick={{ fontSize: 10, fill: 'var(--text-tertiary)' }}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 10, fill: 'var(--text-tertiary)' }}
            tickLine={false}
            width={44}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              fontSize: 11,
              color: 'var(--text-primary)',
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 10, color: 'var(--text-tertiary)' }}
          />
          {iterIds.map((iter, i) => (
            <Line
              key={iter}
              type="monotone"
              dataKey={`iter ${iter}`}
              stroke={ITER_COLORS[i % ITER_COLORS.length]}
              dot={false}
              strokeWidth={1.5}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// ---------------------------------------------------------------------------
// DashboardPanel
// ---------------------------------------------------------------------------

export function DashboardPanel() {
  const activeProjectId = useStore((s) => s.activeProjectId)
  const setSidebarPanel = useStore((s) => s.setSidebarPanel)
  const [activeTab, setActiveTab] = useState<'charts' | 'table'>('charts')

  const dashQuery = useQuery({
    queryKey: ['dashboard', activeProjectId],
    queryFn: () => dashboardApi.getDashboard(activeProjectId!),
    enabled: activeProjectId != null,
  })

  const estimateQuery = useQuery({
    queryKey: ['compute-estimate', activeProjectId],
    queryFn: () => dashboardApi.getComputeEstimate(activeProjectId!),
    enabled: activeProjectId != null,
  })

  const exportMutation = useMutation({
    mutationFn: () => dashboardApi.export(activeProjectId!, true),
    onSuccess: (data) => {
      // Trigger a Markdown download
      const blob = new Blob([data.markdown], { type: 'text/markdown' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${data.filename}.md`
      a.click()
      URL.revokeObjectURL(url)
    },
  })

  if (!activeProjectId) {
    return (
      <div className="flex flex-col h-full items-center justify-center p-6 text-center">
        <BarChart2 size={28} style={{ color: 'var(--text-tertiary)' }} />
        <p className="mt-3 text-sm" style={{ color: 'var(--text-tertiary)' }}>
          Select a project to view the comparison dashboard.
        </p>
      </div>
    )
  }

  const dash = dashQuery.data
  const estimate = estimateQuery.data
  const iterIds = dash?.experiments.map((e) => e.iteration) ?? []

  return (
    <div className="flex flex-col h-full overflow-hidden w-full">
      {/* Header */}
      <div
        className="flex items-center gap-2 px-4 py-3 border-b shrink-0"
        style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-elevated)' }}
      >
        <BarChart2 size={14} style={{ color: 'var(--accent)' }} />
        <span className="text-sm font-medium flex-1" style={{ color: 'var(--text-primary)' }}>
          Dashboard
        </span>

        {/* Compute estimate */}
        {estimate && estimate.estimated_seconds != null && (
          <div className="flex items-center gap-1 text-xs font-mono"
            style={{ color: 'var(--text-tertiary)' }}>
            <Clock size={10} />
            {estimate.estimated_label}
          </div>
        )}

        {/* Refresh */}
        <button
          onClick={() => dashQuery.refetch()}
          disabled={dashQuery.isFetching}
          className="p-1 rounded transition-colors disabled:opacity-40"
          style={{ color: 'var(--text-tertiary)' }}
          title="Refresh"
        >
          <RefreshCw size={12} className={dashQuery.isFetching ? 'animate-spin' : ''} />
        </button>

        {/* Export */}
        <button
          onClick={() => exportMutation.mutate()}
          disabled={exportMutation.isPending || !dash?.experiments.length}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs font-mono border transition-colors disabled:opacity-40"
          style={{
            color: 'var(--accent)',
            borderColor: 'var(--border)',
            backgroundColor: 'transparent',
          }}
          title="Export research note (Markdown)"
        >
          <Download size={10} />
          {exportMutation.isPending ? 'Exporting…' : 'Export'}
        </button>

        {/* Close */}
        <button
          onClick={() => setSidebarPanel(null)}
          className="p-1 rounded transition-colors"
          style={{ color: 'var(--text-tertiary)' }}
          title="Close panel"
        >
          <X size={13} />
        </button>
      </div>

      {/* Tab bar */}
      <div
        className="flex border-b shrink-0"
        style={{ borderColor: 'var(--border)' }}
      >
        {(['charts', 'table'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className="flex items-center gap-1.5 px-4 py-2 text-xs font-mono border-b-2 transition-colors"
            style={{
              borderColor: activeTab === tab ? 'var(--accent)' : 'transparent',
              color: activeTab === tab ? 'var(--accent)' : 'var(--text-tertiary)',
            }}
          >
            {tab === 'charts' ? <BarChart2 size={11} /> : <Table2 size={11} />}
            {tab}
          </button>
        ))}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4">
        {dashQuery.isLoading && (
          <div className="text-xs text-center py-8" style={{ color: 'var(--text-tertiary)' }}>
            Loading…
          </div>
        )}

        {dashQuery.isError && (
          <div className="text-xs text-center py-8" style={{ color: 'var(--danger)' }}>
            Failed to load dashboard data.
          </div>
        )}

        {dash && dash.experiments.length === 0 && (
          <div className="text-xs text-center py-8" style={{ color: 'var(--text-tertiary)' }}>
            No experiment runs yet. Start running experiments to see comparison charts.
          </div>
        )}

        {dash && dash.experiments.length > 0 && (
          <>
            {activeTab === 'charts' && (
              <div>
                {dash.metric_names.length === 0 && (
                  <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                    No metrics parsed yet. Metrics are captured when experiments emit{' '}
                    <code>ALFRED_METRIC</code> markers during a run.
                  </p>
                )}
                {dash.metric_names.map((name) => (
                  <MetricChart
                    key={name}
                    metricName={name}
                    curves={dash.metric_curves}
                    iterIds={iterIds}
                  />
                ))}
              </div>
            )}

            {activeTab === 'table' && (
              <div className="overflow-x-auto">
                <table className="w-full text-xs font-mono border-collapse">
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      <th className="text-left px-2 py-1.5" style={{ color: 'var(--text-tertiary)' }}>iter</th>
                      <th className="text-left px-2 py-1.5" style={{ color: 'var(--text-tertiary)' }}>status</th>
                      <th className="text-left px-2 py-1.5" style={{ color: 'var(--text-tertiary)' }}>runtime</th>
                      <th className="text-left px-2 py-1.5" style={{ color: 'var(--text-tertiary)' }}>commit</th>
                      {dash.metric_names.map((n) => (
                        <th key={n} className="text-right px-2 py-1.5"
                          style={{ color: 'var(--text-tertiary)' }}>
                          {n}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {dash.experiments.map((exp, i) => (
                      <tr
                        key={exp.id}
                        style={{
                          borderBottom: '1px solid var(--border)',
                          backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--bg-inset)',
                        }}
                      >
                        <td className="px-2 py-1.5" style={{ color: 'var(--accent)' }}>
                          {exp.iteration}
                        </td>
                        <td className="px-2 py-1.5">
                          <span style={{ color: statusBadgeColor(exp.status) }}>
                            {exp.status}
                          </span>
                        </td>
                        <td className="px-2 py-1.5" style={{ color: 'var(--text-secondary)' }}>
                          {fmtRuntime(exp.runtime_seconds)}
                        </td>
                        <td className="px-2 py-1.5" style={{ color: 'var(--text-tertiary)' }}>
                          {exp.git_commit || '—'}
                        </td>
                        {dash.metric_names.map((n) => (
                          <td key={n} className="text-right px-2 py-1.5"
                            style={{ color: 'var(--text-primary)' }}>
                            {exp.metrics_summary[n] != null
                              ? exp.metrics_summary[n].toFixed(4)
                              : '—'}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>

                {/* Compute estimate card */}
                {estimate && (
                  <div
                    className="mt-4 px-3 py-2.5 rounded border text-xs"
                    style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-inset)' }}
                  >
                    <div className="flex items-center gap-1.5 mb-0.5">
                      <Clock size={10} style={{ color: 'var(--accent)' }} />
                      <span className="font-medium" style={{ color: 'var(--text-secondary)' }}>
                        Compute estimate
                      </span>
                    </div>
                    <div className="font-mono" style={{ color: 'var(--text-primary)' }}>
                      {estimate.estimated_label}
                    </div>
                    <div className="mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
                      {estimate.note}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
