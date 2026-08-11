import { describe, expect, it } from "vitest";

import type { components } from "@/generated/api/schema";
import {
  actorLabel,
  changeDateLabel,
  changeFields,
  expiryLabel,
  type ProposalSnapshot,
} from "@/lib/proposals";

type Schemas = components["schemas"];

const BASE: ProposalSnapshot = {
  date: "2026-08-13",
  discipline: "cycling",
  purpose: "vo2max",
  status: "planned",
  workout_id: null,
  structure: {
    discipline: "cycling",
    steps: [
      { kind: "steady", role: "warmup", duration_s: 600 },
      { kind: "steady", role: "work", duration_s: 2400 },
      { kind: "steady", role: "cooldown", duration_s: 600 },
    ],
  },
  intent_text: "Six by three at 118 %.",
  success_criteria: [
    { kind: "time_in_band", min_fraction: 0.75 },
    { kind: "duration_floor", min_seconds: 3000 },
  ],
  coach_notes: null,
  duration_s: 3600,
  total_sets: null,
  predicted_load: 84,
  predicted_volume_kg: null,
};

function change(
  kind: Schemas["ChangeKind"],
  before: ProposalSnapshot | null,
  after: ProposalSnapshot | null,
): Schemas["ProposalChangeDiff"] {
  return {
    kind,
    planned_session_id: null,
    date: "2026-08-13",
    discipline: "cycling",
    expected_intent_version: 1,
    before,
    after,
  };
}

describe("what one change actually changes", () => {
  it("marks only the fields that differ", () => {
    const rows = changeFields(
      change("update", BASE, { ...BASE, purpose: "threshold" }),
    );

    expect(rows.filter((row) => row.changed).map((row) => row.key)).toEqual([
      "purpose",
    ]);
    const purpose = rows.find((row) => row.key === "purpose");
    expect(purpose).toMatchObject({ before: "VO₂max", after: "Threshold" });
  });

  it("prints a sub-integer load change rather than the same figure twice", () => {
    // The load is shown to a decimal when it has one, so a re-pin that shifts
    // it below a whole point — the thing the accept would actually write — does
    // not print "84 TSS → 84 TSS" and read as though nothing moved (FIX-F3).
    const rows = changeFields(
      change("update", BASE, { ...BASE, predicted_load: 84.4 }),
    );

    const load = rows.find((row) => row.key === "predicted_load");
    expect(load?.changed).toBe(true);
    expect(load).toMatchObject({ before: "84 TSS", after: "84.4 TSS" });
  });

  it("sees a workout swap and prints the ids in full so it shows", () => {
    // uuid7s minted in the same millisecond share their leading characters (the
    // timestamp), and a batch-seeded library swaps between exactly those. The
    // row now prints the whole id, so a swapped prescription is a visible
    // before/after rather than `0199a000 → 0199a000` (FIX-F2).
    const before = "0199a000-0000-7000-8000-00000000aaaa";
    const after = "0199a000-0000-7000-8000-00000000bbbb";
    const rows = changeFields(
      change(
        "update",
        { ...BASE, workout_id: before },
        { ...BASE, workout_id: after },
      ),
    );

    const workout = rows.find((row) => row.key === "workout_id");
    expect(workout?.changed).toBe(true);
    expect(workout).toMatchObject({ before, after });
  });

  it("marks a criteria-only change, and a structure-only one, as changed", () => {
    // A revision that touches only the body — the success criteria or the
    // prescription — used to project onto no visible field and render as "no
    // field differs" above an enabled Accept. Both are diff rows now (FIX-F1).
    const criteriaOnly = changeFields(
      change("update", BASE, {
        ...BASE,
        success_criteria: [{ kind: "duration_floor", min_seconds: 3000 }],
      }),
    );
    expect(
      criteriaOnly.filter((row) => row.changed).map((row) => row.key),
    ).toEqual(["success_criteria"]);
    expect(
      criteriaOnly.find((row) => row.key === "success_criteria"),
    ).toMatchObject({ before: "2 criteria", after: "1 criterion" });

    const structureOnly = changeFields(
      change("update", BASE, {
        ...BASE,
        structure: {
          discipline: "cycling",
          steps: [{ kind: "steady", role: "work", duration_s: 3600 }],
        },
        duration_s: 3600,
      }),
    );
    expect(
      structureOnly.filter((row) => row.changed).map((row) => row.key),
    ).toEqual(["structure"]);
    expect(structureOnly.find((row) => row.key === "structure")).toMatchObject({
      before: "3 steps, 1:00",
      after: "1 step, 1:00",
    });
  });

  it("does not mistake a fresh copy of an unchanged body for a change", () => {
    // The API mints a new object for each side, so the two JSON fields are
    // compared by value: a structure that was not touched is the same value on
    // both sides even though it is not the same reference.
    const rows = changeFields(
      change("update", BASE, {
        ...BASE,
        purpose: "threshold",
        structure: JSON.parse(JSON.stringify(BASE.structure)),
        success_criteria: JSON.parse(JSON.stringify(BASE.success_criteria)),
      }),
    );

    expect(rows.filter((row) => row.changed).map((row) => row.key)).toEqual([
      "purpose",
    ]);
  });

  it("reads a create as all addition", () => {
    const rows = changeFields(change("create", null, BASE));

    expect(rows.every((row) => row.changed)).toBe(true);
    expect(rows.every((row) => row.before === null)).toBe(true);
    expect(rows.find((row) => row.key === "date")?.after).toBe("13.08.2026");
  });

  it("reads a delete as all removal", () => {
    const rows = changeFields(change("delete", BASE, null));

    expect(rows.every((row) => row.changed)).toBe(true);
    expect(rows.every((row) => row.after === null)).toBe(true);
  });

  it("drops the prediction axis this discipline does not have", () => {
    // Exactly one of the two is ever populated (`ProposalSessionSnapshot`), so
    // the other is not an unchanged field — it is a quantity a bike ride does
    // not have, and "— → —" under "Predicted volume" is noise on every row.
    const keys = changeFields(change("update", BASE, BASE)).map(
      (row) => row.key,
    );

    expect(keys).toContain("predicted_load");
    expect(keys).not.toContain("predicted_volume_kg");
    expect(keys).not.toContain("coach_notes");
  });

  it("keeps the unchanged fields, because a change is read in context", () => {
    const rows = changeFields(
      change("move", BASE, { ...BASE, date: "2026-08-11" }),
    );

    expect(rows.filter((row) => !row.changed).map((row) => row.key)).toEqual([
      "purpose",
      "discipline",
      "status",
      "workout_id",
      "structure",
      "intent_text",
      "success_criteria",
      "predicted_load",
    ]);
  });
});

describe("the date a change is headlined with", () => {
  it("shows a move as the journey, because one date cannot say it", () => {
    // The entry's own `date` is where the session is *now* (the backend fills
    // it from the row), so a header printing only that would headline the move
    // with the date it exists to change.
    const label = changeDateLabel(
      change(
        "move",
        { ...BASE, date: "2026-08-13" },
        { ...BASE, date: "2026-08-11" },
      ),
    );

    expect(label).toBe("13.08.2026 → 11.08.2026");
  });

  it("shows the journey for an update that reschedules while it revises", () => {
    // A reschedule need not be a `move`: an `update` can shift the date and
    // rewrite the intent in one change, and the header has to show both dates
    // or it headlines the change with the day it is leaving (FIX-F6).
    const label = changeDateLabel(
      change(
        "update",
        { ...BASE, date: "2026-08-13" },
        {
          ...BASE,
          date: "2026-08-15",
          intent_text: "Longer, and a day later.",
        },
      ),
    );

    expect(label).toBe("13.08.2026 → 15.08.2026");
  });

  it("shows one formatted date for every other kind", () => {
    expect(changeDateLabel(change("update", BASE, BASE))).toBe("13.08.2026");
    expect(changeDateLabel(change("create", null, BASE))).toBe("13.08.2026");
    expect(changeDateLabel(change("delete", BASE, null))).toBe("13.08.2026");
  });
});

describe("how long a proposal has left", () => {
  const now = new Date("2026-08-07T12:00:00Z");

  it.each([
    ["2026-08-06T12:00:00Z", "expired"],
    ["2026-08-07T23:00:00Z", "expires today"],
    ["2026-08-08T13:00:00Z", "expires tomorrow"],
    ["2026-08-10T13:00:00Z", "expires in 3 days"],
  ])("reads %s as %s", (expiresAt, expected) => {
    expect(expiryLabel(expiresAt, now)).toBe(expected);
  });
});

describe("who wrote it", () => {
  it("keeps the key label and drops the prefix every row shares", () => {
    expect(actorLabel("agent:coach")).toBe("coach");
    expect(actorLabel("athlete")).toBe("athlete");
  });
});
