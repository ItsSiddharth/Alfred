/**
 * MainPanel — the right-hand content area.
 *
 * Stack (top → bottom):
 *   ProgressStrip   always visible, driven by state machine
 *   ChatThread      scrolling message area (persisted + streaming)
 *   ChatBar         fixed bottom input + show-work toggle
 *
 * The panel drawer (Find models / Memory / Tools) is rendered by
 * Sidebar.tsx as a sibling in the root flex row — MainPanel has no
 * panel logic of its own.
 */

import { ProgressStrip } from './ProgressStrip'
import { ChatThread } from '../chat/ChatThread'
import { ChatBar } from '../chat/ChatBar'

export function MainPanel() {
  return (
    <div className="flex flex-col flex-1 min-w-0 h-full overflow-hidden">
      {/* Always-visible progress strip */}
      <ProgressStrip />

      {/* Chat area fills remaining space */}
      <div className="flex flex-col flex-1 min-h-0">
        <ChatThread />
        <ChatBar />
      </div>
    </div>
  )
}