/**
 * MainPanel — the right-hand content area.
 *
 * Stack (top → bottom):
 *   ProgressStrip   always visible
 *   ChatThread      scrolling message area
 *   ChatBar         fixed bottom input
 *
 * Note: The panel drawer (Find models / Memory / Tools) is rendered by
 * Sidebar.tsx as a sibling of the sidebar rail, inside the root flex row.
 * MainPanel has no panel logic of its own.
 */

import { ProgressStrip } from './ProgressStrip'
import { ChatThread } from '../chat/ChatThread'
import { ChatBar } from '../chat/ChatBar'

export function MainPanel() {
  return (
    <div className="flex flex-col flex-1 min-w-0 h-full">
      {/* Always-visible progress strip */}
      <ProgressStrip />

      {/* Chat thread + input */}
      <div className="flex flex-col flex-1 min-h-0">
        <ChatThread />
        <ChatBar />
      </div>
    </div>
  )
}