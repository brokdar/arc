import { describe, expect, it } from "vitest";

import { apiErrorMessages } from "@/lib/api-errors";

describe("apiErrorMessages", () => {
  it("says nothing when nothing failed", () => {
    expect(apiErrorMessages(null)).toEqual([]);
  });

  it("passes a service's own sentence through", () => {
    expect(
      apiErrorMessages({
        detail:
          "A planned session needs exactly one of workout_id or structure",
      }),
    ).toEqual([
      "A planned session needs exactly one of workout_id or structure",
    ]);
  });

  it("locates each of FastAPI's per-field errors", () => {
    expect(
      apiErrorMessages({
        detail: [
          {
            loc: ["body", "structure", "steps", 0, "duration_s"],
            msg: "must be > 0",
          },
          { loc: ["body", "name"], msg: "Field required" },
        ],
      }),
    ).toEqual([
      "structure.steps.0.duration_s: must be > 0",
      "name: Field required",
    ]);
  });

  it("distinguishes an unreachable server from a rejected body", () => {
    expect(apiErrorMessages(new TypeError("fetch failed"))).toEqual([
      "Could not reach the server. Try again.",
    ]);
  });
});
