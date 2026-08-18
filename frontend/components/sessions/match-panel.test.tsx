import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";
import { SessionDetail } from "@/components/sessions/session-detail";
import { AthleteClock } from "@/lib/clock";
import {
  ACTIVITY_IDS,
  ATHLETE_TIMEZONE,
  ingestState,
  PLANNED_IDS,
  seedMergeCandidate,
} from "@/tests/mocks/fixtures";
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
 * The panel is driven through the page, not rendered on its own.
 *
 * It takes the session as a prop, and every action it offers changes that
 * session — so a test that rendered it with a frozen fixture could watch a
 * confirm succeed and never notice that the page around it went on saying
 * "Unmatched". Driving `SessionDetail` puts the real query cache between the
 * mutation and the re-render, which is the thing worth proving.
 */
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

/** The whole match panel: the section the "Plan" heading names. */
function plan(): HTMLElement {
  const heading = screen.getByRole("heading", { name: "Plan" });
  const section = heading.closest("section");
  if (!section) {
    throw new Error("the Plan heading is not inside a section");
  }
  return section as HTMLElement;
}

/** The line the score itself is on, above the breakdown that produced it. */
function similarityHeader(): HTMLElement {
  const label = within(plan()).getByText("Similarity");
  return label.parentElement as HTMLElement;
}

/** One row of the similarity breakdown, by the component it is about. */
function component(label: string): HTMLElement {
  const row = within(plan()).getByText(label).closest("li");
  if (!row) {
    throw new Error(`no breakdown row for ${label}`);
  }
  return row as HTMLElement;
}

/** Wait for the session behind the panel to have loaded. */
async function ready(): Promise<HTMLElement> {
  await screen.findByRole("heading", { name: "Corrections" });
  return await waitFor(() => plan());
}

describe("a pending proposal", () => {
  it("shows what was proposed and the whole score behind it", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    const panel = await ready();

    await within(panel).findByText("Proposed");
    expect(
      within(panel).getByText("Open the top end without digging a hole."),
    ).toBeInTheDocument();
    // 0.6872… — the weighted mean of the three components below, as
    // `app.domain.matching.similarity` computed it.
    expect(within(panel).getByText("69%")).toBeInTheDocument();
    expect(
      within(panel).getByText(/arc proposes and leaves the decision to you/),
    ).toBeInTheDocument();

    // Every component says what it compared, in the unit it compared it in: a
    // ratio alone is not explicable.
    const duration = within(component("Duration"));
    expect(duration.getByText("38%")).toBeInTheDocument();
    expect(duration.getByText("40%")).toBeInTheDocument();
    expect(duration.getByText("57:00")).toBeInTheDocument();
    expect(duration.getByText("2:29:00")).toBeInTheDocument();

    const intensity = within(component("Intensity"));
    expect(intensity.getByText("98%")).toBeInTheDocument();
    expect(intensity.getByText("227 W")).toBeInTheDocument();
    expect(intensity.getByText("231 W")).toBeInTheDocument();

    const structure = within(component("Structure"));
    expect(structure.getByText("80%")).toBeInTheDocument();
    expect(structure.getByText("5 efforts")).toBeInTheDocument();
    expect(structure.getByText("4 efforts")).toBeInTheDocument();
  });

  it("names every component it could not assess, and why", async () => {
    renderDetail(ACTIVITY_IDS.gym);
    const panel = await ready();

    await within(panel).findByText("Proposed");
    // 60% over one component. Scoped to the header, because the structure
    // term below is the same number — which is the point: the score *is* that
    // one component once the other two are left out.
    expect(within(similarityHeader()).getByText("60%")).toBeInTheDocument();

    // A strength prescription states no duration and shares no channel with a
    // session typed in by hand. Neither is a zero: they are left out, the
    // remaining weight is scaled up to 100%, and the sentence the domain wrote
    // about each is on the page.
    expect(
      within(component("Duration")).getByText(
        /the prescription states no duration to compare against/,
      ),
    ).toBeInTheDocument();
    expect(
      within(component("Intensity")).getByText(
        /share neither power nor heart rate/,
      ),
    ).toBeInTheDocument();

    const structure = within(component("Structure"));
    expect(structure.getByText("100%")).toBeInTheDocument();
    expect(structure.getByText("5 sets")).toBeInTheDocument();
    expect(structure.getByText("3 sets")).toBeInTheDocument();

    // The placeholder holds the slot rather than collapsing the row, and
    // carries its reason to a screen reader as well as to a hover.
    expect(
      within(component("Duration")).getAllByRole("img", {
        name: /Not assessed: the prescription states no duration/,
      }).length,
    ).toBeGreaterThan(0);
  });

  it("changes nothing on either side until it is answered", async () => {
    renderDetail(ACTIVITY_IDS.gym);
    const panel = await ready();
    await within(panel).findByText("Proposed");

    // A proposal is a question. The session is still `unmatched` and the
    // planned session still `planned` while it stands — the badge says
    // "Proposed" because that is the more useful truth about a row waiting on
    // an answer, and the stored status is the one asserted here.
    expect(
      ingestState().sessions.find((row) => row.id === ACTIVITY_IDS.gym)?.status,
    ).toBe("unmatched");
    expect(
      ingestState().planned.find((row) => row.id === PLANNED_IDS.strength)
        ?.status,
    ).toBe("planned");
  });
});

describe("answering a proposal", () => {
  it("confirming matches the session and completes the planned one", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.gym);
    const panel = await ready();
    await within(panel).findByText("Proposed");

    await user.click(
      within(panel).getByRole("button", { name: "Yes, this was that session" }),
    );

    // The badge the page reads is the one a *later* GET answers with, so this
    // passes only if the confirm actually moved the stored state.
    await waitFor(() =>
      expect(within(plan()).getByText("Confirmed")).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByText("Matched")).toBeInTheDocument(),
    );
    expect(
      ingestState().planned.find((row) => row.id === PLANNED_IDS.strength)
        ?.status,
    ).toBe("completed");
    // Confirmed is the athlete's, so the two revising actions replace the
    // two answers.
    expect(
      within(plan()).getByRole("button", { name: "Unlink" }),
    ).toBeInTheDocument();
  });

  it("rejecting leaves the session unplanned and the plan untouched", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.gym);
    const panel = await ready();
    await within(panel).findByText("Proposed");

    await user.click(
      within(panel).getByRole("button", { name: "No, it was not" }),
    );

    await waitFor(() =>
      expect(
        within(plan()).getByText("Nothing was planned"),
      ).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByText("Unplanned")).toBeInTheDocument(),
    );
    // Rejecting is not completing: the planned session goes back to open.
    expect(
      ingestState().planned.find((row) => row.id === PLANNED_IDS.strength)
        ?.status,
    ).toBe("planned");
  });

  it("unlinking puts both sides back exactly as they were", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.gym);
    const panel = await ready();
    await within(panel).findByText("Proposed");

    await user.click(
      within(panel).getByRole("button", { name: "Yes, this was that session" }),
    );
    await waitFor(() =>
      expect(within(plan()).getByText("Confirmed")).toBeInTheDocument(),
    );

    // Two clicks, and the second is on the *armed* button the first replaced
    // it with: unlinking is destructive, so it is never one click away.
    await user.click(within(plan()).getByRole("button", { name: "Unlink" }));
    await user.click(within(plan()).getByRole("button", { name: "Unlink" }));

    await waitFor(() =>
      expect(
        within(plan()).getByText("Not linked to the plan"),
      ).toBeInTheDocument(),
    );
    // Not `unplanned` — that is an answer, and an unlink is an undo.
    await waitFor(() =>
      expect(screen.getByText("Unmatched")).toBeInTheDocument(),
    );
    expect(
      ingestState().planned.find((row) => row.id === PLANNED_IDS.strength)
        ?.status,
    ).toBe("planned");
  });
});

describe("linking by hand", () => {
  it("offers only the planned sessions the API would accept", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.trainerRide);
    const panel = await ready();

    await user.click(
      within(panel).getByRole("button", { name: "Link to a planned session" }),
    );
    const picker = await screen.findByText("Which planned session was this?");
    const list = picker.closest("[data-slot='planned-picker']") as HTMLElement;

    // 2026-08-03 ± 3 days. The VO₂ session on the 5th is inside the window and
    // is *not* offered: it already carries the outdoor ride's proposal, and
    // linking to it would be a 409. The strength sessions are the wrong
    // discipline, which would be a 422.
    expect(within(list).getByText(/03\.08\.2026/)).toBeInTheDocument();
    expect(within(list).getByText(/04\.08\.2026/)).toBeInTheDocument();
    expect(within(list).queryByText(/05\.08\.2026/)).not.toBeInTheDocument();
    expect(within(list).queryByText(/06\.08\.2026/)).not.toBeInTheDocument();
  });

  it("links a low-scoring pick as displaced when the athlete says so", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.trainerRide);
    const panel = await ready();

    await user.click(
      within(panel).getByRole("button", { name: "Link to a planned session" }),
    );
    const picker = await screen.findByText("Which planned session was this?");
    const list = picker.closest("[data-slot='planned-picker']") as HTMLElement;
    const long = within(list)
      .getByText(/04\.08\.2026/)
      .closest("li");

    // The two link kinds are offered side by side, each explained: the
    // difference between them is what the week says about the planned session
    // afterwards, not a technicality.
    expect(
      within(list).getByText(/The planned session is marked displaced/),
    ).toBeInTheDocument();
    await user.click(
      within(long as HTMLElement).getByRole("button", {
        name: "Done instead of this",
      }),
    );

    await waitFor(() =>
      expect(within(plan()).getByText("Instead of")).toBeInTheDocument(),
    );
    // 0.3158 — stored however low, because a deliberate link is worth being
    // able to look at afterwards.
    expect(within(similarityHeader()).getByText("32%")).toBeInTheDocument();
    expect(
      ingestState().planned.find((row) => row.id === PLANNED_IDS.long)?.status,
    ).toBe("displaced");
  });

  it("offers to record a low-scoring ordinary link as displaced instead", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.trainerRide);
    const panel = await ready();

    await user.click(
      within(panel).getByRole("button", { name: "Link to a planned session" }),
    );
    const picker = await screen.findByText("Which planned session was this?");
    const list = picker.closest("[data-slot='planned-picker']") as HTMLElement;
    const long = within(list)
      .getByText(/04\.08\.2026/)
      .closest("li");
    await user.click(
      within(long as HTMLElement).getByRole("button", { name: "This was it" }),
    );

    // The similarity cannot be shown before the link is made — the API scores
    // a candidate only by linking it — so the panel says what it turned out to
    // be, and names the reading that usually fits a score this low.
    const offer = await screen.findByRole("status");
    expect(offer).toHaveTextContent("32%");
    expect(offer).toHaveTextContent(/arc would not have proposed it/);

    await user.click(
      within(offer).getByRole("button", { name: "Record it as done instead" }),
    );
    await waitFor(() =>
      expect(within(plan()).getByText("Instead of")).toBeInTheDocument(),
    );
    expect(
      ingestState().planned.find((row) => row.id === PLANNED_IDS.long)?.status,
    ).toBe("displaced");
  });

  it("shows the unlinked state when a displacement conversion fails halfway", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.trainerRide);
    const panel = await ready();

    await user.click(
      within(panel).getByRole("button", { name: "Link to a planned session" }),
    );
    const picker = await screen.findByText("Which planned session was this?");
    const list = picker.closest("[data-slot='planned-picker']") as HTMLElement;
    const long = within(list)
      .getByText(/04\.08\.2026/)
      .closest("li");
    await user.click(
      within(long as HTMLElement).getByRole("button", { name: "This was it" }),
    );
    const offer = await screen.findByRole("status");

    // The conversion is a delete followed by a create. The delete succeeds and
    // the create does not — the panel must show the true intermediate state
    // (no link), not go on rendering the link the delete just removed.
    server.use(
      // Untyped: an infrastructure failure has no schema to conform to.
      http.untyped.post("http://localhost:8000/api/v1/matches", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    await user.click(
      within(offer).getByRole("button", { name: "Record it as done instead" }),
    );

    expect(
      await within(plan()).findByRole("button", {
        name: "Link to a planned session",
      }),
    ).toBeInTheDocument();
  });

  it("marks a session unplanned, and drops the proposal on the way", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.trainerRide);
    const panel = await ready();

    await user.click(
      within(panel).getByRole("button", { name: "Nothing was planned" }),
    );
    await waitFor(() =>
      expect(screen.getByText("Unplanned")).toBeInTheDocument(),
    );
    expect(within(plan()).getByText(/stands on its own/)).toBeInTheDocument();
  });
});

describe("a link the machine made", () => {
  it("is offered for confirmation and can be swapped", async () => {
    const user = userEvent.setup();
    renderDetail(ACTIVITY_IDS.trainerRide);
    const panel = await ready();

    await user.click(
      within(panel).getByRole("button", { name: "Run matching again" }),
    );
    // 0.92 against the threshold session planned for the same evening: at or
    // above the auto-link threshold, so the link is made without asking.
    await waitFor(() =>
      expect(within(plan()).getByText("Auto-linked")).toBeInTheDocument(),
    );
    expect(within(similarityHeader()).getByText("92%")).toBeInTheDocument();
    expect(
      within(plan()).getByRole("button", { name: "Confirm this link" }),
    ).toBeInTheDocument();

    await user.click(
      within(plan()).getByRole("button", { name: "Swap to another session" }),
    );
    const picker = await screen.findByText(
      "Point this link at another planned session",
    );
    const list = picker.closest("[data-slot='planned-picker']") as HTMLElement;
    const long = within(list)
      .getByText(/04\.08\.2026/)
      .closest("li");
    await user.click(
      within(long as HTMLElement).getByRole("button", {
        name: "Move link here",
      }),
    );

    // A retarget is one decision: the old planned session goes back to what it
    // was, the new one takes the link, and the result is the athlete's.
    await waitFor(() =>
      expect(within(plan()).getByText("Confirmed")).toBeInTheDocument(),
    );
    expect(
      within(plan()).getByText("Build durability before the Ötztal."),
    ).toBeInTheDocument();
    const planned = ingestState().planned;
    expect(
      planned.find((row) => row.id === PLANNED_IDS.threshold)?.status,
    ).toBe("planned");
    expect(planned.find((row) => row.id === PLANNED_IDS.long)?.status).toBe(
      "completed",
    );
  });
});

describe("merging two recordings of one ride", () => {
  it("says exactly what it will do before it does it", async () => {
    const user = userEvent.setup();
    const half = seedMergeCandidate();
    renderDetail(ACTIVITY_IDS.outdoorRide);
    await ready();

    const merge = await screen.findByRole("heading", {
      name: "One ride recorded twice",
    });
    const section = merge.closest("[data-slot='panel']") as HTMLElement;
    await user.click(
      within(section).getByRole("button", { name: "Merge into this session" }),
    );

    const question = await screen.findByRole("alertdialog");
    expect(question).toHaveAccessibleName(/its own session row is removed/i);
    expect(question).toHaveAccessibleName(/Its recordings move here/i);

    await user.click(
      within(question).getByRole("button", { name: "Merge them" }),
    );

    await waitFor(() =>
      expect(
        ingestState().sessions.find((row) => row.id === half.id),
      ).toBeUndefined(),
    );
    // Two recordings on one session afterwards, and the panel has nothing
    // left to offer — which is the empty state, not a disappearing control.
    const survivor = ingestState().sessions.find(
      (row) => row.id === ACTIVITY_IDS.outdoorRide,
    );
    expect(survivor?.recordings).toHaveLength(2);
    await waitFor(() =>
      expect(
        screen.getByText(/No other device session was recorded/),
      ).toBeInTheDocument(),
    );
  });

  it("is not offered for a session that was typed in", async () => {
    renderDetail(ACTIVITY_IDS.gym);
    await ready();

    expect(
      screen.queryByRole("heading", { name: "One ride recorded twice" }),
    ).not.toBeInTheDocument();
  });
});

describe("when the plan cannot be loaded", () => {
  it("says so rather than showing an empty picker", async () => {
    const user = userEvent.setup();
    server.use(
      // Untyped: an infrastructure failure has no schema to conform to.
      http.untyped.get("http://localhost:8000/api/v1/planned-sessions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderDetail(ACTIVITY_IDS.trainerRide);
    const panel = await ready();

    await user.click(
      within(panel).getByRole("button", { name: "Link to a planned session" }),
    );
    expect(
      await screen.findByText(/Could not load the plan/),
    ).toBeInTheDocument();
  });
});
