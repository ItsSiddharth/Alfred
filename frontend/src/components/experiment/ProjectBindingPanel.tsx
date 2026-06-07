/**
 * ProjectBindingPanel — Stage 7.1
 *
 * Shown in the Sidebar when the active project is in the "run" stage.
 * Lets the user bind a conda environment name and an experiment folder path,
 * then saves them via PATCH /api/projects/{id}/bind.
 */

import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Terminal, FolderOpen, Loader2, Pencil } from 'lucide-react'
import { runnerApi, type Project } from '../../api/client'
import { useStore } from '../../store'
import { Button } from '../common/Button'

let _bindOptimisticId = -9000

interface ProjectBindingPanelProps {
  project: Project
}

export function ProjectBindingPanel({ project }: ProjectBindingPanelProps) {
  const queryClient = useQueryClient()
  const appendPersistedMessage = useStore((s) => s.appendPersistedMessage)
  const [condaEnv, setCondaEnv] = useState(project.conda_env || '')
  const [expFolder, setExpFolder] = useState(project.experiment_folder || '')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editMode, setEditMode] = useState(false)

  // Sync inputs if the project changes (e.g. after switching projects)
  useEffect(() => {
    setCondaEnv(project.conda_env || '')
    setExpFolder(project.experiment_folder || '')
    setSaved(false)
    setError(null)
    setEditMode(false)
  }, [project.id])

  const isBound = Boolean(project.conda_env && project.experiment_folder)

  const mutation = useMutation({
    mutationFn: () => runnerApi.bind(project.id, condaEnv.trim(), expFolder.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      setSaved(true)
      setError(null)
      setEditMode(false)
      // Confirm binding in the chat thread
      appendPersistedMessage({
        id: _bindOptimisticId--,
        project_id: project.id,
        role: 'assistant',
        content: `**Run environment configured** — conda env \`${condaEnv.trim()}\`, folder \`${expFolder.trim()}\``,
        created_at: new Date().toISOString(),
        kind: 'result',
        metadata_json: '{}',
      })
      setTimeout(() => setSaved(false), 3000)
    },
    onError: (err: Error) => {
      setError(err.message)
      setSaved(false)
    },
  })

  const canSave = condaEnv.trim().length > 0 && expFolder.trim().length > 0
  const showForm = !isBound || editMode

  return (
    <div
      className="mx-2 mt-2 mb-1 rounded border text-sm"
      style={{
        backgroundColor: 'var(--bg-elevated)',
        borderColor: isBound ? 'rgba(52,211,153,0.35)' : 'var(--border)',
      }}
    >
      {/* Header — always visible */}
      <div className="flex items-center gap-2 px-3 py-2">
        {isBound ? (
          <CheckCircle2 size={13} style={{ color: 'var(--running)' }} />
        ) : (
          <Terminal size={13} style={{ color: 'var(--warn)' }} />
        )}
        <span
          className="font-medium text-xs tracking-wide uppercase flex-1"
          style={{ color: isBound ? 'var(--running)' : 'var(--warn)' }}
        >
          {isBound ? 'Bound' : 'Configure run'}
        </span>
        {isBound && !editMode && (
          <button
            onClick={() => setEditMode(true)}
            className="flex items-center gap-1 text-xs px-1.5 py-0.5 rounded"
            style={{ color: 'var(--text-tertiary)', border: '1px solid var(--border)' }}
            title="Edit binding"
          >
            <Pencil size={9} />
            Edit
          </button>
        )}
      </div>

      {/* Compact summary when bound and not editing */}
      {isBound && !editMode && (
        <div className="px-3 pb-2 text-xs font-mono space-y-0.5">
          <div className="flex items-center gap-1.5">
            <Terminal size={10} style={{ color: 'var(--text-tertiary)' }} />
            <span style={{ color: 'var(--text-secondary)' }}>{project.conda_env}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <FolderOpen size={10} style={{ color: 'var(--text-tertiary)' }} />
            <span className="truncate" style={{ color: 'var(--text-tertiary)' }} title={project.experiment_folder}>
              {project.experiment_folder}
            </span>
          </div>
        </div>
      )}

      {/* Full form when not bound or editing */}
      {showForm && (
        <div className="px-3 pb-3 space-y-2">
          {/* Conda env input */}
          <div>
            <label className="flex items-center gap-1.5 text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>
              <Terminal size={11} />
              Conda environment
            </label>
            <input
              type="text"
              value={condaEnv}
              onChange={(e) => { setCondaEnv(e.target.value); setSaved(false); setError(null) }}
              placeholder="e.g. my_ml_env"
              className="w-full px-2 py-1.5 rounded text-xs font-mono outline-none"
              style={{ backgroundColor: 'var(--bg-inset)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)' }}
            />
          </div>

          {/* Experiment folder input */}
          <div>
            <label className="flex items-center gap-1.5 text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>
              <FolderOpen size={11} />
              Experiment folder (absolute path)
            </label>
            <input
              type="text"
              value={expFolder}
              onChange={(e) => { setExpFolder(e.target.value); setSaved(false); setError(null) }}
              placeholder="e.g. /home/user/experiments/run1"
              className="w-full px-2 py-1.5 rounded text-xs font-mono outline-none"
              style={{ backgroundColor: 'var(--bg-inset)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)' }}
            />
            <div className="mt-1 text-xs" style={{ color: 'var(--text-tertiary)' }}>
              Created if it doesn't exist. Must be user-writable.
            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="px-2 py-1.5 rounded text-xs"
              style={{ backgroundColor: 'rgba(239,68,68,0.1)', color: 'var(--danger)', border: '1px solid rgba(239,68,68,0.2)' }}>
              {error}
            </div>
          )}

          {/* Buttons */}
          <div className="flex gap-2">
            <Button
              size="sm"
              onClick={() => mutation.mutate()}
              disabled={!canSave || mutation.isPending}
              style={{ flex: 1, justifyContent: 'center' }}
            >
              {mutation.isPending ? (
                <><Loader2 size={12} className="animate-spin mr-1.5" />Saving…</>
              ) : saved ? (
                <><CheckCircle2 size={12} className="mr-1.5" style={{ color: 'var(--running)' }} />Saved</>
              ) : (
                'Save binding'
              )}
            </Button>
            {editMode && (
              <Button size="sm" variant="ghost" onClick={() => { setEditMode(false); setError(null) }}>
                Cancel
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
