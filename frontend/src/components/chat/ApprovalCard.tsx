/**
 * ApprovalCard — C5 signature "plan card" pattern.
 *
 * Shown when the state machine emits an `approval_request` event.
 * Three actions:
 *   Approve — calls POST /api/projects/{id}/experiments/{expId}/approve
 *   Edit    — opens inline edit mode; fields are editable, then approve
 *   Reject  — opens feedback input, calls POST …/reject
 *
 * Auto-approve: card renders marked "auto-approved" in amber; no buttons shown
 * (machine already proceeded but card is still displayed for transparency).
 *
 * Stage 1 scorecard: per-score citations, collapsible literature landscape,
 *   and a "Re-run with deeper search" button on rejection.
 * Stage 2 plan card: generic key-value with edit support.
 */

import { useState } from 'react'
import {
  CheckCircle2,
  XCircle,
  Pencil,
  Zap,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  RefreshCw,
} from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { experimentsApi, hypothesisApi } from '../../api/client'
import { useStore, type ApprovalRequest } from '../../store'
import { Button } from '../common/Button'

// ---------------------------------------------------------------------------
// Citations list — reused inside each ScoreMeter
// ---------------------------------------------------------------------------

type Citation = { title: string; year?: number; venue?: string; url?: string }

function CitationsList({ citations }: { citations: Citation[] }) {
  if (!citations || citations.length === 0) return null
  return (
    <div className="mt-2 space-y-1">
      {citations.map((c, i) => (
        <div key={i} className="flex items-start gap-1.5">
          <span className="text-xs font-mono shrink-0 mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
            [{c.year ?? '?'}]
          </span>
          <div className="min-w-0">
            {c.url ? (
              <a
                href={c.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs hover:underline flex items-center gap-1"
                style={{ color: 'var(--accent)' }}
              >
                <span className="truncate">{c.title}</span>
                <ExternalLink size={9} className="shrink-0" />
              </a>
            ) : (
              <span className="text-xs" style={{ color: 'var(--text-primary)' }}>{c.title}</span>
            )}
            {c.venue && (
              <span className="text-xs font-mono ml-1" style={{ color: 'var(--text-tertiary)' }}>
                · {c.venue}
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Score meter — Stage 1 scorecard with per-score citations
// ---------------------------------------------------------------------------

interface ScoreMeterProps {
  label: string
  value: number
  rationale?: string
  citations?: Citation[]
}

function ScoreMeter({ label, value, rationale, citations = [] }: ScoreMeterProps) {
  const [expanded, setExpanded] = useState(false)

  const color =
    value >= 65
      ? 'var(--running)'
      : value >= 40
      ? 'var(--warn)'
      : 'var(--danger)'

  const hasDetail = !!rationale || citations.length > 0

  return (
    <div
      className="rounded border overflow-hidden"
      style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-inset)' }}
    >
      <button
        onClick={() => hasDetail && setExpanded((e) => !e)}
        className="w-full flex items-center gap-3 px-3 py-2.5 text-left"
        style={{ cursor: hasDetail ? 'pointer' : 'default' }}
      >
        <span
          className="text-xs font-mono font-medium w-24 shrink-0"
          style={{ color: 'var(--text-secondary)' }}
        >
          {label}
        </span>

        <div
          className="flex-1 h-1.5 rounded-full overflow-hidden"
          style={{ backgroundColor: 'var(--bg-elevated)' }}
        >
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${value}%`, backgroundColor: color }}
          />
        </div>

        <span
          className="text-sm font-mono font-medium w-8 text-right shrink-0"
          style={{ color }}
        >
          {value}
        </span>

        {hasDetail && (
          <span style={{ color: 'var(--text-tertiary)' }}>
            {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          </span>
        )}
      </button>

      {expanded && (
        <div
          className="px-3 pb-3 text-xs border-t"
          style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)', lineHeight: '1.6' }}
        >
          {rationale && <p className="mt-2">{rationale}</p>}
          <CitationsList citations={citations} />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Stage 1 scorecard view — with landscape section
// ---------------------------------------------------------------------------

interface ScorecardViewProps {
  plan: Record<string, unknown>
  editMode: boolean
  editedPlan: Record<string, unknown>
  onEditChange: (key: string, value: unknown) => void
}

function ScorecardView({ plan, editedPlan }: ScorecardViewProps) {
  const [landscapeExpanded, setLandscapeExpanded] = useState(false)

  const scores: Array<{
    label: string
    key: string
    rationaleKey: string
    citationsKey: string
  }> = [
    { label: 'Novelty', key: 'novelty_score', rationaleKey: 'novelty_rationale', citationsKey: 'novelty_citations' },
    { label: 'Gap realness', key: 'gap_score', rationaleKey: 'gap_rationale', citationsKey: 'gap_citations' },
    { label: 'Publishability', key: 'publishability_score', rationaleKey: 'publishability_rationale', citationsKey: 'publishability_citations' },
  ]

  const landscape = plan.landscape as string | undefined
  const cited_papers = (plan.cited_papers as Citation[]) ?? []

  return (
    <div className="space-y-2">
      {scores.map(({ label, key, rationaleKey, citationsKey }) => {
        const perScoreCitations = (plan[citationsKey] as Citation[]) ?? []
        const fallbackCitations = perScoreCitations.length > 0 ? perScoreCitations : cited_papers
        return (
          <ScoreMeter
            key={key}
            label={label}
            value={(editedPlan[key] as number) ?? (plan[key] as number) ?? 0}
            rationale={(plan[rationaleKey] as string | undefined) ?? (plan.rationale as string | undefined)}
            citations={fallbackCitations.slice(0, 5)}
          />
        )
      })}

      {/* Literature landscape (collapsible) */}
      {landscape && (
        <div
          className="rounded border overflow-hidden"
          style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-inset)' }}
        >
          <button
            onClick={() => setLandscapeExpanded((e) => !e)}
            className="w-full flex items-center gap-2 px-3 py-2.5 text-left"
          >
            <span className="text-xs font-mono font-medium flex-1" style={{ color: 'var(--text-tertiary)' }}>
              Literature landscape
            </span>
            <span style={{ color: 'var(--text-tertiary)' }}>
              {landscapeExpanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            </span>
          </button>
          {landscapeExpanded && (
            <div
              className="px-3 pb-3 text-xs border-t"
              style={{
                borderColor: 'var(--border)',
                color: 'var(--text-secondary)',
                lineHeight: '1.7',
                whiteSpace: 'pre-wrap',
              }}
            >
              <p className="mt-2">{landscape}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Generic plan view (Stage 2 / 3)
// ---------------------------------------------------------------------------

interface GenericPlanViewProps {
  plan: Record<string, unknown>
  editMode: boolean
  editedPlan: Record<string, unknown>
  onEditChange: (key: string, value: unknown) => void
}

function GenericPlanView({ plan, editMode, editedPlan, onEditChange }: GenericPlanViewProps) {
  // Keys to skip (internal/meta fields)
  const skipKeys = new Set(['auto_approve', 'stage', 'substage'])

  const entries = Object.entries(plan).filter(([k]) => !skipKeys.has(k))

  if (entries.length === 0) {
    return (
      <div className="text-xs text-center py-4" style={{ color: 'var(--text-tertiary)' }}>
        No plan details to display.
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {entries.map(([key, value]) => {
        const displayKey = key.replace(/_/g, ' ')
        const displayValue =
          typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value ?? '')

        if (editMode) {
          return (
            <div key={key}>
              <label
                className="block text-xs font-mono mb-1"
                style={{ color: 'var(--text-tertiary)' }}
              >
                {displayKey}
              </label>
              <textarea
                rows={Math.min(6, displayValue.split('\n').length + 1)}
                value={String(editedPlan[key] ?? displayValue)}
                onChange={(e) => onEditChange(key, e.target.value)}
                className="w-full px-2.5 py-1.5 rounded text-xs font-mono resize-none outline-none"
                style={{
                  backgroundColor: 'var(--bg-inset)',
                  border: '1px solid var(--border-strong)',
                  color: 'var(--text-primary)',
                }}
              />
            </div>
          )
        }

        return (
          <div
            key={key}
            className="flex gap-3 px-3 py-2 rounded border"
            style={{
              borderColor: 'var(--border)',
              backgroundColor: 'var(--bg-inset)',
            }}
          >
            <span
              className="text-xs font-mono shrink-0 w-32"
              style={{ color: 'var(--text-tertiary)' }}
            >
              {displayKey}
            </span>
            <span
              className="text-xs font-mono flex-1 whitespace-pre-wrap break-words"
              style={{ color: 'var(--text-primary)' }}
            >
              {displayValue}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ApprovalCard
// ---------------------------------------------------------------------------

interface ApprovalCardProps {
  request: ApprovalRequest
}

export function ApprovalCard({ request }: ApprovalCardProps) {
  const { plan, auto_approve, stage, substage, experiment_id } = request
  const activeProjectId = useStore((s) => s.activeProjectId)
  const selectedModel = useStore((s) => s.selectedModel)
  const setApprovalRequest = useStore((s) => s.setApprovalRequest)
  const queryClient = useQueryClient()

  const [editMode, setEditMode] = useState(false)
  const [rejectMode, setRejectMode] = useState(false)
  const [editedPlan, setEditedPlan] = useState<Record<string, unknown>>({ ...plan })
  const [feedback, setFeedback] = useState('')

  const handleEditChange = (key: string, value: unknown) => {
    setEditedPlan((prev) => ({ ...prev, [key]: value }))
  }

  // Determine which plan view to render
  const isScorecard =
    'novelty_score' in plan || 'gap_score' in plan || 'publishability_score' in plan

  // Approve mutation
  const approveMutation = useMutation({
    mutationFn: () => {
      if (!activeProjectId) throw new Error('No active project')
      const expId = experiment_id ?? 0
      return experimentsApi.approve(
        activeProjectId,
        expId,
        editMode ? editedPlan : undefined
      )
    },
    onSuccess: () => {
      setApprovalRequest(null)
      queryClient.invalidateQueries({ queryKey: ['experiments', activeProjectId] })
    },
  })

  // Reject mutation
  const rejectMutation = useMutation({
    mutationFn: () => {
      if (!activeProjectId) throw new Error('No active project')
      const expId = experiment_id ?? 0
      return experimentsApi.reject(activeProjectId, expId, feedback)
    },
    onSuccess: () => {
      setApprovalRequest(null)
    },
  })

  // Re-run mutation (Stage 1 scorecard only)
  const rerunMutation = useMutation({
    mutationFn: async () => {
      if (!activeProjectId) throw new Error('No active project')
      if (!selectedModel) throw new Error('No model selected')
      // First reject the current gate, then start a new run with feedback
      const expId = experiment_id ?? 0
      await experimentsApi.reject(activeProjectId, expId, feedback || 'Re-run requested')
      // Extract hypothesis from plan (rationale is the landscape text used as proxy)
      const hypothesis =
        (plan.hypothesis as string) ||
        (plan.landscape as string | undefined)?.slice(0, 500) ||
        'Re-run hypothesis validation'
      return hypothesisApi.start(activeProjectId, hypothesis, selectedModel, feedback)
    },
    onSuccess: () => {
      setApprovalRequest(null)
    },
  })

  const substageLabel = substage.replace(/_/g, ' ')

  return (
    <div
      className="rounded border overflow-hidden"
      style={{
        backgroundColor: 'var(--bg-surface)',
        borderColor: auto_approve ? 'rgba(245,158,11,0.4)' : 'var(--border-strong)',
      }}
    >
      {/* Header */}
      <div
        className="flex items-center gap-3 px-4 py-3 border-b"
        style={{
          borderColor: 'var(--border)',
          backgroundColor: auto_approve ? 'rgba(245,158,11,0.06)' : 'var(--bg-elevated)',
        }}
      >
        <div
          className="flex items-center gap-2 flex-1"
        >
          {auto_approve ? (
            <Zap size={13} style={{ color: 'var(--warn)' }} />
          ) : (
            <AlertTriangle size={13} style={{ color: 'var(--accent)' }} />
          )}
          <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
            {auto_approve ? 'Auto-approved plan' : 'Plan ready for review'}
          </span>
          <span
            className="text-xs font-mono px-1.5 py-0.5 rounded"
            style={{
              backgroundColor: 'var(--bg-inset)',
              color: 'var(--text-tertiary)',
              border: '1px solid var(--border)',
            }}
          >
            Stage {stage} · {substageLabel}
          </span>
        </div>

        {/* Show-work: edit button always accessible */}
        {!auto_approve && !editMode && !rejectMode && (
          <button
            onClick={() => setEditMode(true)}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs font-mono transition-colors"
            style={{
              color: 'var(--text-tertiary)',
              border: '1px solid var(--border)',
              backgroundColor: 'transparent',
            }}
          >
            <Pencil size={10} />
            Edit
          </button>
        )}
      </div>

      {/* Plan body */}
      <div className="p-4">
        {isScorecard ? (
          <ScorecardView
            plan={plan}
            editMode={editMode}
            editedPlan={editedPlan}
            onEditChange={handleEditChange}
          />
        ) : (
          <GenericPlanView
            plan={plan}
            editMode={editMode}
            editedPlan={editedPlan}
            onEditChange={handleEditChange}
          />
        )}
      </div>

      {/* Reject feedback input */}
      {rejectMode && (
        <div
          className="px-4 pb-3 border-t"
          style={{ borderColor: 'var(--border)' }}
        >
          <label
            className="block text-xs font-mono mb-1.5 mt-3"
            style={{ color: 'var(--text-tertiary)' }}
          >
            Feedback for ALFRED (optional)
          </label>
          <textarea
            rows={2}
            placeholder="What should be changed?"
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            className="w-full px-2.5 py-1.5 rounded text-xs font-mono resize-none outline-none"
            style={{
              backgroundColor: 'var(--bg-inset)',
              border: '1px solid var(--border-strong)',
              color: 'var(--text-primary)',
            }}
          />
        </div>
      )}

      {/* Action buttons — hidden when auto-approved */}
      {!auto_approve && (
        <div
          className="flex items-center gap-2 px-4 py-3 border-t"
          style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-elevated)' }}
        >
          {!rejectMode ? (
            <>
              {/* Approve / Approve edited */}
              <Button
                size="sm"
                onClick={() => approveMutation.mutate()}
                disabled={approveMutation.isPending}
              >
                <CheckCircle2 size={12} />
                {editMode ? 'Approve edited' : 'Approve'}
              </Button>

              {/* Cancel edit */}
              {editMode && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setEditMode(false)
                    setEditedPlan({ ...plan })
                  }}
                >
                  Cancel edit
                </Button>
              )}

              {/* Reject */}
              {!editMode && (
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => setRejectMode(true)}
                >
                  <XCircle size={12} />
                  Reject
                </Button>
              )}
            </>
          ) : (
            <>
              <Button
                size="sm"
                variant="danger"
                onClick={() => rejectMutation.mutate()}
                disabled={rejectMutation.isPending || rerunMutation.isPending}
              >
                <XCircle size={12} />
                {rejectMutation.isPending ? 'Sending…' : 'Send feedback'}
              </Button>

              {/* Re-run with deeper search — Stage 1 scorecard only */}
              {isScorecard && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => rerunMutation.mutate()}
                  disabled={rerunMutation.isPending || rejectMutation.isPending}
                >
                  <RefreshCw size={11} />
                  {rerunMutation.isPending ? 'Starting…' : 'Re-run research'}
                </Button>
              )}

              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setRejectMode(false)
                  setFeedback('')
                }}
              >
                Cancel
              </Button>
            </>
          )}

          {approveMutation.isError && (
            <span className="text-xs ml-2" style={{ color: 'var(--danger)' }}>
              {approveMutation.error instanceof Error
                ? approveMutation.error.message
                : 'Approval failed'}
            </span>
          )}
        </div>
      )}
    </div>
  )
}