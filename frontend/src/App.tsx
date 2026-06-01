/**
 * App — root component.
 *
 * 1. On mount, fetches GET /api/config/status.
 * 2. If needs_setup → renders SetupPage.
 * 3. If configured → renders the main two-column shell and connects WS.
 */

import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { configApi } from './api/client'
import { useStore } from './store'
import { useWebSocket } from './api/useWebSocket'
import { SetupPage } from './pages/SetupPage'
import { Sidebar } from './components/sidebar/Sidebar'
import { MainPanel } from './components/layout/MainPanel'
import { Loader2 } from 'lucide-react'

// ── WS connector — lives inside the configured shell ─────────────────────

function WSConnector() {
  const activeProjectId = useStore((s) => s.activeProjectId)
  useWebSocket(activeProjectId)
  return null
}

// ── Loading screen ────────────────────────────────────────────────────────

function LoadingScreen() {
  return (
    <div
      className="flex items-center justify-center h-full gap-3"
      style={{ backgroundColor: 'var(--bg-base)', color: 'var(--text-tertiary)' }}
    >
      <Loader2 size={18} className="animate-spin" style={{ color: 'var(--accent)' }} />
      <span className="text-sm font-mono">Connecting to ALFRED…</span>
    </div>
  )
}

// ── Main shell ────────────────────────────────────────────────────────────

function Shell() {
  return (
    <div className="flex h-full" style={{ backgroundColor: 'var(--bg-base)' }}>
      <WSConnector />
      <Sidebar />
      <MainPanel />
    </div>
  )
}

// ── App ───────────────────────────────────────────────────────────────────

export default function App() {
  const { configStatus, setConfigStatus } = useStore()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['config-status'],
    queryFn: configApi.getStatus,
    // Poll until configured so the setup page auto-transitions after submit.
    refetchInterval: configStatus === 'needs_setup' ? 1500 : false,
  })

  useEffect(() => {
    if (data) {
      setConfigStatus(data.status)
    }
  }, [data, setConfigStatus])

  if (isLoading || configStatus === 'unknown') return <LoadingScreen />

  if (isError) {
    return (
      <div
        className="flex items-center justify-center h-full"
        style={{ backgroundColor: 'var(--bg-base)', color: 'var(--danger)' }}
      >
        <div className="text-center">
          <div className="font-medium mb-1">Cannot reach ALFRED backend</div>
          <div className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
            Make sure{' '}
            <code className="font-mono" style={{ color: 'var(--accent)' }}>
              python scripts/dev.py
            </code>{' '}
            is running.
          </div>
        </div>
      </div>
    )
  }

  if (configStatus === 'needs_setup') return <SetupPage />

  return <Shell />
}