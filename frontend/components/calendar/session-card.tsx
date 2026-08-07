"use client";

import { PurposeBadge } from "@/components/design/purpose-badge";
import { StatusDot } from "@/components/design/status-dot";
import { DisciplineIcon } from "@/components/icons";
import type { components } from "@/generated/api/schema";
import { formatDurationHm, formatSets } from "@/lib/format";
import { purposeTone } from "@/lib/purpose";
import { cn } from "@/lib/utils";

export type WeekSession = components["schemas"]["WeekSessionRead"];

/** The MIME type a dragged card carries. Namespaced so nothing else claims it. */
export const SESSION_DRAG_TYPE = "application/x-arc-planned-session";

export interface SessionCardProps {
  readonly session: WeekSession;
  /** Today gets the accent treatment — border, tint and a soft outer ring. */
  readonly isToday?: boolean;
  readonly onOpen: (session: WeekSession) => void;
  readonly onDragStateChange?: (dragging: boolean) => void;
}

export function SessionCard({
  session,
  isToday = false,
  onOpen,
  onDragStateChange,
}: SessionCardProps) {
  const tone = purposeTone(session.purpose);
  const missed = session.status === "missed";
  // Cycling sessions are measured in time, strength in sets: the card shows
  // whichever the prescription actually states rather than an empty slot.
  const measure =
    session.discipline === "strength" && session.total_sets !== null
      ? formatSets(session.total_sets)
      : formatDurationHm(session.planned_duration_s);

  return (
    <button
      type="button"
      draggable
      data-session-id={session.id}
      onClick={() => onOpen(session)}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData(SESSION_DRAG_TYPE, session.id);
        // Some browsers refuse to start a drag without a text/plain payload.
        event.dataTransfer.setData("text/plain", session.id);
        onDragStateChange?.(true);
      }}
      onDragEnd={() => onDragStateChange?.(false)}
      className={cn(
        "flex w-full cursor-pointer flex-col gap-2 rounded-card border px-[11px] py-2.5 text-left transition-colors",
        "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2",
        isToday
          ? "border-accent-border bg-accent-surface shadow-[0_0_0_3px_var(--color-accent-wash)] hover:bg-accent-surface-hover"
          : missed
            ? "border-missed-border border-dashed bg-missed-surface opacity-85 hover:opacity-100"
            : "border-hairline-card bg-card hover:bg-card-hover",
      )}
      style={{
        borderLeft: `2px solid ${isToday ? "var(--color-accent)" : tone.edge}`,
      }}
    >
      <span className="flex items-center justify-between gap-2">
        <span
          className={cn(
            "flex items-center gap-1.5",
            isToday ? "text-accent-quiet" : "text-ink-muted",
          )}
        >
          <DisciplineIcon discipline={session.discipline} size={12} />
          <span
            className={cn(
              "font-mono text-xs",
              // Struck through, not dimmed out of readability: a missed
              // session's duration is what was *not* done, which is
              // information. `ink-disabled` is for inactive controls (D85).
              missed && "text-ink-muted line-through decoration-ink-muted/50",
            )}
          >
            {measure}
          </span>
        </span>
        <StatusDot
          status={session.status}
          outline={isToday && session.status === "planned"}
        />
      </span>

      <span
        className={cn(
          "font-medium text-base leading-tight",
          missed ? "text-ink-muted" : "text-ink",
        )}
      >
        {/* A session planned straight from a purpose has no title of its own —
            the purpose is what it is called until someone names it. */}
        {session.title ?? tone.label}
      </span>

      {session.intent_text ? (
        <span
          className={cn(
            "line-clamp-2 text-xs leading-snug",
            isToday ? "text-accent-quiet" : "text-ink-muted",
          )}
        >
          {session.intent_text}
        </span>
      ) : null}

      <span className="flex items-center justify-between gap-1 pt-0.5">
        <PurposeBadge purpose={session.purpose} />
        {/* Steps are the shape of a *ride*; a strength session's lines are
            already summarised by its set count above. */}
        {session.discipline === "cycling" && session.step_count > 0 ? (
          <span className="whitespace-nowrap font-mono text-2xs text-ink-faint">
            {session.step_count} steps
          </span>
        ) : null}
      </span>
    </button>
  );
}
