/**
 * ChatThread — Stage 4 (bug-fix pass 2).
 *
 * Fixes in this version:
 *
 * BUG 1 — Streaming bubble collapses to 2 characters:
 *   Root cause: react-markdown parses tokens incrementally. Each 1-2 char
 *   token creates a tiny <p> element, the flexbox bubble shrinks to fit it,
 *   and subsequent tokens expand the text but the bubble width stays locked
 *   to its smallest render. Fix: render streaming content as plain pre-wrap
 *   text (fast, no parsing, auto-expands). Switch to MarkdownContent only
 *   once isStreaming is false. Also add minWidth to the bubble.
 *
 * BUG 2 — Show Work does nothing:
 *   Root cause: ShowWorkMeta reads metadata_json from the persistedMessage
 *   object, but the placeholder added by msg_start always has '{}'. The
 *   final metadata (model, memory_tokens, memory_block) is written to the
 *   DB by the backend after streaming, but never pushed to the frontend.
 *   Fix: on the 'done' WS event, fetch the message row from the REST API
 *   and update persistedMessages with the real metadata. ShowWorkMeta then
 *   has real data to display.
 *
 * BUG 3 — Approve button appears to do nothing:
 *   Root cause: experiment_id in the approval plan is nested inside
 *   plan.experiment_id AND hoisted to payload.experiment_id by the backend,
 *   but the ApprovalCard uses request.experiment_id ?? 0 — if it's undefined
 *   the API call goes to /experiments/0/approve which 404s silently.
 *   Fix: show a visible error on approve failure instead of swallowing it,
 *   and also read experiment_id from plan if it's missing at the top level.
 *   Additionally, the approval for the DEMO pipeline IS working server-side
 *   (it unblocks the machine), but the UI doesn't give feedback. Add a
 *   "Approved — pipeline continuing" state to the card.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
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
  Wrench,
  Brain,
  AlertTriangle,
  Search,
  Globe,
  BookOpen,
  FileText,
  GitBranch,
  Pencil,
  FlaskConical,
  Activity,
} from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useStore, type PersistedMessage, type ToolCallEvent } from '../../store'
import { experimentsApi, projectsApi } from '../../api/client'
import type { ApprovalRequest } from '../../store'
import { PlotsRow } from './PlotView'
import 'highlight.js/styles/github-dark.css'

// ---------------------------------------------------------------------------
// Markdown renderer (used only when NOT streaming)
// ---------------------------------------------------------------------------

function MarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        code({ className, children, ...props }) {
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
        p: ({ children }) => (
          <p className="mb-2 last:mb-0" style={{ color: 'var(--text-primary)' }}>
            {children}
          </p>
        ),
        ul: ({ children }) => (
          <ul className="list-disc list-inside mb-2 space-y-0.5" style={{ color: 'var(--text-primary)' }}>
            {children}
          </ul>
        ),
        ol: ({ children }) => (
          <ol className="list-decimal list-inside mb-2 space-y-0.5" style={{ color: 'var(--text-primary)' }}>
            {children}
          </ol>
        ),
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 pl-3 my-2 italic"
            style={{ borderColor: 'var(--accent)', color: 'var(--text-secondary)' }}>
            {children}
          </blockquote>
        ),
        h1: ({ children }) => (
          <h1 className="text-base font-medium mb-2 mt-3 first:mt-0" style={{ color: 'var(--text-primary)' }}>
            {children}
          </h1>
        ),
        h2: ({ children }) => (
          <h2 className="text-sm font-medium mb-1.5 mt-3 first:mt-0" style={{ color: 'var(--text-primary)' }}>
            {children}
          </h2>
        ),
        h3: ({ children }) => (
          <h3 className="text-sm font-medium mb-1 mt-2 first:mt-0" style={{ color: 'var(--text-secondary)' }}>
            {children}
          </h3>
        ),
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noopener noreferrer"
            style={{ color: 'var(--accent)', textDecoration: 'underline' }}>
            {children}
          </a>
        ),
        strong: ({ children }) => (
          <strong style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
            {children}
          </strong>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

// ---------------------------------------------------------------------------
// ThinkingTab
// ---------------------------------------------------------------------------

interface ThinkingTabProps {
  content: string
  isStreaming: boolean
  title?: string
  defaultExpanded?: boolean
}

export function ThinkingTab({ content, isStreaming, title = 'Thinking', defaultExpanded = false }: ThinkingTabProps) {
  const showWorkMode = useStore((s) => s.showWorkMode)
  const [expanded, setExpanded] = useState(defaultExpanded || showWorkMode)

  useEffect(() => { if (showWorkMode) setExpanded(true) }, [showWorkMode])
  useEffect(() => { if (!isStreaming && !showWorkMode) setExpanded(false) }, [isStreaming, showWorkMode])

  return (
    <div className="rounded border overflow-hidden"
      style={{ backgroundColor: 'var(--bg-inset)', borderColor: 'var(--border)' }}>
      <button onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left"
        style={{ backgroundColor: expanded ? 'var(--bg-elevated)' : 'transparent' }}>
        <span className={`shrink-0 w-1.5 h-1.5 rounded-full ${isStreaming ? 'pulse-dot' : ''}`}
          style={{ backgroundColor: isStreaming ? 'var(--info)' : 'var(--text-tertiary)' }} />
        <span className="flex-1 text-xs font-mono font-medium"
          style={{ color: isStreaming ? 'var(--info)' : 'var(--text-tertiary)' }}>
          {title}
          {isStreaming && <span className="ml-1 font-normal" style={{ color: 'var(--text-tertiary)' }}>— streaming…</span>}
        </span>
        <span style={{ color: 'var(--text-tertiary)' }}>
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      </button>
      {expanded && (
        <div className="px-3 py-2.5 text-xs font-mono border-t token-stream"
          style={{
            borderColor: 'var(--border)', color: 'var(--text-secondary)',
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            maxHeight: '320px', overflowY: 'auto', lineHeight: '1.7',
          }}>
          {content}
          {isStreaming && (
            <span className="inline-block w-1 h-3 ml-0.5 rounded-sm pulse-dot align-middle"
              style={{ backgroundColor: 'var(--info)' }} />
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ShowWorkMeta — per-message LLM context (model + memory injected)
// ---------------------------------------------------------------------------

function ShowWorkMeta({ metadataJson }: { metadataJson: string }) {
  const showWorkMode = useStore((s) => s.showWorkMode)
  const [expanded, setExpanded] = useState(false)

  if (!showWorkMode) return null

  let meta: Record<string, unknown> = {}
  try { meta = JSON.parse(metadataJson) } catch { return null }

  const memoryTokens = meta.memory_tokens as number | undefined
  const model = meta.model as string | undefined
  const memoryBlock = meta.memory_block as string | undefined

  if (!memoryTokens && !model && !memoryBlock) return null

  return (
    <div className="mt-1 ml-10 mr-4">
      <button onClick={() => setExpanded(e => !e)}
        className="flex items-center gap-1.5 text-xs font-mono"
        style={{ color: 'var(--text-tertiary)' }}>
        <Brain size={10} style={{ color: 'var(--info)' }} />
        {model && <span style={{ color: 'var(--text-secondary)' }}>{model}</span>}
        {memoryTokens != null && memoryTokens > 0 && (
          <span style={{ color: 'var(--info)' }}>· {memoryTokens} memory tokens</span>
        )}
        {memoryBlock && (
          <span className="ml-1" style={{ color: 'var(--text-tertiary)' }}>
            {expanded ? <ChevronDown size={9} /> : <ChevronRight size={9} />}
          </span>
        )}
      </button>
      {expanded && memoryBlock && (
        <div className="mt-1 p-2 rounded text-xs font-mono"
          style={{
            backgroundColor: 'var(--bg-inset)', border: '1px solid var(--border)',
            color: 'var(--text-tertiary)', maxHeight: '160px', overflowY: 'auto',
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          }}>
          {memoryBlock}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ToolCallCard — inline Show Work card for a single tool invocation
// ---------------------------------------------------------------------------

function toolIcon(name: string) {
  if (name.includes('web') || name.includes('ddg')) return <Globe size={11} />
  if (name.includes('arxiv')) return <FileText size={11} />
  if (name.includes('semantic') || name.includes('scholar')) return <BookOpen size={11} />
  if (name.includes('openalex')) return <Search size={11} />
  return <Wrench size={11} />
}

function ToolCallCard({ event }: { event: ToolCallEvent }) {
  const [expanded, setExpanded] = useState(false)
  const query = event.input?.query as string | undefined
  const op = event.input?.op as string | undefined
  const paperId = event.input?.paper_id as string | undefined

  const statusColor =
    event.status === 'done' ? 'var(--running)' :
    event.status === 'error' ? 'var(--danger)' : 'var(--warn)'

  const label = op === 'expand'
    ? `${event.tool_name} expand · ${paperId?.slice(0, 12) ?? ''}`
    : query
    ? `${event.tool_name} · "${query.slice(0, 60)}${query.length > 60 ? '…' : ''}"`
    : event.tool_name

  return (
    <div className="rounded border overflow-hidden"
      style={{ backgroundColor: 'var(--bg-inset)', borderColor: 'var(--border)' }}>
      <button onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-left">
        <span style={{ color: statusColor }}>{toolIcon(event.tool_name)}</span>
        <span className="flex-1 text-xs font-mono truncate" style={{ color: 'var(--text-secondary)' }}>
          {label}
        </span>
        {event.result_count != null && (
          <span className="text-xs font-mono shrink-0" style={{ color: 'var(--text-tertiary)' }}>
            {event.result_count} results
          </span>
        )}
        {event.status === 'running' && (
          <span className="shrink-0 w-1.5 h-1.5 rounded-full pulse-dot" style={{ backgroundColor: 'var(--warn)' }} />
        )}
        {(query || paperId) && (
          <span style={{ color: 'var(--text-tertiary)' }}>
            {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
          </span>
        )}
      </button>
      {expanded && (
        <div className="px-3 pb-2 border-t text-xs font-mono space-y-1"
          style={{ borderColor: 'var(--border)', color: 'var(--text-tertiary)' }}>
          {query && (
            <div className="mt-1.5">
              <span style={{ color: 'var(--text-tertiary)' }}>query: </span>
              <span style={{ color: 'var(--text-secondary)' }}>{query}</span>
            </div>
          )}
          {event.sources && event.sources.length > 0 && (
            <div>
              <span style={{ color: 'var(--text-tertiary)' }}>sources:</span>
              {event.sources.map((s, i) => (
                <div key={i} className="ml-2 truncate">
                  <a href={s} target="_blank" rel="noopener noreferrer"
                    style={{ color: 'var(--accent)', textDecoration: 'underline' }}>
                    {s}
                  </a>
                </div>
              ))}
            </div>
          )}
          {event.error && (
            <div style={{ color: 'var(--danger)' }}>error: {event.error}</div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ShowWorkFeed — live tool call feed, visible only in Show Work mode
// ---------------------------------------------------------------------------

function ShowWorkFeed() {
  const showWorkMode = useStore((s) => s.showWorkMode)
  const toolCalls = useStore((s) => s.toolCalls)
  const [expanded, setExpanded] = useState(true)

  if (!showWorkMode || toolCalls.length === 0) return null

  // toolCalls is newest-first; display oldest-first
  const ordered = [...toolCalls].reverse()

  return (
    <div className="mx-4 my-2 rounded border overflow-hidden"
      style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)' }}>
      <button onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left border-b"
        style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-elevated)' }}>
        <Wrench size={11} style={{ color: 'var(--accent)' }} />
        <span className="flex-1 text-xs font-mono font-medium" style={{ color: 'var(--text-secondary)' }}>
          Research activity · {toolCalls.length} tool call{toolCalls.length !== 1 ? 's' : ''}
        </span>
        <span style={{ color: 'var(--text-tertiary)' }}>
          {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        </span>
      </button>
      {expanded && (
        <div className="p-2 space-y-1">
          {ordered.map((evt, i) => (
            <ToolCallCard key={i} event={evt} />
          ))}
        </div>
      )}
    </div>
  )
}


// ---------------------------------------------------------------------------
// WorkingIndicator — shows when ALFRED is active but not streaming tokens
// ---------------------------------------------------------------------------

function WorkingIndicator() {
  const progress = useStore((s) => s.progress)
  const streamingMsgId = useStore((s) => s.streamingMsgId)
  const logEntries = useStore((s) => s.logEntries)

  const isActive = progress.status === 'running' || progress.status === 'waiting'
  const isStreaming = streamingMsgId !== null
  const hasActiveLogs = Object.values(logEntries).some((e) => e.isStreaming)

  // Only show when the backend is working but nothing is visibly streaming
  if (!isActive || isStreaming || hasActiveLogs) return null

  const label = progress.label || progress.substage || 'Working…'

  return (
    <div className="flex items-start gap-3 px-4 py-2">
      <div
        className="shrink-0 w-7 h-7 rounded flex items-center justify-center mt-0.5"
        style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border)' }}
      >
        <Bot size={13} style={{ color: 'var(--accent)' }} />
      </div>
      <div
        className="rounded px-3 py-2 flex items-center gap-3"
        style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}
      >
        {/* Staggered pulsing dots */}
        <div className="flex gap-1">
          <span className="w-1.5 h-1.5 rounded-full"
            style={{ backgroundColor: 'var(--accent)', animation: 'pulse-dot 1.4s ease-in-out 0ms infinite' }} />
          <span className="w-1.5 h-1.5 rounded-full"
            style={{ backgroundColor: 'var(--accent)', animation: 'pulse-dot 1.4s ease-in-out 280ms infinite' }} />
          <span className="w-1.5 h-1.5 rounded-full"
            style={{ backgroundColor: 'var(--accent)', animation: 'pulse-dot 1.4s ease-in-out 560ms infinite' }} />
        </div>
        <span className="text-xs font-mono" style={{ color: 'var(--text-secondary)' }}>
          {label}
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ResearchLogFeed — collapsable live log entries (non-thinking)
// ---------------------------------------------------------------------------

function ResearchLogFeed() {
  const logEntries = useStore((s) => s.logEntries)
  const [expanded, setExpanded] = useState(true)
  const bodyRef = useRef<HTMLDivElement>(null)

  const logValues = Object.values(logEntries).filter((e) => e.kind === 'log')
  const isAnyStreaming = logValues.some((e) => e.isStreaming)

  // Auto-collapse 2s after streaming stops
  useEffect(() => {
    if (!isAnyStreaming && logValues.length > 0) {
      const t = setTimeout(() => setExpanded(false), 2000)
      return () => clearTimeout(t)
    }
  }, [isAnyStreaming]) // eslint-disable-line react-hooks/exhaustive-deps

  // Re-expand when streaming resumes
  useEffect(() => {
    if (isAnyStreaming) setExpanded(true)
  }, [isAnyStreaming])

  // Auto-scroll body on new lines
  useEffect(() => {
    if (expanded && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [logValues.length, expanded])

  if (logValues.length === 0) return null

  return (
    <div className="mx-4 my-1 rounded border overflow-hidden"
      style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-surface)' }}>
      <button onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-left border-b"
        style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-elevated)' }}>
        <Activity size={11} style={{ color: isAnyStreaming ? 'var(--info)' : 'var(--text-tertiary)' }} />
        <span className="flex-1 text-xs font-mono" style={{ color: 'var(--text-secondary)' }}>
          Research logs · {logValues.length} entr{logValues.length !== 1 ? 'ies' : 'y'}
          {isAnyStreaming && <span className="ml-1" style={{ color: 'var(--info)' }}>— live</span>}
        </span>
        <span style={{ color: 'var(--text-tertiary)' }}>
          {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        </span>
      </button>
      {expanded && (
        <div ref={bodyRef} className="max-h-52 overflow-y-auto px-3 py-2 space-y-0.5">
          {logValues.map((entry) => (
            <div key={entry.messageId} className="flex items-start gap-2 text-xs font-mono py-0.5">
              <span className="shrink-0 mt-0.5" style={{ color: 'var(--accent)' }}>›</span>
              <span style={{ color: 'var(--text-tertiary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {entry.content}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ChatBubble — Fix 1: plain text during streaming, markdown after
// ---------------------------------------------------------------------------

interface ChatBubbleProps {
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  isStreaming?: boolean
  metadataJson?: string
}

function ChatBubble({ role, content, isStreaming = false, metadataJson = '{}' }: ChatBubbleProps) {
  const isUser = role === 'user'

  return (
    <div className="py-1">
      <div className={`flex items-start gap-3 px-4 py-2 ${isUser ? 'flex-row-reverse' : ''}`}
        style={{ minWidth: 0 }}>
        {/* Avatar */}
        <div className="shrink-0 w-7 h-7 rounded flex items-center justify-center mt-0.5"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            border: `1px solid ${isUser ? 'var(--border-strong)' : 'var(--border)'}`,
          }}>
          {isUser
            ? <User size={13} style={{ color: 'var(--text-tertiary)' }} />
            : <Bot size={13} style={{ color: 'var(--accent)' }} />}
        </div>

        {/* Bubble
            Fix 1: minWidth ensures the box never collapses below a readable size.
            During streaming we use plain pre-wrap text so the element width
            tracks the longest line of text naturally as tokens arrive.
            After streaming we switch to MarkdownContent for proper rendering.
        */}
        <div className="rounded px-3 py-2.5 text-sm"
          style={{
            backgroundColor: isUser ? 'var(--bg-elevated)' : 'var(--bg-surface)',
            border: `1px solid ${isUser ? 'var(--border-strong)' : 'var(--border)'}`,
            color: 'var(--text-primary)',
            // Assistant bubbles: fill available width up to 72% so streaming
            // text expands naturally. User bubbles: shrink to content (right-aligned).
            width: isUser ? 'auto' : '100%',
            maxWidth: '72%',
          }}>
          {isUser ? (
            <span className="token-stream" style={{ whiteSpace: 'pre-wrap' }}>
              {content}
            </span>
          ) : isStreaming ? (
            // Fix 1: plain text while streaming — no react-markdown parsing overhead
            // and no width-locking from incremental DOM updates
            <span
              className="token-stream"
              style={{
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                display: 'block',
                lineHeight: '1.6',
              }}
            >
              {content || ''}
              <span
                className="inline-block w-1.5 h-3.5 ml-0.5 rounded-sm pulse-dot align-middle"
                style={{ backgroundColor: 'var(--accent)' }}
              />
            </span>
          ) : (
            // Markdown rendering only once stream is complete
            <MarkdownContent content={content} />
          )}
        </div>
      </div>

      {/* Show Work metadata — Fix 2: renders once metadata_json is populated */}
      {!isUser && <ShowWorkMeta metadataJson={metadataJson} />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Result / Error bubbles
// ---------------------------------------------------------------------------

function ResultBubble({ content }: { content: string }) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <div className="shrink-0 w-7 h-7 rounded flex items-center justify-center mt-0.5"
        style={{ backgroundColor: 'rgba(52,211,153,0.10)', border: '1px solid rgba(52,211,153,0.25)' }}>
        <CheckCircle2 size={13} style={{ color: 'var(--running)' }} />
      </div>
      <div className="rounded px-3 py-2.5 text-sm flex-1"
        style={{ backgroundColor: 'rgba(52,211,153,0.07)', border: '1px solid rgba(52,211,153,0.2)', color: 'var(--text-primary)' }}>
        <MarkdownContent content={content} />
      </div>
    </div>
  )
}

function ErrorBubble({ content }: { content: string }) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <div className="shrink-0 w-7 h-7 rounded flex items-center justify-center mt-0.5"
        style={{ backgroundColor: 'rgba(239,68,68,0.10)', border: '1px solid rgba(239,68,68,0.25)' }}>
        <XCircle size={13} style={{ color: 'var(--danger)' }} />
      </div>
      <div className="rounded px-3 py-2.5 text-sm flex-1"
        style={{ backgroundColor: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--danger)' }}>
        <span className="font-medium text-xs font-mono block mb-1">Error</span>
        <span style={{ color: 'var(--text-secondary)' }}>{content}</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// NextIterCard — plan body for kind === "next_iteration"
// ---------------------------------------------------------------------------

function NextIterCard({
  plan,
  versionMode,
  onVersionModeChange,
}: {
  plan: Record<string, unknown>
  versionMode: 'modify' | 'branch'
  onVersionModeChange: (vm: 'modify' | 'branch') => void
}) {
  const changes = plan.changes as string[] | undefined
  const rationale = plan.rationale as string | undefined
  const iteration = plan.iteration as number | undefined

  return (
    <div className="space-y-2">
      {/* Iteration badge + subtitle */}
      <div className="flex items-center gap-2">
        <span
          className="text-xs font-mono px-1.5 py-0.5 rounded"
          style={{ backgroundColor: 'var(--bg-inset)', color: 'var(--accent)', border: '1px solid var(--border)' }}
        >
          iter {iteration}
        </span>
        <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
          Proposed changes from previous run
        </span>
      </div>

      {/* Changes list */}
      {changes && changes.length > 0 && (
        <div
          className="px-3 py-2 rounded border text-xs space-y-1"
          style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-inset)' }}
        >
          {changes.map((c, i) => (
            <div key={i} className="flex gap-1.5">
              <span style={{ color: 'var(--running)' }}>+</span>
              <span style={{ color: 'var(--text-secondary)' }}>{c}</span>
            </div>
          ))}
        </div>
      )}

      {/* Rationale */}
      {rationale && (
        <div
          className="text-xs px-3 py-2 rounded border italic"
          style={{
            borderColor: 'var(--border)',
            backgroundColor: 'var(--bg-inset)',
            color: 'var(--text-tertiary)',
            lineHeight: '1.6',
          }}
        >
          {rationale}
        </div>
      )}

      {/* Version mode selector */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>
          version mode
        </span>
        <button
          onClick={() => onVersionModeChange('modify')}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono transition-colors"
          style={{
            backgroundColor: versionMode === 'modify' ? 'var(--bg-elevated)' : 'transparent',
            color: versionMode === 'modify' ? 'var(--accent)' : 'var(--text-tertiary)',
            border: `1px solid ${versionMode === 'modify' ? 'var(--accent)' : 'var(--border)'}`,
          }}
        >
          <Pencil size={9} />
          modify
        </button>
        <button
          onClick={() => onVersionModeChange('branch')}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono transition-colors"
          style={{
            backgroundColor: versionMode === 'branch' ? 'var(--bg-elevated)' : 'transparent',
            color: versionMode === 'branch' ? 'var(--accent)' : 'var(--text-tertiary)',
            border: `1px solid ${versionMode === 'branch' ? 'var(--accent)' : 'var(--border)'}`,
          }}
        >
          <GitBranch size={9} />
          branch
        </button>
        <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
          {versionMode === 'branch' ? '— new subfolder' : '— edit in place'}
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// InlineApprovalCard — Fix 3: visible feedback on approve/reject
//                      7.5 addition: next_iteration plan rendering + version mode
// ---------------------------------------------------------------------------

function InlineApprovalCard({ request }: { request: ApprovalRequest }) {
  const activeProjectId = useStore((s) => s.activeProjectId)
  const setApprovalRequest = useStore((s) => s.setApprovalRequest)
  const [feedback, setFeedback] = useState('')
  const [rejectMode, setRejectMode] = useState(false)
  const [approvedState, setApprovedState] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const plan = request.plan
  const isNextIter = plan.kind === 'next_iteration'

  // Version mode state for next_iteration cards (user can toggle before approving)
  const [versionMode, setVersionMode] = useState<'modify' | 'branch'>(
    (plan.version_mode as string) === 'branch' ? 'branch' : 'modify'
  )

  // Fix 3: read experiment_id from top-level OR from inside plan
  const expId = request.experiment_id ?? (request.plan?.experiment_id as number | undefined) ?? 0

  const approveMutation = useMutation({
    mutationFn: () => {
      if (!activeProjectId) throw new Error('No active project')
      if (expId === 0) throw new Error('No experiment ID — is the demo pipeline running?')
      // For next_iteration cards, pass the (possibly updated) version_mode back
      const editedPlan = isNextIter ? { version_mode: versionMode } : undefined
      return experimentsApi.approve(activeProjectId, expId, editedPlan)
    },
    onSuccess: () => {
      setApprovedState(true)
      setErrorMsg(null)
      // Clear the approval gate after a short delay so user sees feedback
      setTimeout(() => setApprovalRequest(null), 1500)
    },
    onError: (err: Error) => {
      setErrorMsg(err.message)
    },
  })

  const rejectMutation = useMutation({
    mutationFn: () => {
      if (!activeProjectId) throw new Error('No active project')
      if (expId === 0) throw new Error('No experiment ID')
      return experimentsApi.reject(activeProjectId, expId, feedback)
    },
    onSuccess: () => {
      setApprovalRequest(null)
    },
    onError: (err: Error) => {
      setErrorMsg(err.message)
    },
  })

  const isAutoApprove = request.auto_approve

  // Scores from plan (hypothesis validator)
  const hasScores = 'novelty_score' in plan || 'gap_score' in plan
  const scores: Array<{ label: string; value: number }> = hasScores ? [
    { label: 'Novelty', value: (plan.novelty_score as number) ?? 0 },
    { label: 'Gap', value: (plan.gap_score as number) ?? 0 },
    { label: 'Publishability', value: (plan.publishability_score as number) ?? 0 },
  ] : []

  // Fields to suppress from generic key-value display
  const HIDDEN_KEYS = new Set([
    'experiment_id', 'auto_approve', 'stage', 'substage',
    'kind', 'iteration', 'changes', 'rationale', 'version_mode',
  ])

  if (approvedState) {
    return (
      <div className="rounded border px-4 py-3 flex items-center gap-2"
        style={{ backgroundColor: 'rgba(52,211,153,0.07)', borderColor: 'rgba(52,211,153,0.3)' }}>
        <CheckCircle2 size={14} style={{ color: 'var(--running)' }} />
        <span className="text-sm" style={{ color: 'var(--running)' }}>
          {isNextIter ? 'Approved — generating next iteration…' : 'Approved — pipeline continuing…'}
        </span>
      </div>
    )
  }

  return (
    <div className="rounded border overflow-hidden"
      style={{
        backgroundColor: 'var(--bg-surface)',
        borderColor: isAutoApprove ? 'rgba(245,158,11,0.4)' : 'var(--border-strong)',
      }}>
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b"
        style={{
          borderColor: 'var(--border)',
          backgroundColor: isAutoApprove ? 'rgba(245,158,11,0.06)' : 'var(--bg-elevated)',
        }}>
        {isNextIter
          ? <GitBranch size={13} style={{ color: 'var(--accent)' }} />
          : <AlertTriangle size={13} style={{ color: isAutoApprove ? 'var(--warn)' : 'var(--accent)' }} />
        }
        <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
          {isNextIter
            ? `Iteration ${plan.iteration as number} proposal`
            : isAutoApprove ? 'Auto-approved' : 'Review & approve'
          }
        </span>
        <span className="text-xs font-mono px-1.5 py-0.5 rounded ml-auto"
          style={{ backgroundColor: 'var(--bg-inset)', color: 'var(--text-tertiary)', border: '1px solid var(--border)' }}>
          {isNextIter
            ? `awaiting_next`
            : `Stage ${request.stage} · ${request.substage.replace(/_/g, ' ')}`
          }
        </span>
      </div>

      {/* Plan content */}
      <div className="p-4 space-y-2">
        {isNextIter ? (
          <NextIterCard
            plan={plan}
            versionMode={versionMode}
            onVersionModeChange={setVersionMode}
          />
        ) : (
          <>
            {/* Score meters */}
            {scores.map(({ label, value }) => {
              if (value == null) return null
              const color = value >= 65 ? 'var(--running)' : value >= 40 ? 'var(--warn)' : 'var(--danger)'
              return (
                <div key={label} className="flex items-center gap-3">
                  <span className="text-xs font-mono w-24 shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                    {label}
                  </span>
                  <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--bg-elevated)' }}>
                    <div className="h-full rounded-full" style={{ width: `${value}%`, backgroundColor: color }} />
                  </div>
                  <span className="text-sm font-mono w-8 text-right shrink-0" style={{ color }}>{value}</span>
                </div>
              )
            })}

            {/* Rationale */}
            {(plan.rationale as string | undefined) && (
              <div className="text-xs px-3 py-2 rounded border"
                style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-inset)', color: 'var(--text-secondary)', lineHeight: '1.6' }}>
                {plan.rationale as string}
              </div>
            )}

            {/* Generic key-value for non-scorecard plans */}
            {!hasScores && Object.entries(plan)
              .filter(([k]) => !HIDDEN_KEYS.has(k))
              .map(([key, val]) => (
                <div key={key} className="flex gap-3 px-3 py-2 rounded border text-xs"
                  style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-inset)' }}>
                  <span className="font-mono w-32 shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                    {key.replace(/_/g, ' ')}
                  </span>
                  <span className="font-mono flex-1 break-words" style={{ color: 'var(--text-primary)' }}>
                    {typeof val === 'object' ? JSON.stringify(val) : String(val ?? '')}
                  </span>
                </div>
              ))}
          </>
        )}
      </div>

      {/* Error message */}
      {errorMsg && (
        <div className="px-4 pb-2 text-xs" style={{ color: 'var(--danger)' }}>
          ⚠ {errorMsg}
        </div>
      )}

      {/* Reject feedback */}
      {rejectMode && (
        <div className="px-4 pb-3 border-t" style={{ borderColor: 'var(--border)' }}>
          <label className="block text-xs font-mono mb-1.5 mt-3" style={{ color: 'var(--text-tertiary)' }}>
            Feedback for ALFRED (optional)
          </label>
          <textarea rows={2} placeholder="What should be changed?"
            value={feedback} onChange={e => setFeedback(e.target.value)}
            className="w-full px-2.5 py-1.5 rounded text-xs font-mono resize-none outline-none"
            style={{
              backgroundColor: 'var(--bg-inset)', border: '1px solid var(--border-strong)',
              color: 'var(--text-primary)',
            }} />
        </div>
      )}

      {/* Action buttons — hidden when auto-approved */}
      {!isAutoApprove && (
        <div className="flex items-center gap-2 px-4 py-3 border-t"
          style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-elevated)' }}>
          {!rejectMode ? (
            <>
              <button
                onClick={() => approveMutation.mutate()}
                disabled={approveMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium disabled:opacity-40 transition-colors"
                style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-base)', border: '1px solid var(--accent)' }}>
                <CheckCircle2 size={12} />
                {approveMutation.isPending ? 'Approving…' : isNextIter ? 'Run next iteration' : 'Approve'}
              </button>
              <button
                onClick={() => setRejectMode(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"
                style={{ backgroundColor: 'transparent', color: 'var(--danger)', border: '1px solid var(--danger)' }}>
                <XCircle size={12} />
                {isNextIter ? 'Stop here' : 'Reject'}
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => rejectMutation.mutate()}
                disabled={rejectMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium disabled:opacity-40"
                style={{ backgroundColor: 'transparent', color: 'var(--danger)', border: '1px solid var(--danger)' }}>
                <XCircle size={12} />
                {rejectMutation.isPending ? 'Sending…' : 'Send feedback'}
              </button>
              <button
                onClick={() => { setRejectMode(false); setFeedback('') }}
                className="px-3 py-1.5 rounded text-sm"
                style={{ color: 'var(--text-tertiary)', border: '1px solid var(--border)' }}>
                Cancel
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Persisted message dispatcher
// ---------------------------------------------------------------------------

function PersistedMessageRow({ message, isStreaming }: { message: PersistedMessage; isStreaming: boolean }) {
  switch (message.kind) {
    case 'chat':
      return (
        <ChatBubble
          role={message.role}
          content={message.content}
          isStreaming={isStreaming}
          metadataJson={message.metadata_json}
        />
      )
    case 'thinking':
      return (
        <div className="px-4 py-2">
          <ThinkingTab content={message.content} isStreaming={false} />
        </div>
      )
    case 'result':
      return <ResultBubble content={message.content} />
    case 'error':
      return <ErrorBubble content={message.content} />
    case 'plan':
      return <ResultBubble content={`**Plan approved**\n\n${message.content}`} />
    default:
      return null
  }
}

// ---------------------------------------------------------------------------
// Skip hypothesis action — shown when project is in hypothesis stage with no messages
// ---------------------------------------------------------------------------

function SkipHypothesisAction() {
  const activeProjectId = useStore((s) => s.activeProjectId)
  const setActiveProjectStage = useStore((s) => s.setActiveProjectStage)
  const queryClient = useQueryClient()
  const [done, setDone] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () => projectsApi.skipHypothesis(activeProjectId!),
    onSuccess: () => {
      setDone(true)
      setActiveProjectStage('setup')
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: (e: Error) => setErr(e.message),
  })

  if (done) {
    return (
      <div className="flex items-center gap-2 px-4 py-2 rounded border text-xs"
        style={{ borderColor: 'rgba(52,211,153,0.3)', backgroundColor: 'rgba(52,211,153,0.07)', color: 'var(--running)' }}>
        <CheckCircle2 size={11} />
        Skipped to experiment design — describe what you'd like to build.
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center gap-2">
      <button
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        className="flex items-center gap-2 px-4 py-2 rounded border text-sm transition-colors disabled:opacity-40"
        style={{
          borderColor: 'var(--border)',
          backgroundColor: 'var(--bg-elevated)',
          color: 'var(--text-secondary)',
        }}
      >
        <FlaskConical size={13} style={{ color: 'var(--accent)' }} />
        {mutation.isPending ? 'Skipping…' : 'Skip research → Jump to experiment design'}
      </button>
      <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
        or send a message to start the literature review first
      </span>
      {err && <span className="text-xs" style={{ color: 'var(--danger)' }}>{err}</span>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  const activeProjectId = useStore((s) => s.activeProjectId)
  const activeProjectStage = useStore((s) => s.activeProjectStage)

  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 select-none">
      <div className="w-12 h-12 rounded-xl flex items-center justify-center"
        style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
        <Bot size={24} style={{ color: 'var(--accent)' }} />
      </div>
      <div className="text-center">
        <div className="font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
          ALFRED is ready
        </div>
        <div className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
          {activeProjectId
            ? 'Describe your research hypothesis to start the literature review.'
            : 'Select or create a project, then describe your research hypothesis.'}
        </div>
      </div>
      {/* Skip button — only shown when in hypothesis stage */}
      {activeProjectId && activeProjectStage === 'hypothesis' && (
        <SkipHypothesisAction />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ChatThread
// ---------------------------------------------------------------------------

export function ChatThread() {
  const persistedMessages = useStore((s) => s.persistedMessages)
  const streamingMessages = useStore((s) => s.streamingMessages)
  const streamingMsgId = useStore((s) => s.streamingMsgId)
  const logEntries = useStore((s) => s.logEntries)
  const approvalRequest = useStore((s) => s.approvalRequest)
  const activeProjectId = useStore((s) => s.activeProjectId)
  const activeProjectStage = useStore((s) => s.activeProjectStage)
  const activePlots = useStore((s) => s.activePlots)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const userScrolledUp = useRef(false)

  const persistedIds = new Set(persistedMessages.map((m) => m.id))
  const orphanStreamEntries = Object.values(streamingMessages).filter(
    (s) => !persistedIds.has(parseInt(s.messageId, 10))
  )
  const logValues = Object.values(logEntries)
  const thinkingEntries = logValues.filter((e) => e.kind === 'thinking')

  const hasContent =
    persistedMessages.length > 0 ||
    orphanStreamEntries.length > 0 ||
    logValues.length > 0 ||
    approvalRequest !== null

  // Track whether user has scrolled away from the bottom
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current
    if (!el) return
    userScrolledUp.current = el.scrollHeight - el.scrollTop - el.clientHeight > 80
  }, [])

  // Scroll to bottom on new content — instant to avoid competing smooth-scroll animations
  useEffect(() => {
    if (userScrolledUp.current) return
    const el = scrollContainerRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [persistedMessages, streamingMessages, logEntries, approvalRequest])

  if (!activeProjectId) {
    return (
      <div className="flex-1 overflow-y-auto flex flex-col" style={{ backgroundColor: 'var(--bg-base)' }}>
        <EmptyState />
      </div>
    )
  }

  return (
    <div
      ref={scrollContainerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto flex flex-col"
      style={{ backgroundColor: 'var(--bg-base)' }}
    >
      {!hasContent ? (
        <EmptyState />
      ) : (
        <div className="flex flex-col py-2 min-h-full">

          {/* Primary source of truth: persisted DB rows */}
          {persistedMessages.map((msg) => (
            <PersistedMessageRow
              key={`persisted-${msg.id}`}
              message={msg}
              isStreaming={msg.id === streamingMsgId}
            />
          ))}

          {/* Live thinking entries (collapsable tab, already styled) */}
          {thinkingEntries.map((entry) => (
            <div key={entry.messageId} className="px-4 py-2">
              <ThinkingTab content={entry.content} isStreaming={entry.isStreaming} title="Thinking" defaultExpanded />
            </div>
          ))}

          {/* Research log entries — grouped in a collapsable feed */}
          <ResearchLogFeed />

          {/* Show Work: inline tool call feed */}
          <ShowWorkFeed />

          {/* Orphan streams (no DB row yet — fallback only) */}
          {orphanStreamEntries.map((msg) => {
            if (msg.kind === 'thinking') {
              return (
                <div key={msg.messageId} className="px-4 py-2">
                  <ThinkingTab content={msg.content} isStreaming={msg.isStreaming} title="Thinking" defaultExpanded />
                </div>
              )
            }
            return (
              <ChatBubble key={msg.messageId} role="assistant" content={msg.content} isStreaming={msg.isStreaming} />
            )
          })}

          {/* Experiment plots — rendered below logs, before approval */}
          {activePlots.length > 0 && <PlotsRow plots={activePlots} />}

          {/* Approval card */}
          {approvalRequest !== null && (
            <div className="px-4 py-3">
              <InlineApprovalCard request={approvalRequest} />
            </div>
          )}

          {/* ALFRED working indicator — visible when backend is running but not streaming */}
          <WorkingIndicator />

          {/* Skip research — persists during active hypothesis stage even with messages */}
          {activeProjectStage === 'hypothesis' && approvalRequest === null && (
            <div className="px-4 pb-3 flex justify-center">
              <SkipHypothesisAction />
            </div>
          )}
        </div>
      )}
    </div>
  )
}