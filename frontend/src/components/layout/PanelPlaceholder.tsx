/**
 * Placeholder panel shown in the sidebar for Memory / Tools / Find Models
 * until those stages are built. Communicates clearly what's coming.
 */

import React from 'react'
import { BrainCircuit, Wrench, Cpu } from 'lucide-react'
import { type SidebarPanel } from '../../store'

const PANEL_INFO: Record<
  NonNullable<SidebarPanel>,
  { icon: React.ReactNode; title: string; description: string; stage: string }
> = {
  memory: {
    icon: <BrainCircuit size={20} />,
    title: 'Memory',
    description:
      'ALFRED will remember facts, preferences, mistakes, and dataset references across sessions. The compiled memory block is injected into every agent context.',
    stage: 'Stage 3',
  },
  tools: {
    icon: <Wrench size={20} />,
    title: 'Tools',
    description:
      'Pluggable tool bus: web search, arXiv, Semantic Scholar, and OpenAlex. New tools can be added via tools.yaml with no code changes.',
    stage: 'Stage 4',
  },
  'find-models': {
    icon: <Cpu size={20} />,
    title: 'Find models',
    description:
      'Detect your GPU VRAM, browse research-friendly Ollama models, see VRAM fit estimates, and pull models with live progress.',
    stage: 'Stage 1',
  },
}

interface PanelPlaceholderProps {
  panel: NonNullable<SidebarPanel>
}

export function PanelPlaceholder({ panel }: PanelPlaceholderProps) {
  const info = PANEL_INFO[panel]

  return (
    <div
      className="flex flex-col gap-4 p-4"
      style={{ color: 'var(--text-secondary)' }}
    >
      <div
        className="flex items-center gap-2 pb-3 border-b"
        style={{ borderColor: 'var(--border)', color: 'var(--accent)' }}
      >
        {info.icon}
        <span className="font-medium text-sm">{info.title}</span>
      </div>

      <p className="text-sm leading-relaxed">{info.description}</p>

      <div
        className="flex items-center gap-2 px-3 py-2 rounded border text-sm font-mono"
        style={{
          backgroundColor: 'var(--bg-inset)',
          borderColor: 'var(--border)',
          color: 'var(--text-tertiary)',
        }}
      >
        <span style={{ color: 'var(--warn)' }}>⏳</span>
        Implemented in {info.stage}
      </div>
    </div>
  )
}