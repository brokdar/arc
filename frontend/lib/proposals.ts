import type { components } from "@/generated/api/schema";
import { DISCIPLINE_LABELS } from "@/lib/activity";
import { formatDayMonthYear, formatDurationHm } from "@/lib/format";
import { purposeLabel, STATUS_TONES } from "@/lib/purpose";

type Schemas = components["schemas"];

export type Proposal = Schemas["ProposalRead"];
export type ProposalStatus = Schemas["ProposalStatus"];
export type ProposalChangeDiff = Schemas["ProposalChangeDiff"];
export type ProposalSnapshot = Schemas["ProposalSessionSnapshot"];
export type ChangeKind = Schemas["ChangeKind"];

/** Every cached page of the inbox, whichever status it was filtered by. */
export const PROPOSALS_QUERY_PREFIX = ["get", "/api/v1/proposals"] as const;

/**
 * The statuses, in the order the filter offers them.
 *
 * `pending` first because it is the default and the only one that is a queue;
 * the other five are outcomes, ordered by how they were reached — the athlete
 * answered (accepted, rejected), or nobody did (lapsed, superseded, resolved
 * by reality).
 */
export const PROPOSAL_STATUSES: readonly ProposalStatus[] = [
  "pending",
  "accepted",
  "rejected",
  "lapsed",
  "superseded",
  "resolved_by_reality",
];

/**
 * What each status *means*, not what it is called in the database.
 *
 * "Lapsed" and "overtaken by what you did" are both "nobody answered", and the
 * difference between them is the whole point of the second one: the plan stood
 * because the expiry ran out, or because the athlete went and trained
 * something that contradicted the suggestion. A label reading `resolved by
 * reality` would make the athlete look that up.
 */
export const PROPOSAL_STATUS_LABELS: Readonly<Record<ProposalStatus, string>> =
  {
    pending: "Waiting on you",
    accepted: "Accepted",
    rejected: "Rejected",
    lapsed: "Lapsed",
    superseded: "Superseded",
    resolved_by_reality: "Overtaken by what you did",
  };

/**
 * The tone a status badge carries.
 *
 * Only `pending` is coloured, and it borrows the warn tint the quarantine
 * queue uses for the same statement — something is waiting on you. Every
 * resolved status is neutral: a proposal the athlete rejected is not a
 * failure, and painting it red would say it was.
 */
export const PROPOSAL_STATUS_TONES: Readonly<Record<ProposalStatus, string>> = {
  pending: "border-warn-border bg-warn-surface text-status-under",
  accepted: "border-hairline text-status-completed",
  rejected: "border-hairline text-ink-muted",
  lapsed: "border-hairline text-ink-muted",
  superseded: "border-hairline text-ink-muted",
  resolved_by_reality: "border-hairline text-ink-muted",
};

/** What one change would do to the plan, in the verb the athlete would use. */
export const CHANGE_KIND_LABELS: Readonly<Record<ChangeKind, string>> = {
  create: "Add",
  update: "Change",
  move: "Move",
  delete: "Remove",
};

/**
 * The tone a change-kind badge carries.
 *
 * Adding and removing a session are the two that change what is *in* the
 * plan, so they take the completed green and the missed red — the colours the
 * calendar already spends on "this happened" and "this did not". Changing and
 * moving one take the accent, because both leave the plan's shape alone and
 * differ only in which field they touch; the word beside the colour is what
 * tells them apart, and it always is (no badge here is colour alone).
 */
export const CHANGE_KIND_TONES: Readonly<Record<ChangeKind, string>> = {
  create: "text-status-completed",
  update: "text-accent",
  move: "text-accent",
  delete: "text-status-missed",
};

/**
 * One row of a change's diff: a field, what it was, and what it would become.
 *
 * `changed` is computed rather than asserted, so a proposal that touches one
 * field of a session cannot render as though it rewrote every one. A `create`
 * has no before and a `delete` no after; both still list every field, because
 * "what would this add?" and "what would this take away?" are questions about
 * the whole session rather than about a difference.
 *
 * It is computed on the **raw** snapshot values and never on `before`/`after`,
 * which are what the rows *render*: both predictions are shown at a precision
 * a page can read, so a sub-integer re-pin that the accept would actually write
 * can print the same figure on both sides, and the structured fields render as
 * a summary that two different bodies can share. Comparing the rendered strings
 * made a real change of the prescription render as "no field differs", which is
 * the diff lying about the change it exists to describe. The two JSON fields —
 * `structure` and `success_criteria` — are compared by value (`same`), because
 * the API mints a fresh object for each side and reference identity would call
 * every one of them a change.
 */
export interface FieldDiff {
  readonly key: keyof ProposalSnapshot;
  readonly label: string;
  readonly before: string | null;
  readonly after: string | null;
  readonly changed: boolean;
}

/**
 * Whether two raw values of a field are equal by value.
 *
 * `structure` is a workout body and `success_criteria` a list of them, so a
 * fresh object off the wire is `!==` an identical one and needs a structural
 * comparison; every scalar field is compared with `===`.
 */
function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) {
    return true;
  }
  if (
    typeof a !== "object" ||
    typeof b !== "object" ||
    a === null ||
    b === null
  ) {
    return false;
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) {
      return false;
    }
    return a.every((item, index) => deepEqual(item, b[index]));
  }
  const aKeys = Object.keys(a as Record<string, unknown>);
  const bKeys = Object.keys(b as Record<string, unknown>);
  if (aKeys.length !== bKeys.length) {
    return false;
  }
  return aKeys.every((key) =>
    deepEqual(
      (a as Record<string, unknown>)[key],
      (b as Record<string, unknown>)[key],
    ),
  );
}

/** A load or volume figure at a precision a change of it survives (FIX-F3). */
function formatMagnitude(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/**
 * How many prescribed blocks a structure holds — steps for an endurance body,
 * exercises across the groups for a strength one. Zero for an empty structure,
 * which is what a session described only by a library id (or nothing) carries.
 */
function structureBlocks(structure: ProposalSnapshot["structure"]): number {
  const body = structure as {
    steps?: unknown[];
    groups?: { items?: unknown[] }[];
  };
  if (Array.isArray(body.steps)) {
    return body.steps.length;
  }
  if (Array.isArray(body.groups)) {
    return body.groups.reduce(
      (total, group) =>
        total + (Array.isArray(group?.items) ? group.items.length : 0),
      0,
    );
  }
  return 0;
}

/** How each field of a snapshot is named and rendered. */
const FIELDS: readonly {
  key: keyof ProposalSnapshot;
  label: string;
  format: (snapshot: ProposalSnapshot) => string | null;
  /** Numerals, dates and ids are mono; prose is not (UI convention 5). */
  mono: boolean;
  /** How two raw values of this field are compared; `===` unless given. */
  same?: (a: unknown, b: unknown) => boolean;
}[] = [
  {
    key: "date",
    label: "Date",
    mono: true,
    format: (s) => formatDayMonthYear(s.date),
  },
  {
    key: "purpose",
    label: "Purpose",
    mono: false,
    format: (s) => purposeLabel(s.purpose),
  },
  {
    key: "discipline",
    label: "Discipline",
    mono: false,
    // A cross-discipline change (a ride becomes a lift) otherwise repaints the
    // card header silently: the header carries the change's *current*
    // discipline, so only a row of its own shows the swap (FIX-F7).
    format: (s) => DISCIPLINE_LABELS[s.discipline],
  },
  {
    key: "status",
    label: "Status",
    mono: false,
    format: (s) => STATUS_TONES[s.status].label,
  },
  {
    key: "workout_id",
    label: "Workout",
    mono: true,
    // The full id, not a prefix: two uuid7s minted in the same millisecond
    // share their first several characters (the ms timestamp), and a
    // batch-seeded library swaps between exactly those — a shortened id drew a
    // real swap as `0199a000 → 0199a000` (FIX-F2). The proposal carries no
    // name, and inventing one would be a claim; "Described inline" is the
    // honest reading of a session prescribed by a body of its own.
    format: (s) => (s.workout_id === null ? "Described inline" : s.workout_id),
  },
  {
    key: "structure",
    label: "Prescription",
    mono: false,
    same: deepEqual,
    // A summary, not the body itself: enough that a change of the prescription
    // shows (the block count moves with steps or exercises, the magnitude with
    // the duration or the sets), so a structure-only revision is no longer a
    // silent "no field differs" above an enabled Accept (FIX-F1). Absent when
    // there is no body — a library-only or unstructured session — so the row
    // does not print "— → —" on every ride.
    format: (s) => {
      const blocks = structureBlocks(s.structure);
      if (blocks === 0) {
        return null;
      }
      if (s.discipline === "strength") {
        const sets =
          s.total_sets === null
            ? ""
            : `, ${s.total_sets} set${s.total_sets === 1 ? "" : "s"}`;
        return `${blocks} exercise${blocks === 1 ? "" : "s"}${sets}`;
      }
      const time =
        s.duration_s === null ? "" : `, ${formatDurationHm(s.duration_s)}`;
      return `${blocks} step${blocks === 1 ? "" : "s"}${time}`;
    },
  },
  {
    key: "intent_text",
    label: "Intent",
    mono: false,
    format: (s) => s.intent_text,
  },
  {
    key: "success_criteria",
    label: "Success criteria",
    mono: false,
    same: deepEqual,
    // Counted, for the same reason the structure is summarised: a criteria-only
    // revision must move a row rather than pass as unchanged (FIX-F1). Absent
    // when a session states none.
    format: (s) => {
      const count = s.success_criteria.length;
      if (count === 0) {
        return null;
      }
      return `${count} ${count === 1 ? "criterion" : "criteria"}`;
    },
  },
  {
    key: "coach_notes",
    label: "Notes",
    mono: false,
    format: (s) => s.coach_notes,
  },
  {
    key: "predicted_load",
    label: "Predicted load",
    mono: true,
    // Absent rather than zero when the prescription has no power target: "0
    // TSS" would be a claim the arithmetic never made (the same rule the
    // Today panel follows). Shown to a decimal when it has one, so a re-pin
    // that shifts the load below a whole point does not print twice the same.
    format: (s) =>
      s.predicted_load === null
        ? null
        : `${formatMagnitude(s.predicted_load)} TSS`,
  },
  {
    key: "predicted_volume_kg",
    label: "Predicted volume",
    mono: true,
    format: (s) =>
      s.predicted_volume_kg === null
        ? null
        : `${formatMagnitude(s.predicted_volume_kg)} kg`,
  },
];

/** Whether a field of this diff renders in the mono face. */
export function isMonoField(key: keyof ProposalSnapshot): boolean {
  return FIELDS.find((field) => field.key === key)?.mono ?? false;
}

/**
 * The whole of one change, field by field.
 *
 * The two prediction axes are both in the list and only one is ever populated
 * — an endurance session has a TSS-equivalent and a strength one kilograms
 * (`ProposalSessionSnapshot`) — so a row whose value is absent on *both* sides
 * is dropped entirely. It is not an unchanged field; it is a quantity this
 * discipline does not have, and printing "— → —" under "Predicted volume" for
 * a bike ride is noise that costs the reader a beat every time.
 */
export function changeFields(change: ProposalChangeDiff): FieldDiff[] {
  const rows: FieldDiff[] = [];
  for (const field of FIELDS) {
    const before = change.before ? field.format(change.before) : null;
    const after = change.after ? field.format(change.after) : null;
    if (before === null && after === null) {
      continue;
    }
    const equal = field.same ?? ((a, b) => a === b);
    rows.push({
      key: field.key,
      label: field.label,
      before,
      after,
      // Raw, not rendered (see `FieldDiff`). A missing snapshot makes every
      // row of a `create` or a `delete` a change: there is no value on that
      // side to be equal to, and `null === null` on two fields of two absent
      // sessions is not agreement.
      changed:
        change.before === null ||
        change.after === null ||
        !equal(change.before[field.key], change.after[field.key]),
    });
  }
  return rows;
}

/**
 * The date a change's header carries.
 *
 * The API's entry-level `date` is the date the change is *about*: the target
 * for a `create`, and the session's current date for everything else — so
 * where the session is now, with where it would go living in `after.date`
 * (`ProposalChangeDiff`). A header that printed only the former would headline
 * a reschedule with the one date it is trying to get rid of, so any change
 * whose `after` lands on a different day shows the journey — a `move`, but
 * also an `update` that reschedules while it revises (FIX-F6) — and everything
 * that stays put shows its single date.
 *
 * Formatted, like every other date in the diff: the header and the Date row
 * are the same date said twice, and printing one as `2026-08-12` beside the
 * other as `12.08.2026` reads as two different facts.
 */
export function changeDateLabel(change: ProposalChangeDiff): string {
  const from = formatDayMonthYear(change.date);
  if (change.after === null) {
    return from;
  }
  const to = formatDayMonthYear(change.after.date);
  return to === from ? from : `${from} → ${to}`;
}

/**
 * Whether a proposal can still be accepted or rejected right now.
 *
 * `pending` alone is not enough: expiry is enforced at accept time and the
 * sweep that flips a lapsed one to `lapsed` runs on a schedule, so a proposal
 * can read `pending` with its expiry already behind it. Offering Accept on it
 * invites a click that the server answers 409 — the athlete is being asked to
 * apply a plan change the plan will refuse (FIX-F4).
 */
export function isActionable(
  proposal: Proposal,
  now: number = Date.now(),
): boolean {
  return proposal.status === "pending" && Date.parse(proposal.expires_at) > now;
}

/**
 * How long a pending proposal has left, said in days.
 *
 * Days rather than hours because that is the unit the decision is made in: a
 * proposal about Thursday's session written on Monday is answered when the
 * athlete next opens the app, not in the next ninety minutes. "Today" is the
 * floor — anything under a day is the last chance to answer, and rounding it
 * to "in 0 days" would read as already gone.
 */
export function expiryLabel(expiresAt: string, now: Date = new Date()): string {
  const remainingMs = Date.parse(expiresAt) - now.getTime();
  if (Number.isNaN(remainingMs)) {
    return "";
  }
  if (remainingMs <= 0) {
    return "expired";
  }
  const days = Math.floor(remainingMs / 86_400_000);
  if (days === 0) {
    return "expires today";
  }
  return days === 1 ? "expires tomorrow" : `expires in ${days} days`;
}

/**
 * Who wrote a proposal, as the audit actor spells it.
 *
 * `agent:<key-label>` is the actor string the MCP server records
 * (`app.domain.actor`), and the label is the half that identifies *which*
 * agent — the prefix is the same on every row and carries no information once
 * the page has already said this is the coach.
 */
export function actorLabel(actor: string): string {
  return actor.startsWith("agent:") ? actor.slice("agent:".length) : actor;
}
