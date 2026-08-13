import { describe, expect, it } from "vitest";

import type { components } from "@/generated/api/schema";
import {
  canReject,
  describeReport,
  INGEST_OUTCOMES,
  QUARANTINE_REASONS,
  QUARANTINE_STATUSES,
  REJECT_OFFERS,
  waitingLabel,
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

  it("offers reject for both verdicts the API lets you overrule", () => {
    // Generalised from duplicates-only: `implausible_channel` is overrulable too,
    // the cleaner nulls what it cannot believe — so the ride survives its
    // broken strap. Everything else is still a 409.
    expect(canReject("suspected_duplicate")).toBe(true);
    expect(canReject("implausible_channel")).toBe(true);
    expect(canReject("unreadable_file")).toBe(false);
    expect(canReject("too_short")).toBe(false);
    expect(canReject("no_samples")).toBe(false);
    expect(canReject("non_monotonic_timestamps")).toBe(false);
  });

  it("says what each reject actually does, in that verdict's own terms", () => {
    // The two are different acts, so one shared "Not a duplicate" over both
    // would describe only one of them.
    expect(REJECT_OFFERS.suspected_duplicate?.label).toBe("Not a duplicate");
    expect(REJECT_OFFERS.implausible_channel?.label).toBe("Ingest it anyway");
    expect(REJECT_OFFERS.implausible_channel?.question).toContain("blanked");
    // And the confirm side still offers discard for both: the remedy names it.
    expect(QUARANTINE_REASONS.implausible_channel.remedy).toContain("Discard");
  });
});

describe("waitingLabel", () => {
  // The endpoint's `total` counts every record, resolved ones included, and a
  // page is a page — so "how many are waiting on you" is answerable only where
  // the page has demonstrably reached past the pending ones.
  it("counts exactly when a resolved record proves the pending ran out", () => {
    expect(waitingLabel({ pending: 2, onPage: 3, offset: 0, total: 9 })).toBe(
      "2 waiting",
    );
    expect(waitingLabel({ pending: 0, onPage: 3, offset: 0, total: 9 })).toBe(
      "nothing waiting",
    );
  });

  it("counts exactly when the whole queue is on the page", () => {
    expect(waitingLabel({ pending: 3, onPage: 3, offset: 0, total: 3 })).toBe(
      "3 waiting",
    );
  });

  it("refuses to state a total it cannot see the end of", () => {
    // Fifty pending on a fifty-record page and fifty-eight records behind it:
    // the ones past the cut may be pending too, so the number is a floor.
    expect(
      waitingLabel({ pending: 50, onPage: 50, offset: 0, total: 58 }),
    ).toBe("at least 50 waiting");
  });

  it("reports the page, not the queue, once past the first one", () => {
    // Anywhere but the first page the pending records are behind us.
    expect(waitingLabel({ pending: 5, onPage: 8, offset: 50, total: 58 })).toBe(
      "5 waiting on this page",
    );
    expect(waitingLabel({ pending: 0, onPage: 8, offset: 50, total: 58 })).toBe(
      "nothing waiting on this page",
    );
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
