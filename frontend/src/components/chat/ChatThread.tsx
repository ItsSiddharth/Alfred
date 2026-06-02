/**
 * ChatThread — renders the full message history for an active project.
 *
 * Message kinds (C5):
 *   chat      — markdown bubble (user left, assistant right with avatar)
 *   thinking  — collapsible accordion, pulsing --info dot while streaming
 *   plan      — plan card with Approve / Edit / Reject (handled by PlanCard)
 *   result    — green-tinted result bubble
 *   error     — red-tinted error bubble
 *
 * Render order:
 *   1. persistedMessages (from DB, loaded on project open)
 *   2. streamingMessages (live tokens from WS, keyed by message_id)
 *   3. logEntries (thinking / tool_call entries)
 *
 * Auto-scrolls to bottom on new content.
 */

import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import {
  Bot,
  User,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Wrench,
} from 'lucide-react'
import { useStore, type PersistedMessage, type LogEntry } from '../../store'
import { ApprovalCard } from './ApprovalCard'
import 'highlight.js/styles/github-dark.css'

// ---------------------------------------------------------------------------
// Markdown renderer
// ---------------------------------------------------------------------------

function MarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        code({ node, className, children, ...props }) {
          const isBlock = className?.startsWith('language-')
          if (isBlock) {
            return (
              <pre
                className="rounded overflow-x-auto my-2"
                style={{ backgroundColor: 'var(--bg-inset)', padding: '12px' }}
              >
                <code
                  className={className}
                  style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '12px' }}
                  {...props}
                >
                  {children}
                </code>
              </pre>
            )
          }
          return (
            <code
              className="px-1 py-0.5 rounded text-xs font-mono"
              style={{
                backgroundColor: 'var(--bg-inset)',
                color: 'var(--accent)',
                border: '1px solid var(--border)',
              }}
              {...props}
            >
              {children}
            </code>
          )
        },
        p({ children }) {
          return (
            <p className="mb-2 last:mb-0" style={{ color: 'var(--text-primary)' }}>
              {children}
            </p>
          )
        },
        ul({ children }) {
          return (
            <ul
              className="list-disc list-inside mb-2 space-y-0.5"
              style={{ color: 'var(--text-primary)' }}
            >
              {children}
            </ul>
          )
        },
        ol({ children }) {
          return (
            <ol
              className="list-decimal list-inside mb-2 space-y-0.5"
              style={{ color: 'var(--text-primary)' }}
            >
              {children}
            </ol>
          )
        },
        blockquote({ children }) {
          return (
            <blockquote
              className="border-l-2 pl-3 my-2 italic"
              style={{
                borderColor: 'var(--accent)',
                color: 'var(--text-secondary)',
              }}
            >
              {children}
            </blockquote>
          )
        },
        h1({ children }) {
          return (
            <h1 className="text-base font-medium mb-2 mt-3 first:mt-0" style={{ color: 'var(--text-primary)' }}>
              {children}
            </h1>
          )
        },
        h2({ children }) {
          return (
            <h2 className="text-sm font-medium mb-1.5 mt-3 first:mt-0" style={{ color: 'var(--text-primary)' }}>
              {children}
            </h2>
          )
        },
        h3({ children }) {
          return (
            <h3 className="text-sm font-medium mb-1 mt-2 first:mt-0" style={{ color: 'var(--text-secondary)' }}>
              {children}
            </h3>
          )
        },
        a({ href, children }) {
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: 'var(--accent)', textDecoration: 'underline' }}
            >
              {children}
            </a>
          )
        },
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

// ---------------------------------------------------------------------------
// ThinkingTab — collapsible accordion (C5 signature pattern)
// ---------------------------------------------------------------------------

interface ThinkingTabProps {
  content: string
  isStreaming: boolean
  title?: string
  defaultExpanded?: boolean
}

export function ThinkingTab({
  content,
  isStreaming,
  title = 'Thinking',
  defaultExpanded = false,
}: ThinkingTabProps) {
  const showWorkMode = useStore((s) => s.showWorkMode)
  // Auto-expand when showWorkMode is on.
  const [expanded, setExpanded] = useState(defaultExpanded || showWorkMode)

  // Sync with showWorkMode changes.
  useEffect(() => {
    if (showWorkMode) setExpanded(true)
  }, [showWorkMode])

  // Collapse when streaming finishes (unless showWorkMode).
  useEffect(() => {
    if (!isStreaming && !showWorkMode) {
      setExpanded(false)
    }
  }, [isStreaming, showWorkMode])

  return (
    <div
      className="rounded border overflow-hidden"
      style={{
        backgroundColor: 'var(--bg-inset)',
        borderColor: 'var(--border)',
      }}
    >
      {/* Header */}
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left transition-colors"
        style={{
          backgroundColor: expanded ? 'var(--bg-elevated)' : 'transparent',
        }}
      >
        {/* Pulsing dot — info colour while streaming */}
        <span
          className={`shrink-0 w-1.5 h-1.5 rounded-full ${isStreaming ? 'pulse-dot' : ''}`}
          style={{
            backgroundColor: isStreaming ? 'var(--info)' : 'var(--text-tertiary)',
          }}
        />

        <span
          className="flex-1 text-xs font-mono font-medium"
          style={{ color: isStreaming ? 'var(--info)' : 'var(--text-tertiary)' }}
        >
          {title}
          {isStreaming && (
            <span className="ml-1 font-normal" style={{ color: 'var(--text-tertiary)' }}>
              — streaming…
            </span>
          )}
        </span>

        <span style={{ color: 'var(--text-tertiary)' }}>
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      </button>

      {/* Content */}
      {expanded && (
        <div
          className="px-3 py-2.5 text-xs font-mono border-t token-stream"
          style={{
            borderColor: 'var(--border)',
            color: 'var(--text-secondary)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: '320px',
            overflowY: 'auto',
            lineHeight: '1.7',
          }}
        >
          {content}
          {isStreaming && (
            <span
              className="inline-block w-1 h-3 ml-0.5 rounded-sm pulse-dot align-middle"
              style={{ backgroundColor: 'var(--info)' }}
            />
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ToolCallEntry — shown when showWorkMode is on
// ---------------------------------------------------------------------------

function ToolCallEntry({ entry }: { entry: LogEntry }) {
  const showWorkMode = useStore((s) => s.showWorkMode)
  if (!showWorkMode) return null

  return (
    <div
      className="flex items-start gap-2 px-3 py-2 rounded border text-xs font-mono"
      style={{
        backgroundColor: 'var(--bg-inset)',
        borderColor: 'var(--border)',
        color: 'var(--text-tertiary)',
      }}
    >
      <Wrench size={11} className="mt-0.5 shrink-0" style={{ color: 'var(--accent)' }} />
      <span style={{ color: 'var(--text-secondary)' }}>{entry.content}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Chat bubble — user or assistant
// ---------------------------------------------------------------------------

interface ChatBubbleProps {
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  isStreaming?: boolean
  messageId?: string
}

function ChatBubble({ role, content, isStreaming = false, messageId }: ChatBubbleProps) {
  const isUser = role === 'user'

  return (
    <div
      className={`flex items-start gap-3 px-4 py-3 ${isUser ? 'flex-row-reverse' : ''}`}
    >
      {/* Avatar */}
      <div
        className="shrink-0 w-7 h-7 rounded flex items-center justify-center mt-0.5"
        style={{
          backgroundColor: isUser ? 'var(--bg-elevated)' : 'var(--bg-elevated)',
          border: `1px solid ${isUser ? 'var(--border-strong)' : 'var(--border)'}`,
        }}
      >
        {isUser ? (
          <User size={13} style={{ color: 'var(--text-tertiary)' }} />
        ) : (
          <Bot size={13} style={{ color: 'var(--accent)' }} />
        )}
      </div>

      {/* Bubble */}
      <div
        className="rounded px-3 py-2.5 text-sm"
        style={{
          backgroundColor: isUser ? 'var(--bg-elevated)' : 'var(--bg-surface)',
          border: `1px solid ${isUser ? 'var(--border-strong)' : 'var(--border)'}`,
          color: 'var(--text-primary)',
          maxWidth: '72%',
        }}
      >
        {isUser ? (
          <span className="token-stream" style={{ whiteSpace: 'pre-wrap' }}>
            {content}
          </span>
        ) : (
          <div className="prose-alfred">
            <MarkdownContent content={content} />
          </div>
        )}
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

// ---------------------------------------------------------------------------
// Result bubble — stage result output
// ---------------------------------------------------------------------------

function ResultBubble({ content }: { content: string }) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <div
        className="shrink-0 w-7 h-7 rounded flex items-center justify-center mt-0.5"
        style={{ backgroundColor: 'rgba(52,211,153,0.10)', border: '1px solid rgba(52,211,153,0.25)' }}
      >
        <CheckCircle2 size={13} style={{ color: 'var(--running)' }} />
      </div>
      <div
        className="rounded px-3 py-2.5 text-sm flex-1"
        style={{
          backgroundColor: 'rgba(52,211,153,0.07)',
          border: '1px solid rgba(52,211,153,0.2)',
          color: 'var(--text-primary)',
        }}
      >
        <MarkdownContent content={content} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Error bubble
// ---------------------------------------------------------------------------

function ErrorBubble({ content }: { content: string }) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <div
        className="shrink-0 w-7 h-7 rounded flex items-center justify-center mt-0.5"
        style={{ backgroundColor: 'rgba(239,68,68,0.10)', border: '1px solid rgba(239,68,68,0.25)' }}
      >
        <XCircle size={13} style={{ color: 'var(--danger)' }} />
      </div>
      <div
        className="rounded px-3 py-2.5 text-sm flex-1"
        style={{
          backgroundColor: 'rgba(239,68,68,0.07)',
          border: '1px solid rgba(239,68,68,0.2)',
          color: 'var(--danger)',
        }}
      >
        <span className="font-medium text-xs font-mono block mb-1" style={{ color: 'var(--danger)' }}>
          Error
        </span>
        <span style={{ color: 'var(--text-secondary)' }}>{content}</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Persisted message dispatcher
// ---------------------------------------------------------------------------

function PersistedMessageRow({ message }: { message: PersistedMessage }) {
  switch (message.kind) {
    case 'chat':
      return (
        <ChatBubble
          role={message.role}
          content={message.content}
        />
      )
    case 'thinking':
      return (
        <div className="px-4 py-2">
          <ThinkingTab
            content={message.content}
            isStreaming={false}
            title="Thinking"
          />
        </div>
      )
    case 'result':
      return <ResultBubble content={message.content} />
    case 'error':
      return <ErrorBubble content={message.content} />
    case 'plan':
      // Plan cards in history are shown as result bubbles (approval already resolved)
      return <ResultBubble content={`**Plan approved**\n\n${message.content}`} />
    default:
      return null
  }
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

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
          Select or create a project, then describe your research hypothesis.
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ChatThread
// ---------------------------------------------------------------------------

export function ChatThread() {
  const persistedMessages = useStore((s) => s.persistedMessages)
  const streamingMessages = useStore((s) => s.streamingMessages)
  const logEntries = useStore((s) => s.logEntries)
  const approvalRequest = useStore((s) => s.approvalRequest)
  const activeProjectId = useStore((s) => s.activeProjectId)
  const bottomRef = useRef<HTMLDivElement>(null)

  const streamEntries = Object.values(streamingMessages)
  const logValues = Object.values(logEntries)
  const hasContent =
    persistedMessages.length > 0 ||
    streamEntries.length > 0 ||
    logValues.length > 0 ||
    approvalRequest !== null

  // Auto-scroll to bottom on any new content
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [persistedMessages, streamingMessages, logEntries, approvalRequest])

  if (!activeProjectId) {
    return (
      <div
        className="flex-1 overflow-y-auto flex flex-col"
        style={{ backgroundColor: 'var(--bg-base)' }}
      >
        <EmptyState />
      </div>
    )
  }

  return (
    <div
      className="flex-1 overflow-y-auto flex flex-col"
      style={{ backgroundColor: 'var(--bg-base)' }}
    >
      {!hasContent ? (
        <EmptyState />
      ) : (
        <div className="flex flex-col py-2 min-h-full">
          {/* Persisted messages from DB */}
          {persistedMessages.map((msg) => (
            <PersistedMessageRow key={msg.id} message={msg} />
          ))}

          {/* Live thinking / log entries */}
          {logValues.map((entry) => {
            if (entry.kind === 'thinking') {
              return (
                <div key={entry.messageId} className="px-4 py-2">
                  <ThinkingTab
                    content={entry.content}
                    isStreaming={entry.isStreaming}
                    title="Thinking"
                    defaultExpanded
                  />
                </div>
              )
            }
            if (entry.kind === 'tool_call') {
              return <ToolCallEntry key={entry.messageId} entry={entry} />
            }
            // Generic log entry
            return (
              <div key={entry.messageId} className="px-4 py-1">
                <div
                  className="text-xs font-mono px-3 py-1.5 rounded border"
                  style={{
                    backgroundColor: 'var(--bg-inset)',
                    borderColor: 'var(--border)',
                    color: 'var(--text-tertiary)',
                    whiteSpace: 'pre-wrap',
                  }}
                >
                  {entry.content}
                </div>
              </div>
            )
          })}

          {/* Live streaming chat messages */}
          {streamEntries.map((msg) => {
            if (msg.kind === 'thinking') {
              return (
                <div key={msg.messageId} className="px-4 py-2">
                  <ThinkingTab
                    content={msg.content}
                    isStreaming={msg.isStreaming}
                    title="Thinking"
                    defaultExpanded
                  />
                </div>
              )
            }
            return (
              <ChatBubble
                key={msg.messageId}
                role="assistant"
                content={msg.content}
                isStreaming={msg.isStreaming}
                messageId={msg.messageId}
              />
            )
          })}

          {/* Approval card — shown when machine blocks at awaiting_approval */}
          {approvalRequest !== null && (
            <div className="px-4 py-3">
              <ApprovalCard request={approvalRequest} />
            </div>
          )}
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}