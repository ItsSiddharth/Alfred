/**
 * MainPanel — the right-hand content area.
 *
 * Stack (top → bottom):
 *   ProgressStrip   always visible
 *   SidebarPanel    (shown when a nav item is active) — overlays the thread
 *   ChatThread      scrolling message area
 *   ChatBar         fixed bottom input
 */

import { ProgressStrip } from './ProgressStrip'
import { PanelPlaceholder } from './PanelPlaceholder'
import { ChatThread } from '../chat/ChatThread'
import { ChatBar } from '../chat/ChatBar'
import { useStore } from '../../store'
import { X } from 'lucide-react'

export function MainPanel() {
  const { sidebarPanel, setSidebarPanel } = useStore()

  return (
    <div className="flex flex-col flex-1 min-w-0 h-full">
      {/* Always-visible progress strip */}
      <ProgressStrip />

      {/* Inline panel drawer (slides in from the left of main) */}
      {sidebarPanel !== null && (
        <div
          className="absolute top-0 left-[280px] right-0 z-10 flex"
          style={{ height: '100%', pointerEvents: 'none' }}
        >
          <div
            className="relative flex flex-col"
            style={{
              width: '320px',
              backgroundColor: 'var(--bg-surface)',
              borderRight: '1px solid var(--border)',
              borderLeft: '1px solid var(--border)',
              pointerEvents: 'all',
              height: '100%',
              overflowY: 'auto',
            }}
          >
            <button
              onClick={() => setSidebarPanel(null)}
              className="absolute top-3 right-3 p-1 rounded transition-colors"
              style={{ color: 'var(--text-tertiary)' }}
              title="Close panel"
            >
              <X size={14} />
            </button>
            <PanelPlaceholder panel={sidebarPanel} />
          </div>
        </div>
      )}

      {/* Chat thread + input */}
      <div className="flex flex-col flex-1 min-h-0 relative">
        <ChatThread />
        <ChatBar />
      </div>
    </div>
  )
}