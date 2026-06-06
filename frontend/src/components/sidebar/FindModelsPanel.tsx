/**
 * FindModelsPanel — sidebar panel for discovering and managing Ollama models.
 *
 * Sections:
 *  1. Hardware banner — detected GPU/VRAM/RAM
 *  2. Ollama status — running or install guidance
 *  3. Installed models — with select + delete
 *  4. Recommended catalog — ranked by VRAM fit with pull buttons
 */

import { useState } from 'react'
import {
  useQuery,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query'
import {
  Cpu,
  Download,
  Trash2,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  ChevronDown,
  ChevronRight,
  Loader2,
  Zap,
  HardDrive,
} from 'lucide-react'
import { modelsApi, type CatalogModel, type LocalModel, type VramFit } from '../../api/client'
import { useStore } from '../../store'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtBytes(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`
  return `${bytes} B`
}

function fmtVram(gb: number): string {
  return gb === 0 ? '— GB' : `${gb.toFixed(1)} GB`
}

// ---------------------------------------------------------------------------
// VRAM fit badge
// ---------------------------------------------------------------------------

interface FitBadgeProps {
  fit: VramFit
  requiredGb: number
}

function FitBadge({ fit, requiredGb }: FitBadgeProps) {
  const configs: Record<VramFit, { color: string; bg: string; icon: React.ReactNode; label: string }> = {
    fits: {
      color: 'var(--running)',
      bg: 'rgba(52,211,153,0.10)',
      icon: <CheckCircle2 size={10} />,
      label: 'fits',
    },
    tight: {
      color: 'var(--warn)',
      bg: 'rgba(245,158,11,0.10)',
      icon: <AlertTriangle size={10} />,
      label: 'tight',
    },
    too_large: {
      color: 'var(--danger)',
      bg: 'rgba(239,68,68,0.10)',
      icon: <XCircle size={10} />,
      label: 'too large',
    },
  }
  const cfg = configs[fit]
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-mono"
      style={{ color: cfg.color, backgroundColor: cfg.bg }}
    >
      {cfg.icon}
      {fmtVram(requiredGb)} · {cfg.label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Hardware banner
// ---------------------------------------------------------------------------

function HardwareBanner() {
  const { data: hw, isLoading } = useQuery({
    queryKey: ['hardware'],
    queryFn: modelsApi.getHardware,
    staleTime: 60_000,
  })

  if (isLoading) {
    return (
      <div
        className="mx-3 mb-3 p-3 rounded border"
        style={{ backgroundColor: 'var(--bg-inset)', borderColor: 'var(--border)' }}
      >
        <div className="flex items-center gap-2">
          <Loader2 size={13} className="animate-spin" style={{ color: 'var(--accent)' }} />
          <span className="text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>
            Detecting hardware…
          </span>
        </div>
      </div>
    )
  }

  if (!hw) return null

  const backendColors: Record<string, string> = {
    cuda: 'var(--running)',
    metal: 'var(--accent)',
    cpu: 'var(--text-tertiary)',
  }

  return (
    <div
      className="mx-3 mb-3 p-3 rounded border"
      style={{ backgroundColor: 'var(--bg-inset)', borderColor: 'var(--border)' }}
    >
      <div className="flex items-center gap-2 mb-2">
        <Cpu size={13} style={{ color: backendColors[hw.backend] }} />
        <span
          className="text-xs font-mono font-medium"
          style={{ color: backendColors[hw.backend] }}
        >
          {hw.backend.toUpperCase()}
        </span>
        <span className="text-xs font-mono truncate" style={{ color: 'var(--text-secondary)' }}>
          {hw.gpu_name}
        </span>
      </div>

      <div className="flex gap-4">
        {hw.backend !== 'cpu' && (
          <div>
            <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
              VRAM
            </div>
            <div className="text-xs font-mono font-medium" style={{ color: 'var(--text-primary)' }}>
              {fmtVram(hw.total_vram_gb)}
              <span className="font-normal ml-1" style={{ color: 'var(--text-tertiary)' }}>
                ({fmtVram(hw.free_vram_gb)} free)
              </span>
            </div>
          </div>
        )}
        <div>
          <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
            RAM
          </div>
          <div className="text-xs font-mono font-medium" style={{ color: 'var(--text-primary)' }}>
            {hw.total_ram_gb.toFixed(0)} GB
          </div>
        </div>
        <div>
          <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
            CPU cores
          </div>
          <div className="text-xs font-mono font-medium" style={{ color: 'var(--text-primary)' }}>
            {hw.cpu_count}
          </div>
        </div>
      </div>

      {hw.backend === 'cpu' && (
        <div className="mt-2 text-xs" style={{ color: 'var(--warn)' }}>
          No GPU detected — models will run on CPU (slower).
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Ollama status banner
// ---------------------------------------------------------------------------

function OllamaStatus({ available, guidance }: { available: boolean; guidance?: string }) {
  if (available) {
    return (
      <div
        className="mx-3 mb-2 flex items-center gap-2 px-3 py-2 rounded border text-xs font-mono"
        style={{
          backgroundColor: 'rgba(52,211,153,0.07)',
          borderColor: 'rgba(52,211,153,0.25)',
          color: 'var(--running)',
        }}
      >
        <CheckCircle2 size={11} />
        Ollama running
      </div>
    )
  }
  return (
    <div
      className="mx-3 mb-2 p-3 rounded border text-xs"
      style={{
        backgroundColor: 'rgba(239,68,68,0.07)',
        borderColor: 'rgba(239,68,68,0.25)',
        color: 'var(--danger)',
      }}
    >
      <div className="flex items-center gap-1.5 font-medium mb-1">
        <XCircle size={11} />
        Ollama not running
      </div>
      <div style={{ color: 'var(--text-secondary)' }}>{guidance}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Installed model row
// ---------------------------------------------------------------------------

interface InstalledModelRowProps {
  model: LocalModel
  isSelected: boolean
  onSelect: () => void
  onDelete: () => void
  isDeleting: boolean
}

function InstalledModelRow({
  model,
  isSelected,
  onSelect,
  onDelete,
  isDeleting,
}: InstalledModelRowProps) {
  return (
    <div
      className="flex items-center gap-2 px-3 py-2 rounded transition-colors"
      style={{
        backgroundColor: isSelected ? 'var(--bg-elevated)' : 'transparent',
        border: isSelected ? '1px solid var(--border)' : '1px solid transparent',
      }}
    >
      <button
        onClick={onSelect}
        className="flex-1 flex items-center gap-2 text-left min-w-0"
        title="Select this model"
      >
        {isSelected && (
          <CheckCircle2 size={12} style={{ color: 'var(--running)', flexShrink: 0 }} />
        )}
        <div className="min-w-0">
          <div
            className="text-xs font-mono truncate"
            style={{ color: isSelected ? 'var(--accent)' : 'var(--text-primary)' }}
          >
            {model.name}
          </div>
          <div className="text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>
            {fmtBytes(model.size)}
            {model.details?.parameter_size && ` · ${model.details.parameter_size}`}
            {model.details?.quantization_level && ` · ${model.details.quantization_level}`}
          </div>
        </div>
      </button>

      <button
        onClick={onDelete}
        disabled={isDeleting}
        className="shrink-0 p-1 rounded transition-colors disabled:opacity-40"
        style={{ color: 'var(--text-tertiary)' }}
        title="Delete model"
      >
        {isDeleting ? (
          <Loader2 size={13} className="animate-spin" />
        ) : (
          <Trash2 size={13} />
        )}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Catalog model card
// ---------------------------------------------------------------------------

interface CatalogCardProps {
  model: CatalogModel
  isInstalled: boolean
  isPulling: boolean
  onPull: () => void
  onSelect: () => void
}

function CatalogCard({ model, isInstalled, isPulling, onPull, onSelect }: CatalogCardProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div
      className="mx-3 mb-1.5 rounded border overflow-hidden"
      style={{
        backgroundColor: 'var(--bg-inset)',
        borderColor: model.fit === 'fits' ? 'var(--border)' : model.fit === 'tight' ? 'rgba(245,158,11,0.2)' : 'rgba(239,68,68,0.15)',
        opacity: model.fit === 'too_large' ? 0.65 : 1,
      }}
    >
      {/* Header row */}
      <div className="flex items-start gap-2 p-2.5">
        <button
          onClick={() => setExpanded((e) => !e)}
          className="flex-1 flex items-start gap-2 text-left min-w-0"
        >
          <span className="mt-0.5 shrink-0" style={{ color: 'var(--text-tertiary)' }}>
            {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className="text-xs font-mono font-medium"
                style={{ color: 'var(--text-primary)' }}
              >
                {model.display_name}
              </span>
              <FitBadge fit={model.fit} requiredGb={model.required_vram_gb} />
              {isInstalled && (
                <span
                  className="text-xs px-1.5 py-0.5 rounded font-mono"
                  style={{
                    backgroundColor: 'rgba(52,211,153,0.10)',
                    color: 'var(--running)',
                  }}
                >
                  installed
                </span>
              )}
            </div>
            <div className="text-xs mt-0.5 line-clamp-1" style={{ color: 'var(--text-secondary)' }}>
              {model.description}
            </div>
          </div>
        </button>

        {/* Action button */}
        {isInstalled ? (
          <button
            onClick={onSelect}
            className="shrink-0 flex items-center gap-1 px-2 py-1 rounded text-xs font-mono transition-colors"
            style={{
              backgroundColor: 'rgba(56,189,248,0.10)',
              color: 'var(--accent)',
              border: '1px solid rgba(56,189,248,0.25)',
            }}
          >
            <Zap size={10} />
            Use
          </button>
        ) : (
          <button
            onClick={onPull}
            disabled={isPulling || model.fit === 'too_large'}
            className="shrink-0 flex items-center gap-1 px-2 py-1 rounded text-xs font-mono transition-colors disabled:opacity-40"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              color: isPulling ? 'var(--running)' : 'var(--text-secondary)',
              border: '1px solid var(--border)',
            }}
            title={model.fit === 'too_large' ? 'Exceeds available VRAM' : 'Pull from Ollama'}
          >
            {isPulling ? (
              <>
                <Loader2 size={10} className="animate-spin" />
                Pulling…
              </>
            ) : (
              <>
                <Download size={10} />
                Pull
              </>
            )}
          </button>
        )}
      </div>

      {/* Expanded details */}
      {expanded && (
        <div
          className="px-3 pb-2.5 pt-0 border-t"
          style={{ borderColor: 'var(--border)' }}
        >
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs font-mono">
            <div style={{ color: 'var(--text-tertiary)' }}>
              Params:{' '}
              <span style={{ color: 'var(--text-secondary)' }}>{model.params_b}B</span>
            </div>
            <div style={{ color: 'var(--text-tertiary)' }}>
              Quant:{' '}
              <span style={{ color: 'var(--text-secondary)' }}>Q{model.quant_bits}</span>
            </div>
            <div style={{ color: 'var(--text-tertiary)' }}>
              Context:{' '}
              <span style={{ color: 'var(--text-secondary)' }}>{model.context_k}k</span>
            </div>
            <div style={{ color: 'var(--text-tertiary)' }}>
              VRAM est.:{' '}
              <span style={{ color: 'var(--text-secondary)' }}>
                {fmtVram(model.required_vram_gb)}
              </span>
            </div>
          </div>
          {model.strengths.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {model.strengths.map((s) => (
                <span
                  key={s}
                  className="px-1.5 py-0.5 rounded text-xs"
                  style={{
                    backgroundColor: 'var(--bg-elevated)',
                    color: 'var(--text-tertiary)',
                    border: '1px solid var(--border)',
                  }}
                >
                  {s}
                </span>
              ))}
            </div>
          )}
          <div
            className="mt-2 text-xs font-mono"
            style={{ color: 'var(--text-tertiary)' }}
          >
            Tag:{' '}
            <span style={{ color: 'var(--accent)' }}>{model.ollama_tag}</span>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export function FindModelsPanel() {
  const qc = useQueryClient()
  const { selectedModel, setSelectedModel, pullingModels, addPullingModel, removePullingModel } =
    useStore()

  // Ollama health (poll every 5s so status reflects changes quickly)
  const { data: health } = useQuery({
    queryKey: ['ollama-health'],
    queryFn: modelsApi.getHealth,
    refetchInterval: 5000,
  })

  // Local (installed) models
  const { data: localData, isLoading: localLoading } = useQuery({
    queryKey: ['local-models'],
    queryFn: modelsApi.getLocal,
    refetchInterval: 8000,
  })

  // Recommended catalog
  const { data: recData, isLoading: recLoading } = useQuery({
    queryKey: ['recommended-models'],
    queryFn: modelsApi.getRecommended,
    staleTime: 30_000,
  })

  const localModels: LocalModel[] = localData?.models ?? []
  const localNames = new Set(localModels.map((m) => m.name))
  const catalogModels = recData?.models ?? []

  // Pull mutation
  const pullMutation = useMutation({
    mutationFn: (tag: string) => modelsApi.pull(tag, 'global'),
    onMutate: (tag) => addPullingModel(tag),
    onSettled: (_data, _err, tag) => {
      removePullingModel(tag)
      qc.invalidateQueries({ queryKey: ['local-models'] })
      qc.invalidateQueries({ queryKey: ['ollama-health'] })
    },
  })

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (name: string) => modelsApi.delete(name),
    onSuccess: (_data, name) => {
      if (selectedModel === name) setSelectedModel('')
      qc.invalidateQueries({ queryKey: ['local-models'] })
      qc.invalidateQueries({ queryKey: ['ollama-health'] })
    },
  })

  const [deletingModel, setDeletingModel] = useState<string | null>(null)

  const handleDelete = (name: string) => {
    setDeletingModel(name)
    deleteMutation.mutate(name, { onSettled: () => setDeletingModel(null) })
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Panel header */}
      <div
        className="flex items-center gap-2 px-4 py-3 border-b shrink-0"
        style={{ borderColor: 'var(--border)' }}
      >
        <Cpu size={14} style={{ color: 'var(--accent)' }} />
        <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
          Find models
        </span>
      </div>

      <div className="flex-1 overflow-y-auto py-3">
        {/* Hardware banner */}
        <HardwareBanner />

        {/* Ollama status */}
        {health && (
          <OllamaStatus available={health.available} guidance={health.guidance} />
        )}

        {/* Installed models */}
        <div className="px-3 mb-1">
          <div
            className="flex items-center gap-2 mb-1.5"
          >
            <HardDrive size={12} style={{ color: 'var(--text-tertiary)' }} />
            <span className="text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
              Installed
            </span>
            {localLoading && (
              <Loader2 size={10} className="animate-spin" style={{ color: 'var(--text-tertiary)' }} />
            )}
          </div>
        </div>

        {!localLoading && localModels.length === 0 && (
          <div className="px-3 mb-3">
            <div
              className="text-xs py-2 px-3 rounded border"
              style={{
                color: 'var(--text-tertiary)',
                borderColor: 'var(--border)',
                backgroundColor: 'var(--bg-inset)',
              }}
            >
              No models installed. Pull one from the catalog below.
            </div>
          </div>
        )}

        <div className="px-0 mb-4">
          {localModels.map((m) => (
            <div key={m.name} className="px-3 mb-1">
              <InstalledModelRow
                model={m}
                isSelected={selectedModel === m.name}
                onSelect={() => setSelectedModel(m.name)}
                onDelete={() => handleDelete(m.name)}
                isDeleting={deletingModel === m.name}
              />
            </div>
          ))}
        </div>

        {/* Catalog */}
        <div className="px-3 mb-1.5">
          <div className="flex items-center gap-2">
            <Download size={12} style={{ color: 'var(--text-tertiary)' }} />
            <span
              className="text-xs font-medium uppercase tracking-wider"
              style={{ color: 'var(--text-tertiary)' }}
            >
              Recommended catalog
            </span>
            {recLoading && (
              <Loader2 size={10} className="animate-spin" style={{ color: 'var(--text-tertiary)' }} />
            )}
          </div>
          <div className="text-xs mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
            Ranked by fit for your hardware
          </div>
        </div>

        {catalogModels.map((m) => (
          <CatalogCard
            key={m.ollama_tag}
            model={m}
            isInstalled={localNames.has(m.ollama_tag)}
            isPulling={pullingModels.has(m.ollama_tag)}
            onPull={() => pullMutation.mutate(m.ollama_tag)}
            onSelect={() => setSelectedModel(m.ollama_tag)}
          />
        ))}

        {/* Bottom padding */}
        <div className="h-4" />
      </div>
    </div>
  )
}