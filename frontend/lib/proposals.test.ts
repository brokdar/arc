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
  intent_text: "Six by three at 118 %.",
  coach_notes: null,
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

  it("compares raw values, not the rendered ones", () => {
    // Both predictions are rounded for display, so 84.4 and 84 print the same
    // "84 TSS". The change is still real — it is what the accept would write —
    // and a row that read as unchanged would hide it behind its own rounding.
    const rows = changeFields(
      change("update", BASE, { ...BASE, predicted_load: 84.4 }),
    );

    expect(rows.filter((row) => row.changed).map((row) => row.key)).toEqual([
      "predicted_load",
    ]);
  });

  it("sees a workout swap between two ids that render alike", () => {
    // uuid7s minted in the same minute share their first eight characters, and
    // eight characters is all the Workout row prints. Two same-batch workouts
    // are exactly the case a proposal swaps between, and comparing the printed
    // form said "no field differs" about a swapped prescription.
    const rows = changeFields(
      change(
        "update",
        { ...BASE, workout_id: "0199a000-0000-7000-8000-00000000aaaa" },
        {
          ...BASE,
          workout_id: "0199a000-0000-7000-8000-00000000bbbb",
        },
      ),
    );

    const workout = rows.find((row) => row.key === "workout_id");
    expect(workout?.changed).toBe(true);
    // Still rendered short on both sides: the fix is to the comparison, not to
    // what the row prints.
    expect(workout).toMatchObject({ before: "0199a000", after: "0199a000" });
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
      "status",
      "workout_id",
      "intent_text",
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
