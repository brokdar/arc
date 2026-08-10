import type { components } from "@/generated/api/schema";
import { formatDayMonthYear } from "@/lib/format";
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
 * field of a session cannot render as though it rewrote all nine. A `create`
 * has no before and a `delete` no after; both still list every field, because
 * "what would this add?" and "what would this take away?" are questions about
 * the whole session rather than about a difference.
 *
 * It is computed on the **raw** snapshot values and never on `before`/`after`,
 * which are what the rows *render*: a workout id renders as its first eight
 * characters and two uuid7s minted in the same minute share them, and both
 * predictions are rounded to the figure a page prints. Comparing the rendered
 * strings made a real swap of the prescription — the thing the accept would
 * actually do — render as "no field differs", which is the diff lying about
 * the change it exists to describe.
 */
export interface FieldDiff {
  readonly key: keyof ProposalSnapshot;
  readonly label: string;
  readonly before: string | null;
  readonly after: string | null;
  readonly changed: boolean;
}

/** How each field of a snapshot is named and rendered. */
const FIELDS: readonly {
  key: keyof ProposalSnapshot;
  label: string;
  format: (snapshot: ProposalSnapshot) => string | null;
  /** Numerals, dates and ids are mono; prose is not (UI convention 5). */
  mono: boolean;
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
    key: "status",
    label: "Status",
    mono: false,
    format: (s) => STATUS_TONES[s.status].label,
  },
  {
    key: "workout_id",
    label: "Workout",
    mono: true,
    // The id, shortened the way a file hash is (`SessionDetail`): the proposal
    // carries no name for it, and inventing one would be a claim. "Described
    // inline" is the other real answer — a session prescribed by a structure
    // of its own rather than out of the library.
    format: (s) =>
      s.workout_id === null ? "Described inline" : s.workout_id.slice(0, 8),
  },
  {
    key: "intent_text",
    label: "Intent",
    mono: false,
    format: (s) => s.intent_text,
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
    // Today panel follows).
    format: (s) =>
      s.predicted_load === null ? null : `${Math.round(s.predicted_load)} TSS`,
  },
  {
    key: "predicted_volume_kg",
    label: "Predicted volume",
    mono: true,
    format: (s) =>
      s.predicted_volume_kg === null
        ? null
        : `${Math.round(s.predicted_volume_kg)} kg`,
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
        change.before[field.key] !== change.after[field.key],
    });
  }
  return rows;
}

/**
 * The date a change's header carries.
 *
 * The API's entry-level `date` is the date the change is *about*: the target
 * for a `create`, and the session's current date for everything else — so on
 * a `move` it is where the session is now, and where it would go lives in
 * `after.date` (`ProposalChangeDiff`). A header that printed only the former
 * would headline a move with the one date the move is trying to get rid of,
 * so a move shows the journey and every other kind shows its single date.
 *
 * Formatted, like every other date in the diff: the header and the Date row
 * are the same date said twice, and printing one as `2026-08-12` beside the
 * other as `12.08.2026` reads as two different facts.
 */
export function changeDateLabel(change: ProposalChangeDiff): string {
  const from = formatDayMonthYear(change.date);
  if (change.kind !== "move" || change.after === null) {
    return from;
  }
  const to = formatDayMonthYear(change.after.date);
  return to === from ? from : `${from} → ${to}`;
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
