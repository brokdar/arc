import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";
import {
  SessionCoachNotes,
  WeekCoachNotes,
} from "@/components/coach/coach-notes";
import { SessionDetail } from "@/components/sessions/session-detail";
import { AthleteClock } from "@/lib/clock";
import { mondayOf } from "@/lib/dates";
import {
  ACTIVITY_IDS,
  ATHLETE_TIMEZONE,
  athleteToday,
  COACH_MODEL,
  NOTE_IDS,
  noteList,
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

function renderWith(node: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AthleteClock timezone={ATHLETE_TIMEZONE}>{node}</AthleteClock>
    </QueryClientProvider>,
  );
}

/** One note card, by the id of the note it is about. */
async function noteCard(text: RegExp): Promise<HTMLElement> {
  const paragraph = await screen.findByText(text);
  const card = paragraph.closest("[data-testid='coach-note']");
  if (!card) {
    throw new Error("that text is not inside a coach note");
  }
  return card as HTMLElement;
}

describe("a coach note", () => {
  it("attributes the model, the key and the moment", async () => {
    renderWith(<SessionCoachNotes sessionId={ACTIVITY_IDS.outdoorRide} />);
    const card = await noteCard(/held the tempo band/);

    // All three, because "the coach" is not one thing over a season: a note
    // written by a model that has since been replaced is still on the session.
    expect(within(card).getByText(COACH_MODEL)).toBeInTheDocument();
    expect(within(card).getByText("coach")).toBeInTheDocument();
    // On the *athlete's* clock, not in UTC: `AGENT_NOW` is 06:30Z and the
    // fixture puts the athlete at UTC+14, so the note reads 20:30 on their
    // own 7 August. Rendered in UTC this said 06:30 with nothing naming the
    // zone, on a page whose every other date is athlete-local (issue #62).
    expect(within(card).getByText("07.08 20:30")).toBeInTheDocument();
  });

  it("tells an evaluation from an annotation", async () => {
    renderWith(<SessionCoachNotes sessionId={ACTIVITY_IDS.outdoorRide} />);
    await noteCard(/held the tempo band/);

    expect(
      screen.getAllByTestId("coach-note").map((card) => card.dataset.kind),
    ).toEqual(["evaluation", "annotation"]);
    expect(screen.getByText("Evaluation")).toBeInTheDocument();
  });

  it("shows what it cites, and says so when it cites nothing", async () => {
    renderWith(<SessionCoachNotes sessionId={ACTIVITY_IDS.outdoorRide} />);
    const evaluation = await noteCard(/held the tempo band/);
    const annotation = await noteCard(/coffee stop at the same hour/);

    expect(
      within(evaluation).getByText(ACTIVITY_IDS.outdoorRide.slice(0, 8)),
    ).toBeInTheDocument();
    expect(within(annotation).getByText("Cites nothing.")).toBeInTheDocument();
  });

  it("draws nothing at all when the coach has said nothing", async () => {
    server.use(
      http.get("/api/v1/agent-notes", ({ response }) =>
        response(200).json({ items: [] }),
      ),
    );
    const { container } = renderWith(
      <SessionCoachNotes sessionId={ACTIVITY_IDS.outdoorRide} />,
    );

    await waitFor(() => {
      expect(container).toBeEmptyDOMElement();
    });
  });

  it("says a failed load was a failure, in the server's own words", async () => {
    server.use(
      http.get("/api/v1/agent-notes", ({ response }) =>
        response(422).json({ detail: "Give exactly one subject." }),
      ),
    );
    renderWith(<SessionCoachNotes sessionId={ACTIVITY_IDS.outdoorRide} />);

    // An empty panel is how "no notes" renders, so a failed load has to be
    // visible or it reads as silence. What is visible is what the API said:
    // it answered, so "Is the API reachable?" is a question it already
    // answered — see `loadFailureMessage`.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Give exactly one subject.",
    );
  });
});

describe("the dispute toggle", () => {
  it("rates a note up and remembers it", async () => {
    const user = userEvent.setup();
    renderWith(<SessionCoachNotes sessionId={ACTIVITY_IDS.outdoorRide} />);
    const card = await noteCard(/held the tempo band/);

    await user.click(within(card).getByRole("button", { name: "Useful" }));

    await waitFor(() => {
      expect(
        within(card).getByRole("button", { name: "Useful" }),
      ).toHaveAttribute("aria-pressed", "true");
    });
    expect(
      noteList().find((note) => note.id === NOTE_IDS.rideEvaluation)?.dispute,
    ).toBe("up");
  });

  it("takes a rating back when the same thumb is tapped again", async () => {
    const user = userEvent.setup();
    renderWith(<SessionCoachNotes sessionId={ACTIVITY_IDS.outdoorRide} />);
    // The annotation arrives already rated up, so the first tap is the one
    // that clears it — the third state of a two-button toggle.
    const card = await noteCard(/coffee stop at the same hour/);
    expect(
      within(card).getByRole("button", { name: "Useful" }),
    ).toHaveAttribute("aria-pressed", "true");

    await user.click(within(card).getByRole("button", { name: "Useful" }));

    await waitFor(() => {
      expect(
        within(card).getByRole("button", { name: "Useful" }),
      ).toHaveAttribute("aria-pressed", "false");
    });
    const stored = noteList().find(
      (note) => note.id === NOTE_IDS.rideAnnotation,
    );
    expect(stored?.dispute).toBeNull();
    // A cleared rating has no instant: there is nothing standing that was said
    // at one.
    expect(stored?.disputed_at).toBeNull();
  });

  it("says a refused rating was refused, and leaves the toggle alone", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/v1/agent-notes/{note_id}/dispute", ({ response }) =>
        response(403).json({ detail: "This key may not write." }),
      ),
    );
    renderWith(<SessionCoachNotes sessionId={ACTIVITY_IDS.outdoorRide} />);
    const card = await noteCard(/held the tempo band/);

    await user.click(within(card).getByRole("button", { name: "Useful" }));

    expect(await within(card).findByRole("alert")).toHaveTextContent(
      "This key may not write.",
    );
    // The pressed state is read off the note the server returns, so a refused
    // tap that printed nothing would be indistinguishable from one the page
    // never received.
    expect(
      within(card).getByRole("button", { name: "Useful" }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(
      noteList().find((note) => note.id === NOTE_IDS.rideEvaluation)?.dispute,
    ).toBeNull();
  });

  it("swaps one thumb for the other rather than clearing", async () => {
    const user = userEvent.setup();
    renderWith(<SessionCoachNotes sessionId={ACTIVITY_IDS.outdoorRide} />);
    const card = await noteCard(/coffee stop at the same hour/);

    await user.click(within(card).getByRole("button", { name: "Wrong" }));

    await waitFor(() => {
      expect(
        noteList().find((note) => note.id === NOTE_IDS.rideAnnotation)?.dispute,
      ).toBe("down");
    });
    expect(within(card).getByRole("button", { name: "Wrong" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});

describe("where notes are read", () => {
  it("puts a week's notes on the week", async () => {
    renderWith(<WeekCoachNotes week={mondayOf(athleteToday())} />);

    expect(
      await screen.findByText(/The week did what it was for/),
    ).toBeInTheDocument();
    // One subject per note: the ride's two notes are not this week's.
    expect(screen.getAllByTestId("coach-note")).toHaveLength(1);
  });

  it("puts a session's notes on the session page, under the scoring", async () => {
    renderWith(<SessionDetail sessionId={ACTIVITY_IDS.outdoorRide} />);

    await screen.findByRole("heading", { name: "Corrections" });
    const coach = await screen.findByRole("heading", { name: "Coach" });
    expect(coach).toBeInTheDocument();
    expect(screen.getByText(/held the tempo band/)).toBeInTheDocument();
  });
});
