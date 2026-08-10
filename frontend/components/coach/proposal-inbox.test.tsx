import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ProposalInbox } from "@/components/coach/proposal-inbox";
import { PROPOSAL_IDS, proposalById } from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

function renderInbox() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ProposalInbox />
    </QueryClientProvider>,
  );
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

  it("draws a 409 as a state, and leaves the proposal waiting", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/v1/proposals/{proposal_id}/accept", ({ response }) =>
        response(409).json({
          detail:
            "Thursday's session has been revised since this proposal was written (intent v3 → v4).",
        }),
      ),
    );
    renderInbox();
    const card = await pendingCard();

    await user.click(within(card).getByRole("button", { name: "Accept" }));

    expect(
      await within(card).findByText("The plan moved underneath this proposal."),
    ).toBeInTheDocument();
    // The server's own sentence, which is the half that names what moved.
    expect(within(card).getByText(/intent v3 → v4/)).toBeInTheDocument();
    // Still pending, still answerable: a 409 here is not a failed write, it is
    // the world having moved.
    expect(card.dataset.status).toBe("pending");
    expect(within(card).getByRole("button", { name: "Accept" })).toBeEnabled();
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
