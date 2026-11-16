import "fake-indexeddb/auto";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IDBFactory } from "fake-indexeddb";
import { delay, HttpResponse } from "msw";
import type * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ActiveSession } from "@/components/strength/session-model";
import { TodayView } from "@/components/today/today-view";
import { AthleteClock } from "@/lib/clock";
import { mondayOf } from "@/lib/dates";
import { LocaleProvider } from "@/lib/i18n/provider";
import { addQueued, putPacket, putTraining } from "@/lib/offline/db";
import {
  ATHLETE_TIMEZONE,
  athleteRecord,
  athleteToday,
  planWeekFixture,
  SESSION_IDS,
} from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { packetFixture } from "@/tests/mocks/handlers-packet";
import { server } from "@/tests/mocks/server";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: React.PropsWithChildren<{ href: string }>) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

/**
 * Today's states for a strength session — still to do, under way, done — and
 * the red flag that can stand in front of all of them (strength UAT B10, U3,
 * U5, W9, W1).
 */

const today = athleteToday();
const SESSION = SESSION_IDS.strength;
const NOW = Date.now();

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
});

function renderToday(locale = "en") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LocaleProvider locale={locale}>
        <AthleteClock timezone={ATHLETE_TIMEZONE}>
          <TodayView />
        </AthleteClock>
      </LocaleProvider>
    </QueryClientProvider>,
  );
}

/** The fixture's lift, on today, with the status and link the test needs. */
function serveLift(overrides: Record<string, unknown> = {}) {
  const week = planWeekFixture(mondayOf(today));
  const card = week.days
    .flatMap((day) => day.sessions)
    .find((session) => session.id === SESSION);
  if (!card) throw new Error("no lift in the week fixture");
  const served = {
    ...week,
    days: week.days.map((day) => ({
      ...day,
      sessions:
        day.date === today ? [{ ...card, date: today, ...overrides }] : [],
    })),
  };
  server.use(
    http.get("/api/v1/plan/week", ({ response }) => response(200).json(served)),
  );
}

function stored(overrides: Partial<ActiveSession> = {}): ActiveSession {
  return {
    plannedSessionId: SESSION,
    sessionClientId: "0199c000-0000-7000-8000-00000000c001",
    startedAt: NOW - 30 * 60_000,
    sets: [],
    lineIndex: 0,
    rest: null,
    finished: false,
    ...overrides,
  };
}

function raiseServerFlag(
  kind: "emergency" | "advice",
  severity: "severe" | "mild",
) {
  server.use(
    http.get("/api/v1/athlete", ({ response }) =>
      response(200).json({
        ...athleteRecord(),
        red_flag_active: true,
        red_flag_kind: kind,
        red_flag_severity: severity,
        red_flag_note: "Stopped a gym session: chest pain.",
      }),
    ),
  );
}

describe("a session's state on Today", () => {
  it("offers Start for a session not begun", async () => {
    serveLift({ status: "planned" });
    renderToday();

    const start = await screen.findByRole("link", { name: /^Start / });
    expect(start).toHaveAttribute("href", `/train/${SESSION}`);
    expect(screen.queryByRole("link", { name: /^Resume / })).toBeNull();
  });

  it("offers Resume, not Start, for a session under way on this phone", async () => {
    serveLift({ status: "planned" });
    await putTraining(SESSION, stored());
    renderToday();

    const resume = await screen.findByRole("link", { name: /^Resume / });
    expect(resume).toHaveAttribute("href", `/train/${SESSION}`);
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Start / })).toBeNull();
  });

  it("shows a done state and Reopen inside the 24-hour window", async () => {
    serveLift({ status: "planned" });
    await putTraining(
      SESSION,
      stored({ finished: true, endedAt: NOW - 60 * 60_000 }),
    );
    renderToday();

    const reopen = await screen.findByRole("link", { name: /^Reopen / });
    expect(reopen).toHaveAttribute("href", `/train/${SESSION}`);
    expect(screen.getByText("Done. Waiting to upload.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Start / })).toBeNull();
  });

  it("offers no Reopen once the 24 hours have passed", async () => {
    serveLift({ status: "planned" });
    await putTraining(
      SESSION,
      stored({ finished: true, endedAt: NOW - 25 * 60 * 60_000 }),
    );
    renderToday();

    await screen.findByText("Done. Waiting to upload.");
    expect(screen.queryByRole("link", { name: /^Reopen / })).toBeNull();
  });

  it("links a completed session to the recorded one", async () => {
    const recorded = "0199a000-0000-7000-8000-0000000000aa";
    serveLift({ status: "completed", matched_session_id: recorded });
    renderToday();

    const link = await screen.findByRole("link", {
      name: /^Open the recorded/,
    });
    expect(link).toHaveAttribute("href", `/sessions/${recorded}`);
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Start / })).toBeNull();
  });
});

describe("the workout's name on a card (W9)", () => {
  it("shows it on the online card", async () => {
    serveLift({ status: "planned", title: "UAT Lower + Pull" });
    renderToday();

    // Once in the week list beside it, once on the card itself.
    expect(await screen.findAllByText("UAT Lower + Pull")).toHaveLength(2);
  });

  it("shows it on the offline card, which also has no second 'train without a plan'", async () => {
    const packet = packetFixture(today, new Date());
    const [first] = packet.sessions;
    if (!first) throw new Error("fixture has no session");
    await putPacket({
      packet: {
        ...packet,
        sessions: [{ ...first, title: "UAT Lower + Pull" }],
      },
      storedAt: Date.now(),
    });
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response.untyped(HttpResponse.error()),
      ),
    );
    renderToday();

    const section = await screen.findByTestId("offline-sessions");
    expect(within(section).getByText("UAT Lower + Pull")).toBeInTheDocument();
    // The toolbar carries it; one inside the plan's card was a duplicate.
    expect(
      within(section).queryByRole("link", { name: /without a plan/i }),
    ).toBeNull();
  });
});

describe("the stored plan while the week is still loading (U3)", () => {
  it("shows the cached session instead of waiting out a slow request", async () => {
    await putPacket({
      packet: packetFixture(today, new Date()),
      storedAt: Date.now(),
    });
    server.use(
      http.get("/api/v1/plan/week", async () => {
        await delay("infinite");
        return HttpResponse.error();
      }),
    );
    renderToday();

    expect(await screen.findByTestId("offline-sessions")).toBeInTheDocument();
    expect(screen.queryByText("Loading today…")).not.toBeInTheDocument();
  });
});

describe("a standing red flag on Today (B10)", () => {
  it("states an emergency flag in the gym screen's words, from the server", async () => {
    serveLift({ status: "planned" });
    raiseServerFlag("emergency", "severe");
    renderToday();

    const panel = await screen.findByTestId("today-red-flag");
    expect(panel).toHaveTextContent("Stop. Call emergency services now.");
    expect(
      within(panel).getByRole("button", { name: "All better" }),
    ).toBeInTheDocument();
  });

  it("knows a stop whose upload has not landed yet, before the server does", async () => {
    serveLift({ status: "planned" });
    await putTraining(
      SESSION,
      stored({ finished: true, endedAt: NOW - 120_000, redFlag: "emergency" }),
    );
    await addQueued(
      {
        kind: "red_flag",
        mutation_id: "0199c000-0000-7000-8000-00000000d001",
        client_edit_time: new Date(NOW - 120_000).toISOString(),
        session_client_id: "0199c000-0000-7000-8000-00000000c001",
        symptom: "chest_pain",
      },
      NOW - 120_000,
    );
    renderToday();

    expect(await screen.findByTestId("today-red-flag")).toHaveTextContent(
      "Stop. Call emergency services now.",
    );
  });

  it("asks before Start while an emergency flag stands, and 'Not today' stays put", async () => {
    serveLift({ status: "planned" });
    raiseServerFlag("emergency", "severe");
    const user = userEvent.setup();
    renderToday();

    await user.click(await screen.findByRole("link", { name: /^Start / }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("You stopped for a red flag");
    await user.click(within(dialog).getByRole("button", { name: "Not today" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("lowers the flag when the athlete says they are cleared", async () => {
    serveLift({ status: "planned" });
    raiseServerFlag("emergency", "severe");
    const sent: unknown[] = [];
    server.use(
      http.patch("/api/v1/athlete", async ({ request, response }) => {
        sent.push(await request.json());
        return response(200).json({
          ...athleteRecord(),
          red_flag_active: false,
        });
      }),
    );
    const user = userEvent.setup();
    renderToday();

    await user.click(await screen.findByRole("link", { name: /^Start / }));
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "I'm cleared, continue" }),
    );

    await waitFor(() => expect(sent).toEqual([{ red_flag_active: false }]));
  });

  it("does not gate Start for a milder flag", async () => {
    serveLift({ status: "planned" });
    raiseServerFlag("advice", "mild");
    const user = userEvent.setup();
    renderToday();

    const start = await screen.findByRole("link", { name: /^Start / });
    await screen.findByRole("button", { name: /Red flag up/ });
    await user.click(start);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByTestId("today-red-flag")).toBeNull();
  });
});

describe("Today in German (W1)", () => {
  it("has no English left on a strength session", async () => {
    serveLift({ status: "planned" });
    renderToday("de");

    await screen.findByRole("link", { name: /^.*starten/i });
    const page = document.body;
    expect(page).toHaveTextContent("Maximalkraft");
    expect(page).toHaveTextContent("10 Sätze Maximalkraft");
    for (const english of [
      "sets of",
      "movements",
      "per side",
      "Report",
      "MAX STRENGTH",
      "Success criteria",
    ]) {
      expect(page).not.toHaveTextContent(english);
    }
  });
});
