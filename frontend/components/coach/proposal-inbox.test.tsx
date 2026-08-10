import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { ProposalInbox } from "@/components/coach/proposal-inbox";
import { SidebarNav } from "@/components/shell/sidebar-nav";
import { $api } from "@/lib/api/client";
import type { Proposal } from "@/lib/proposals";
import {
  PROPOSAL_IDS,
  plannedSessionFixture,
  proposalById,
  SESSION_IDS,
  WORKOUT_IDS,
} from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

vi.mock("next/navigation", () => ({ usePathname: () => "/proposals" }));

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
 * The inbox, optionally beside something else that reads the same cache.
 *
 * An accept rewrites the plan, and the surfaces that show the plan are not on
 * this page — so what an accept invalidates can only be tested by mounting one
 * of them next to it, sharing the one query client the app has.
 */
function renderInbox(beside?: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ProposalInbox />
      {beside}
    </QueryClientProvider>,
  );
}

/**
 * A stand-in for the two panels that read one planned session by id: Today's
 * session panel and the calendar's session sheet. Both are showing exactly the
 * prescription an accepted proposal rewrites, and neither shares a cache key
 * with the week or with the list — `["get", "/api/v1/planned-sessions"]` is a
 * different key, element for element, not a shorter one.
 */
function PlannedSessionProbe() {
  $api.useQuery("get", "/api/v1/planned-sessions/{planned_session_id}", {
    params: { path: { planned_session_id: SESSION_IDS.vo2 } },
  });
  return null;
}

/**
 * A stand-in for the match and session views. Accepting a `delete` cascades the
 * planned session's `session_matches` and nulls the scores hung off them, so an
 * open match list left uninvalidated goes on drawing a link to a session the
 * accept just removed.
 */
function MatchesProbe() {
  $api.useQuery("get", "/api/v1/matches", {
    params: { query: { limit: 25 } },
  });
  return null;
}

/** The pending proposal as the fixtures hold it, or a loud failure. */
function pendingFixture(): Proposal {
  const proposal = proposalById(PROPOSAL_IDS.pending);
  if (!proposal) {
    throw new Error("no pending proposal in the fixtures");
  }
  return proposal;
}

/**
 * The same proposal, with its `update` change swapping one workout for
 * another out of the same batch — the case the shortened id cannot show.
 */
function swapWorkout(proposal: Proposal): Proposal {
  const [update, ...rest] = proposal.diff;
  const { before, after } = update;
  if (before === null || after === null) {
    throw new Error("the pending fixture no longer opens with an update");
  }
  return {
    ...proposal,
    diff: [
      {
        ...update,
        before: { ...before, workout_id: WORKOUT_IDS.vo2 },
        after: { ...after, workout_id: WORKOUT_IDS.long },
      },
      ...rest,
    ],
  };
}

/**
 * The same proposal, with its `update` change touching only the body — the
 * success criteria — and nothing a scalar column would show. The case that
 * used to render as "no field differs" above an enabled Accept.
 */
function reviseCriteria(proposal: Proposal): Proposal {
  const [update, ...rest] = proposal.diff;
  const { before, after } = update;
  if (before === null || after === null) {
    throw new Error("the pending fixture no longer opens with an update");
  }
  const body = { discipline: "cycling", steps: [] };
  return {
    ...proposal,
    diff: [
      {
        ...update,
        before: {
          ...before,
          purpose: after.purpose,
          intent_text: after.intent_text,
          predicted_load: after.predicted_load,
          structure: body,
          success_criteria: [
            { kind: "time_in_band", min_fraction: 0.75 },
            { kind: "duration_floor", min_seconds: 3000 },
          ],
        },
        after: {
          ...after,
          structure: body,
          success_criteria: [{ kind: "duration_floor", min_seconds: 3000 }],
        },
      },
      ...rest,
    ],
  };
}

/** The one pending proposal's card. */
async function pendingCard(): Promise<HTMLElement> {
  const cards = await screen.findAllByTestId("proposal");
  const card = cards.find((element) => element.dataset.status === "pending") as
    | HTMLElement
    | undefined;
  if (!card) {
    throw new Error("no pending proposal on the page");
  }
  return card;
}

/** One field row of the diff, wherever in the card it is. */
function field(card: HTMLElement, name: string): HTMLElement {
  const row = within(card)
    .getAllByTestId("diff-field")
    .find((element) => element.dataset.field === name);
  if (!row) {
    throw new Error(`no diff row for ${name}`);
  }
  return row;
}

describe("the inbox", () => {
  it("opens on what is waiting, and asks the API for exactly that", async () => {
    const asked = vi.fn();
    server.use(
      http.get("/api/v1/proposals", ({ query, response }) => {
        asked(query.get("status"));
        return response(200).json({
          items: [],
          total: 0,
          offset: 0,
          limit: 25,
        });
      }),
    );
    renderInbox();

    await screen.findByText("Nothing is waiting on you.");
    // The filter has to reach the server, not just the render: filtering a
    // page of pending proposals down to the pending ones proves nothing.
    expect(asked).toHaveBeenCalledWith("pending");
  });

  it("names what would fill an empty queue", async () => {
    server.use(
      http.get("/api/v1/proposals", ({ response }) =>
        response(200).json({ items: [], total: 0, offset: 0, limit: 25 }),
      ),
    );
    renderInbox();

    // UI convention 3: an empty state names the action that supplies the
    // missing input — here, connecting an agent, which is not a control on
    // this page but is still the answer to "why is this empty?".
    expect(
      await screen.findByText(/coaching agent connected to arc's MCP server/),
    ).toBeInTheDocument();
  });

  it("shows the rationale, the actor and the expiry", async () => {
    renderInbox();
    const card = await pendingCard();

    expect(
      within(card).getByText(/Saturday's three hours/),
    ).toBeInTheDocument();
    expect(within(card).getByText("coach")).toBeInTheDocument();
    expect(within(card).getByText(/expires in/)).toBeInTheDocument();
    expect(within(card).getByText("Waiting on you")).toBeInTheDocument();
  });

  it("shows the supersede link, so a replaced proposal is traceable", async () => {
    renderInbox();
    const card = await pendingCard();

    expect(
      within(card).getByText(
        `supersedes ${PROPOSAL_IDS.superseded.slice(0, 8)}`,
      ),
    ).toBeInTheDocument();
  });

  it("filters to a resolved status on demand", async () => {
    const user = userEvent.setup();
    renderInbox();
    await pendingCard();

    await user.selectOptions(screen.getByLabelText("Show"), "rejected");

    await waitFor(() => {
      expect(screen.getAllByTestId("proposal")).toHaveLength(1);
    });
    expect(screen.getByTestId("proposal").dataset.status).toBe("rejected");
    // The reason the athlete gave is kept and shown back to them.
    expect(
      screen.getByText(/Four rides in that week is not happening/),
    ).toBeInTheDocument();
  });
});

describe("the diff", () => {
  it("renders one card per change, one of each kind", async () => {
    renderInbox();
    const card = await pendingCard();

    expect(
      within(card)
        .getAllByTestId("proposal-change")
        .map((change) => change.dataset.kind),
    ).toEqual(["update", "move", "create", "delete"]);
  });

  it("draws the changed field apart from the unchanged ones", async () => {
    renderInbox();
    const card = await pendingCard();
    const update = within(card).getAllByTestId("proposal-change")[0];

    // The purpose moved and the date did not — and the fixture's two
    // snapshots agree on the date character for character, so a diff that
    // marked everything changed would fail here rather than look plausible.
    expect(field(update, "purpose").dataset.changed).toBe("true");
    expect(field(update, "date").dataset.changed).toBe("false");
    expect(
      within(field(update, "purpose")).getByText("VO₂max"),
    ).toBeInTheDocument();
    expect(
      within(field(update, "purpose")).getByText("Threshold"),
    ).toBeInTheDocument();
  });

  it("shows a move as the date and nothing else", async () => {
    renderInbox();
    const card = await pendingCard();
    const move = within(card).getAllByTestId("proposal-change")[1];

    expect(
      within(move)
        .getAllByTestId("diff-field")
        .filter((row) => row.dataset.changed === "true")
        .map((row) => row.dataset.field),
    ).toEqual(["date"]);
    expect(within(move).getByText("1 field differs")).toBeInTheDocument();
  });

  it("headlines a move with both of its dates, formatted", async () => {
    renderInbox();
    const card = await pendingCard();
    const move = within(card).getAllByTestId("proposal-change")[1];

    // The change's own `date` is where the session is now — the target lives
    // in `after` — so the header has to show the journey or it headlines the
    // move with the date it exists to change. Formatted like the Date row
    // below it, not the raw ISO the API sends.
    expect(
      within(move).getByText("12.08.2026 → 11.08.2026"),
    ).toBeInTheDocument();
  });

  it("sees a workout swap and prints both ids in full so it shows", async () => {
    // `WORKOUT_IDS` are uuid7s from one batch, so they share their leading
    // characters. The row prints the whole id now, so the swap is a visible
    // before/after rather than the `0199a000 → 0199a000` a shortened id drew —
    // which the card had counted as "no field differs" and offered for accept.
    server.use(
      http.get("/api/v1/proposals", ({ response }) =>
        response(200).json({
          items: [swapWorkout(pendingFixture())],
          total: 1,
          offset: 0,
          limit: 25,
        }),
      ),
    );
    renderInbox();
    const card = await pendingCard();
    const update = within(card).getAllByTestId("proposal-change")[0];
    const workout = field(update, "workout_id");

    expect(workout.dataset.changed).toBe("true");
    expect(within(update).getByText(/fields differ/)).toBeInTheDocument();
    // Both full ids on the page, one struck through and one not — a swap the
    // athlete can actually see, which a shared eight-character prefix hid.
    expect(within(workout).getByText(WORKOUT_IDS.vo2)).toBeInTheDocument();
    expect(within(workout).getByText(WORKOUT_IDS.long)).toBeInTheDocument();
  });

  it("shows a body-only revision as a changed field, not 'no field differs'", async () => {
    // A revision that touches only the success criteria projects onto no
    // scalar field; before the snapshot carried the body it rendered as "no
    // field differs" above an enabled Accept. It is a diff row now (FIX-F1).
    server.use(
      http.get("/api/v1/proposals", ({ response }) =>
        response(200).json({
          items: [reviseCriteria(pendingFixture())],
          total: 1,
          offset: 0,
          limit: 25,
        }),
      ),
    );
    renderInbox();
    const card = await pendingCard();
    const update = within(card).getAllByTestId("proposal-change")[0];

    expect(field(update, "success_criteria").dataset.changed).toBe("true");
    expect(within(update).getByText(/differs?/)).toBeInTheDocument();
    expect(
      within(update).queryByText("no field differs"),
    ).not.toBeInTheDocument();
  });

  it("shows the concurrency token the accept will re-check", async () => {
    renderInbox();
    const card = await pendingCard();
    const update = within(card).getAllByTestId("proposal-change")[0];

    expect(within(update).getByText("intent v3")).toBeInTheDocument();
  });

  it("gives a create no before side to strike through", async () => {
    renderInbox();
    const card = await pendingCard();
    const create = within(card).getAllByTestId("proposal-change")[2];

    for (const row of within(create).getAllByTestId("diff-field")) {
      expect(row.dataset.changed).toBe("true");
    }
    expect(
      within(create).queryByText("no field differs"),
    ).not.toBeInTheDocument();
  });
});

describe("answering a proposal", () => {
  it("accepts it, and the card stops waiting on you", async () => {
    const user = userEvent.setup();
    renderInbox();
    const card = await pendingCard();

    await user.click(within(card).getByRole("button", { name: "Accept" }));

    // The list is re-fetched and the accepted proposal is no longer in the
    // pending filter — which is the only proof the invalidation ran.
    await waitFor(() => {
      expect(screen.queryAllByTestId("proposal")).toHaveLength(0);
    });
    expect(proposalById(PROPOSAL_IDS.pending)?.status).toBe("accepted");
  });

  it("refetches the session a panel elsewhere is showing", async () => {
    const user = userEvent.setup();
    const fetched = vi.fn();
    server.use(
      http.get(
        "/api/v1/planned-sessions/{planned_session_id}",
        ({ params, response }) => {
          fetched(params.planned_session_id);
          return response(200).json(
            plannedSessionFixture(params.planned_session_id),
          );
        },
      ),
    );
    renderInbox(<PlannedSessionProbe />);
    const card = await pendingCard();
    await waitFor(() => {
      expect(fetched).toHaveBeenCalledTimes(1);
    });

    await user.click(within(card).getByRole("button", { name: "Accept" }));

    // The accept rewrote this session's prescription. Today's panel and the
    // session sheet read it by id and neither shares a key with the week, so
    // without their own invalidation they go on showing the intent the accept
    // replaced until something else happens to refetch it.
    await waitFor(() => {
      expect(fetched).toHaveBeenCalledTimes(2);
    });
  });

  it("refetches the matches a panel elsewhere is showing", async () => {
    const user = userEvent.setup();
    const fetched = vi.fn();
    server.use(
      http.get("/api/v1/matches", ({ response }) => {
        fetched();
        return response(200).json({
          items: [],
          total: 0,
          offset: 0,
          limit: 25,
        });
      }),
    );
    renderInbox(<MatchesProbe />);
    const card = await pendingCard();
    await waitFor(() => {
      expect(fetched).toHaveBeenCalledTimes(1);
    });

    await user.click(within(card).getByRole("button", { name: "Accept" }));

    // Accepting a delete cascades `session_matches`; a match view that shares
    // this query client would keep a link to a removed session without its own
    // invalidation (FIX-F5).
    await waitFor(() => {
      expect(fetched).toHaveBeenCalledTimes(2);
    });
  });

  it("drops the count in the sidebar with the proposal it counted", async () => {
    const user = userEvent.setup();
    renderInbox(<SidebarNav />);
    const card = await pendingCard();
    expect(await screen.findByTestId("pending-proposals")).toHaveTextContent(
      "1",
    );

    await user.click(within(card).getByRole("button", { name: "Accept" }));

    // The badge is the count of what is waiting on the athlete, read from its
    // own one-item query — an accept that left it standing would say something
    // is waiting on every page of the app.
    await waitFor(() => {
      expect(screen.queryByTestId("pending-proposals")).not.toBeInTheDocument();
    });
  });

  it("sends the reason with a rejection, and shows it back", async () => {
    const user = userEvent.setup();
    renderInbox();
    const card = await pendingCard();

    await user.click(within(card).getByRole("button", { name: "Reject" }));
    await user.type(
      screen.getByLabelText(/Why not/),
      "Racing on Sunday, not touching the week.",
    );
    await user.click(screen.getByRole("button", { name: "Reject it" }));

    await waitFor(() => {
      expect(proposalById(PROPOSAL_IDS.pending)?.status).toBe("rejected");
    });
    // Echoed by the handler, so a form that dropped the field would fail here.
    expect(proposalById(PROPOSAL_IDS.pending)?.resolution_note).toBe(
      "Racing on Sunday, not touching the week.",
    );
  });

  it("rejects without a reason rather than demanding one", async () => {
    const user = userEvent.setup();
    renderInbox();
    const card = await pendingCard();

    await user.click(within(card).getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Reject it" }));

    await waitFor(() => {
      expect(proposalById(PROPOSAL_IDS.pending)?.status).toBe("rejected");
    });
    // Empty is null, not "": the API's field is nullable and an empty string
    // would be a reason the athlete never gave.
    expect(proposalById(PROPOSAL_IDS.pending)?.resolution_note).toBeNull();
  });

  it("backs out of a rejection without sending anything", async () => {
    const user = userEvent.setup();
    renderInbox();
    const card = await pendingCard();

    await user.click(within(card).getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Keep it waiting" }));

    expect(screen.queryByLabelText(/Why not/)).not.toBeInTheDocument();
    expect(proposalById(PROPOSAL_IDS.pending)?.status).toBe("pending");
  });

  // The three ways an accept comes back 409, each with the sentence the server
  // actually sends for it — they must not all read as "the plan moved" (FIX-F4).
  it.each([
    [
      "the plan was revised under it",
      "Change 0: planned session 0199a000 has moved on — this change was computed against intent version 3, but version 4 is in force. Re-read the session and propose again.",
    ],
    [
      "it expired before it was answered",
      "This proposal expired at 2026-08-07T06:30:00+00:00 and cannot be accepted. The committed plan stands.",
    ],
    [
      "it is no longer pending",
      "This proposal is already accepted; it cannot become accepted.",
    ],
  ])("renders the server's own words when %s", async (_why, detail) => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/v1/proposals/{proposal_id}/accept", ({ response }) =>
        response(409).json({ detail }),
      ),
    );
    renderInbox();
    const card = await pendingCard();

    await user.click(within(card).getByRole("button", { name: "Accept" }));

    // The distinct sentence, not a single house phrase that would be wrong for
    // two of the three kinds.
    expect(await within(card).findByText(detail)).toBeInTheDocument();
    // Still pending and still on offer: a 409 here is not a failed write, it is
    // the world having moved, and the proposal it was written against stands.
    expect(card.dataset.status).toBe("pending");
    expect(within(card).getByRole("button", { name: "Accept" })).toBeEnabled();
  });

  it("offers no Accept on a proposal whose expiry has already passed", async () => {
    // Expiry is enforced at accept time and the sweep runs on a schedule, so a
    // proposal can read `pending` with its expiry behind it. Offering Accept on
    // it only earns a 409 — so the buttons come off (FIX-F4).
    server.use(
      http.get("/api/v1/proposals", ({ response }) =>
        response(200).json({
          items: [
            {
              ...pendingFixture(),
              expires_at: "2026-08-01T06:30:00Z",
            },
          ],
          total: 1,
          offset: 0,
          limit: 25,
        }),
      ),
    );
    renderInbox();
    const card = await pendingCard();

    expect(card.dataset.status).toBe("pending");
    expect(within(card).getByText(/^expired ·/)).toBeInTheDocument();
    expect(
      within(card).queryByRole("button", { name: "Accept" }),
    ).not.toBeInTheDocument();
    expect(
      within(card).queryByRole("button", { name: "Reject" }),
    ).not.toBeInTheDocument();
  });

  it("offers no buttons on a proposal that is already resolved", async () => {
    const user = userEvent.setup();
    renderInbox();
    await pendingCard();

    await user.selectOptions(screen.getByLabelText("Show"), "lapsed");

    const card = await waitFor(() => screen.getByTestId("proposal"));
    expect(card.dataset.status).toBe("lapsed");
    expect(
      within(card).queryByRole("button", { name: "Accept" }),
    ).not.toBeInTheDocument();
    // The expiry is on every proposal, not only the pending ones: on a lapsed
    // one it is the reason it lapsed.
    expect(within(card).getByText(/^expired ·/)).toBeInTheDocument();
  });

  it("says the load could not be read rather than showing an empty queue", async () => {
    server.use(
      http.get("/api/v1/proposals", ({ response }) =>
        response(401).json({ detail: "Not authenticated." }),
      ),
    );
    renderInbox();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /session has expired/i,
    );
  });
});
