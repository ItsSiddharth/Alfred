/**
 * ChatBar — bottom input bar.
 *
 * Stage 0: placeholder model picker + send button (send is a no-op until
 * Stage 1 wires Ollama). The model picker populates from /api/models in Stage 1.
 */

import React, { useState, useRef } from 'react'
import { Send, ChevronDown } from 'lucide-react'
import { useStore } from '../../store'

export function ChatBar() {
  const [value, setValue] = useState('')
  const { selectedModel, activeProjectId } = useStore()
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleSend = () => {
    if (!value.trim() || !activeProjectId) return
    // Stage 1+ will send to the WS / REST here.
    console.info('[ChatBar] send (wired in Stage 1):', value.trim())
    setValue('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value)
    // Auto-grow textarea up to ~6 lines
    const el = e.target
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`
  }

  return (
    <div
      className="shrink-0 border-t px-4 py-3"
      style={{
        backgroundColor: 'var(--bg-surface)',
        borderColor: 'var(--border)',
      }}
    >
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
            activeProjectId
              ? 'Describe your research hypothesis…'
              : 'Select a project to start chatting.'
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
          disabled={!value.trim() || !activeProjectId}
          className="shrink-0 p-1.5 rounded transition-colors duration-100 disabled:opacity-30"
          style={{ color: value.trim() ? 'var(--accent)' : 'var(--text-tertiary)' }}
          title="Send (Enter)"
        >
          <Send size={16} />
        </button>
      </div>

      {/* Model picker row */}
      <div className="flex items-center gap-2 mt-2">
        <span className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
          Model:
        </span>
        <button
          className="flex items-center gap-1 text-sm px-2 py-0.5 rounded border transition-colors"
          style={{
            color: 'var(--text-secondary)',
            borderColor: 'var(--border)',
            backgroundColor: 'var(--bg-elevated)',
          }}
          title="Model picker — available in Stage 1"
          onClick={() => {
            // Stage 1 will open Find Models panel here.
            useStore.getState().setSidebarPanel('find-models')
          }}
        >
          {selectedModel || 'No model selected'}
          <ChevronDown size={12} />
        </button>
        <span className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
          (install a model via Find models →)
        </span>
      </div>
    </div>
  )
}