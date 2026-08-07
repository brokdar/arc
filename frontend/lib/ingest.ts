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
      "Too much of one channel is outside any physical range for a spike to explain — a mis-paired sensor, usually. Fix the pairing and re-record.",
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
 * Whether "this is not a duplicate" is an offer worth making.
 *
 * Only a `suspected_duplicate` holds something safe to ingest; the API answers
 * 409 for every other reason (D98), and disagreeing with the parser does not
 * make the bytes readable. So the button is not rendered rather than rendered
 * and refused.
 */
export function canReject(reason: QuarantineReason): boolean {
  return reason === "suspected_duplicate";
}

/**
 * What an upload achieved, as a sentence the panel can print.
 *
 * Branches on `outcome`, never on the status code: the API answers 200 for a
 * file it refused, because a quarantined file is a *result* (D97). A client
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
