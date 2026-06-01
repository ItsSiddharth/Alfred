/**
 * SetupPage — shown on first run when backend returns status=needs_setup.
 *
 * Asks the user to choose a workspace directory. On submit calls
 * POST /api/config/setup and transitions to the main shell.
 */

import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { configApi } from '../api/client'
import { useStore } from '../store'
import { FolderOpen, AlertCircle } from 'lucide-react'
import { Button } from '../components/common/Button'

export function SetupPage() {
  const [workspace, setWorkspace] = useState('~/alfred-workspace')
  const { setConfigStatus } = useStore()

  const mutation = useMutation({
    mutationFn: () => configApi.setup(workspace.trim()),
    onSuccess: () => setConfigStatus('configured'),
  })

  return (
    <div
      className="flex items-center justify-center h-full"
      style={{ backgroundColor: 'var(--bg-base)' }}
    >
      <div
        className="w-full max-w-md rounded border p-8"
        style={{
          backgroundColor: 'var(--bg-surface)',
          borderColor: 'var(--border)',
        }}
      >
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <span
            className="text-xl font-medium font-mono tracking-widest"
            style={{ color: 'var(--accent)' }}
          >
            ALFRED
          </span>
          <span className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
            first-run setup
          </span>
        </div>

        <h1 className="font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
          Choose your workspace
        </h1>
        <p className="text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>
          ALFRED stores all projects, datasets, logs, and the database inside this
          directory. It must be a path you have write access to — no admin required.
        </p>

        {/* Input */}
        <label className="block mb-1 text-sm" style={{ color: 'var(--text-secondary)' }}>
          Workspace path
        </label>
        <div className="flex gap-2 mb-4">
          <div
            className="flex items-center gap-2 flex-1 px-3 py-2 rounded border"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              borderColor: mutation.isError ? 'var(--danger)' : 'var(--border-strong)',
            }}
          >
            <FolderOpen size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
            <input
              type="text"
              value={workspace}
              onChange={(e) => setWorkspace(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && mutation.mutate()}
              className="flex-1 bg-transparent outline-none text-sm font-mono"
              style={{ color: 'var(--text-primary)', caretColor: 'var(--accent)' }}
              spellCheck={false}
            />
          </div>
        </div>

        {/* Error message */}
        {mutation.isError && (
          <div
            className="flex items-start gap-2 px-3 py-2 rounded border mb-4 text-sm"
            style={{
              backgroundColor: '#ef44441a',
              borderColor: 'var(--danger)',
              color: 'var(--danger)',
            }}
          >
            <AlertCircle size={14} className="mt-0.5 shrink-0" />
            <span>
              {mutation.error instanceof Error
                ? mutation.error.message
                : 'Setup failed. Check the path and try again.'}
            </span>
          </div>
        )}

        <Button
          onClick={() => mutation.mutate()}
          disabled={!workspace.trim() || mutation.isPending}
          className="w-full justify-center"
        >
          {mutation.isPending ? 'Setting up…' : 'Set up workspace'}
        </Button>

        {/* What gets created */}
        <div
          className="mt-6 p-3 rounded border font-mono text-sm"
          style={{
            backgroundColor: 'var(--bg-inset)',
            borderColor: 'var(--border)',
            color: 'var(--text-tertiary)',
          }}
        >
          <div className="mb-1" style={{ color: 'var(--text-secondary)' }}>
            Will create:
          </div>
          <div>{workspace || '~/alfred-workspace'}/</div>
          <div className="ml-3">├── logs/</div>
          <div className="ml-3">├── projects/</div>
          <div className="ml-3">├── datasets/</div>
          <div className="ml-3">└── db.sqlite</div>
        </div>
      </div>
    </div>
  )
}