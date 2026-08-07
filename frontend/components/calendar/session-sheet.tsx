"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type { WeekSession } from "@/components/calendar/session-card";
import { Panel } from "@/components/design/panel";
import { PurposeBadge } from "@/components/design/purpose-badge";
import { SectionLabel } from "@/components/design/section-label";
import { StatusDot } from "@/components/design/status-dot";
import { WorkoutProfileBars } from "@/components/design/workout-profile-bars";
import { DisciplineIcon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetCloseButton,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { describeCriterion } from "@/lib/criteria";
import { weekdayLabel } from "@/lib/dates";
import {
  formatDayMonthYear,
  formatDurationClock,
  formatDurationHm,
  formatSets,
} from "@/lib/format";
import { purposeLabel, STATUS_TONES } from "@/lib/purpose";
import type { StrengthStructure } from "@/lib/workout-profile";

type Schemas = components["schemas"];

export interface SessionSheetProps {
  /** The card that was clicked. `null` closes the sheet. */
  readonly session: WeekSession | null;
  readonly onClose: () => void;
  readonly onMove: (sessionId: string, toDate: string) => void;
  readonly onCopy: (sessionId: string, toDate: string) => void;
  readonly onDelete: (sessionId: string) => void;
  readonly busy?: boolean;
}

/**
 * The full planned session, in a side sheet.
 *
 * The calendar card carries a summary (D55); everything else — the step tree,
 * the criteria, the coach notes, the intent history — lives behind
 * `GET /planned-sessions/{id}` and is fetched when the sheet opens. The card
 * we already have renders the header immediately, so the sheet is never blank
 * while that request is in flight.
 */
export function SessionSheet({
  session,
  onClose,
  onMove,
  onCopy,
  onDelete,
  busy = false,
}: SessionSheetProps) {
  const { data: detail, isPending } = $api.useQuery(
    "get",
    "/api/v1/planned-sessions/{planned_session_id}",
    { params: { path: { planned_session_id: session?.id ?? "" } } },
    { enabled: session !== null },
  );

  if (session === null) {
    return null;
  }

  const intent = detail?.intent;
  const title = session.title ?? purposeLabel(session.purpose);
  const structure = intent?.structure;
  const strength =
    structure?.discipline === "strength"
      ? (structure as StrengthStructure)
      : null;

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent>
        <header className="flex flex-col gap-2.5 border-hairline border-b px-6 py-5">
          <div className="flex items-center gap-2.5">
            <PurposeBadge purpose={session.purpose} size="md" />
            <span className="font-mono text-xs text-ink-faint">
              {weekdayLabel(session.date)} {formatDayMonthYear(session.date)}
            </span>
            <span className="ml-auto flex items-center gap-1.5">
              <StatusDot status={session.status} />
              <span className="text-ink-muted text-xs">
                {STATUS_TONES[session.status].label}
              </span>
            </span>
            <SheetCloseButton />
          </div>
          <SheetTitle>{title}</SheetTitle>
          <SheetDescription>
            {intent?.intent_text ??
              session.intent_text ??
              "No intent recorded for this session."}
          </SheetDescription>
          <div className="flex items-center gap-3 pt-1 font-mono text-2xs text-ink-faint">
            <span className="flex items-center gap-1.5">
              <DisciplineIcon discipline={session.discipline} size={12} />
              {session.discipline === "strength"
                ? formatSets(session.total_sets)
                : formatDurationHm(session.planned_duration_s)}
            </span>
            <span>
              intent v{intent?.version ?? session.intent_version}
              {intent?.edited_post_hoc ? " · edited post hoc" : ""}
            </span>
          </div>
        </header>

        <div className="flex flex-col gap-5 px-6 py-5">
          {isPending ? (
            <p className="text-ink-muted text-sm">Loading the prescription…</p>
          ) : null}

          {structure?.discipline === "cycling" ? (
            <section className="flex flex-col gap-2.5">
              <SectionLabel level={3}>Workout profile</SectionLabel>
              <WorkoutProfileBars structure={structure} size="detail" />
              <StepList structure={structure} />
            </section>
          ) : null}

          {strength ? (
            <section className="flex flex-col gap-2.5">
              <SectionLabel level={3}>Prescription</SectionLabel>
              <StrengthGroups structure={strength} />
            </section>
          ) : null}

          {intent?.coach_notes ? (
            <section className="flex flex-col gap-2">
              <SectionLabel level={3}>Coach notes</SectionLabel>
              <Panel className="px-4 py-3 text-ink-secondary text-base leading-relaxed">
                {intent.coach_notes}
              </Panel>
            </section>
          ) : null}

          <section className="flex flex-col gap-2">
            <SectionLabel level={3}>Success criteria</SectionLabel>
            {intent && intent.success_criteria.length > 0 ? (
              <ul className="flex flex-col gap-1.5">
                {intent.success_criteria.map((criterion) => (
                  <li
                    key={JSON.stringify(criterion)}
                    className="flex items-start gap-2 text-ink-secondary text-sm"
                  >
                    <span
                      aria-hidden
                      className="mt-1.5 size-1 shrink-0 rounded-full bg-accent"
                    />
                    {describeCriterion(criterion)}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-ink-muted text-sm">
                {isPending ? "…" : "No criteria on this session."}
              </p>
            )}
          </section>

          <SessionActions
            session={session}
            busy={busy}
            onMove={onMove}
            onCopy={onCopy}
            onDelete={onDelete}
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}

/** Move / copy / delete / edit, the four things a plan entry can have done to it. */
function SessionActions({
  session,
  busy,
  onMove,
  onCopy,
  onDelete,
}: {
  session: WeekSession;
  busy: boolean;
  onMove: (sessionId: string, toDate: string) => void;
  onCopy: (sessionId: string, toDate: string) => void;
  onDelete: (sessionId: string) => void;
}) {
  const [moveDate, setMoveDate] = useState(session.date);
  const [copyDate, setCopyDate] = useState(session.date);

  // Reopening the sheet on a different card must not keep the previous card's
  // dates in the inputs.
  useEffect(() => {
    setMoveDate(session.date);
    setCopyDate(session.date);
  }, [session.date]);

  return (
    <section className="flex flex-col gap-3 border-hairline border-t pt-5">
      <SectionLabel level={3}>Actions</SectionLabel>

      <div className="flex items-end gap-2">
        <div className="flex flex-1 flex-col gap-1">
          <label htmlFor="session-move-date" className="text-ink-muted text-xs">
            Move to
          </label>
          <Input
            id="session-move-date"
            type="date"
            value={moveDate}
            onChange={(event) => setMoveDate(event.target.value)}
            className="font-mono"
          />
        </div>
        <Button
          variant="secondary"
          disabled={busy || moveDate === session.date}
          onClick={() => onMove(session.id, moveDate)}
        >
          Move
        </Button>
      </div>

      <div className="flex items-end gap-2">
        <div className="flex flex-1 flex-col gap-1">
          <label htmlFor="session-copy-date" className="text-ink-muted text-xs">
            Copy to
          </label>
          <Input
            id="session-copy-date"
            type="date"
            value={copyDate}
            onChange={(event) => setCopyDate(event.target.value)}
            className="font-mono"
          />
        </div>
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => onCopy(session.id, copyDate)}
        >
          Copy
        </Button>
      </div>

      <div className="flex items-center gap-2 pt-1">
        <Button
          variant="outline"
          render={
            // The workout creator lands in the next slice; the link is real so
            // the affordance does not have to be rebuilt then.
            <Link href={`/workouts/${session.workout_id ?? "new"}`}>Edit</Link>
          }
        />
        <Button
          variant="destructive"
          className="ml-auto"
          disabled={busy}
          onClick={() => onDelete(session.id)}
        >
          Delete
        </Button>
      </div>
    </section>
  );
}

/** The flattened step list under the profile — what the bars actually are. */
function StepList({
  structure,
}: {
  structure: Schemas["EnduranceStructureSchema-Output"];
}) {
  const steps = describeSteps(structure.steps);
  if (steps.length === 0) {
    return null;
  }
  return (
    <ul className="flex flex-col">
      {steps.map((step, index) => (
        <li
          // Steps are positions in a tree, not entities; the tree is replaced
          // wholesale whenever the prescription changes.
          // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
          key={index}
          className="flex items-center justify-between gap-3 border-hairline border-b py-1.5 text-sm last:border-b-0"
        >
          <span className="text-ink-secondary">{step.label}</span>
          <span className="font-mono text-ink-muted text-xs">
            {formatDurationClock(step.durationS)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function describeSteps(
  steps: readonly (
    | Schemas["SteadyStepSchema"]
    | Schemas["RampStepSchema"]
    | Schemas["RepeatBlockSchema-Output"]
  )[],
  prefix = "",
): { label: string; durationS: number | null }[] {
  const out: { label: string; durationS: number | null }[] = [];
  for (const step of steps) {
    if (step.kind === "repeat") {
      out.push(...describeSteps(step.children, `${prefix}${step.times}× `));
    } else {
      const role = step.role.charAt(0).toUpperCase() + step.role.slice(1);
      out.push({
        label: `${prefix}${step.name ?? role}`,
        durationS: step.duration_s ?? null,
      });
    }
  }
  return out;
}

/** Strength lines, grouped; more than one line in a group is a superset. */
function StrengthGroups({ structure }: { structure: StrengthStructure }) {
  return (
    <div className="flex flex-col gap-3">
      {structure.groups.map((group, groupIndex) => (
        <Panel
          // Groups are ordered positions in the prescription, not entities.
          // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
          key={groupIndex}
          className="flex flex-col gap-1.5 px-4 py-3"
        >
          {group.label ? (
            <SectionLabel>{group.label}</SectionLabel>
          ) : group.items.length > 1 ? (
            <SectionLabel>Superset</SectionLabel>
          ) : null}
          {group.items.map((item) => (
            <div
              key={`${item.exercise_id}-${item.sets}-${item.reps}`}
              className="flex items-baseline justify-between gap-3 text-sm"
            >
              <span className="text-ink-secondary">
                {prettifySlug(item.exercise_id)}
              </span>
              <span className="font-mono text-ink text-xs">
                {item.sets}×{item.reps}
                {describeLoad(item.load)}
                {item.rir === null || item.rir === undefined
                  ? ""
                  : ` · RIR ${item.rir}`}
              </span>
            </div>
          ))}
        </Panel>
      ))}
    </div>
  );
}

function describeLoad(load: Schemas["LoadSchema"]): string {
  switch (load.kind) {
    case "kg":
      return load.value === null || load.value === undefined
        ? ""
        : ` · ${load.value} kg`;
    case "percent_e1rm":
      return load.value === null || load.value === undefined
        ? ""
        : ` · ${Math.round(load.value * 100)}% e1RM`;
    case "rpe":
      return load.value === null || load.value === undefined
        ? ""
        : ` · RPE ${load.value}`;
    case "bodyweight":
      return " · bodyweight";
  }
}

/** `barbell-back-squat` → `Barbell back squat`, until the catalogue is joined. */
function prettifySlug(slug: string): string {
  const words = slug.replace(/[-_]/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
