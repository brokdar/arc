import { describe, expect, it } from "vitest";

import {
  apiErrorMessages,
  HTTP_STATUS,
  isUnauthorized,
  loadFailureMessage,
} from "@/lib/api-errors";

/** A body as `lib/api/client.ts` tags one on its way past. */
function answered(status: number, body: object): object {
  return Object.defineProperty({ ...body }, HTTP_STATUS, { value: status });
}

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

describe("the status a body was answered with", () => {
  it("is invisible to everything that reads the body as a payload", () => {
    const body = answered(401, { detail: "Not authenticated" });

    // The tag is a symbol, so the body is still exactly what the server sent.
    expect(JSON.parse(JSON.stringify(body))).toEqual({
      detail: "Not authenticated",
    });
    expect(Object.keys(body)).toEqual(["detail"]);
    // And the sentence a form prints is unchanged by it.
    expect(apiErrorMessages(body)).toEqual(["Not authenticated"]);
  });

  it("tells the session guard's 401 from every other refusal", () => {
    expect(isUnauthorized(answered(401, { detail: "Not authenticated" }))).toBe(
      true,
    );
    expect(isUnauthorized(answered(404, { detail: "No such session" }))).toBe(
      false,
    );
    // An untagged body is one nothing recorded a status for — not a 401.
    expect(isUnauthorized({ detail: "Not authenticated" })).toBe(false);
    expect(isUnauthorized(new TypeError("fetch failed"))).toBe(false);
    expect(isUnauthorized(null)).toBe(false);
  });
});

describe("loadFailureMessage", () => {
  it("asks about the network only when the network is the open question", () => {
    expect(loadFailureMessage(new TypeError("fetch failed"), "the queue")).toBe(
      "Could not load the queue. Is the API reachable?",
    );
    expect(
      loadFailureMessage(answered(500, { detail: "boom" }), "the log"),
    ).toBe("Could not load the log. Is the API reachable?");
  });

  it("names the remedy when the API answered that the session is gone", () => {
    expect(
      loadFailureMessage(
        answered(401, { detail: "Not authenticated" }),
        "the queue",
      ),
    ).toBe("Your session has expired. Log in again to see the queue.");
  });
});
