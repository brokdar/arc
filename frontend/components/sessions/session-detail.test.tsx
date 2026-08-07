import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { SessionDetail } from "@/components/sessions/session-detail";
import { ACTIVITY_IDS } from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
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

function renderDetail(sessionId: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SessionDetail sessionId={sessionId} />
    </QueryClientProvider>,
  );
}

/** Wait until the session itself is on screen, not the loading shell. */
async function ready(): Promise<void> {
  await screen.findByRole("heading", { name: "Corrections" });
}

/** The value rendered under one metric label. */
function metric(label: string): HTMLElement {
  const term = screen.getAllByText(label).find((node) => node.closest("dt"));
  const value = term?.closest("dt")?.nextElementSibling;
  if (!value) {
    throw new Error(`no metric labelled ${label}`);
  }
  return value as HTMLElement;
}

describe("a device session", () => {
  it("states the session in its own timezone", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    await ready();

    expect(metric("Local date")).toHaveTextContent("05.08.2026");
    // 05:14 UTC in Europe/Zurich is 07:14 — the clock the athlete rode by.
    expect(metric("Started")).toHaveTextContent("07:14");
    expect(metric("Ended")).toHaveTextContent("09:53");
    expect(metric("Timezone")).toHaveTextContent("Europe/Zurich");
  });

  it("prints the wall clock and the recording time as two different numbers", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    await ready();

    // 05:14 to 07:53 is 2:39 on the clock; 2:29 of it was being recorded.
    // They used to be the same number twice in two formats, because the row's
    // `duration_s` *is* the recording time for a device session — which hid
    // the very ten minutes the panel below prints as the paused total.
    expect(metric("Duration")).toHaveTextContent("2:39");
    expect(metric("Recording time")).toHaveTextContent("2:29:00");
    expect(metric("Stops")).toHaveTextContent("1 · 10:00 paused");
  });

  it("shows one number twice only where there was nothing to subtract", async () => {
    renderDetail(ACTIVITY_IDS.trainerRide);
    await ready();

    // A clean trainer hour: 16:02 to 17:02, no stops, so elapsed and recording
    // genuinely agree — and the page says so rather than implying a pause.
    expect(metric("Duration")).toHaveTextContent("1:00");
    expect(metric("Recording time")).toHaveTextContent("1:00:00");
    expect(metric("Stops")).toHaveTextContent("0");
  });

  it("reads a fixed-offset timezone the way the backend writes it", async () => {
    renderDetail(ACTIVITY_IDS.trainerRide);
    await ready();

    // 16:02 UTC at UTC+02:00 is 18:02, without an IANA name in sight (D93).
    expect(metric("Timezone")).toHaveTextContent("UTC+02:00");
    expect(metric("Started")).toHaveTextContent("18:02");
  });

  it("shows its arithmetic: elapsed, the stops, and what is left", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    await ready();

    expect(metric("Elapsed")).toHaveTextContent("2:39:00");
    expect(metric("Recording")).toHaveTextContent("2:29:00");
    expect(metric("Moving")).toHaveTextContent("2:25:12");
    // The paused total is derived from the stop's row range, not asserted:
    // 3600–4200 on the 1 Hz grid is 600 rows and therefore ten minutes.
    expect(metric("Stops")).toHaveTextContent("1 · 10:00 paused");
    expect(metric("Sample gap")).toHaveTextContent("1.0 s median");
    expect(metric("Repairs")).toHaveTextContent("3");
  });

  it("says which meter produced each channel, and how that was decided", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    await ready();

    expect(screen.getByText("Quarq DZero")).toBeInTheDocument();
    // FIT names candidates and nothing that chose between them (D96), so the
    // tie-break is printed as a tie-break.
    expect(
      screen.getByText("chosen: lowest device_index among 2 candidates"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("of Quarq DZero, Garmin Edge 830"),
    ).toBeInTheDocument();
    expect(screen.getByText("Garmin HRM-Pro")).toBeInTheDocument();
    expect(screen.getByText("chosen: only candidate")).toBeInTheDocument();
  });

  it("holds the slot of a channel the file never carried", async () => {
    renderDetail(ACTIVITY_IDS.trainerRide);
    await ready();

    // No heart-rate strap: not a session that recorded zero bpm.
    expect(
      screen.getByRole("img", {
        name: "Not assessed: No heart rate in this recording",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", {
        name: "Not assessed: No RPE logged for this session",
      }),
    ).toBeInTheDocument();
    // A clean file has nothing repaired, and says 0 rather than nothing.
    expect(metric("Repairs")).toHaveTextContent("0");
  });

  it("lists the channels the recording actually holds", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    await ready();

    for (const channel of ["power", "hr", "cadence", "speed", "lat", "lon"]) {
      expect(screen.getByText(channel)).toBeInTheDocument();
    }
  });
});

describe("a hand-entered session", () => {
  it("accounts for having no device file at all", async () => {
    renderDetail(ACTIVITY_IDS.gym);
    await ready();

    expect(screen.getByText(/No device file/)).toBeInTheDocument();
    // Nothing was subtracted, because there were no pauses to subtract.
    expect(
      within(metric("Recording time")).getByRole("img", {
        name: "Not assessed: Entered by hand — there were no pauses to subtract",
      }),
    ).toBeInTheDocument();
    // Wall clock either way: a manual session's duration *is* end minus start.
    expect(metric("Duration")).toHaveTextContent("1:00");
    expect(metric("RPE")).toHaveTextContent("7/10");
    expect(
      screen.getByText("Felt strong; added a set of pull-ups at the end."),
    ).toBeInTheDocument();
  });

  it("lists the sets that were logged", async () => {
    renderDetail(ACTIVITY_IDS.gym);
    await ready();

    const table = screen.getByRole("table");
    expect(within(table).getAllByText("Back Squat")).toHaveLength(2);
    expect(within(table).getByText("102.5 kg")).toBeInTheDocument();
    // Bodyweight is not zero kilos.
    expect(
      within(table).getByRole("img", {
        name: "Not assessed: Bodyweight, or no load recorded",
      }),
    ).toBeInTheDocument();
    expect(within(table).getByText(/Pull-up/)).toBeInTheDocument();
  });
});

describe("correcting a session", () => {
  it("records a discipline override as one", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.trainerRide);
    await ready();

    expect(
      screen.getByText(/by a guess from the channels present/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Set discipline" }),
    ).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("Discipline"), "other");
    await user.click(screen.getByRole("button", { name: "Set discipline" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Saved — Other on 03.08.2026.",
    );
    // The page shows what the server answered with, not what was typed.
    await waitFor(() => {
      expect(
        screen.getByText(/by you — you corrected this/),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: "Set discipline" }),
    ).toBeDisabled();
  });

  it("re-derives the date when the timezone is corrected", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.trainerRide);
    await ready();

    // 16:02 UTC is the 4th in Auckland, not the 3rd — which is the whole
    // point of storing the zone rather than the date (D93).
    const field = screen.getByLabelText(/Timezone/);
    await user.clear(field);
    await user.type(field, "Pacific/Auckland");
    await user.click(screen.getByRole("button", { name: "Set timezone" }));

    expect(await screen.findByRole("status")).toHaveTextContent("04.08.2026");
    await waitFor(() => {
      expect(metric("Local date")).toHaveTextContent("04.08.2026");
    });
    expect(metric("Started")).toHaveTextContent("04:02");
  });

  it("prints the API's refusal of a timezone it cannot resolve", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.trainerRide);
    await ready();

    const field = screen.getByLabelText(/Timezone/);
    await user.clear(field);
    await user.type(field, "Middle-earth/Shire");
    await user.click(screen.getByRole("button", { name: "Set timezone" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /neither 'UTC', a UTC±HH:MM offset, nor a known IANA timezone name/,
    );
    // Nothing moved: a refused correction corrected nothing.
    expect(metric("Local date")).toHaveTextContent("03.08.2026");
  });
});

describe("a link that resolves to nothing", () => {
  it("refuses to spend a path segment that is not an id", async () => {
    renderDetail("../../etc/passwd");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That is not a session id.",
    );
    expect(
      screen.getByRole("link", { name: "Back to the log" }),
    ).toHaveAttribute("href", "/sessions");
  });

  it("says a well-formed id was not found rather than showing an empty page", async () => {
    server.use(
      http.get("/api/v1/sessions/{session_id}", ({ response }) =>
        response(404).json({ detail: "Session not found" }),
      ),
    );
    renderDetail(ACTIVITY_IDS.outdoorRide);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Session not found",
    );
  });
});
