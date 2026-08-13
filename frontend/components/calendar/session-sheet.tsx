"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";

import type { WeekSession } from "@/components/calendar/session-card";
import { AnchorProvenance } from "@/components/design/anchor-provenance";
import { ConfirmButton } from "@/components/design/confirm";
import { DiscardPrompt, useDirtyClose } from "@/components/design/dirty-close";
import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { PurposeBadge } from "@/components/design/purpose-badge";
import { ResolvedStepList } from "@/components/design/resolved-steps";
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
import { apiErrorMessages } from "@/lib/api-errors";
import { describeCriterion } from "@/lib/criteria";
import { weekdayLabel } from "@/lib/dates";
import {
  formatDayMonthYear,
  formatDurationHm,
  formatPercent,
  formatSets,
} from "@/lib/format";
import { purposeLabel, STATUS_TONES } from "@/lib/purpose";
import { describeSets, type StrengthStructure } from "@/lib/workout-profile";

type Schemas = components["schemas"];
type PredictedLoad = Schemas["PredictedLoadRead"];
type PredictedVolume = Schemas["PredictedVolumeRead"];
type PlannedSession = Schemas["PlannedSessionRead"];

export interface SessionSheetProps {
  /** The session the address bar says is open. `null` closes the sheet. */
  readonly sessionId: string | null;
  /**
   * The calendar card for `sessionId`, when the week on screen carries one.
   *
   * An optimisation, never a requirement: it renders the header from the first
   * frame instead of a request later. A link to a session on another week
   * arrives with no card, and the sheet reads the same facts off the session
   * it fetches.
   */
  readonly card: WeekSession | null;
  readonly onClose: () => void;
  readonly onMove: (sessionId: string, toDate: string) => void;
  readonly onCopy: (sessionId: string, toDate: string) => void;
  readonly onDelete: (sessionId: string) => void;
  /**
   * Opens the plan form on this session, pre-filled — editing **this session**,
   * never the library workout it was planned from.
   *
   * A planned session carries a frozen snapshot of its prescription plus its
   * own purpose, intent, notes and criteria, so editing the library workout
   * would change neither this session nor any other already planned from it.
   * One button doing the visibly-nothing thing is the worse failure, so
   * changing the library is a separate, explicitly-labelled route into
   * `/workouts/{id}`.
   */
  readonly onEdit: (sessionId: string, date: string) => void;
  readonly busy?: boolean;
  /**
   * Why the last action from this sheet failed. Rendered here rather than on
   * the page behind it, because the sheet is what stays open when a delete or
   * a copy is refused — a message on a surface the athlete cannot see is the
   * same as no message.
   */
  readonly problems?: readonly string[];
  /** What the last action from this sheet achieved, when it achieved it. */
  readonly notice?: string | null;
}

/**
 * What the sheet's header states, from whichever source has it.
 *
 * The card and the session resource carry the same facts under different
 * names — the card because a calendar needs them without a second request,
 * the resource because it is the session. This is the one place that knows
 * they are the same facts, so nothing below has to ask which one it is
 * looking at.
 */
interface SheetHeader {
  readonly date: string;
  readonly discipline: WeekSession["discipline"];
  readonly purpose: WeekSession["purpose"];
  readonly status: WeekSession["status"];
  readonly title: string;
  readonly intentText: string | null;
  readonly intentVersion: number;
  readonly plannedDurationS: number | null;
  readonly totalSets: number | null;
}

/**
 * The full planned session, in a side sheet.
 *
 * **Which session is open is the URL's** (`/calendar?session=<id>`), and this
 * component is handed the id rather than an object: a sheet is a place, and a
 * place has to survive a reload, a bookmark and a Back press (UI convention 1).
 *
 * The calendar card carries a summary; everything else — the step tree,
 * the criteria, the coach notes, the intent history — lives behind
 * `GET /planned-sessions/{id}` and is fetched when the sheet opens. When the
 * card is on screen it renders the header immediately, so the sheet is never
 * blank while that request is in flight; when the link arrived from somewhere
 * else the same header is read off the session itself, one request later.
 *
 * The move and copy pickers are a *draft*: a date typed into one and not acted
 * on is work, and an outside press used to throw it away without a word. Now
 * anything unapplied makes the sheet ask before it closes (`useDirtyClose`).
 */
export function SessionSheet({
  sessionId,
  card,
  onClose,
  onMove,
  onCopy,
  onDelete,
  onEdit,
  busy = false,
  problems = [],
  notice = null,
}: SessionSheetProps) {
  const {
    data: detail,
    isPending,
    error: detailError,
  } = $api.useQuery(
    "get",
    "/api/v1/planned-sessions/{planned_session_id}",
    { params: { path: { planned_session_id: sessionId ?? "" } } },
    { enabled: sessionId !== null },
  );

  /**
   * The library workout's name, fetched only when there is no card to read it
   * off. A session's title *is* its workout's name (`app.services.plan._card`)
   * and the session resource does not restate it, so a deep link would
   * otherwise head the sheet with the purpose while the card behind it says
   * the workout — the same session, named two ways, on one screen.
   */
  const namedWorkoutId =
    card === null ? (detail?.intent.workout_id ?? null) : null;
  const { data: workout } = $api.useQuery(
    "get",
    "/api/v1/workouts/{workout_id}",
    { params: { path: { workout_id: namedWorkoutId ?? "" } } },
    { enabled: namedWorkoutId !== null },
  );

  const header = sheetHeader(card, detail ?? null, workout?.name ?? null);
  const sessionDate = header?.date ?? "";
  const [moveDate, setMoveDate] = useState(sessionDate);
  const [copyDate, setCopyDate] = useState(sessionDate);

  // Reopening the sheet on a different card must not keep the previous card's
  // dates in the inputs — nor may the date arriving a request late leave the
  // pickers empty behind it.
  useEffect(() => {
    setMoveDate(sessionDate);
    setCopyDate(sessionDate);
  }, [sessionDate]);

  // An unapplied date in either picker is the only draft this sheet holds.
  const guard = useDirtyClose({
    dirty:
      sessionDate !== "" &&
      (moveDate !== sessionDate || copyDate !== sessionDate),
    onClose,
  });

  if (sessionId === null) {
    return null;
  }

  // Nothing to head the sheet with yet: either the fetch is in flight, or the
  // link names a session this plan does not have. A link that resolves to
  // nothing says so — closing over it would make a dead link look like one
  // that worked.
  if (header === null) {
    return (
      <Sheet open onOpenChange={guard.onOpenChange}>
        <SheetContent>
          <header className="flex flex-col gap-2.5 border-hairline border-b px-6 py-5">
            <div className="flex items-center justify-end">
              <SheetCloseButton />
            </div>
            <SheetTitle>
              {detailError ? "Could not open this session" : "Opening…"}
            </SheetTitle>
            <SheetDescription>
              {detailError
                ? (apiErrorMessages(detailError)[0] ??
                  "This link names a session that is not in the plan.")
                : "Fetching the session this link names."}
            </SheetDescription>
          </header>
        </SheetContent>
      </Sheet>
    );
  }

  const intent = detail?.intent;
  const structure = intent?.structure;
  const strength =
    structure?.discipline === "strength"
      ? (structure as StrengthStructure)
      : null;

  return (
    <Sheet open onOpenChange={guard.onOpenChange}>
      <SheetContent>
        <header className="flex flex-col gap-2.5 border-hairline border-b px-6 py-5">
          <div className="flex items-center gap-2.5">
            <PurposeBadge purpose={header.purpose} size="md" />
            <span className="font-mono text-xs text-ink-faint">
              {weekdayLabel(header.date)} {formatDayMonthYear(header.date)}
            </span>
            <span className="ml-auto flex items-center gap-1.5">
              <StatusDot status={header.status} />
              <span className="text-ink-muted text-xs">
                {STATUS_TONES[header.status].label}
              </span>
            </span>
            <SheetCloseButton />
          </div>
          <SheetTitle>{header.title}</SheetTitle>
          <SheetDescription>
            {header.intentText ?? "No intent recorded for this session."}
          </SheetDescription>
          <div className="flex items-center gap-3 pt-1 font-mono text-2xs text-ink-faint">
            <span className="flex items-center gap-1.5">
              <DisciplineIcon discipline={header.discipline} size={12} />
              {header.discipline === "strength"
                ? formatSets(header.totalSets)
                : formatDurationHm(header.plannedDurationS)}
            </span>
            <span>
              intent v{header.intentVersion}
              {intent?.edited_post_hoc ? " · edited post hoc" : ""}
            </span>
          </div>
        </header>

        <div className="flex flex-col gap-5 px-6 py-5">
          {isPending ? (
            <p className="text-ink-muted text-sm">Loading the prescription…</p>
          ) : null}

          {detailError ? (
            <p role="alert" className="text-destructive text-sm">
              {apiErrorMessages(detailError)[0] ??
                "Could not load this prescription."}
            </p>
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

          {strength && detail ? (
            <PredictedVolumeSection
              predicted={detail.predicted_volume}
              structure={strength}
            />
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
                {intent.success_criteria.map((criterion, index) => (
                  <li
                    // Criteria are ordered values, not entities: two identical
                    // ones are the same rule twice, and the list is replaced
                    // wholesale on every intent version.
                    // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
                    key={index}
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
            sessionId={sessionId}
            date={header.date}
            workoutId={card?.workout_id ?? intent?.workout_id ?? null}
            busy={busy}
            moveDate={moveDate}
            copyDate={copyDate}
            onMoveDateChange={setMoveDate}
            onCopyDateChange={setCopyDate}
            onMove={onMove}
            onCopy={onCopy}
            onDelete={onDelete}
            onEdit={onEdit}
          />

          {notice ? (
            <p role="status" className="text-sm text-status-completed">
              {notice}
            </p>
          ) : null}

          {problems.length > 0 ? (
            <ul
              role="alert"
              className="flex flex-col gap-1 rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
            >
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          ) : null}

          {guard.confirming ? (
            <DiscardPrompt
              what="the date you typed"
              onDiscard={guard.discard}
              onKeepEditing={guard.keepEditing}
            />
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}

/**
 * The header's facts, from the session when it has arrived and from the card
 * until then. `null` while there is neither.
 *
 * The session wins every field it holds: a card can be a refetch behind an
 * edit, and the resource is the session. The one thing it does not hold is the
 * title — a session is named after the library workout it was planned from,
 * and only the card (or `GET /workouts/{id}`) knows that name.
 */
function sheetHeader(
  card: WeekSession | null,
  detail: PlannedSession | null,
  workoutName: string | null,
): SheetHeader | null {
  if (detail) {
    const { intent } = detail;
    return {
      date: detail.date,
      discipline: detail.discipline,
      purpose: intent.purpose,
      status: detail.status,
      title: card?.title ?? workoutName ?? purposeLabel(intent.purpose),
      intentText: intent.intent_text,
      intentVersion: intent.version,
      plannedDurationS: intent.summary.total_duration_s,
      totalSets: intent.summary.total_sets,
    };
  }
  if (card) {
    return {
      date: card.date,
      discipline: card.discipline,
      purpose: card.purpose,
      status: card.status,
      title: card.title ?? purposeLabel(card.purpose),
      intentText: card.intent_text,
      intentVersion: card.intent_version,
      plannedDurationS: card.planned_duration_s,
      totalSets: card.total_sets,
    };
  }
  return null;
}

/** Move / copy / delete / edit, the four things a plan entry can have done to it. */
function SessionActions({
  sessionId,
  date,
  workoutId,
  busy,
  moveDate,
  copyDate,
  onMoveDateChange,
  onCopyDateChange,
  onMove,
  onCopy,
  onDelete,
  onEdit,
}: {
  sessionId: string;
  date: string;
  workoutId: string | null;
  busy: boolean;
  moveDate: string;
  copyDate: string;
  onMoveDateChange: (date: string) => void;
  onCopyDateChange: (date: string) => void;
  onMove: (sessionId: string, toDate: string) => void;
  onCopy: (sessionId: string, toDate: string) => void;
  onDelete: (sessionId: string) => void;
  onEdit: (sessionId: string, date: string) => void;
}) {
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
            onChange={(event) => onMoveDateChange(event.target.value)}
            className="font-mono"
          />
        </div>
        <Button
          variant="secondary"
          disabled={busy || moveDate === date}
          onClick={() => onMove(sessionId, moveDate)}
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
            onChange={(event) => onCopyDateChange(event.target.value)}
            className="font-mono"
          />
        </div>
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => onCopy(sessionId, copyDate)}
        >
          Copy
        </Button>
      </div>

      <div className="flex items-center gap-2 pt-1">
        {/* "Edit" means this *session* — its purpose, intent, criteria and
            prescription — not the library workout it may have come from.
            Editing the workout would change every session planned from it,
            which is exactly what the frozen snapshot exists to prevent. */}
        <Button variant="outline" onClick={() => onEdit(sessionId, date)}>
          Edit session
        </Button>
        {workoutId ? (
          <Button
            variant="ghost"
            className="text-ink-muted"
            render={<Link href={`/workouts/${workoutId}`}>Open workout</Link>}
          />
        ) : null}
        {/* Deleting a planned session destroys its intent history with it, and
            there is no undo. Two clicks, in the button's own slot. */}
        <ConfirmButton
          className="ml-auto"
          label="Delete"
          question="Delete this session?"
          confirmLabel="Delete"
          disabled={busy}
          onConfirm={() => onDelete(sessionId)}
        />
      </div>
    </section>
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
      <details className="rounded-button border border-hairline-faint bg-inset px-3 py-2">
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
 * The other axis: what a lifting session is expected to move, in kilograms.
 *
 * Never in the TSS slot and never added to one (spec v2 §5.4, §8.3) — the two
 * quantities have their own sections for the same reason the week rail gives
 * them their own columns. Volume load is `Σ sets × reps × kg`, so a session
 * prescribed in %e1RM or RPE has none until it is performed; that is
 * `NotAssessed` with the reason the prescription itself supplies, never a 0.
 */
function PredictedVolumeSection({
  predicted,
  structure,
}: {
  predicted: PredictedVolume | null;
  structure: StrengthStructure;
}) {
  const reason = unmeasuredVolumeReason(structure);
  return (
    <section className="flex flex-col gap-2">
      <SectionLabel level={3}>Predicted volume</SectionLabel>
      {predicted && predicted.volume_load_kg !== null ? (
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="font-mono font-semibold text-2xl">
            {Math.round(predicted.volume_load_kg)}
          </span>
          <span className="text-ink-muted text-xs">kg</span>
          <span className="font-mono text-ink-muted text-xs">
            {formatSets(predicted.total_sets)}
          </span>
          {/* Same rule as the ride's coverage: a volume computed from three of
              ten sets is not the session's volume, and the count says so. */}
          <span className="ml-auto font-mono text-2xs text-ink-faint">
            {formatPercent(predicted.coverage)} of the sets are prescribed in
            kilograms
          </span>
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          <div className="flex items-baseline gap-2">
            <NotAssessed
              reason={reason.short}
              className="font-semibold text-2xl"
            />
            <span className="text-ink-muted text-xs">kg</span>
            {predicted ? (
              <span className="font-mono text-ink-muted text-xs">
                {formatSets(predicted.total_sets)}
              </span>
            ) : null}
          </div>
          <p className="text-ink-muted text-sm">{reason.sentence}</p>
        </div>
      )}
    </section>
  );
}

/** How a load is prescribed, in the words the sentence below uses. */
const LOAD_KIND_WORDS: Readonly<Record<Schemas["LoadKind"], string>> = {
  kg: "kilograms",
  percent_e1rm: "%e1RM",
  rpe: "RPE",
  bodyweight: "bodyweight",
};

/**
 * Why this session has no volume load, read off the prescription itself.
 *
 * Never "no data": volume load is kilograms, and a session that states none is
 * saying something specific about how it is prescribed. Naming the forms it
 * *did* use is what makes the missing number legible (UI convention 3).
 */
function unmeasuredVolumeReason(structure: StrengthStructure): {
  short: string;
  sentence: string;
} {
  const kinds = new Set<Schemas["LoadKind"]>();
  for (const group of structure.groups) {
    for (const item of group.items) {
      kinds.add(item.load.kind);
    }
  }
  const words = [...kinds].map((kind) => LOAD_KIND_WORDS[kind]);
  if (words.length === 0) {
    return {
      short: "Nothing prescribed",
      sentence:
        "This session prescribes no movements yet, so there is no volume to total.",
    };
  }
  return {
    short: "No set is prescribed in kilograms",
    sentence:
      `Volume load is Σ sets × reps × kg, and this session prescribes its loads as ${listWords(words)}. ` +
      "The kilograms exist once it is performed, not before.",
  };
}

/** `a`, `a and b`, `a, b and c` — a list as a sentence says it. */
function listWords(words: readonly string[]): string {
  if (words.length <= 1) {
    return words[0] ?? "";
  }
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
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
          {group.items.map((item, itemIndex) => (
            <div
              // Lines are ordered positions in a group: the same movement
              // twice is a legal prescription, and the group is replaced
              // wholesale on every intent version.
              // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
              key={itemIndex}
              className="flex items-baseline justify-between gap-3 text-sm"
            >
              <span className="text-ink-secondary">
                {nameOf(item.exercise_id)}
              </span>
              <span className="font-mono text-ink text-xs">
                {describeSets(item)}
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
