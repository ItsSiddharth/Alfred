/**
 * PipelineProgressStrip — always-visible stage/substage/progress bar.
 * Driven entirely by Zustand progress state, fed by WS events.
 */
import React from "react";
import { useStore } from "../../store";

const STATUS_COLORS: Record<string, string> = {
  running: "var(--running)",
  waiting: "var(--warn)",
  error:   "var(--danger)",
  done:    "var(--success)",
  idle:    "var(--text-tertiary)",
};

const STAGE_NAMES: Record<number, string> = {
  0: "setup",
  1: "hypothesis",
  2: "experiment",
  3: "run",
};

export function PipelineProgressStrip() {
  const { progress } = useStore();
  const { stage, substage, label, current, total, status } = progress;

  const color = STATUS_COLORS[status] ?? STATUS_COLORS.idle;
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;
  const stageName = STAGE_NAMES[stage] ?? `stage ${stage}`;
  const isIdle = status === "idle";

  return (
    <div
      className="h-8 flex items-center gap-3 px-4 border-b border-[var(--border)] bg-[var(--bg-inset)] shrink-0 select-none"
      title={label}
    >
      {/* Status dot */}
      <span
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${
          status === "running" ? "animate-pulse" : ""
        }`}
        style={{ backgroundColor: color }}
      />

      {/* Stage badge */}
      <span
        className="text-[10px] font-mono px-1.5 py-0.5 rounded border shrink-0"
        style={{ borderColor: color, color }}
      >
        {stageName}
      </span>

      {/* Substage */}
      {!isIdle && substage && (
        <span className="text-[10px] font-mono text-[var(--text-tertiary)] shrink-0">
          {substage.replace(/_/g, " ")}
        </span>
      )}

      {/* Progress bar + fraction */}
      {total > 0 && (
        <>
          <div className="flex-1 h-1 bg-[var(--border)] rounded-full overflow-hidden min-w-0">
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{ width: `${pct}%`, backgroundColor: color }}
            />
          </div>
          <span className="text-[10px] font-mono text-[var(--text-tertiary)] shrink-0">
            {current}/{total}
          </span>
        </>
      )}

      {/* Label */}
      {label && !isIdle && (
        <span className="text-[10px] font-mono text-[var(--text-secondary)] truncate flex-1 min-w-0">
          {label}
        </span>
      )}

      {isIdle && (
        <span className="text-[10px] font-mono text-[var(--text-tertiary)]">
          ready
        </span>
      )}
    </div>
  );
}