/**
 * Sidebar — Stage 2 edition.
 *
 * New in Stage 2:
 *  - On project select: fetches message history from DB and populates store
 *  - Auto-approve toggle per project (calls POST /api/projects/{id}/auto_approve)
 *  - Project current_stage colour-coded dot
 *  - Panel drawer includes FindModelsPanel (real), Memory/Tools (stubs)
 */

import React, { useState } from 'react'
import {
  BrainCircuit,
  Wrench,
  Cpu,
  Plus,
  FolderOpen,
  ChevronRight,
  X,
  Zap,
  ZapOff,
} from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi, messagesApi, type Project } from '../../api/client'
import { useStore, type SidebarPanel } from '../../store'
import { Button } from '../common/Button'
import { FindModelsPanel } from './FindModelsPanel'

// ── Panel nav item ────────────────────────────────────────────────────────

interface NavItemProps {
  icon: React.ReactNode
  label: string
  panel: SidebarPanel
}

function NavItem({ icon, label, panel }: NavItemProps) {
  const { sidebarPanel, setSidebarPanel } = useStore()
  const active = sidebarPanel === panel

  return (
    <button
      onClick={() => setSidebarPanel(panel)}
      className="w-full flex items-center gap-2.5 px-3 py-2 rounded text-sm transition-colors duration-100"
      style={{
        backgroundColor: active ? 'var(--bg-elevated)' : 'transparent',
        color: active ? 'var(--accent)' : 'var(--text-secondary)',
        border: active ? '1px solid var(--border)' : '1px solid transparent',
      }}
      title={label}
    >
      {icon}
      <span className="truncate">{label}</span>
      {active && <ChevronRight size={12} className="ml-auto shrink-0" />}
    </button>
  )
}

// ── Panel drawer ──────────────────────────────────────────────────────────

function PanelDrawer({ panel }: { panel: SidebarPanel }) {
  const { setSidebarPanel } = useStore()
  if (!panel) return null

  const titles: Record<NonNullable<SidebarPanel>, string> = {
    memory: 'Memory',
    tools: 'Tools',
    'find-models': 'Find models',
  }

  const renderContent = () => {
    if (panel === 'find-models') return <FindModelsPanel />

    const descriptions: Record<string, string> = {
      memory:
        'Memory engine — built in Stage 3. Will show remembered facts, preferences, and dataset references.',
      tools:
        'Tool bus — built in Stage 4. Will show registered tools, enable/disable toggles, and recent call logs.',
    }

    return (
      <div className="flex flex-col h-full">
        <div
          className="flex items-center justify-between px-4 py-3 border-b shrink-0"
          style={{ borderColor: 'var(--border)' }}
        >
          <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
            {titles[panel]}
          </span>
          <button
            onClick={() => setSidebarPanel(null)}
            className="p-1 rounded transition-colors"
            style={{ color: 'var(--text-tertiary)' }}
          >
            <X size={14} />
          </button>
        </div>
        <div className="flex-1 flex items-center justify-center p-6 text-center">
          <div>
            <div className="text-sm font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>
              {titles[panel]}
            </div>
            <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
              {descriptions[panel]}
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
      className="flex flex-col h-full shrink-0"
      style={{
        width: '300px',
        backgroundColor: 'var(--bg-surface)',
        borderRight: '1px solid var(--border)',
      }}
    >
      {renderContent()}
    </div>
  )
}

// ── Auto-approve toggle ───────────────────────────────────────────────────

interface AutoApproveToggleProps {
  project: Project
}

function AutoApproveToggle({ project }: AutoApproveToggleProps) {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (value: boolean) =>
      projectsApi.setAutoApprove(project.id, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  return (
    <button
      onClick={() => mutation.mutate(!project.auto_approve)}
      disabled={mutation.isPending}
      className="flex items-center gap-1.5 text-xs font-mono px-2 py-1 rounded border transition-colors disabled:opacity-40"
      title={
        project.auto_approve
          ? 'Auto-approve on — click to require manual approval'
          : 'Auto-approve off — click to auto-approve plans'
      }
      style={{
        color: project.auto_approve ? 'var(--warn)' : 'var(--text-tertiary)',
        borderColor: project.auto_approve
          ? 'rgba(245,158,11,0.4)'
          : 'var(--border)',
        backgroundColor: project.auto_approve
          ? 'rgba(245,158,11,0.07)'
          : 'transparent',
      }}
    >
      {project.auto_approve ? <Zap size={10} /> : <ZapOff size={10} />}
      {project.auto_approve ? 'Auto' : 'Manual'}
    </button>
  )
}

// ── Project list item ─────────────────────────────────────────────────────

interface ProjectItemProps {
  project: Project
  isActive: boolean
  onSelect: (project: Project) => void
}

function ProjectItem({ project, isActive, onSelect }: ProjectItemProps) {
  const stageColors: Record<string, string> = {
    hypothesis: 'var(--info)',
    setup: 'var(--warn)',
    run: 'var(--running)',
  }

  return (
    <div
      className="rounded transition-colors duration-100"
      style={{
        backgroundColor: isActive ? 'var(--bg-elevated)' : 'transparent',
        border: isActive ? '1px solid var(--border)' : '1px solid transparent',
      }}
    >
      <button
        onClick={() => onSelect(project)}
        className="w-full flex items-start gap-2 px-3 py-2 text-left"
      >
        <FolderOpen
          size={14}
          className="mt-0.5 shrink-0"
          style={{ color: isActive ? 'var(--accent)' : 'var(--text-tertiary)' }}
        />
        <div className="flex-1 min-w-0">
          <div
            className="text-sm truncate"
            style={{
              color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
            }}
          >
            {project.name}
          </div>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span
              className="w-1.5 h-1.5 rounded-full shrink-0"
              style={{
                backgroundColor:
                  stageColors[project.current_stage] ?? 'var(--text-tertiary)',
              }}
            />
            <span className="text-xs truncate" style={{ color: 'var(--text-tertiary)' }}>
              {project.current_stage}
            </span>
          </div>
        </div>
      </button>

      {/* Auto-approve toggle — only visible for active project */}
      {isActive && (
        <div className="flex justify-end px-3 pb-2">
          <AutoApproveToggle project={project} />
        </div>
      )}
    </div>
  )
}

// ── New project form ───────────────────────────────────────────────────────

interface NewProjectFormProps {
  onDone: () => void
  onCreated: (project: Project) => void
}

function NewProjectForm({ onDone, onCreated }: NewProjectFormProps) {
  const [name, setName] = useState('')
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: () => projectsApi.create({ name: name.trim() }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      onCreated(project)
      onDone()
    },
  })

  return (
    <div
      className="mx-2 mb-2 p-3 rounded border"
      style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)' }}
    >
      <div className="text-sm font-medium mb-2" style={{ color: 'var(--text-primary)' }}>
        New project
      </div>
      <input
        autoFocus
        type="text"
        placeholder="Project name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && name.trim()) mutation.mutate()
          if (e.key === 'Escape') onDone()
        }}
        className="w-full px-2.5 py-1.5 rounded text-sm font-sans outline-none"
        style={{
          backgroundColor: 'var(--bg-inset)',
          color: 'var(--text-primary)',
          border: '1px solid var(--border-strong)',
        }}
      />
      <div className="flex gap-2 mt-2">
        <Button
          size="sm"
          onClick={() => name.trim() && mutation.mutate()}
          disabled={!name.trim() || mutation.isPending}
        >
          {mutation.isPending ? 'Creating…' : 'Create'}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  )
}

// ── Sidebar ────────────────────────────────────────────────────────────────

export function Sidebar() {
  const { activeProjectId, setActiveProjectId, setPersistedMessages, sidebarPanel } =
    useStore()
  const [showNewForm, setShowNewForm] = useState(false)

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })

  // When a project is selected: set active + load message history
  const handleSelectProject = async (project: Project) => {
    setActiveProjectId(project.id)
    try {
      const msgs = await messagesApi.list(project.id, 200)
      setPersistedMessages(msgs)
    } catch (err) {
      console.warn('[Sidebar] Failed to load messages:', err)
      setPersistedMessages([])
    }
  }

  const handleCreated = (project: Project) => {
    handleSelectProject(project)
  }

  return (
    <>
      {/* Fixed sidebar rail */}
      <aside
        className="flex flex-col h-full shrink-0"
        style={{
          width: '280px',
          backgroundColor: 'var(--bg-surface)',
          borderRight: '1px solid var(--border)',
        }}
      >
        {/* Logo / brand */}
        <div
          className="flex items-center gap-2.5 px-4 py-3 border-b"
          style={{ borderColor: 'var(--border)' }}
        >
          <span
            className="text-lg font-medium font-mono tracking-widest"
            style={{ color: 'var(--accent)' }}
          >
            ALFRED
          </span>
          <span className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
            research agent
          </span>
        </div>

        {/* Top nav */}
        <nav className="flex flex-col gap-0.5 px-2 py-3">
          <NavItem icon={<BrainCircuit size={15} />} label="Memory" panel="memory" />
          <NavItem icon={<Wrench size={15} />} label="Tools" panel="tools" />
          <NavItem icon={<Cpu size={15} />} label="Find models" panel="find-models" />
        </nav>

        <div className="mx-3 border-t" style={{ borderColor: 'var(--border)' }} />

        {/* Project history */}
        <div className="flex flex-col flex-1 min-h-0 pt-3">
          <div className="flex items-center justify-between px-3 mb-2">
            <span className="text-sm font-medium" style={{ color: 'var(--text-tertiary)' }}>
              Projects
            </span>
            <button
              onClick={() => setShowNewForm(true)}
              className="flex items-center gap-1 text-sm px-1.5 py-0.5 rounded transition-colors"
              style={{ color: 'var(--accent)' }}
              title="New project"
            >
              <Plus size={13} />
              New
            </button>
          </div>

          {showNewForm && (
            <NewProjectForm
              onDone={() => setShowNewForm(false)}
              onCreated={handleCreated}
            />
          )}

          <div className="flex-1 overflow-y-auto px-2 pb-3 flex flex-col gap-0.5">
            {isLoading && (
              <div className="px-3 py-2 text-sm" style={{ color: 'var(--text-tertiary)' }}>
                Loading…
              </div>
            )}
            {!isLoading && projects.length === 0 && !showNewForm && (
              <div className="px-3 py-2 text-sm" style={{ color: 'var(--text-tertiary)' }}>
                No projects yet. Click New to start.
              </div>
            )}
            {projects.map((p) => (
              <ProjectItem
                key={p.id}
                project={p}
                isActive={p.id === activeProjectId}
                onSelect={handleSelectProject}
              />
            ))}
          </div>
        </div>
      </aside>

      {/* Panel drawer */}
      <PanelDrawer panel={sidebarPanel} />
    </>
  )
}