"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";

import type { WeekSession } from "@/components/calendar/session-card";
import { NotAssessed } from "@/components/design/not-assessed";
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
import { useExercises } from "@/components/workouts/exercise-catalogue";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { describeCriterion, stepRoleLabel } from "@/lib/criteria";
import { weekdayLabel } from "@/lib/dates";
import {
  formatDayMonthYear,
  formatDurationClock,
  formatDurationHm,
  formatPercent,
  formatSets,
} from "@/lib/format";
import { purposeLabel, STATUS_TONES } from "@/lib/purpose";
import { anchorLabel } from "@/lib/targets";
import type { StrengthStructure } from "@/lib/workout-profile";

type Schemas = components["schemas"];
type PinnedAnchor = Schemas["PinnedAnchorRead"];
type ResolvedStep = Schemas["ResolvedStepRead"];
type ResolvedTarget = Schemas["ResolvedTargetRead"];
type PredictedLoad = Schemas["PredictedLoadRead"];
type PlannedSession = Schemas["PlannedSessionRead"];

export interface SessionSheetProps {
  /** The card that was clicked. `null` closes the sheet. */
  readonly session: WeekSession | null;
  readonly onClose: () => void;
  readonly onMove: (sessionId: string, toDate: string) => void;
  readonly onCopy: (sessionId: string, toDate: string) => void;
  readonly onDelete: (sessionId: string) => void;
  /** Opens the plan form on this session, pre-filled. */
  readonly onEdit: (session: WeekSession) => void;
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
  onEdit,
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
              <ResolvedStepList steps={detail?.resolved_steps ?? []} />
              <AnchorProvenance anchors={detail?.pinned_anchors ?? []} />
            </section>
          ) : null}

          {structure?.discipline === "cycling" && detail ? (
            <PredictedLoadSection detail={detail} />
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
            onEdit={onEdit}
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
  onEdit,
}: {
  session: WeekSession;
  busy: boolean;
  onMove: (sessionId: string, toDate: string) => void;
  onCopy: (sessionId: string, toDate: string) => void;
  onDelete: (sessionId: string) => void;
  onEdit: (session: WeekSession) => void;
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
        {/* "Edit" means this *session* — its purpose, intent, criteria and
            prescription — not the library workout it may have come from.
            Editing the workout would change every session planned from it,
            which is exactly what the frozen snapshot exists to prevent. */}
        <Button variant="outline" onClick={() => onEdit(session)}>
          Edit session
        </Button>
        {session.workout_id ? (
          <Button
            variant="ghost"
            className="text-ink-muted"
            render={
              <Link href={`/workouts/${session.workout_id}`}>Open workout</Link>
            }
          />
        ) : null}
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

/**
 * The flattened step list under the profile — what the bars actually are.
 *
 * Rendered from the API's `resolved_steps` rather than from the step tree,
 * for the reason F2 exists: each step's target is shown **both ways**, the
 * prescription (`88–93 % FTP`, which is what survives an FTP change) beside
 * the watts the athlete actually rides. Repeats are already expanded there, so
 * the list and the bars above it are the same eleven things in the same order.
 */
function ResolvedStepList({ steps }: { steps: readonly ResolvedStep[] }) {
  if (steps.length === 0) {
    return null;
  }
  return (
    <ul className="flex flex-col">
      {steps.map((step) => (
        <li
          key={step.index}
          className="flex items-baseline justify-between gap-3 border-hairline border-b py-1.5 text-sm last:border-b-0"
        >
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="text-ink-secondary">
              {step.name ?? capitalise(stepRoleLabel(step.role))}
            </span>
            <StepTargets step={step} />
          </span>
          <span className="shrink-0 font-mono text-ink-muted text-xs">
            {step.duration_s !== null
              ? formatDurationClock(step.duration_s)
              : step.distance_m !== null
                ? `${step.distance_m} m`
                : "—"}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** The channel order a prescription is read in: power first, cadence last. */
const CHANNEL_ORDER: readonly Schemas["Channel"][] = ["power", "hr", "cadence"];

/** One step's targets, prescribed and resolved. A ramp says both of its ends. */
function StepTargets({ step }: { step: ResolvedStep }) {
  const channels = CHANNEL_ORDER.filter((channel) =>
    step.start_targets.some((target) => target.channel === channel),
  );
  if (channels.length === 0) {
    return null;
  }
  return (
    <span className="flex flex-wrap gap-x-2.5 gap-y-0.5">
      {channels.map((channel) => {
        const start = step.start_targets.find((t) => t.channel === channel);
        const end = step.end_targets.find((t) => t.channel === channel);
        if (!start) {
          return null;
        }
        const ramped =
          step.is_ramp && end && end.prescribed !== start.prescribed;
        const resolved = resolvedText(start);
        const resolvedEnd = ramped && end ? resolvedText(end) : null;
        return (
          <span key={channel} className="font-mono text-2xs">
            <span className="text-ink-muted">
              {start.prescribed}
              {ramped && end ? ` → ${end.prescribed}` : ""}
            </span>
            {resolved ? (
              <span className="text-ink-faint">
                {" · "}
                {resolved}
                {resolvedEnd ? ` → ${resolvedEnd}` : ""}
              </span>
            ) : null}
          </span>
        );
      })}
    </span>
  );
}

/** `220–232 W`, or `null` when the target resolved against no anchor. */
function resolvedText(target: ResolvedTarget): string | null {
  if (target.resolved_low === null || target.resolved_high === null) {
    return null;
  }
  const low = roundTenth(target.resolved_low);
  const high = roundTenth(target.resolved_high);
  return low === high
    ? `${low} ${target.unit}`
    : `${low}–${high} ${target.unit}`;
}

function roundTenth(value: number): number {
  return Math.round(value * 10) / 10;
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * The one line that says what the numbers above were resolved against.
 *
 * `SessionIntent.pinned_anchor_versions` is the product's most distinctive
 * invariant (D49) and it is worth nothing invisible: an FTP of 250 W that was
 * *estimated* is a different claim from one that was *tested*, and every watt
 * on this sheet inherits whichever it is. So the provenance is rendered as its
 * own mark, and the three non-tested kinds are marked differently from
 * `tested` rather than merely being spelled differently.
 */
function AnchorProvenance({ anchors }: { anchors: readonly PinnedAnchor[] }) {
  if (anchors.length === 0) {
    return null;
  }
  return (
    <p className="flex flex-wrap items-baseline gap-x-1.5 text-2xs text-ink-faint">
      <span>Resolved against</span>
      {anchors.map((anchor, index) => (
        <Fragment key={anchor.anchor_version_id}>
          {index > 0 ? <span aria-hidden>·</span> : null}
          <span className="font-mono text-ink-muted">
            {anchorLabel(anchor.anchor_type)} {roundTenth(anchor.value)}{" "}
            {anchor.unit}
          </span>
          <span aria-hidden>·</span>
          <ProvenanceMark provenance={anchor.provenance} />
          <span aria-hidden>·</span>
          <span className="font-mono">
            effective {formatDayMonthYear(anchor.effective_date)}
          </span>
        </Fragment>
      ))}
    </p>
  );
}

/**
 * How each provenance reads. `tested` is the only one that is a measurement;
 * the other three are marked as claims, which is what makes an estimate read
 * as an estimate.
 */
const PROVENANCE_MARKS: Readonly<
  Record<Schemas["Provenance"], { label: string; note: string }>
> = {
  tested: {
    label: "tested",
    note: "Measured in a test protocol — the strongest anchor there is.",
  },
  estimated: {
    label: "estimated",
    note: "An estimate, not a test. Every number resolved from it is only as good as the estimate.",
  },
  athlete_reported: {
    label: "athlete-reported",
    note: "Reported by the athlete rather than measured here.",
  },
  assumed: {
    label: "assumed",
    note: "Assumed for want of anything better. Treat every number resolved from it as provisional.",
  },
};

function ProvenanceMark({ provenance }: { provenance: Schemas["Provenance"] }) {
  const mark = PROVENANCE_MARKS[provenance];
  const tested = provenance === "tested";
  return (
    <span
      data-provenance={provenance}
      data-untested={tested ? undefined : "true"}
      title={mark.note}
      className={
        tested
          ? "text-status-completed"
          : "cursor-help text-status-under underline decoration-dotted decoration-status-under/60 underline-offset-[3px]"
      }
    >
      {mark.label}
    </span>
  );
}

/**
 * What the prescription is expected to cost, with the arithmetic behind it.
 *
 * The explanation is data attached to the number (B5), not copy attached to
 * this page, so the disclosure below is a rendering of the artefact rather
 * than a paragraph someone wrote here — the MCP tool hands the coaching agent
 * the same four fields. Quiet by default: present, not loud.
 */
function PredictedLoadSection({ detail }: { detail: PlannedSession }) {
  const predicted = detail.predicted_load;
  return (
    <section className="flex flex-col gap-2">
      <SectionLabel level={3}>Predicted load</SectionLabel>
      {predicted ? (
        <PredictedLoadFigure predicted={predicted} />
      ) : (
        <div className="flex flex-col gap-1">
          <div className="flex items-baseline gap-2">
            <NotAssessed
              reason={unpredictableReason(detail).short}
              className="font-semibold text-2xl"
            />
            <span className="text-ink-muted text-xs">TSS</span>
          </div>
          <p className="text-ink-muted text-sm">
            {unpredictableReason(detail).sentence}
          </p>
        </div>
      )}
    </section>
  );
}

function PredictedLoadFigure({ predicted }: { predicted: PredictedLoad }) {
  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-mono font-semibold text-2xl">
          {Math.round(predicted.load)}
        </span>
        <span className="text-ink-muted text-xs">TSS</span>
        <span className="font-mono text-ink-muted text-xs">
          IF {predicted.intensity_factor.toFixed(2)}
        </span>
        {/* The coverage travels with the number for the same reason the week's
            does: a total computed from part of a session is not that session's
            total, and saying so is cheaper than being wrong quietly. */}
        <span className="ml-auto font-mono text-2xs text-ink-faint">
          {formatPercent(predicted.coverage)} of the time carried a power target
        </span>
      </div>
      <details className="rounded-button border border-hairline bg-inset px-3 py-2">
        <summary className="cursor-pointer text-ink-muted text-xs">
          How this was computed
        </summary>
        <div className="mt-2.5 flex flex-col gap-2.5">
          <p className="font-mono text-2xs text-ink-secondary">
            {predicted.explanation.formula}
          </p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-2xs">
            {Object.entries(predicted.explanation.inputs).map(
              ([name, value]) => (
                <Fragment key={name}>
                  <dt className="text-ink-faint">{name}</dt>
                  <dd className="font-mono text-ink-muted">{value}</dd>
                </Fragment>
              ),
            )}
          </dl>
          {predicted.explanation.assumptions.length > 0 ? (
            <ul className="flex flex-col gap-1 text-2xs text-ink-muted">
              {predicted.explanation.assumptions.map((assumption) => (
                <li key={assumption} className="flex items-start gap-2">
                  <span
                    aria-hidden
                    className="mt-1.5 size-1 shrink-0 rounded-full bg-hairline-strong"
                  />
                  {assumption}
                </li>
              ))}
            </ul>
          ) : null}
          {predicted.explanation.citation ? (
            <p className="text-2xs text-ink-faint italic">
              {predicted.explanation.citation}
            </p>
          ) : null}
        </div>
      </details>
    </>
  );
}

/**
 * Why this session has no predicted load, derived from what is actually there.
 *
 * Never "no data": the honest answer is one of three specific things, and each
 * one names a different remedy (UI convention 3).
 */
function unpredictableReason(detail: PlannedSession): {
  short: string;
  sentence: string;
} {
  const pinnedFtp = detail.pinned_anchors.some(
    (anchor) => anchor.anchor_type === "ftp",
  );
  if (!pinnedFtp) {
    return {
      short: "No FTP anchor pinned",
      sentence:
        "Not predictable: no FTP anchor is pinned to this session, so there is nothing to resolve a percentage against.",
    };
  }
  const hasPower = detail.resolved_steps.some((step) =>
    [...step.start_targets, ...step.end_targets].some(
      (target) => target.channel === "power",
    ),
  );
  if (!hasPower) {
    return {
      short: "No power target",
      sentence:
        "Not predictable: no step of this prescription states a power target, and load is integrated over watts.",
    };
  }
  if (detail.resolved_steps.some((step) => step.distance_m !== null)) {
    return {
      short: "Prescribed by distance",
      sentence:
        "Not predictable: at least one step is prescribed by distance, so there is no duration to integrate over.",
    };
  }
  return {
    short: "Not predictable from this prescription",
    sentence:
      "Not predictable from what this prescription states. Nothing is assumed in its place.",
  };
}

/** Strength lines, grouped; more than one line in a group is a superset. */
function StrengthGroups({ structure }: { structure: StrengthStructure }) {
  // Real names from the catalogue rather than a prettified slug: `barbell-
  // back-squat` reads correctly by luck, `db_rdl` does not.
  const { nameOf } = useExercises();
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
                {nameOf(item.exercise_id)}
              </span>
              <span className="font-mono text-ink text-xs">
                {item.sets}×{item.reps}
                {" · "}
                {/* The load column holds its slot even when the prescription
                    states no load: a blank there reads as bodyweight, which
                    is a different instruction (UI convention 4). */}
                {describeLoad(item.load) ?? (
                  <NotAssessed reason="No load prescribed for this movement" />
                )}
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

/** A prescribed load in words, or `null` when the prescription states none. */
function describeLoad(load: Schemas["LoadSchema"]): string | null {
  switch (load.kind) {
    case "kg":
      return load.value === null || load.value === undefined
        ? null
        : `${load.value} kg`;
    case "percent_e1rm":
      return load.value === null || load.value === undefined
        ? null
        : `${Math.round(load.value * 100)}% e1RM`;
    case "rpe":
      return load.value === null || load.value === undefined
        ? null
        : `RPE ${load.value}`;
    case "bodyweight":
      return "bodyweight";
  }
}
