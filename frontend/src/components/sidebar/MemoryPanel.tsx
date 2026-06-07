/**
 * MemoryPanel — Stage 3 memory sidebar panel.
 *
 * Layout (top → bottom):
 *   1. Compiled memory doc — read-only Markdown, token count, Recompile button,
 *      stale indicator.
 *   2. Add item form — inline quick-add for any type.
 *   3. Raw items list — grouped by type, filterable, with inline edit/delete.
 *
 * All writes go through memoryApi and invalidate the TanStack Query cache.
 * The "Recompile" button calls POST /compile and refreshes the compiled doc.
 */

import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  BrainCircuit,
  RefreshCw,
  Plus,
  Pencil,
  Trash2,
  Check,
  X,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  Loader2,
} from 'lucide-react'
import {
  useQuery,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query'
import {
  memoryApi,
  type MemoryItem,
  type MemoryType,
  type MemoryItemCreate,
} from '../../api/memoryClient'
import { useStore } from '../../store'
import { Button } from '../common/Button'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TYPE_LABELS: Record<MemoryType, string> = {
  fact: 'Facts',
  mistake: 'Mistakes',
  preference: 'Preferences',
  dataset_ref: 'Datasets',
}

const TYPE_COLORS: Record<MemoryType, string> = {
  fact: 'var(--accent)',
  mistake: 'var(--danger)',
  preference: 'var(--info)',
  dataset_ref: 'var(--running)',
}

const TYPE_BG: Record<MemoryType, string> = {
  fact: 'rgba(56,189,248,0.08)',
  mistake: 'rgba(239,68,68,0.08)',
  preference: 'rgba(167,139,250,0.08)',
  dataset_ref: 'rgba(52,211,153,0.08)',
}

const ALL_TYPES: MemoryType[] = ['fact', 'mistake', 'preference', 'dataset_ref']

// ---------------------------------------------------------------------------
// Compiled doc section
// ---------------------------------------------------------------------------

interface CompiledDocSectionProps {
  projectId: number
  selectedModel: string
}

function CompiledDocSection({ projectId, selectedModel }: CompiledDocSectionProps) {
  const qc = useQueryClient()
  const [expanded, setExpanded] = useState(true)

  const { data: compiled, isLoading } = useQuery({
    queryKey: ['memory-compiled', projectId],
    queryFn: () => memoryApi.getCompiled(projectId),
    refetchOnWindowFocus: false,
  })

  const compileMutation = useMutation({
    mutationFn: () => memoryApi.compile(projectId, selectedModel || undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['memory-compiled', projectId] })
      qc.invalidateQueries({ queryKey: ['memory-items', projectId] })
    },
  })

  const isStale = compiled?.is_stale ?? true
  const tokenCount = compiled?.token_estimate ?? 0
  const itemCount = compiled?.item_count ?? 0

  return (
    <div
      className="mx-3 mb-3 rounded border overflow-hidden"
      style={{
        borderColor: isStale ? 'rgba(245,158,11,0.35)' : 'var(--border)',
        backgroundColor: 'var(--bg-inset)',
      }}
    >
      {/* Header */}
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left"
        style={{ backgroundColor: 'var(--bg-elevated)' }}
      >
        <BrainCircuit size={13} style={{ color: 'var(--accent)', flexShrink: 0 }} />
        <span className="flex-1 text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
          Compiled memory
        </span>

        {isStale && (
          <span
            className="flex items-center gap-1 text-xs font-mono px-1.5 py-0.5 rounded"
            style={{
              color: 'var(--warn)',
              backgroundColor: 'rgba(245,158,11,0.10)',
              border: '1px solid rgba(245,158,11,0.25)',
            }}
          >
            <AlertTriangle size={9} />
            stale
          </span>
        )}

        {tokenCount > 0 && (
          <span className="text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>
            ~{tokenCount} tok · {itemCount} items
          </span>
        )}

        <span style={{ color: 'var(--text-tertiary)' }}>
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      </button>

      {/* Compiled markdown */}
      {expanded && (
        <div>
          <div
            className="px-3 py-2.5 text-xs overflow-y-auto"
            style={{
              color: 'var(--text-secondary)',
              maxHeight: '220px',
              lineHeight: '1.6',
            }}
          >
            {isLoading ? (
              <div className="flex items-center gap-2" style={{ color: 'var(--text-tertiary)' }}>
                <Loader2 size={11} className="animate-spin" />
                Loading…
              </div>
            ) : (
              <ReactMarkdown
                components={{
                  p: ({ children }) => (
                    <p className="mb-1.5 last:mb-0" style={{ color: 'var(--text-secondary)' }}>
                      {children}
                    </p>
                  ),
                  ul: ({ children }) => (
                    <ul className="list-disc list-inside mb-1.5 space-y-0.5">{children}</ul>
                  ),
                  li: ({ children }) => (
                    <li style={{ color: 'var(--text-secondary)' }}>{children}</li>
                  ),
                  strong: ({ children }) => (
                    <strong style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                      {children}
                    </strong>
                  ),
                  h2: ({ children }) => (
                    <h2
                      className="text-xs font-medium mt-2 mb-1 first:mt-0"
                      style={{ color: 'var(--text-primary)' }}
                    >
                      {children}
                    </h2>
                  ),
                  h3: ({ children }) => (
                    <h3
                      className="text-xs font-medium mt-1.5 mb-0.5 first:mt-0"
                      style={{ color: 'var(--text-tertiary)' }}
                    >
                      {children}
                    </h3>
                  ),
                  em: ({ children }) => (
                    <em style={{ color: 'var(--text-tertiary)' }}>{children}</em>
                  ),
                }}
              >
                {compiled?.markdown ?? '_No compiled memory yet._'}
              </ReactMarkdown>
            )}
          </div>

          {/* Recompile button */}
          <div
            className="flex items-center justify-between px-3 py-2 border-t"
            style={{ borderColor: 'var(--border)' }}
          >
            <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
              {isStale
                ? 'Items changed — recompile to update'
                : 'Up to date'}
            </span>
            <button
              onClick={() => compileMutation.mutate()}
              disabled={compileMutation.isPending}
              className="flex items-center gap-1.5 text-xs font-mono px-2 py-1 rounded border transition-colors disabled:opacity-40"
              style={{
                color: isStale ? 'var(--warn)' : 'var(--accent)',
                borderColor: isStale ? 'rgba(245,158,11,0.4)' : 'rgba(56,189,248,0.3)',
                backgroundColor: isStale ? 'rgba(245,158,11,0.07)' : 'rgba(56,189,248,0.07)',
              }}
            >
              {compileMutation.isPending ? (
                <Loader2 size={10} className="animate-spin" />
              ) : (
                <RefreshCw size={10} />
              )}
              {compileMutation.isPending ? 'Compiling…' : 'Recompile'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Add item form
// ---------------------------------------------------------------------------

interface AddItemFormProps {
  projectId: number
  onDone: () => void
}

function AddItemForm({ projectId, onDone }: AddItemFormProps) {
  const qc = useQueryClient()
  const [type, setType] = useState<MemoryType>('fact')
  const [content, setContent] = useState('')
  const [tags, setTags] = useState('')

  const mutation = useMutation({
    mutationFn: (data: MemoryItemCreate) => memoryApi.createItem(projectId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['memory-items', projectId] })
      qc.invalidateQueries({ queryKey: ['memory-compiled', projectId] })
      setContent('')
      setTags('')
      onDone()
    },
  })

  const handleSubmit = () => {
    if (!content.trim()) return
    mutation.mutate({ type, content: content.trim(), tags: tags.trim(), source: 'user' })
  }

  return (
    <div
      className="mx-3 mb-3 p-3 rounded border"
      style={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border)' }}
    >
      <div className="text-xs font-medium mb-2" style={{ color: 'var(--text-primary)' }}>
        Add memory item
      </div>

      {/* Type selector */}
      <div className="flex gap-1 mb-2 flex-wrap">
        {ALL_TYPES.map((t) => (
          <button
            key={t}
            onClick={() => setType(t)}
            className="px-2 py-0.5 rounded text-xs font-mono border transition-colors"
            style={{
              backgroundColor: type === t ? TYPE_BG[t] : 'transparent',
              color: type === t ? TYPE_COLORS[t] : 'var(--text-tertiary)',
              borderColor: type === t ? TYPE_COLORS[t] + '60' : 'var(--border)',
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Content */}
      <textarea
        rows={2}
        autoFocus
        placeholder={`Describe the ${type}…`}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit()
          if (e.key === 'Escape') onDone()
        }}
        className="w-full px-2.5 py-1.5 rounded text-xs font-sans resize-none outline-none mb-2"
        style={{
          backgroundColor: 'var(--bg-inset)',
          border: '1px solid var(--border-strong)',
          color: 'var(--text-primary)',
          lineHeight: '1.5',
        }}
      />

      {/* Tags */}
      <input
        type="text"
        placeholder="Tags (comma-separated, optional)"
        value={tags}
        onChange={(e) => setTags(e.target.value)}
        className="w-full px-2.5 py-1 rounded text-xs font-mono outline-none mb-2"
        style={{
          backgroundColor: 'var(--bg-inset)',
          border: '1px solid var(--border)',
          color: 'var(--text-secondary)',
        }}
      />

      <div className="flex gap-2">
        <Button
          size="sm"
          onClick={handleSubmit}
          disabled={!content.trim() || mutation.isPending}
        >
          {mutation.isPending ? 'Saving…' : 'Save'}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>

      {mutation.isError && (
        <div className="text-xs mt-1.5" style={{ color: 'var(--danger)' }}>
          {mutation.error instanceof Error ? mutation.error.message : 'Failed to save'}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Inline item row (view + edit + delete)
// ---------------------------------------------------------------------------

interface ItemRowProps {
  item: MemoryItem
  projectId: number
}

function ItemRow({ item, projectId }: ItemRowProps) {
  const qc = useQueryClient()
  const [editMode, setEditMode] = useState(false)
  const [editContent, setEditContent] = useState(item.content)
  const [editTags, setEditTags] = useState(item.tags)

  const updateMutation = useMutation({
    mutationFn: (data: { content?: string; tags?: string; active?: boolean }) =>
      memoryApi.updateItem(projectId, item.id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['memory-items', projectId] })
      qc.invalidateQueries({ queryKey: ['memory-compiled', projectId] })
      setEditMode(false)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => memoryApi.deleteItem(projectId, item.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['memory-items', projectId] })
      qc.invalidateQueries({ queryKey: ['memory-compiled', projectId] })
    },
  })

  const handleSave = () => {
    if (!editContent.trim()) return
    updateMutation.mutate({ content: editContent.trim(), tags: editTags.trim() })
  }

  const tagList = item.tags
    ? item.tags.split(',').map((t) => t.trim()).filter(Boolean)
    : []

  if (editMode) {
    return (
      <div
        className="px-3 py-2 border-b last:border-b-0"
        style={{ borderColor: 'var(--border)' }}
      >
        <textarea
          rows={2}
          autoFocus
          value={editContent}
          onChange={(e) => setEditContent(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSave()
            if (e.key === 'Escape') setEditMode(false)
          }}
          className="w-full px-2 py-1 rounded text-xs font-sans resize-none outline-none mb-1.5"
          style={{
            backgroundColor: 'var(--bg-inset)',
            border: '1px solid var(--border-strong)',
            color: 'var(--text-primary)',
          }}
        />
        <input
          type="text"
          value={editTags}
          onChange={(e) => setEditTags(e.target.value)}
          placeholder="tags (comma-separated)"
          className="w-full px-2 py-0.5 rounded text-xs font-mono outline-none mb-1.5"
          style={{
            backgroundColor: 'var(--bg-inset)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
        />
        <div className="flex gap-1.5">
          <button
            onClick={handleSave}
            disabled={!editContent.trim() || updateMutation.isPending}
            className="flex items-center gap-1 text-xs px-2 py-0.5 rounded border disabled:opacity-40"
            style={{ color: 'var(--running)', borderColor: 'rgba(52,211,153,0.4)' }}
          >
            <Check size={10} />
            Save
          </button>
          <button
            onClick={() => { setEditContent(item.content); setEditTags(item.tags); setEditMode(false) }}
            className="flex items-center gap-1 text-xs px-2 py-0.5 rounded border"
            style={{ color: 'var(--text-tertiary)', borderColor: 'var(--border)' }}
          >
            <X size={10} />
            Cancel
          </button>
        </div>
      </div>
    )
  }

  return (
    <div
      className="group flex items-start gap-2 px-3 py-2 border-b last:border-b-0 transition-colors"
      style={{ borderColor: 'var(--border)' }}
    >
      <div className="flex-1 min-w-0">
        <div className="text-xs leading-relaxed" style={{ color: 'var(--text-primary)' }}>
          {item.content}
        </div>
        {tagList.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {tagList.map((tag) => (
              <span
                key={tag}
                className="px-1 py-0.5 rounded text-xs font-mono"
                style={{
                  backgroundColor: 'var(--bg-elevated)',
                  color: 'var(--text-tertiary)',
                  border: '1px solid var(--border)',
                }}
              >
                {tag}
              </span>
            ))}
          </div>
        )}
        <div className="text-xs mt-0.5 font-mono" style={{ color: 'var(--text-tertiary)' }}>
          {item.source} · {new Date(item.created_at).toLocaleDateString()}
        </div>
      </div>

      {/* Action buttons — visible on hover */}
      <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
        <button
          onClick={() => setEditMode(true)}
          className="p-1 rounded transition-colors"
          style={{ color: 'var(--text-tertiary)' }}
          title="Edit"
        >
          <Pencil size={11} />
        </button>
        <button
          onClick={() => deleteMutation.mutate()}
          disabled={deleteMutation.isPending}
          className="p-1 rounded transition-colors disabled:opacity-40"
          style={{ color: 'var(--text-tertiary)' }}
          title="Delete"
        >
          {deleteMutation.isPending ? (
            <Loader2 size={11} className="animate-spin" />
          ) : (
            <Trash2 size={11} />
          )}
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Items list — grouped by type with filter tabs
// ---------------------------------------------------------------------------

interface ItemsListProps {
  projectId: number
}

function ItemsList({ projectId }: ItemsListProps) {
  const [filterType, setFilterType] = useState<MemoryType | null>(null)

  const { data: items = [], isLoading } = useQuery({
    queryKey: ['memory-items', projectId, filterType],
    queryFn: () =>
      memoryApi.listItems(projectId, {
        type: filterType ?? undefined,
        active_only: true,
        include_global: true,
      }),
    refetchOnWindowFocus: false,
  })

  // Group by type
  const grouped = items.reduce<Record<string, MemoryItem[]>>((acc, item) => {
    const key = item.type
    if (!acc[key]) acc[key] = []
    acc[key].push(item)
    return acc
  }, {})

  return (
    <div>
      {/* Filter tabs */}
      <div className="flex items-center gap-1 px-3 mb-2 flex-wrap">
        <button
          onClick={() => setFilterType(null)}
          className="px-2 py-0.5 rounded text-xs font-mono border transition-colors"
          style={{
            color: filterType === null ? 'var(--accent)' : 'var(--text-tertiary)',
            borderColor: filterType === null ? 'rgba(56,189,248,0.4)' : 'var(--border)',
            backgroundColor: filterType === null ? 'rgba(56,189,248,0.08)' : 'transparent',
          }}
        >
          all ({items.length})
        </button>
        {ALL_TYPES.map((t) => {
          const count = (grouped[t] ?? []).length
          if (count === 0 && filterType !== t) return null
          return (
            <button
              key={t}
              onClick={() => setFilterType(t === filterType ? null : t)}
              className="px-2 py-0.5 rounded text-xs font-mono border transition-colors"
              style={{
                color: filterType === t ? TYPE_COLORS[t] : 'var(--text-tertiary)',
                borderColor: filterType === t ? TYPE_COLORS[t] + '60' : 'var(--border)',
                backgroundColor: filterType === t ? TYPE_BG[t] : 'transparent',
              }}
            >
              {t} ({count})
            </button>
          )
        })}
      </div>

      {/* Content */}
      {isLoading && (
        <div className="flex items-center gap-2 px-3 py-3 text-xs" style={{ color: 'var(--text-tertiary)' }}>
          <Loader2 size={11} className="animate-spin" />
          Loading items…
        </div>
      )}

      {!isLoading && items.length === 0 && (
        <div className="px-3 py-3 text-xs" style={{ color: 'var(--text-tertiary)' }}>
          No memory items yet. Add one with the button above.
        </div>
      )}

      {/* Grouped sections */}
      {!isLoading &&
        Object.entries(grouped).map(([type, typeItems]) => (
          <div
            key={type}
            className="mx-3 mb-2 rounded border overflow-hidden"
            style={{ borderColor: 'var(--border)' }}
          >
            {/* Group header */}
            <div
              className="flex items-center gap-2 px-3 py-1.5"
              style={{ backgroundColor: TYPE_BG[type as MemoryType] }}
            >
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{ backgroundColor: TYPE_COLORS[type as MemoryType] }}
              />
              <span
                className="text-xs font-medium font-mono"
                style={{ color: TYPE_COLORS[type as MemoryType] }}
              >
                {TYPE_LABELS[type as MemoryType]}
              </span>
              <span className="text-xs font-mono ml-auto" style={{ color: 'var(--text-tertiary)' }}>
                {typeItems.length}
              </span>
            </div>

            {/* Items */}
            <div style={{ backgroundColor: 'var(--bg-inset)' }}>
              {typeItems.map((item) => (
                <ItemRow key={item.id} item={item} projectId={projectId} />
              ))}
            </div>
          </div>
        ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// MemoryPanel — main export
// ---------------------------------------------------------------------------

export function MemoryPanel() {
  const activeProjectId = useStore((s) => s.activeProjectId)
  const selectedModel = useStore((s) => s.selectedModel)
  const setSidebarPanel = useStore((s) => s.setSidebarPanel)
  const [showAddForm, setShowAddForm] = useState(false)

  if (!activeProjectId) {
    return (
      <div className="flex flex-col h-full">
        <div
          className="flex items-center gap-2 px-4 py-3 border-b shrink-0"
          style={{ borderColor: 'var(--border)' }}
        >
          <BrainCircuit size={14} style={{ color: 'var(--accent)' }} />
          <span className="text-sm font-medium flex-1" style={{ color: 'var(--text-primary)' }}>
            Memory
          </span>
          <button onClick={() => setSidebarPanel(null)} className="p-1 rounded" style={{ color: 'var(--text-tertiary)' }} title="Close">
            <X size={13} />
          </button>
        </div>
        <div className="flex-1 flex items-center justify-center p-6 text-center">
          <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
            Select a project to view its memory.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Panel header */}
      <div
        className="flex items-center gap-2 px-4 py-3 border-b shrink-0"
        style={{ borderColor: 'var(--border)' }}
      >
        <BrainCircuit size={14} style={{ color: 'var(--accent)' }} />
        <span className="text-sm font-medium flex-1" style={{ color: 'var(--text-primary)' }}>
          Memory
        </span>
        <button
          onClick={() => setShowAddForm((v) => !v)}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded border transition-colors"
          style={{
            color: showAddForm ? 'var(--accent)' : 'var(--text-tertiary)',
            borderColor: showAddForm ? 'rgba(56,189,248,0.4)' : 'var(--border)',
            backgroundColor: showAddForm ? 'rgba(56,189,248,0.07)' : 'transparent',
          }}
          title="Add memory item"
        >
          <Plus size={11} />
          Add
        </button>
        <button onClick={() => setSidebarPanel(null)} className="p-1 rounded" style={{ color: 'var(--text-tertiary)' }} title="Close">
          <X size={13} />
        </button>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto py-3">
        {/* Compiled doc */}
        <CompiledDocSection
          projectId={activeProjectId}
          selectedModel={selectedModel}
        />

        {/* Add item form */}
        {showAddForm && (
          <AddItemForm
            projectId={activeProjectId}
            onDone={() => setShowAddForm(false)}
          />
        )}

        {/* Raw items list */}
        <div className="px-0">
          <div className="px-3 mb-2">
            <span className="text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
              Raw items
            </span>
          </div>
          <ItemsList projectId={activeProjectId} />
        </div>

        {/* Bottom padding */}
        <div className="h-4" />
      </div>
    </div>
  )
}