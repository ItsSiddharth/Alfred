/**
 * ChatThread — renders streaming WS tokens and eventual persisted messages.
 *
 * In Stage 0 we render only the live streaming messages from the Zustand store
 * (fed by WS demo events). Persisted message rendering (markdown, plan cards,
 * thinking tabs) is added in Stage 2.
 */

import { useEffect, useRef } from 'react'
import { useStore } from '../../store'
import { Bot } from 'lucide-react'

function StreamingBubble({ content, isStreaming }: { content: string; isStreaming: boolean }) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      {/* Avatar */}
      <div
        className="shrink-0 w-7 h-7 rounded flex items-center justify-center mt-0.5"
        style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border)' }}
      >
        <Bot size={14} style={{ color: 'var(--accent)' }} />
      </div>

      {/* Bubble */}
      <div
        className="flex-1 rounded px-3 py-2.5 text-sm token-stream"
        style={{
          backgroundColor: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          color: 'var(--text-primary)',
          maxWidth: '80%',
        }}
      >
        {content}
        {isStreaming && (
          <span
            className="inline-block w-1.5 h-3.5 ml-0.5 rounded-sm pulse-dot align-middle"
            style={{ backgroundColor: 'var(--accent)' }}
          />
        )}
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 select-none">
      <div
        className="w-12 h-12 rounded-xl flex items-center justify-center"
        style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border)' }}
      >
        <Bot size={24} style={{ color: 'var(--accent)' }} />
      </div>
      <div className="text-center">
        <div className="font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
          ALFRED is ready
        </div>
        <div className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
          Select or create a project to begin.
        </div>
      </div>
    </div>
  )
}

export function ChatThread() {
  const streamingMessages = useStore((s) => s.streamingMessages)
  const bottomRef = useRef<HTMLDivElement>(null)

  const entries = Object.values(streamingMessages)

  // Auto-scroll to bottom on new tokens
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [streamingMessages])

  return (
    <div
      className="flex-1 overflow-y-auto flex flex-col"
      style={{ backgroundColor: 'var(--bg-base)' }}
    >
      {entries.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="flex flex-col py-2">
          {entries.map((msg) => (
            <StreamingBubble
              key={msg.messageId}
              content={msg.content}
              isStreaming={msg.isStreaming}
            />
          ))}
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}