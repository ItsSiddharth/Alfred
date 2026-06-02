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
 * The card content is determined by the plan shape:
 *   - novelty_score / gap_score / publishability_score → Stage 1 scorecard
 *   - dataset / architecture / metrics → Stage 2 experiment plan
 *   - generic key-value → fallback
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
} from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { experimentsApi } from '../../api/client'
import { useStore, type ApprovalRequest } from '../../store'
import { Button } from '../common/Button'

// ---------------------------------------------------------------------------
// Score meter — Stage 1 scorecard
// ---------------------------------------------------------------------------

interface ScoreMeterProps {
  label: string
  value: number
  rationale?: string
}

function ScoreMeter({ label, value, rationale }: ScoreMeterProps) {
  const [expanded, setExpanded] = useState(false)

  const color =
    value >= 65
      ? 'var(--running)'
      : value >= 40
      ? 'var(--warn)'
      : 'var(--danger)'

  return (
    <div
      className="rounded border overflow-hidden"
      style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-inset)' }}
    >
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center gap-3 px-3 py-2.5 text-left"
      >
        <span
          className="text-xs font-mono font-medium w-24 shrink-0"
          style={{ color: 'var(--text-secondary)' }}
        >
          {label}
        </span>

        {/* Bar */}
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

        <span style={{ color: 'var(--text-tertiary)' }}>
          {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        </span>
      </button>

      {expanded && rationale && (
        <div
          className="px-3 pb-2.5 text-xs border-t"
          style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)', lineHeight: '1.6' }}
        >
          {rationale}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Stage 1 scorecard view
// ---------------------------------------------------------------------------

interface ScorecardViewProps {
  plan: Record<string, unknown>
  editMode: boolean
  editedPlan: Record<string, unknown>
  onEditChange: (key: string, value: unknown) => void
}

function ScorecardView({ plan, editMode, editedPlan, onEditChange }: ScorecardViewProps) {
  const scores = [
    {
      label: 'Novelty',
      key: 'novelty_score',
      rationale: plan.novelty_rationale as string | undefined,
    },
    {
      label: 'Gap realness',
      key: 'gap_score',
      rationale: plan.gap_rationale as string | undefined,
    },
    {
      label: 'Publishability',
      key: 'publishability_score',
      rationale: plan.publishability_rationale as string | undefined,
    },
  ]

  const papers = (plan.cited_papers as Array<{ title: string; year: number; venue: string; url?: string }>) ?? []

  return (
    <div className="space-y-2">
      {scores.map(({ label, key, rationale }) => (
        <ScoreMeter
          key={key}
          label={label}
          value={(editedPlan[key] as number) ?? (plan[key] as number) ?? 0}
          rationale={rationale ?? (plan.rationale as string | undefined)}
        />
      ))}

      {plan.rationale && (
        <div
          className="px-3 py-2.5 rounded border text-xs"
          style={{
            borderColor: 'var(--border)',
            backgroundColor: 'var(--bg-inset)',
            color: 'var(--text-secondary)',
            lineHeight: '1.6',
          }}
        >
          <div className="font-medium font-mono mb-1" style={{ color: 'var(--text-tertiary)' }}>
            Summary
          </div>
          {plan.rationale as string}
        </div>
      )}

      {papers.length > 0 && (
        <div
          className="rounded border overflow-hidden"
          style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-inset)' }}
        >
          <div
            className="px-3 py-2 text-xs font-mono font-medium border-b"
            style={{ borderColor: 'var(--border)', color: 'var(--text-tertiary)' }}
          >
            Key citations
          </div>
          <div className="divide-y" style={{ borderColor: 'var(--border)' }}>
            {papers.map((p, i) => (
              <div key={i} className="flex items-start gap-2 px-3 py-2">
                <div className="flex-1 min-w-0">
                  <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>
                    {p.url ? (
                      <a
                        href={p.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="hover:underline flex items-center gap-1"
                        style={{ color: 'var(--accent)' }}
                      >
                        {p.title}
                        <ExternalLink size={9} />
                      </a>
                    ) : (
                      p.title
                    )}
                  </div>
                  <div className="text-xs font-mono mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
                    {p.year} · {p.venue}
                  </div>
                </div>
              </div>
            ))}
          </div>
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

  const stageLabel = stage === 1 ? 'Hypothesis' : stage === 2 ? 'Setup' : 'Run'
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
                disabled={rejectMutation.isPending}
              >
                <XCircle size={12} />
                {rejectMutation.isPending ? 'Sending…' : 'Send feedback'}
              </Button>
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