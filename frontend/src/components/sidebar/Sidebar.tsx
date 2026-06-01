/**
 * Fixed left sidebar — ~280px wide per C5.
 *
 * Top section: Memory | Tools | Find models (placeholder panels for Stage 0).
 * Bottom section: project list + New project button.
 */

import React, { useState } from 'react'
import { BrainCircuit, Wrench, Cpu, Plus, FolderOpen, ChevronRight } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi, type Project } from '../../api/client'
import { useStore, type SidebarPanel } from '../../store'
import { Button } from '../common/Button'

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

// ── Project list item ─────────────────────────────────────────────────────

interface ProjectItemProps {
  project: Project
  isActive: boolean
  onSelect: (id: number) => void
}

function ProjectItem({ project, isActive, onSelect }: ProjectItemProps) {
  const stageColors: Record<string, string> = {
    hypothesis: 'var(--info)',
    setup: 'var(--warn)',
    run: 'var(--running)',
  }

  return (
    <button
      onClick={() => onSelect(project.id)}
      className="w-full flex items-start gap-2 px-3 py-2 rounded text-left transition-colors duration-100"
      style={{
        backgroundColor: isActive ? 'var(--bg-elevated)' : 'transparent',
        border: isActive ? '1px solid var(--border)' : '1px solid transparent',
      }}
    >
      <FolderOpen
        size={14}
        className="mt-0.5 shrink-0"
        style={{ color: isActive ? 'var(--accent)' : 'var(--text-tertiary)' }}
      />
      <div className="flex-1 min-w-0">
        <div
          className="text-sm truncate"
          style={{ color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)' }}
        >
          {project.name}
        </div>
        <div className="flex items-center gap-1.5 mt-0.5">
          <span
            className="w-1.5 h-1.5 rounded-full shrink-0"
            style={{ backgroundColor: stageColors[project.current_stage] ?? 'var(--text-tertiary)' }}
          />
          <span className="text-sm truncate" style={{ color: 'var(--text-tertiary)' }}>
            {project.current_stage}
          </span>
        </div>
      </div>
    </button>
  )
}

// ── New project modal (minimal inline form) ────────────────────────────────

interface NewProjectFormProps {
  onDone: () => void
}

function NewProjectForm({ onDone }: NewProjectFormProps) {
  const [name, setName] = useState('')
  const queryClient = useQueryClient()
  const { setActiveProjectId } = useStore()

  const mutation = useMutation({
    mutationFn: () => projectsApi.create({ name: name.trim() }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      setActiveProjectId(project.id)
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
  const { activeProjectId, setActiveProjectId } = useStore()
  const [showNewForm, setShowNewForm] = useState(false)

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })

  return (
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

      {/* Top nav — Memory / Tools / Find models */}
      <nav className="flex flex-col gap-0.5 px-2 py-3">
        <NavItem icon={<BrainCircuit size={15} />} label="Memory" panel="memory" />
        <NavItem icon={<Wrench size={15} />} label="Tools" panel="tools" />
        <NavItem icon={<Cpu size={15} />} label="Find models" panel="find-models" />
      </nav>

      <div className="mx-3 border-t" style={{ borderColor: 'var(--border)' }} />

      {/* Project history — fills remaining space */}
      <div className="flex flex-col flex-1 min-h-0 pt-3">
        <div
          className="flex items-center justify-between px-3 mb-2"
        >
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

        {showNewForm && <NewProjectForm onDone={() => setShowNewForm(false)} />}

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
              onSelect={setActiveProjectId}
            />
          ))}
        </div>
      </div>
    </aside>
  )
}