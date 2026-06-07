/**
 * PlotView — renders one experiment plot with PNG/ASCII toggle.
 *
 * The PNG is base64-encoded in the store (no network request).
 * ASCII view uses a <pre> block for terminal-style display in the thinking tab.
 */

import { useState } from 'react'
import { BarChart2, Terminal } from 'lucide-react'
import type { PlotEntry } from '../../store'

interface PlotViewProps {
  plot: PlotEntry
}

export function PlotView({ plot }: PlotViewProps) {
  const [showAscii, setShowAscii] = useState(false)
  const hasAscii = plot.ascii_art && !plot.ascii_art.startsWith('[')

  return (
    <div
      className="rounded border overflow-hidden flex flex-col"
      style={{
        borderColor: 'var(--border)',
        backgroundColor: 'var(--bg-surface)',
        minWidth: '280px',
        maxWidth: '480px',
        flex: '0 0 auto',
      }}
    >
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 py-2 border-b"
        style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-elevated)' }}
      >
        <BarChart2 size={12} style={{ color: 'var(--accent)' }} />
        <span
          className="flex-1 text-xs font-mono truncate"
          style={{ color: 'var(--text-secondary)' }}
          title={plot.filename}
        >
          {plot.filename}
        </span>

        {hasAscii && (
          <button
            onClick={() => setShowAscii((a) => !a)}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-mono transition-colors"
            style={{
              color: showAscii ? 'var(--accent)' : 'var(--text-tertiary)',
              border: `1px solid ${showAscii ? 'var(--accent)' : 'var(--border)'}`,
              backgroundColor: showAscii ? 'rgba(99,102,241,0.08)' : 'transparent',
            }}
            title={showAscii ? 'Show PNG' : 'Show ASCII'}
          >
            <Terminal size={10} />
            {showAscii ? 'PNG' : 'ASCII'}
          </button>
        )}
      </div>

      {/* Plot content */}
      {showAscii ? (
        <pre
          className="p-3 text-xs font-mono overflow-auto"
          style={{
            color: 'var(--text-secondary)',
            backgroundColor: 'var(--bg-inset)',
            lineHeight: 1.2,
            whiteSpace: 'pre',
            maxHeight: '240px',
          }}
        >
          {plot.ascii_art}
        </pre>
      ) : (
        <div
          className="flex items-center justify-center p-2"
          style={{ backgroundColor: 'var(--bg-inset)', minHeight: '120px' }}
        >
          <img
            src={`data:image/png;base64,${plot.base64_png}`}
            alt={plot.filename}
            style={{
              maxWidth: '100%',
              maxHeight: '320px',
              objectFit: 'contain',
              borderRadius: '4px',
            }}
          />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// PlotsRow — horizontal scroll row of PlotView cards
// ---------------------------------------------------------------------------

interface PlotsRowProps {
  plots: PlotEntry[]
}

export function PlotsRow({ plots }: PlotsRowProps) {
  if (plots.length === 0) return null

  return (
    <div className="px-4 py-3">
      <div
        className="flex items-center gap-2 mb-2"
        style={{ color: 'var(--text-tertiary)' }}
      >
        <BarChart2 size={11} />
        <span className="text-xs font-mono">
          {plots.length} plot{plots.length !== 1 ? 's' : ''} — experiment output
        </span>
      </div>

      <div
        className="flex gap-3 overflow-x-auto pb-1"
        style={{ scrollbarWidth: 'thin' }}
      >
        {plots.map((plot, i) => (
          <PlotView key={`${plot.filename}-${i}`} plot={plot} />
        ))}
      </div>
    </div>
  )
}
