import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import type * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SessionDetail } from "@/components/sessions/session-detail";
import { AthleteClock } from "@/lib/clock";
import {
  ACTIVITY_IDS,
  ATHLETE_TIMEZONE,
  contestDeclaration,
  PLANNED_IDS,
  scoringFor,
  seedScoredSession,
  statedScoring,
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
 * The panel is driven through the page, for the reason the match panel's tests
 * are: every action it offers writes a resource the page also reads, so a test
 * that rendered it in isolation could watch a declaration succeed and never
 * notice that nothing around it changed. The real query cache between the
 * mutation and the re-render is the thing worth proving.
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

/** The judgement panel: the section the "Judgement" heading names. */
function judgement(): HTMLElement {
  const heading = screen.getByRole("heading", { name: "Judgement" });
  const section = heading.closest("section");
  if (!section) {
    throw new Error("the Judgement heading is not inside a section");
  }
  return section;
}

/** The alignment panel, once its own query has answered. */
async function alignment(): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", { name: "Alignment" });
  const section = heading.closest("section");
  if (!section) {
    throw new Error("the Alignment heading is not inside a section");
  }
  return section;
}

/** Wait for the page and its score to have loaded. */
async function ready(): Promise<HTMLElement> {
  await screen.findByRole("heading", { name: "Corrections" });
  return await waitFor(() => {
    const panel = judgement();
    if (within(panel).queryByTestId("axis-grid") === null) {
      throw new Error("the score has not arrived");
    }
    return panel;
  });
}

/** The score the mock answers with — the domain's own, never restated here. */
const SCORE = statedScoring(ACTIVITY_IDS.outdoorRide, PLANNED_IDS.vo2, 0).score;

beforeEach(() => {
  seedScoredSession(ACTIVITY_IDS.outdoorRide, PLANNED_IDS.vo2);
});

describe("a session nothing has scored", () => {
  it("names the missing input instead of showing an empty panel", async () => {
    // The trainer ride is unmatched, and an unmatched session is not scored.
    renderDetail(ACTIVITY_IDS.trainerRide);
    await screen.findByRole("heading", { name: "Corrections" });

    await within(judgement()).findByText(/a pending proposal is a question/i);
  });

  it("leaves the rest of the page standing when the score cannot be read", async () => {
    // A 200 that is not a score — a stale cache, a proxy, a fake that answers
    // every path under `/sessions/` with the session. The panel degrades to an
    // empty grid; the five sections around it have nothing to do with scoring
    // and must still render. This is the failure `e2e/inbox.spec.ts` hit, one
    // layer down from where it was found.
    server.use(
      http.untyped.get("http://localhost:8000/api/v1/sessions/:id/score", () =>
        HttpResponse.json({ purpose: "vo2max" }),
      ),
    );
    renderDetail(ACTIVITY_IDS.outdoorRide);

    await screen.findByRole("heading", { name: "Corrections" });
    expect(screen.getByRole("heading", { name: "Recording" })).toBeVisible();
    expect(await screen.findByTestId("axis-grid")).toBeEmptyDOMElement();
  });
});

describe("the axes", () => {
  it("shows every axis the purpose carries, with the domain's own numbers", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    const panel = await ready();
    const grid = within(panel).getByTestId("axis-grid");

    // Three axes for `vo2max`: completion, adherence, pacing — in the
    // template's order, and holding their positions.
    expect(
      within(grid)
        .getAllByRole("term")
        .map((term) => term.textContent),
    ).toEqual(["Completion", "Adherence", "Pacing"]);

    for (const axis of SCORE.axes) {
      const percent = `${Math.round((axis.value ?? 0) * 100)}%`;
      expect(within(grid).getByText(percent)).toBeInTheDocument();
    }
  });

  it("prints the suggestion beside the rule that produced it", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    const panel = await ready();

    expect(
      within(panel).getByText(SCORE.verdict_rationale),
    ).toBeInTheDocument();
  });

  it("expands the criteria under an axis, marking what could not be checked", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    const panel = await ready();

    // The duration floor this ride failed, under Completion.
    const summary = within(panel).getByText(/^Completion — /);
    await userEvent.click(summary);
    const floor = SCORE.axes
      .flatMap((axis) => axis.criteria)
      .find((one) => one.kind === "duration_floor");
    if (!floor) {
      throw new Error("the generated score carries no duration floor");
    }
    expect(within(panel).getByText(floor.detail)).toBeInTheDocument();
    expect(
      within(panel).getAllByRole("img", {
        name: floor.passed ? "Passed" : "Failed",
      }).length,
    ).toBeGreaterThan(0);
  });

  it("shows the criteria this purpose is not scored on", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    const panel = await ready();

    // The heart-rate ceiling: `vo2max` has no discipline axis, and a criterion
    // nobody can see is a promise nobody kept.
    expect(SCORE.other_criteria.length).toBeGreaterThan(0);
    await userEvent.click(
      within(panel).getByText(/^Not scored on this purpose — /),
    );
    expect(
      within(panel).getByText(SCORE.other_criteria[0].detail),
    ).toBeInTheDocument();
  });
});

describe("declaring a verdict", () => {
  it("confirms the suggestion, and needs the reasons the API needs", async () => {
    // The generated score suggests `abandoned`, which is not `as_intended` —
    // so confirming it opens the reason picker rather than sending a
    // declaration the server would refuse.
    expect(SCORE.suggested_verdict).toBe("abandoned");

    renderDetail(ACTIVITY_IDS.outdoorRide);
    const panel = await ready();

    await userEvent.click(
      within(panel).getByRole("button", { name: /^Confirm — abandoned$/ }),
    );
    const form = within(panel).getByTestId("declare-form");
    expect(
      within(form).getByRole("radio", { name: /Abandoned/ }),
    ).toBeChecked();

    // Nothing may be sent until a reason is given.
    expect(within(form).getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.click(within(form).getByText("Time"));
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));

    await within(panel).findByText("You said");
    expect(scoringFor(ACTIVITY_IDS.outdoorRide)?.declaration).toMatchObject({
      declared_verdict: "abandoned",
      reasons: [expect.objectContaining({ reasons: ["time"], version: 1 })],
    });
  });

  it("sends the override and its reasons in the order they were picked", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    const panel = await ready();

    await userEvent.click(
      within(panel).getByRole("button", { name: "It was something else" }),
    );
    const form = within(panel).getByTestId("declare-form");
    await userEvent.click(within(form).getByRole("radio", { name: /Under/ }));
    // Picked out of list order on purpose: the order is data — the first is
    // the main one — so a revision that only reorders them is a real revision.
    await userEvent.click(within(form).getByText("Fatigue"));
    await userEvent.click(within(form).getByText("Time"));
    await userEvent.type(
      within(form).getByLabelText(/In your own words/),
      "Legs were empty from Saturday.",
    );
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));

    await within(panel).findByText("You said");
    // The request body, as the handler recorded it — not a canned reply.
    expect(scoringFor(ACTIVITY_IDS.outdoorRide)?.declaration).toMatchObject({
      declared_verdict: "under",
      suggested_at_declaration: "abandoned",
      reasons: [
        expect.objectContaining({
          reasons: ["fatigue", "time"],
          note: "Legs were empty from Saturday.",
        }),
      ],
    });
  });

  it("refuses a fourth reason on this side of the request", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    const panel = await ready();

    await userEvent.click(
      within(panel).getByRole("button", { name: "It was something else" }),
    );
    const form = within(panel).getByTestId("declare-form");
    await userEvent.click(within(form).getByRole("radio", { name: /Under/ }));
    for (const reason of ["Time", "Weather", "Heat"]) {
      await userEvent.click(within(form).getByText(reason));
    }

    // Three is the most a declaration carries, and the picker says so rather
    // than letting the athlete compose a 422.
    expect(
      within(form).getByText(/Three is the most a declaration carries/),
    ).toBeInTheDocument();
    const fourth = within(form)
      .getByText("Traffic")
      .closest("label")
      ?.querySelector("input");
    expect(fourth).toBeDisabled();
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        scoringFor(ACTIVITY_IDS.outdoorRide)?.declaration?.reasons[0].reasons,
      ).toEqual(["time", "weather", "heat"]),
    );
  });

  it("does not send reasons the athlete can no longer see", async () => {
    // Switching to "As intended" hides the reason picker and leaves nothing
    // on screen that says "time" — but the server accepts reasons on an
    // `as_intended` declaration, so a payload that still carried them would
    // put a reason into the athlete's own testimony that they watched
    // themselves drop.
    renderDetail(ACTIVITY_IDS.outdoorRide);
    const panel = await ready();

    await userEvent.click(
      within(panel).getByRole("button", { name: /^Confirm — abandoned$/ }),
    );
    await userEvent.click(
      within(within(panel).getByTestId("declare-form")).getByText("Time"),
    );
    await userEvent.click(
      within(within(panel).getByTestId("declare-form")).getByRole("button", {
        name: "Save",
      }),
    );
    await within(panel).findByText("You said");

    await userEvent.click(
      within(panel).getByRole("button", { name: "Change what you said" }),
    );
    const form = within(panel).getByTestId("declare-form");
    expect(within(form).getByText("Time").closest("label")).toBeInTheDocument();
    await userEvent.click(
      within(form).getByRole("radio", { name: /As intended/ }),
    );
    // The picker is gone, so the reasons are gone.
    expect(within(form).queryByText("Time")).toBeNull();
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(scoringFor(ACTIVITY_IDS.outdoorRide)?.declaration).toMatchObject({
        declared_verdict: "as_intended",
      }),
    );
    expect(scoringFor(ACTIVITY_IDS.outdoorRide)?.declaration?.reasons).toEqual(
      [],
    );
  });

  it("revises the reasons afterwards, appending rather than overwriting", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    const panel = await ready();

    await userEvent.click(
      within(panel).getByRole("button", { name: /^Confirm — abandoned$/ }),
    );
    await userEvent.click(
      within(within(panel).getByTestId("declare-form")).getByText("Weather"),
    );
    await userEvent.click(
      within(within(panel).getByTestId("declare-form")).getByRole("button", {
        name: "Save",
      }),
    );
    await within(panel).findByText("You said");

    await userEvent.click(
      within(panel).getByRole("button", { name: "Revise the reasons" }),
    );
    const form = within(panel).getByTestId("declare-form");
    // The verdict is not up for revision here — only why.
    expect(within(form).queryByRole("radio")).toBeNull();
    await userEvent.click(within(form).getByText("Weather"));
    await userEvent.click(within(form).getByText("Illness"));
    await userEvent.click(
      within(form).getByRole("button", { name: "Save the reasons" }),
    );

    await waitFor(() =>
      expect(
        scoringFor(ACTIVITY_IDS.outdoorRide)?.declaration?.reasons,
      ).toHaveLength(2),
    );
    expect(
      scoringFor(ACTIVITY_IDS.outdoorRide)?.declaration?.reasons.map(
        (one) => one.reasons,
      ),
    ).toEqual([["weather"], ["illness"]]);
  });
});

describe("a contested declaration", () => {
  it("puts the machine's new opinion beside the athlete's, and offers a re-confirm", async () => {
    seedScoredSession(ACTIVITY_IDS.outdoorRide, PLANNED_IDS.vo2);
    const record = scoringFor(ACTIVITY_IDS.outdoorRide);
    if (!record) {
      throw new Error("the session was not seeded");
    }
    record.declaration = {
      declared_verdict: "under",
      declared_at: "2026-08-07T09:10:00Z",
      // What the machine was saying when the athlete overruled it.
      suggested_at_declaration: "abandoned",
      contested: false,
      contested_at: null,
      contested_verdict: null,
      reasons: [
        {
          version: 1,
          recorded_at: "2026-08-07T09:10:00Z",
          revision_reason: null,
          reasons: ["time"],
          note: null,
          recorded_by: "athlete",
        },
      ],
    };
    // A *new* opinion: `different_session` is neither what was declared nor
    // what was being suggested at the time, which is the whole rule.
    contestDeclaration(ACTIVITY_IDS.outdoorRide, "different_session");

    renderDetail(ACTIVITY_IDS.outdoorRide);
    const panel = await ready();

    const banner = await within(panel).findByTestId("contested-banner");
    expect(banner).toHaveTextContent("A different session");
    expect(banner).toHaveTextContent("Under");
    expect(banner).toHaveTextContent(/that is what stands/);

    await userEvent.click(
      within(banner).getByRole("button", { name: "Re-confirm under" }),
    );

    // Re-declaring is the athlete ruling on the current opinion, and it clears
    // the flag — with the declaration itself untouched.
    await waitFor(() =>
      expect(scoringFor(ACTIVITY_IDS.outdoorRide)?.declaration).toMatchObject({
        declared_verdict: "under",
        contested: false,
        contested_verdict: null,
      }),
    );
    await waitFor(() =>
      expect(within(panel).queryByTestId("contested-banner")).toBeNull(),
    );
  });
});

describe("the alignment offset", () => {
  it("shows the pairing, the exclusions and the steps nothing answered", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    await ready();
    const table = within(await alignment()).getByTestId("alignment-table");
    const held = statedScoring(
      ACTIVITY_IDS.outdoorRide,
      PLANNED_IDS.vo2,
      0,
    ).alignment;

    // One row per pair the assignment made, kept or refused, plus one per
    // step nothing answered. Header row included.
    expect(within(table).getAllByRole("row")).toHaveLength(
      1 +
        held.aligned.length +
        held.excluded.length +
        held.unmatched_steps.length +
        held.unmatched_intervals.length,
    );
    expect(
      within(table).getByText(/too unlike the step to trust the pairing/),
    ).toBeInTheDocument();
    expect(
      within(table).getByText("No detected effort answered this step."),
    ).toBeInTheDocument();
  });

  it("sends the offset, and re-pairs the steps against what came back", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    await ready();
    const panel = await alignment();

    const field = within(panel).getByLabelText(/Offset/);
    await userEvent.clear(field);
    await userEvent.type(field, "-1200");
    await userEvent.click(
      within(panel).getByRole("button", { name: "Re-align and rescore" }),
    );

    await waitFor(() =>
      expect(scoringFor(ACTIVITY_IDS.outdoorRide)).toMatchObject({
        offset_s: -1200,
        // One transaction, both artefacts: a score never points at an
        // alignment that is not the one in force.
        alignment_version: 2,
        score_version: 2,
      }),
    );

    // The table follows: at −1200 s the first prescribed effort is the one
    // nothing answered, where at 0 s it was paired.
    const after = statedScoring(
      ACTIVITY_IDS.outdoorRide,
      PLANNED_IDS.vo2,
      -1200,
    ).alignment;
    expect(after.unmatched_steps).toEqual([1]);
    const table = within(await alignment()).getByTestId("alignment-table");
    await waitFor(() =>
      expect(within(panel).getByText("version 2")).toBeInTheDocument(),
    );
    // Step 1 was paired at 0 s and is unanswered at −1200 s.
    expect(table).toHaveTextContent("No detected effort answered this step.");
  });

  it("will not send an offset that has not changed", async () => {
    renderDetail(ACTIVITY_IDS.outdoorRide);
    await ready();

    expect(
      within(await alignment()).getByRole("button", {
        name: "Re-align and rescore",
      }),
    ).toBeDisabled();
  });
});
