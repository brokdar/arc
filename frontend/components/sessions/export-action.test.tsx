import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { SessionDetail } from "@/components/sessions/session-detail";
import { AthleteClock } from "@/lib/clock";
import { ACTIVITY_IDS, ATHLETE_TIMEZONE } from "@/tests/mocks/fixtures";
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

/**
 * What the real API answers for the gym fixture session's text export —
 * written by the backend and asserted there byte for byte
 * (`backend/tests/unit/test_exports_api.py`), served here by the mock.
 */
const EXPECTED_TEXT = readFileSync(
  resolve(
    import.meta.dirname,
    "../../../backend/tests/vectors/session_export.txt",
  ),
  "utf8",
);

/**
 * A clipboard that records what it was given.
 *
 * Installed after `userEvent.setup()`, which brings its own stub — one that
 * round-trips through a `Blob` jsdom and Node disagree about.
 */
function recordingClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  return writeText;
}

/** The session page, as the athlete opens it. */
function renderDetail(sessionId: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AthleteClock timezone={ATHLETE_TIMEZONE}>
        <SessionDetail sessionId={sessionId} />
      </AthleteClock>
    </QueryClientProvider>,
  );
}

describe("session export action", () => {
  it("copies the workout as text in two taps from the session page", async () => {
    const user = userEvent.setup();
    const clipboard = recordingClipboard();
    renderDetail(ACTIVITY_IDS.gym);

    // Tap 1: open the export panel.
    await user.click(await screen.findByRole("button", { name: "Export" }));
    // Tap 2: copy.
    await user.click(screen.getByRole("button", { name: "Copy as text" }));

    expect(await screen.findByText("Copied")).toBeInTheDocument();
    expect(clipboard).toHaveBeenCalledExactlyOnceWith(EXPECTED_TEXT);
  });

  it("offers the text and the CSV as downloads of this session", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.gym);

    await user.click(await screen.findByRole("button", { name: "Export" }));
    const panel = screen.getByRole("group", { name: "Export this session" });

    const text = within(panel).getByRole("link", { name: "Download text" });
    const csv = within(panel).getByRole("link", { name: "Download CSV" });
    expect(text).toHaveAttribute(
      "href",
      `http://localhost:8000/api/v1/sessions/${ACTIVITY_IDS.gym}/export?format=text`,
    );
    expect(csv).toHaveAttribute(
      "href",
      `http://localhost:8000/api/v1/sessions/${ACTIVITY_IDS.gym}/export?format=csv`,
    );
    expect(csv).toHaveAttribute("download");
  });

  it("says so when the export cannot be fetched, and copies nothing", async () => {
    server.use(
      http.get("/api/v1/sessions/{session_id}/export", ({ response }) =>
        response(404).json({ detail: "Session not found" }),
      ),
    );
    const user = userEvent.setup();
    const clipboard = recordingClipboard();
    renderDetail(ACTIVITY_IDS.gym);

    await user.click(await screen.findByRole("button", { name: "Export" }));
    await user.click(screen.getByRole("button", { name: "Copy as text" }));

    expect(
      await screen.findByText("Could not copy: Session not found"),
    ).toBeInTheDocument();
    expect(clipboard).not.toHaveBeenCalled();
  });

  it("starts closed: the page shows one Export button, not the menu", async () => {
    renderDetail(ACTIVITY_IDS.gym);

    const trigger = await screen.findByRole("button", { name: "Export" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("button", { name: "Copy as text" }),
    ).not.toBeInTheDocument();
  });
});
