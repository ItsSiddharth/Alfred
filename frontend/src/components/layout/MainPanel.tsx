import { useEffect, useLayoutEffect, useRef } from 'react'
import { ChatThread } from '../chat/ChatThread'
import { ChatBar } from '../chat/ChatBar'
import { useStore } from '../../store'
import { Activity, X } from 'lucide-react'

// ---------------------------------------------------------------------------
// ShowWorkConsole — fixed-height live backend log panel
// Renders between the chat thread and chat bar when showWorkMode is on.
// Stays pinned to the bottom so the user always sees the latest activity.
// ---------------------------------------------------------------------------

const PHASE_COLORS: Record<string, string> = {
  train: 'var(--running)',
  eval: 'var(--accent)',
  preprocess: '#f59e0b',
  fix: '#f87171',
  error: '#f87171',
  generate: '#a78bfa',
  propose: 'var(--info)',
  run: 'var(--text-tertiary)',
  setup: '#f59e0b',
  log: 'var(--text-tertiary)',
}

function ShowWorkConsole() {
  const showWorkMode = useStore((s) => s.showWorkMode)
  const toggleShowWork = useStore((s) => s.toggleShowWork)
  const logEntries = useStore((s) => s.logEntries)
  const bodyRef = useRef<HTMLDivElement>(null)
  const pinnedToBottom = useRef(true)
  // Tracks the scrollTop WE set. If the actual scrollTop is significantly less,
  // the user scrolled up and we stop following. No event listener needed for this —
  // the mismatch is detected in useLayoutEffect before every paint.
  const expectedScrollTop = useRef(0)

  const entries = Object.values(logEntries)
    .filter((e) => e.kind === 'log')
    .slice(-200)

  const isLive = entries.some((e) => e.isStreaming)

  // Wheel: immediate unpin on upward scroll (fires before position changes).
  useEffect(() => {
    const el = bodyRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY < 0) pinnedToBottom.current = false
    }
    el.addEventListener('wheel', onWheel, { passive: true })
    return () => el.removeEventListener('wheel', onWheel)
  }, [showWorkMode])

  // Reset to pinned state whenever the console opens.
  useEffect(() => {
    if (showWorkMode) {
      pinnedToBottom.current = true
      expectedScrollTop.current = 0
    }
  }, [showWorkMode])

  // After every render, BEFORE paint:
  // • If pinned: compare actual vs expected scrollTop. If user moved it away,
  //   unpin. Otherwise scroll to bottom and record the new expected position.
  // • If unpinned: re-pin only if the user manually scrolled to the very bottom.
  useLayoutEffect(() => {
    const el = bodyRef.current
    if (!el) return

    if (pinnedToBottom.current) {
      if (el.scrollTop < expectedScrollTop.current - 10) {
        // User scrolled up from where we left it → stop following.
        pinnedToBottom.current = false
        return
      }
      el.scrollTop = el.scrollHeight
      expectedScrollTop.current = el.scrollTop
    } else {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight
      if (dist < 5) {
        pinnedToBottom.current = true
        el.scrollTop = el.scrollHeight
        expectedScrollTop.current = el.scrollTop
      }
    }
  })

  if (!showWorkMode) return null

  return (
    <div
      className="shrink-0 border-t flex flex-col"
      style={{
        borderColor: 'var(--border)',
        backgroundColor: '#070A0F',
        height: '260px',
      }}
    >
      {/* Console header bar */}
      <div
        className="flex items-center gap-2 px-3 py-1.5 shrink-0 border-b"
        style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-inset)' }}
      >
        <Activity
          size={11}
          style={{ color: isLive ? 'var(--running)' : 'var(--text-tertiary)' }}
          className={isLive ? 'pulse-dot' : ''}
        />
        <span className="text-xs font-mono font-medium flex-1" style={{ color: 'var(--text-secondary)' }}>
          Backend console
          {isLive && (
            <span className="ml-2 text-xs font-normal" style={{ color: 'var(--running)' }}>
              — live
            </span>
          )}
        </span>
        {entries.length > 0 && (
          <span className="text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>
            {entries.length} entries
          </span>
        )}
        <button
          onClick={toggleShowWork}
          className="p-1 rounded hover:opacity-70 transition-opacity"
          style={{ color: 'var(--text-tertiary)' }}
          title="Close backend console"
        >
          <X size={11} />
        </button>
      </div>

      {/* Console body */}
      <div
        ref={bodyRef}
        className="flex-1 overflow-y-auto px-3 py-2 font-mono text-xs"
        style={{ lineHeight: '1.6' }}
      >
        {entries.length === 0 ? (
          <div style={{ color: 'var(--text-tertiary)' }}>
            Waiting for backend activity…
          </div>
        ) : (
          entries.map((entry) => {
            const phaseColor = PHASE_COLORS[entry.phase] ?? 'var(--text-tertiary)'
            return (
              <div key={entry.messageId} className="flex items-start gap-2 mb-0.5">
                <span
                  className="shrink-0 opacity-60 text-right"
                  style={{ color: phaseColor, minWidth: '52px' }}
                >
                  {entry.phase}
                </span>
                <span
                  style={{
                    color:
                      entry.phase === 'fix' || entry.phase === 'error'
                        ? '#fca5a5'
                        : entry.phase === 'train'
                        ? '#6ee7b7'
                        : 'var(--text-secondary)',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                    flex: 1,
                  }}
                >
                  {entry.content}
                  {entry.isStreaming && (
                    <span
                      className="inline-block w-1.5 h-3 ml-0.5 rounded-sm align-middle pulse-dot"
                      style={{ backgroundColor: phaseColor }}
                    />
                  )}
                </span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

export function MainPanel() {
  return (
    <div className="flex flex-col flex-1 min-w-0 h-full overflow-hidden">
      <div className="flex flex-col flex-1 min-h-0">
        <ChatThread />
        <ShowWorkConsole />
        <ChatBar />
      </div>
    </div>
  )
}
