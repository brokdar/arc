import { describe, expect, it } from "vitest";

import type { ActiveSession } from "@/components/strength/session-model";
import { athleteRecord } from "@/tests/mocks/fixtures";

import { activeRedFlag, LOCAL_FLAG_WINDOW_MS } from "./red-flag-state";

const NOW = Date.parse("2026-11-16T12:00:00Z");

function stopped(kind: "emergency" | "advice", agoMs = 60_000): ActiveSession {
  return {
    plannedSessionId: "p1",
    sessionClientId: "c1",
    startedAt: NOW - agoMs - 600_000,
    sets: [],
    lineIndex: 0,
    rest: null,
    finished: true,
    endedAt: NOW - agoMs,
    redFlag: kind,
  };
}

const quiet = athleteRecord();
const raised = {
  ...quiet,
  red_flag_active: true,
  red_flag_kind: "emergency" as const,
  red_flag_severity: "severe" as const,
  red_flag_note: "Stopped a gym session: chest pain.",
};

describe("activeRedFlag", () => {
  it("is nothing when nothing is up", () => {
    expect(
      activeRedFlag({
        athlete: quiet,
        local: [],
        flagUploadPending: false,
        now: NOW,
      }),
    ).toBeNull();
  });

  it("takes the server's flag, severe for an emergency", () => {
    const flag = activeRedFlag({
      athlete: raised,
      local: [],
      flagUploadPending: false,
      now: NOW,
    });
    expect(flag).toMatchObject({
      kind: "emergency",
      severe: true,
      source: "server",
    });
  });

  it("knows a stop whose upload has not landed, though the server says all clear", () => {
    const flag = activeRedFlag({
      athlete: quiet,
      local: [stopped("emergency")],
      flagUploadPending: true,
      now: NOW,
    });
    expect(flag).toMatchObject({ kind: "emergency", source: "phone" });
  });

  it("does not resurrect a stop the server has since cleared", () => {
    expect(
      activeRedFlag({
        athlete: quiet,
        local: [stopped("emergency")],
        flagUploadPending: false,
        now: NOW,
      }),
    ).toBeNull();
  });

  it("believes the phone's own stop offline, for a day", () => {
    const offline = (agoMs: number) =>
      activeRedFlag({
        athlete: undefined,
        local: [stopped("advice", agoMs)],
        flagUploadPending: false,
        now: NOW,
      });
    expect(offline(60_000)).toMatchObject({ kind: "advice", severe: false });
    expect(offline(LOCAL_FLAG_WINDOW_MS + 1)).toBeNull();
  });
});
