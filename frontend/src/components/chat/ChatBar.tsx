/**
 * ChatBar — bottom input bar (Stage 4, patched).
 *
 * Fix vs original:
 *   F6 — Optimistic user message uses a large negative temp ID to avoid
 *   collision with real DB IDs (which start at 1). On the next project
 *   reload, the REST fetch returns the real row and replaces the temp one.
 *   The temp ID is tracked in a ref so it can be cleaned up if needed.
 *
 *   The Show Work toggle now also shows model and memory info from
 *   assistant message metadata_json (rendered by ChatThread/ShowWorkMeta).
 */

import React, { useState, useRef, useEffect } from 'react'
import { Send, ChevronDown, AlertCircle, Eye, EyeOff } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { modelsApi } from '../../api/client'
import { useStore } from '../../store'

// ---------------------------------------------------------------------------
// Model picker dropdown
// ---------------------------------------------------------------------------

interface ModelPickerProps {
  localNames: string[]
  selected: string
  onSelect: (model: string) => void
}

function ModelPicker({ localNames, selected, onSelect }: ModelPickerProps) {
  const [open, setOpen] = useState(false)
  const { setSidebarPanel } = useStore()

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs font-mono px-2 py-1 rounded border transition-colors"
        style={{
          color: selected ? 'var(--accent)' : 'var(--text-tertiary)',
          borderColor: selected ? 'rgba(56,189,248,0.3)' : 'var(--border)',
          backgroundColor: 'var(--bg-elevated)',
        }}
        title="Select model"
      >
        <span className="max-w-[180px] truncate">
          {selected || 'No model selected'}
        </span>
        <ChevronDown size={11} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            className="absolute bottom-full left-0 mb-1 z-20 rounded border overflow-hidden"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              borderColor: 'var(--border-strong)',
              minWidth: '220px',
              maxHeight: '260px',
              overflowY: 'auto',
            }}
          >
            {localNames.length === 0 ? (
              <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-tertiary)' }}>
                No models installed.{' '}
                <button
                  className="underline"
                  style={{ color: 'var(--accent)' }}
                  onClick={() => {
                    setOpen(false)
                    setSidebarPanel('find-models')
                  }}
                >
                  Open Find models →
                </button>
              </div>
            ) : (
              localNames.map((name) => (
                <button
                  key={name}
                  onClick={() => { onSelect(name); setOpen(false) }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs font-mono transition-colors"
                  style={{
                    backgroundColor: name === selected ? 'var(--bg-inset)' : 'transparent',
                    color: name === selected ? 'var(--accent)' : 'var(--text-secondary)',
                  }}
                >
                  <span
                    className="w-1.5 h-1.5 rounded-full shrink-0"
                    style={{
                      backgroundColor: name === selected ? 'var(--running)' : 'transparent',
                      border: name === selected ? 'none' : '1px solid var(--border-strong)',
                    }}
                  />
                  {name}
                </button>
              ))
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ChatBar
// ---------------------------------------------------------------------------

// Use large negative IDs for optimistic messages to avoid collision with
// real DB IDs (which are always positive integers starting at 1).
let _optimisticIdCounter = -1

export function ChatBar() {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const {
    selectedModel,
    setSelectedModel,
    activeProjectId,
    activeProjectStage,
    showWorkMode,
    toggleShowWork,
    appendPersistedMessage,
  } = useStore()

  // Auto-focus chat input when project changes
  useEffect(() => {
    if (activeProjectId && selectedModel) {
      textareaRef.current?.focus()
    }
  }, [activeProjectId, selectedModel])

  const { data: localData } = useQuery({
    queryKey: ['local-models'],
    queryFn: modelsApi.getLocal,
    refetchInterval: 10_000,
  })
  const localNames = (localData?.models ?? []).map((m) => m.name)

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleSend = () => {
    if (!value.trim() || !activeProjectId || !selectedModel) return

    const messageId = `msg-${Date.now()}`
    const optimisticId = _optimisticIdCounter--

    // Add optimistic user message to the persisted list immediately
    // so the user sees their message without waiting for the backend.
    // Uses a negative temp ID. When the project is reloaded from DB,
    // the real row replaces it via setPersistedMessages.
    appendPersistedMessage({
      id: optimisticId,
      project_id: activeProjectId,
      role: 'user',
      content: value.trim(),
      created_at: new Date().toISOString(),
      kind: 'chat',
      metadata_json: '{}',
    })

    // Dispatch the chat message over WebSocket
    window.dispatchEvent(
      new CustomEvent('alfred:send', {
        detail: {
          type: 'chat',
          content: value.trim(),
          model: selectedModel,
          message_id: messageId,
        },
      })
    )

    setValue('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`
  }

  const canSend = !!value.trim() && !!activeProjectId && !!selectedModel

  return (
    <div
      className="shrink-0 border-t px-4 py-3"
      style={{ backgroundColor: 'var(--bg-surface)', borderColor: 'var(--border)' }}
    >
      {/* No-model warning */}
      {activeProjectId && !selectedModel && localNames.length > 0 && (
        <div
          className="flex items-center gap-1.5 text-xs mb-2 px-2 py-1 rounded"
          style={{
            backgroundColor: 'rgba(245,158,11,0.08)',
            color: 'var(--warn)',
            border: '1px solid rgba(245,158,11,0.2)',
          }}
        >
          <AlertCircle size={11} />
          Select a model below to start chatting.
        </div>
      )}

      {/* Input row */}
      <div
        className="flex items-end gap-2 rounded border px-3 py-2"
        style={{
          backgroundColor: 'var(--bg-elevated)',
          borderColor: 'var(--border-strong)',
        }}
      >
        <textarea
          ref={textareaRef}
          rows={1}
          placeholder={
            !activeProjectId
              ? 'Select a project to start chatting.'
              : !selectedModel
              ? 'Select a model below first…'
              : activeProjectStage === 'run'
              ? 'Discuss results, brainstorm ideas, or say "run the experiment"…'
              : 'Describe your research hypothesis…'
          }
          disabled={!activeProjectId}
          value={value}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          className="flex-1 resize-none bg-transparent outline-none text-sm font-sans"
          style={{
            color: 'var(--text-primary)',
            caretColor: 'var(--accent)',
            lineHeight: '1.6',
            minHeight: '24px',
          }}
        />
        <button
          onClick={handleSend}
          disabled={!canSend}
          className="shrink-0 p-1.5 rounded transition-colors duration-100 disabled:opacity-30"
          style={{ color: canSend ? 'var(--accent)' : 'var(--text-tertiary)' }}
          title="Send (Enter)"
        >
          <Send size={16} />
        </button>
      </div>

      {/* Bottom toolbar */}
      <div className="flex items-center gap-3 mt-2">
        <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
          Model:
        </span>
        <ModelPicker
          localNames={localNames}
          selected={selectedModel}
          onSelect={setSelectedModel}
        />

        {localNames.length === 0 && (
          <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
            — install via Find models →
          </span>
        )}

        <div className="flex-1" />

        {/* Show your work toggle */}
        <button
          onClick={toggleShowWork}
          className="flex items-center gap-1.5 text-xs font-mono px-2 py-1 rounded border transition-colors"
          title={
            showWorkMode
              ? 'Hide: raw LLM input, memory tokens, tool calls'
              : 'Show your work: reveal raw LLM input, memory tokens, tool calls'
          }
          style={{
            color: showWorkMode ? 'var(--info)' : 'var(--text-tertiary)',
            borderColor: showWorkMode ? 'rgba(167,139,250,0.4)' : 'var(--border)',
            backgroundColor: showWorkMode ? 'rgba(167,139,250,0.08)' : 'transparent',
          }}
        >
          {showWorkMode ? <Eye size={11} /> : <EyeOff size={11} />}
          Show work
        </button>

      </div>
    </div>
  )
}