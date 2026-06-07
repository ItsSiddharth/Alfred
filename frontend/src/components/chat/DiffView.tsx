/**
 * DiffView — renders a unified diff string with syntax highlighting.
 *
 * + lines: green   - lines: red   @@ lines: accent   headers: dim
 * Large diffs collapse to maxLines with a "show all" toggle.
 */

import { useState } from 'react'

interface DiffViewProps {
  diff: string
  maxLines?: number
}

function lineStyle(line: string): { color: string; bg: string } {
  if (line.startsWith('+') && !line.startsWith('+++'))
    return { color: 'var(--running)', bg: 'rgba(34,197,94,0.07)' }
  if (line.startsWith('-') && !line.startsWith('---'))
    return { color: 'var(--danger)', bg: 'rgba(239,68,68,0.07)' }
  if (line.startsWith('@@'))
    return { color: 'var(--accent)', bg: 'rgba(99,102,241,0.06)' }
  return { color: 'var(--text-tertiary)', bg: 'transparent' }
}

export function DiffView({ diff, maxLines = 60 }: DiffViewProps) {
  const [expanded, setExpanded] = useState(false)

  if (!diff) {
    return (
      <div
        className="text-xs font-mono px-3 py-2"
        style={{ color: 'var(--text-tertiary)' }}
      >
        (no previous version — showing new file)
      </div>
    )
  }

  const lines = diff.split('\n')
  const displayLines = expanded ? lines : lines.slice(0, maxLines)
  const hasMore = lines.length > maxLines

  return (
    <div
      className="rounded border overflow-hidden text-xs font-mono"
      style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-inset)' }}
    >
      <div
        className="overflow-x-auto"
        style={{
          maxHeight: expanded ? '70vh' : '380px',
          overflowY: 'auto',
        }}
      >
        <table
          className="w-full"
          style={{ borderSpacing: 0, tableLayout: 'fixed' }}
        >
          <colgroup>
            <col style={{ width: '2.5rem' }} />
            <col />
          </colgroup>
          <tbody>
            {displayLines.map((line, i) => {
              const { color, bg } = lineStyle(line)
              return (
                <tr key={i} style={{ backgroundColor: bg }}>
                  <td
                    className="px-2 py-0 text-right select-none border-r"
                    style={{
                      color: 'var(--text-tertiary)',
                      borderColor: 'var(--border)',
                      lineHeight: '1.6',
                      verticalAlign: 'top',
                    }}
                  >
                    {i + 1}
                  </td>
                  <td
                    className="px-3 py-0 whitespace-pre"
                    style={{ color, lineHeight: '1.6', overflow: 'visible' }}
                  >
                    {line}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {hasMore && (
        <div
          className="flex items-center justify-between px-3 py-2 border-t"
          style={{ borderColor: 'var(--border)' }}
        >
          <span style={{ color: 'var(--text-tertiary)' }}>
            {expanded ? `${lines.length} lines` : `${maxLines} of ${lines.length} lines`}
          </span>
          <button
            onClick={() => setExpanded((e) => !e)}
            className="px-2 py-1 rounded transition-colors"
            style={{
              color: 'var(--accent)',
              border: '1px solid var(--border)',
              backgroundColor: 'transparent',
            }}
          >
            {expanded ? 'Show less' : `Show all ${lines.length} lines`}
          </button>
        </div>
      )}
    </div>
  )
}
