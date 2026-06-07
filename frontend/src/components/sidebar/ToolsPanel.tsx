/**
 * ToolsPanel — Stage 4 sidebar panel.
 * Shows registered tools with enable/disable toggles, test runner, recent call log.
 */

import { useEffect, useState } from 'react'
import { Wrench, RefreshCw, ChevronDown, ChevronRight, Play, ToggleLeft, ToggleRight, Loader2, X } from 'lucide-react'
import { useStore } from '../../store'

// ---------------------------------------------------------------------------
// API helpers (inline — no separate client file needed)
// ---------------------------------------------------------------------------

const API = 'http://localhost:8000'

async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`)
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

interface ToolInfo {
  name: string
  description: string
  enabled: boolean
  has_schema: boolean
  parameters?: Record<string, unknown>
}

interface ToolCallRecord {
  id: number
  tool_name: string
  input_json: string
  output_summary: string
  created_at: string
}

// ---------------------------------------------------------------------------
// Tool row
// ---------------------------------------------------------------------------

function ToolRow({ tool, onToggle }: { tool: ToolInfo; onToggle: (name: string, v: boolean) => void }) {
  const [expanded, setExpanded] = useState(false)
  const [testInput, setTestInput] = useState('{}')
  const [testResult, setTestResult] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)

  const handleTest = async () => {
    setTesting(true); setTestResult(null)
    try {
      let input: Record<string, unknown> = {}
      try { input = JSON.parse(testInput) } catch {}
      const res = await apiPost<unknown>(`/api/tools/test/${tool.name}`, { input })
      setTestResult(JSON.stringify(res, null, 2))
    } catch (e) {
      setTestResult(`Error: ${e}`)
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="border rounded mb-1.5 overflow-hidden" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-elevated)' }}>
      <div className="flex items-center gap-2 px-3 py-2">
        <button onClick={() => setExpanded(v => !v)} style={{ color: 'var(--text-tertiary)' }}>
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
        <span className="flex-1 text-xs font-mono" style={{ color: 'var(--text-primary)' }}>{tool.name}</span>
        <button onClick={() => onToggle(tool.name, !tool.enabled)} title={tool.enabled ? 'Disable' : 'Enable'}>
          {tool.enabled
            ? <ToggleRight size={18} style={{ color: 'var(--running)' }} />
            : <ToggleLeft size={18} style={{ color: 'var(--text-tertiary)' }} />}
        </button>
      </div>

      {expanded && (
        <div className="px-3 pb-3 border-t" style={{ borderColor: 'var(--border)' }}>
          <p className="text-xs mt-2 mb-2 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{tool.description}</p>
          <div className="text-xs font-mono mb-1" style={{ color: 'var(--text-tertiary)' }}>test input (JSON)</div>
          <textarea
            value={testInput}
            onChange={e => setTestInput(e.target.value)}
            rows={3}
            className="w-full rounded p-1.5 text-xs font-mono resize-none outline-none"
            style={{ backgroundColor: 'var(--bg-inset)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
          />
          <button
            onClick={handleTest}
            disabled={testing}
            className="mt-1 flex items-center gap-1 px-2 py-1 rounded text-xs font-mono disabled:opacity-50"
            style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)' }}
          >
            {testing ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}
            run
          </button>
          {testResult && (
            <pre className="mt-2 p-2 rounded text-xs font-mono max-h-40 overflow-y-auto whitespace-pre-wrap"
              style={{ backgroundColor: 'var(--bg-inset)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
              {testResult}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Live WS tool calls (from store)
// ---------------------------------------------------------------------------

function LiveCalls() {
  const { toolCalls } = useStore()
  if (toolCalls.length === 0) return null
  return (
    <div className="mb-3">
      <div className="text-xs font-mono uppercase tracking-wider mb-1.5" style={{ color: 'var(--text-tertiary)' }}>live activity</div>
      <div className="space-y-1 max-h-36 overflow-y-auto">
        {toolCalls.slice(0, 20).map((tc, i) => (
          <div key={i} className="px-2 py-1.5 rounded border" style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)' }}>
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono" style={{ color: tc.status === 'done' ? 'var(--running)' : tc.status === 'error' ? 'var(--danger)' : 'var(--warn)' }}>●</span>
              <span className="text-xs font-mono" style={{ color: 'var(--text-primary)' }}>{tc.tool_name}</span>
              {tc.result_count != null && <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>{tc.result_count} results</span>}
            </div>
            {tc.sources?.slice(0, 3).map((s, j) => (
              <div key={j} className="text-xs truncate mt-0.5 font-mono" style={{ color: 'var(--accent)' }}>→ {s}</div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Recent DB calls
// ---------------------------------------------------------------------------

function RecentCalls({ projectId }: { projectId: number }) {
  const [calls, setCalls] = useState<ToolCallRecord[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setCalls(await apiGet(`/api/tools/calls/${projectId}`)) } catch {}
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [projectId])

  return (
    <div className="mt-3 pt-3 border-t" style={{ borderColor: 'var(--border)' }}>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-mono uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>recent calls</span>
        <button onClick={load} disabled={loading} style={{ color: 'var(--text-tertiary)' }}>
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      {calls.length === 0
        ? <p className="text-xs italic" style={{ color: 'var(--text-tertiary)' }}>No tool calls yet.</p>
        : (
          <div className="space-y-1 max-h-40 overflow-y-auto">
            {calls.map(c => (
              <div key={c.id} className="px-2 py-1.5 rounded border" style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)' }}>
                <div className="flex justify-between">
                  <span className="text-xs font-mono" style={{ color: 'var(--accent)' }}>{c.tool_name}</span>
                  <span className="text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>
                    {new Date(c.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                </div>
                <div className="text-xs truncate mt-0.5" style={{ color: 'var(--text-secondary)' }}>{c.output_summary}</div>
              </div>
            ))}
          </div>
        )
      }
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export function ToolsPanel() {
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [loading, setLoading] = useState(true)
  const { activeProjectId, setSidebarPanel } = useStore()

  const loadTools = async () => {
    setLoading(true)
    try { setTools(await apiGet('/api/tools/')) } catch {}
    finally { setLoading(false) }
  }
  useEffect(() => { loadTools() }, [])

  const handleToggle = async (name: string, enabled: boolean) => {
    try {
      await apiPost(`/api/tools/${name}/${enabled ? 'enable' : 'disable'}`)
      setTools(prev => prev.map(t => t.name === name ? { ...t, enabled } : t))
    } catch {}
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b shrink-0" style={{ borderColor: 'var(--border)' }}>
        <Wrench size={14} style={{ color: 'var(--accent)' }} />
        <span className="text-sm font-medium flex-1" style={{ color: 'var(--text-primary)' }}>Tools</span>
        <button onClick={loadTools} className="p-1 rounded" style={{ color: 'var(--text-tertiary)' }} title="Refresh">
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
        </button>
        <button onClick={() => setSidebarPanel(null)} className="p-1 rounded" style={{ color: 'var(--text-tertiary)' }} title="Close">
          <X size={13} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3">
        <LiveCalls />

        {loading
          ? <div className="flex justify-center py-8"><Loader2 size={16} className="animate-spin" style={{ color: 'var(--text-tertiary)' }} /></div>
          : tools.length === 0
            ? <p className="text-xs italic" style={{ color: 'var(--text-tertiary)' }}>No tools registered.</p>
            : tools.map(t => <ToolRow key={t.name} tool={t} onToggle={handleToggle} />)
        }

        {activeProjectId != null && <RecentCalls projectId={activeProjectId} />}
      </div>
    </div>
  )
}