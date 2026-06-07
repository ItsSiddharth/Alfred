/**
 * GitHistoryPanel — shows git commit log for an experiment folder with
 * per-commit rollback (hard-reset with confirmation).
 */

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GitCommit, RotateCcw, AlertTriangle } from 'lucide-react'
import { runnerApi, type GitLogEntry, type Project } from '../../api/client'

interface GitHistoryPanelProps {
  project: Project
}

export function GitHistoryPanel({ project }: GitHistoryPanelProps) {
  const queryClient = useQueryClient()
  const [confirmHash, setConfirmHash] = useState<string | null>(null)
  const [rollbackError, setRollbackError] = useState<string | null>(null)

  const { data: commits = [], isLoading } = useQuery({
    queryKey: ['gitLog', project.id],
    queryFn: () => runnerApi.gitLog(project.id),
    refetchInterval: 30_000,
    enabled: Boolean(project.experiment_folder),
  })

  const rollbackMutation = useMutation({
    mutationFn: (hash: string) => runnerApi.rollback(project.id, hash),
    onSuccess: () => {
      setConfirmHash(null)
      setRollbackError(null)
      queryClient.invalidateQueries({ queryKey: ['gitLog', project.id] })
    },
    onError: (err: Error) => {
      setRollbackError(err.message)
    },
  })

  if (!project.experiment_folder) return null

  return (
    <div className="mt-2">
      {/* Section header */}
      <div
        className="flex items-center gap-1.5 mb-1.5 text-xs font-mono"
        style={{ color: 'var(--text-tertiary)' }}
      >
        <GitCommit size={10} />
        <span>git history</span>
      </div>

      {isLoading && (
        <div className="text-xs font-mono px-1 py-1" style={{ color: 'var(--text-tertiary)' }}>
          Loading…
        </div>
      )}

      {!isLoading && commits.length === 0 && (
        <div className="text-xs font-mono px-1 py-1" style={{ color: 'var(--text-tertiary)' }}>
          No commits yet.
        </div>
      )}

      {rollbackError && (
        <div className="text-xs px-2 py-1 rounded mb-1" style={{ color: 'var(--danger)' }}>
          {rollbackError}
        </div>
      )}

      <div className="space-y-0.5">
        {commits.map((commit: GitLogEntry) =>
          confirmHash === commit.hash ? (
            <ConfirmRollback
              key={commit.hash}
              commit={commit}
              isPending={rollbackMutation.isPending}
              onConfirm={() => rollbackMutation.mutate(commit.hash)}
              onCancel={() => { setConfirmHash(null); setRollbackError(null) }}
            />
          ) : (
            <CommitRow
              key={commit.hash}
              commit={commit}
              onRollback={() => setConfirmHash(commit.hash)}
            />
          )
        )}
      </div>
    </div>
  )
}

// ── Single commit row ──────────────────────────────────────────────────────

function CommitRow({
  commit,
  onRollback,
}: {
  commit: GitLogEntry
  onRollback: () => void
}) {
  const dateStr = (() => {
    try { return new Date(commit.date).toLocaleDateString() }
    catch { return commit.date }
  })()

  return (
    <div
      className="group flex items-center gap-1.5 rounded px-1.5 py-0.5 text-xs"
      style={{ backgroundColor: 'transparent', transition: 'background-color 100ms' }}
      onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-elevated)')}
      onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
    >
      <span className="font-mono shrink-0" style={{ color: 'var(--accent)', minWidth: '44px' }}>
        {commit.short_hash}
      </span>
      <span
        className="flex-1 truncate"
        style={{ color: 'var(--text-secondary)' }}
        title={commit.message}
      >
        {commit.message}
      </span>
      <span className="shrink-0" style={{ color: 'var(--text-tertiary)' }}>
        {dateStr}
      </span>
      <button
        onClick={onRollback}
        title="Roll back to this commit"
        className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0 p-0.5 rounded"
        style={{ color: 'var(--text-tertiary)', border: '1px solid transparent' }}
        onMouseEnter={e => {
          e.currentTarget.style.color = 'var(--danger)'
          e.currentTarget.style.borderColor = 'rgba(239,68,68,0.4)'
        }}
        onMouseLeave={e => {
          e.currentTarget.style.color = 'var(--text-tertiary)'
          e.currentTarget.style.borderColor = 'transparent'
        }}
      >
        <RotateCcw size={10} />
      </button>
    </div>
  )
}

// ── Rollback confirmation inline ───────────────────────────────────────────

function ConfirmRollback({
  commit,
  isPending,
  onConfirm,
  onCancel,
}: {
  commit: GitLogEntry
  isPending: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div
      className="rounded border px-2 py-1.5 text-xs"
      style={{
        borderColor: 'rgba(239,68,68,0.4)',
        backgroundColor: 'rgba(239,68,68,0.06)',
      }}
    >
      <div className="flex items-center gap-1 mb-1" style={{ color: 'var(--danger)' }}>
        <AlertTriangle size={10} />
        <span className="font-mono font-medium">
          Roll back to {commit.short_hash}?
        </span>
      </div>
      <div className="mb-1.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
        Uncommitted changes will be lost.
      </div>
      <div className="flex gap-1.5">
        <button
          onClick={onConfirm}
          disabled={isPending}
          className="px-2 py-0.5 rounded text-xs font-medium disabled:opacity-40"
          style={{ backgroundColor: 'var(--danger)', color: 'white', border: 'none' }}
        >
          {isPending ? 'Rolling back…' : 'Confirm'}
        </button>
        <button
          onClick={onCancel}
          className="px-2 py-0.5 rounded text-xs"
          style={{ color: 'var(--text-tertiary)', border: '1px solid var(--border)' }}
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
