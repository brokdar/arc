import { describe, expect, it } from "vitest";

import {
  contentHash,
  ingestedSessionFixture,
  longQuarantineFixture,
  noteList,
  patchAthlete,
  proposalList,
  resetAgentState,
  sessionRunFixture,
} from "@/tests/mocks/fixtures";

/**
 * The fixtures test themselves where a type cannot.
 *
 * `contentHash` is typed `string` and `file_hash` is typed `string`, so the
 * one property that matters about a digest — that it looks like one — is
 * exactly the property the generated schema cannot check. It was wrong:
 * `Math.imul` is signed, so around two words in five stringified with a
 * leading `-`, and `Number.parseInt(hash.slice(0, 6), 16)` downstream returned
 * NaN for them. The same goes for the arithmetic in the paged fixtures: the
 * shape is proved by the compiler, the sums are not.
 */

/** Sixty-four lowercase hex digits, and nothing else — sha256's own shape. */
const SHA256 = /^[0-9a-f]{64}$/;

describe("contentHash", () => {
  it("produces 64 hex digits for anything it is given", () => {
    const samples = [
      "",
      "FIT ride bytes",
      // The exact bytes `inbox.test.tsx` uploads, which is where the signed
      // overflow used to surface.
      "FIT the very same ride",
      "date,power\n",
      "a synthetic FIT file",
      ...Array.from({ length: 200 }, (_, index) => `lap-${index}`),
      ...Array.from({ length: 200 }, (_, index) => `ride-${index}`),
      // The NUL is written as an escape rather than typed in: a raw one in the
      // source makes git call this whole file binary, and a test whose diffs
      // nobody can read is a test nobody reviews.
      "\x00￿é non-ascii ✓",
    ];
    for (const sample of samples) {
      expect(
        contentHash(sample),
        `digest of ${JSON.stringify(sample)}`,
      ).toMatch(SHA256);
    }
  });

  it("is a function of the content, which is what makes a duplicate one", () => {
    expect(contentHash("same bytes")).toBe(contentHash("same bytes"));
    expect(contentHash("same bytes")).not.toBe(contentHash("other bytes"));
  });

  it("hands the ingest fixture a seed it can read as a number", () => {
    // The downstream consumer: a digest with a `-` in it made this NaN, and
    // every duration derived from it NaN with it.
    const hash = contentHash("FIT the very same ride");
    expect(Number.isNaN(Number.parseInt(hash.slice(0, 6), 16))).toBe(false);
    const session = ingestedSessionFixture(hash, "ride.fit");
    expect(Number.isFinite(session.duration_s)).toBe(true);
    expect(session.duration_s).toBeGreaterThan(0);
  });
});

describe("the paged fixtures", () => {
  it("orders the queue the way the API sorts it: pending first", () => {
    const queue = longQuarantineFixture(55, 3);

    expect(queue).toHaveLength(58);
    const firstResolved = queue.findIndex((row) => row.status !== "pending");
    expect(firstResolved).toBe(55);
    // A resolved record has a resolution time; a pending one does not.
    expect(
      queue.every(
        (row) => (row.resolved_at === null) === (row.status === "pending"),
      ),
    ).toBe(true);
    expect(new Set(queue.map((row) => row.id)).size).toBe(58);
  });

  it("keeps each ride's duration equal to elapsed minus its stops", () => {
    for (const session of sessionRunFixture(30)) {
      const [recording] = session.recordings;
      if (!recording) {
        throw new Error("every row in the run is a device session");
      }
      const paused = recording.recording_stops.reduce(
        (total, stop) => total + (stop.end_index - stop.start_index),
        0,
      );
      // D101, exactly: elapsed − recording is the sum of the stop rows.
      expect(recording.elapsed_time_s - recording.recording_time_s).toBe(
        paused,
      );
      // And the row's duration is the recording time, as `_duration` returns.
      expect(session.duration_s).toBe(recording.recording_time_s);
      expect(session.recording_time_s).toBe(recording.recording_time_s);
      // `end_time − start_time` is the elapsed time, not the recording time.
      expect(
        (Date.parse(session.end_time) - Date.parse(session.start_time)) / 1000,
      ).toBe(recording.elapsed_time_s);
    }
  });

  it("reads newest first, one day apart", () => {
    const run = sessionRunFixture(30).map((session) => session.local_date);

    expect(run[0]).toBe("2026-08-06");
    expect(run[1]).toBe("2026-08-05");
    expect([...run].sort().reverse()).toEqual(run);
    expect(new Set(run).size).toBe(30);
  });
});

describe("the coach's fixtures", () => {
  it("gives every change at least one field it actually changes", () => {
    // The backend computes the after-snapshot by *applying* a change to the
    // before-snapshot, so a field a change does not touch is the same value
    // twice. Compared by value, not by reference: the snapshot now carries the
    // structured body — `structure` and `success_criteria` — and a change that
    // touches only the body (a criteria-only revision) is a real change the
    // diff must show, so this counts it as touched rather than demanding a
    // scalar column move (which projected such a change onto "no field
    // differs" back when the body was not in the snapshot at all).
    for (const proposal of proposalList()) {
      for (const change of proposal.diff) {
        if (!change.before || !change.after) {
          continue;
        }
        const before = change.before;
        const after = change.after;
        const touched = Object.keys(after).filter(
          (key) =>
            JSON.stringify(after[key as keyof typeof after]) !==
            JSON.stringify(before[key as keyof typeof before]),
        );
        expect(
          touched.length,
          `${proposal.id} / ${change.kind} changes nothing`,
        ).toBeGreaterThan(0);
      }
    }
  });

  it("gives each change kind the sides it is allowed to have", () => {
    // A create has no before and a delete no after; the other two have both.
    // Any other combination is a change the API could not have computed.
    for (const proposal of proposalList()) {
      for (const change of proposal.diff) {
        expect(change.before === null, `${change.kind} before`).toBe(
          change.kind === "create",
        );
        expect(change.after === null, `${change.kind} after`).toBe(
          change.kind === "delete",
        );
        // A create names no session yet, and everything else names one.
        expect(change.planned_session_id === null).toBe(
          change.kind === "create",
        );
      }
    }
  });

  it("carries exactly one prediction axis per snapshot", () => {
    // An endurance session has a TSS-equivalent and no kilograms, a strength
    // one has kilograms and no TSS. They are different quantities and a
    // snapshot holding both would be one the API cannot produce.
    for (const proposal of proposalList()) {
      for (const change of proposal.diff) {
        for (const side of [change.before, change.after]) {
          if (!side) {
            continue;
          }
          expect(
            side.predicted_load !== null && side.predicted_volume_kg !== null,
            `${side.discipline} on ${side.date} predicts both axes`,
          ).toBe(false);
          if (side.discipline === "strength") {
            expect(side.predicted_load).toBeNull();
          } else {
            expect(side.predicted_volume_kg).toBeNull();
          }
        }
      }
    }
  });

  it("resolves every proposal that is not pending, and no pending one", () => {
    for (const proposal of proposalList()) {
      expect(
        proposal.resolved_at === null,
        `${proposal.status} resolved_at`,
      ).toBe(proposal.status === "pending");
      // A supersede link points at a proposal that exists and points back.
      if (proposal.superseded_by_id) {
        const successor = proposalList().find(
          (other) => other.id === proposal.superseded_by_id,
        );
        expect(successor?.supersedes_id).toBe(proposal.id);
        expect(proposal.status).toBe("superseded");
      }
    }
  });

  it("gives every note exactly one subject", () => {
    // `session_id` or `plan_week`, never both and never neither: a note has
    // one subject, so the API refuses a query with two and cannot store a row
    // with none.
    for (const note of noteList()) {
      expect(
        (note.session_id === null) !== (note.plan_week === null),
        `${note.id} has ${note.session_id ? "a session" : "no session"} and ${
          note.plan_week ? "a week" : "no week"
        }`,
      ).toBe(true);
      // A rating and its instant travel together, in both directions.
      expect(note.dispute === null).toBe(note.disputed_at === null);
    }
  });

  it("dates a plan-week note on the Monday the week starts on", () => {
    for (const note of noteList()) {
      if (note.plan_week) {
        expect(new Date(`${note.plan_week}T00:00:00Z`).getUTCDay()).toBe(1);
      }
    }
  });

  it("refuses a red flag lowered with a note or a severity still attached", () => {
    // The backend keeps note and severity supplied in the patch and lets the
    // domain refuse the pair while the flag is down — they describe an illness
    // the same request says is over (`app.domain.athlete`). A handler that
    // silently swallowed them would let a broken form pass.
    resetAgentState();
    const refusal = patchAthlete({
      red_flag_active: false,
      red_flag_severity: "severe",
      red_flag_note: "Still sore.",
    });
    expect(refusal).toEqual({
      detail:
        "red_flag_note and red_flag_severity may only be set while " +
        "red_flag_active is set",
    });

    // Lowering it on its own is honoured, and clears the pair.
    const cleared = patchAthlete({ red_flag_active: false });
    expect(cleared).toMatchObject({
      athlete: { red_flag_active: false, red_flag_note: null },
    });
    resetAgentState();
  });
});
