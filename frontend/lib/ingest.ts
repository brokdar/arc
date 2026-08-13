import type { components } from "@/generated/api/schema";

export type QuarantineReason = components["schemas"]["QuarantineReason"];
export type QuarantineStatus = components["schemas"]["QuarantineStatus"];
export type IngestOutcome = components["schemas"]["IngestOutcome"];
export type QuarantineRecord = components["schemas"]["QuarantineRecordRead"];
export type IngestReport = components["schemas"]["IngestReportRead"];

/** Every cached quarantine page, whatever offset it was fetched at. */
export const QUARANTINE_QUERY_PREFIX = [
  "get",
  "/api/v1/ingest/quarantine",
] as const;

/** Every cached page of the ingest log. */
export const INGEST_EVENTS_QUERY_PREFIX = [
  "get",
  "/api/v1/ingest/events",
] as const;

/**
 * The machine-readable verdicts the pipeline records, said in English.
 *
 * The enum is the contract and the sentence is the interface: `too_short` is
 * what the column holds, "Too short to be a session" is what the athlete
 * reads, and the second line says what to *do* about it — an inbox row that
 * names a problem and no remedy is the dead end UI convention 3 forbids.
 *
 * Keyed by the generated enum, so a reason added to the backend fails the
 * type-check here before it reaches a page as a raw `snake_case` string.
 */
export interface ReasonCopy {
  /** The heading of the row. */
  readonly title: string;
  /** What the athlete can do about it, in one sentence. */
  readonly remedy: string;
}

export const QUARANTINE_REASONS: Readonly<
  Record<QuarantineReason, ReasonCopy>
> = {
  suspected_duplicate: {
    title: "Suspected duplicate",
    remedy:
      "It overlaps a session already recorded. Discard this copy, or say it is not a duplicate and it will be ingested as its own session.",
  },
  unreadable_file: {
    title: "Could not be read",
    remedy:
      "No parser could open the file. Re-export it from the head unit and drop it into the inbox again.",
  },
  no_samples: {
    title: "No samples in the file",
    remedy:
      "The file parsed but holds no data points. Re-export it and drop it in again.",
  },
  non_monotonic_timestamps: {
    title: "Timestamps run backwards",
    remedy:
      "The recording's clock jumps backwards, so no timeline can be built from it. Re-export it and drop it in again.",
  },
  too_short: {
    title: "Too short to be a session",
    remedy:
      "Under two minutes of recording. If that was a real session, record it by hand instead.",
  },
  implausible_channel: {
    title: "A channel is systematically implausible",
    remedy:
      "Too much of one channel is outside any physical range for a spike to explain — a mis-paired sensor, usually. Discard this copy and fix the pairing, or ingest it anyway: the broken channel arrives blanked and the rest of the ride is kept.",
  },
};

/** What the athlete decided about a quarantined file, said in English. */
export const QUARANTINE_STATUSES: Readonly<Record<QuarantineStatus, string>> = {
  pending: "Waiting on you",
  confirmed_discarded: "Discarded",
  rejected_ingested: "Ingested anyway",
};

/**
 * What the pipeline did with one file, said in English.
 *
 * `duplicate_file` is a success and reads like one: re-seeing a hash is the
 * idempotency guarantee working, not a failure to ingest.
 */
export const INGEST_OUTCOMES: Readonly<Record<IngestOutcome, string>> = {
  ingested: "Ingested",
  duplicate_file: "Already had it",
  quarantined: "Quarantined",
  error: "Failed",
};

/**
 * What "overrule this verdict" is called, per verdict that can be overruled.
 *
 * Two of them can (generalising the earlier "only a `suspected_duplicate`"):
 * `suspected_duplicate` waives the duplicate checks, and
 * `implausible_channel` waives the implausible-channel check and nothing else
 * — the cleaner nulls what it cannot believe, so the ride is ingested with the
 * broken channel blanked rather than with garbage in it. The API answers 409
 * for every other reason, so those get no button at all rather than a button
 * that is refused.
 *
 * The copy differs because the *act* differs, and a single "Not a duplicate"
 * over both would describe only one of them. Each entry says what the button
 * does, not which enum member it belongs to.
 */
export interface RejectOffer {
  /** The button that opens the question. */
  readonly label: string;
  /** What the confirm strip asks before it happens. */
  readonly question: string;
  /** The button that goes through with it. */
  readonly confirmLabel: string;
}

export const REJECT_OFFERS: Readonly<
  Partial<Record<QuarantineReason, RejectOffer>>
> = {
  suspected_duplicate: {
    label: "Not a duplicate",
    question: "Ingest this file as its own session?",
    confirmLabel: "Ingest it",
  },
  implausible_channel: {
    label: "Ingest it anyway",
    question: "Ingest it anyway — the broken channel arrives blanked?",
    confirmLabel: "Ingest it",
  },
};

/**
 * Whether overruling the verdict is an offer worth making.
 *
 * Derived from `REJECT_OFFERS` rather than re-listing the reasons, so a
 * verdict the product learns to overrule cannot become offerable without
 * copy that says what overruling it does.
 */
export function canReject(reason: QuarantineReason): boolean {
  return REJECT_OFFERS[reason] !== undefined;
}

/**
 * How many files are waiting on the athlete, said only as far as it is known.
 *
 * The count is a page's worth of records, and the endpoint's `total` is *every*
 * record — resolved ones included — so neither number is "how many are
 * pending". What makes an answer possible at all is the server's sort order:
 * `GET /ingest/quarantine` returns pending first (`list_quarantine`). So the
 * pending total is known exactly when this page has already reached past them:
 * either a resolved record appears on it, or it is the last page. Otherwise
 * the page is solid pending and there may be more behind it, and the label
 * says "at least" rather than a number it cannot stand behind.
 *
 * Anywhere but the first page the question is not answerable at all — the
 * pending records are behind us — so the label reports the page instead.
 */
export function waitingLabel(queue: {
  /** Pending records on the page in hand. */
  readonly pending: number;
  /** Records on the page in hand, pending or not. */
  readonly onPage: number;
  /** The offset this page was fetched at. */
  readonly offset: number;
  /** Every record the queue holds, whatever its status. */
  readonly total: number;
}): string {
  const { pending, onPage, offset, total } = queue;
  const lastPage = offset + onPage >= total;
  if (offset > 0) {
    return lastPage && pending === 0
      ? "nothing waiting on this page"
      : `${pending} waiting on this page`;
  }
  if (pending < onPage || lastPage) {
    return pending === 0 ? "nothing waiting" : `${pending} waiting`;
  }
  return `at least ${pending} waiting`;
}

/**
 * What an upload achieved, as a sentence the panel can print.
 *
 * Branches on `outcome`, never on the status code: the API answers 200 for a
 * file it refused, because a quarantined file is a *result*. A client
 * that read the status instead would report "uploaded" and show nothing.
 */
export function describeReport(report: IngestReport): string {
  const sessions = report.session_ids.length;
  switch (report.outcome) {
    case "ingested":
      return sessions === 1
        ? `${report.filename} was ingested.`
        : `${report.filename} was ingested as ${sessions} sessions.`;
    case "duplicate_file":
      return `${report.filename} was already ingested — nothing changed.`;
    case "quarantined":
      return `${report.filename} was quarantined: it is waiting on you below.`;
    case "error":
      return `${report.filename} could not be ingested.`;
  }
}
