"use client";

import { PurposeBadge } from "@/components/design/purpose-badge";
import { CompletionDot } from "@/components/design/status-dot";
import { DisciplineIcon } from "@/components/icons";
import type { components } from "@/generated/api/schema";
import { formatDurationHm, formatSets } from "@/lib/format";
import { purposeTone } from "@/lib/purpose";
import { type CompletionState, completionTone } from "@/lib/scoring";
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
  // The card's own status is the fallback, not a default: every `SessionStatus`
  // member is also a `CompletionState`, and `completion_state(status, null)` in
  // the domain is exactly this — the state a session is in before any verdict
  // refines it. So a payload that predates the field, or one whose state did
  // not arrive, still colours correctly rather than taking the week down.
  const state = session.completion_state ?? session.status;
  const stateTone = completionTone(state);
  const missed = state === "missed";
  // Judged, and judged as something other than what was asked for. The card
  // says so in words as well as in the dot's colour: "under" is the whole
  // point of looking at last week, and a 6px dot is not where it should have
  // to be read from.
  const judged = stateTone !== null && JUDGED_STATES.has(state);
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
        <CompletionDot state={state} outline={isToday && state === "planned"} />
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
        {/* The verdict first, where there is one: a link state is how the card
            got here and the verdict is what it turned out to be, and only one
            of those is worth a badge. A `completed` dot and a `displaced` dot
            are the *consequences* of a confirmed link and of an
            executed-instead-of one, so repeating them here would be saying the
            same thing twice; a **pending** proposal changes neither status by
            design (D140), which makes it the one link state a card is
            otherwise silent about. */}
        {judged && stateTone ? (
          <span
            className="whitespace-nowrap rounded-badge border px-1 py-px text-2xs"
            style={{ color: stateTone.color, borderColor: stateTone.color }}
          >
            {stateTone.label}
          </span>
        ) : session.match_status === "pending" ? (
          <span
            title="A proposal is waiting on you: open the session it came from to confirm or reject it"
            className="whitespace-nowrap rounded-badge border border-accent-border bg-accent-surface px-1 py-px text-2xs text-accent"
          >
            Proposal
          </span>
        ) : session.discipline === "cycling" && session.step_count > 0 ? (
          /* Steps are the shape of a *ride*; a strength session's lines are
             already summarised by its set count above. */
          <span className="whitespace-nowrap font-mono text-2xs text-ink-faint">
            {session.step_count} steps
          </span>
        ) : null}
      </span>
    </button>
  );
}

/**
 * States that are a **verdict** — something a person ruled on, or the machine
 * suggested and nobody has overruled.
 *
 * `completed` is not one of them: it is the gap between ingest and the first
 * score, and badging it "Recorded, not yet judged" would put a label on every
 * card for the seconds before anything had an opinion. `missed`, `planned` and
 * `unplanned` are facts about the calendar rather than judgements, and the dot
 * already carries them.
 */
const JUDGED_STATES: ReadonlySet<CompletionState> = new Set<CompletionState>([
  "completed-as_intended",
  "under",
  "over",
  "abandoned",
  "different_session",
  "displaced",
]);
