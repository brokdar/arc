import { describe, expect, it } from "vitest";

import type { components } from "@/generated/api/schema";
import {
  canReject,
  describeReport,
  INGEST_OUTCOMES,
  QUARANTINE_REASONS,
  QUARANTINE_STATUSES,
} from "@/lib/ingest";

type Schemas = components["schemas"];

/** A report shaped the way `IngestService.upload` answers with one. */
function report(over: Partial<Schemas["IngestReportRead"]>) {
  return {
    filename: "ride.fit",
    file_hash: "a".repeat(64),
    outcome: "ingested" as const,
    detail: null,
    session_ids: ["0199a000-0000-7000-8000-000000000101"],
    quarantine_ids: [],
    ...over,
  };
}

describe("the ingest vocabulary", () => {
  it("has a sentence for every verdict the backend can record", () => {
    // The enums are the contract; these tables are the interface. A member
    // added to either one fails the type-check before it reaches a page as a
    // raw snake_case string — this pins that the tables are *complete*.
    const reasons: readonly Schemas["QuarantineReason"][] = [
      "no_samples",
      "non_monotonic_timestamps",
      "too_short",
      "implausible_channel",
      "unreadable_file",
      "suspected_duplicate",
    ];
    for (const reason of reasons) {
      expect(QUARANTINE_REASONS[reason].title).not.toBe("");
      // Every reason says what to do about it: UI convention 3 applies to a
      // row as much as to an empty page.
      expect(QUARANTINE_REASONS[reason].remedy).not.toBe("");
    }
    expect(Object.keys(QUARANTINE_STATUSES)).toEqual([
      "pending",
      "confirmed_discarded",
      "rejected_ingested",
    ]);
    // "Already had it" rather than a failure: a known hash is the idempotency
    // guarantee working.
    expect(INGEST_OUTCOMES.duplicate_file).toBe("Already had it");
  });

  it("offers reject only where there is something safe to ingest", () => {
    expect(canReject("suspected_duplicate")).toBe(true);
    expect(canReject("unreadable_file")).toBe(false);
    expect(canReject("too_short")).toBe(false);
  });
});

describe("describeReport", () => {
  it("says what happened, per outcome", () => {
    expect(describeReport(report({}))).toBe("ride.fit was ingested.");
    expect(
      describeReport(
        report({ session_ids: ["a", "b"] as unknown as string[] }),
      ),
    ).toBe("ride.fit was ingested as 2 sessions.");
    expect(describeReport(report({ outcome: "duplicate_file" }))).toBe(
      "ride.fit was already ingested — nothing changed.",
    );
    expect(
      describeReport(
        report({
          outcome: "quarantined",
          session_ids: [],
          quarantine_ids: ["q"],
        }),
      ),
    ).toBe("ride.fit was quarantined: it is waiting on you below.");
    expect(describeReport(report({ outcome: "error", session_ids: [] }))).toBe(
      "ride.fit could not be ingested.",
    );
  });
});
