import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { Inbox } from "@/components/ingest/inbox";
import { ACTIVITY_IDS, QUARANTINE_IDS } from "@/tests/mocks/fixtures";
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

function renderInbox() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Inbox />
    </QueryClientProvider>,
  );
}

/** A file with the given name and bytes, as `<input type=file>` would hand it. */
function activityFile(name: string, bytes: string): File {
  return new File([bytes], name, { type: "application/octet-stream" });
}

/** Put a file into the upload control and submit it. */
async function upload(user: ReturnType<typeof userEvent.setup>, file: File) {
  await user.upload(screen.getByLabelText("Activity file"), file);
  await user.click(screen.getByRole("button", { name: "Upload" }));
}

/**
 * The card for one quarantine record, found by the filename it names.
 *
 * Scoped to the cards rather than the page, because the same filename is in
 * the ingest log below — which is the point of the log.
 */
function cardFor(filename: string): HTMLElement {
  const card = screen
    .getAllByTestId("quarantine-record")
    .find((element) => within(element).queryAllByText(filename).length > 0);
  if (!card) {
    throw new Error(`no quarantine card for ${filename}`);
  }
  return card;
}

describe("the inbox queue", () => {
  it("says why each file stopped, in words rather than an enum", async () => {
    renderInbox();

    expect(await screen.findByText("Suspected duplicate")).toBeInTheDocument();
    expect(screen.getByText("Could not be read")).toBeInTheDocument();
    // The API's own detail, verbatim — the row explains this file, not the
    // category it fell into.
    expect(
      within(cardFor("wahoo-2026-08-05.fit")).getByText(
        /87% of this activity's time range overlaps/,
      ),
    ).toBeInTheDocument();
    expect(
      within(cardFor("corrupt-export.fit")).getByText(
        "not a FIT file: bad header magic",
      ),
    ).toBeInTheDocument();
  });

  it("links a suspected duplicate to the session it looks like", async () => {
    renderInbox();
    await screen.findByText("Suspected duplicate");

    expect(
      within(cardFor("wahoo-2026-08-05.fit")).getByRole("link", {
        name: "The session it looks like",
      }),
    ).toHaveAttribute("href", `/sessions/${ACTIVITY_IDS.outdoorRide}`);
  });

  it("offers 'not a duplicate' only where there is something safe to ingest", async () => {
    renderInbox();
    await screen.findByText("Suspected duplicate");

    // The API answers 409 for every other reason (D98), so the unreadable
    // file is offered the one answer that exists: discard it.
    expect(
      within(cardFor("wahoo-2026-08-05.fit")).getByRole("button", {
        name: "Not a duplicate",
      }),
    ).toBeInTheDocument();
    expect(
      within(cardFor("corrupt-export.fit")).queryByRole("button", {
        name: "Not a duplicate",
      }),
    ).not.toBeInTheDocument();
  });

  it("keeps records already decided below the ones that are not", async () => {
    renderInbox();
    await screen.findByText("Suspected duplicate");

    expect(screen.getByText("Already decided")).toBeInTheDocument();
    const resolved = cardFor("2026-07-30-lap.fit");
    expect(within(resolved).getByText("Discarded")).toBeInTheDocument();
    // A decided record offers no buttons: there is nothing left to decide.
    expect(
      within(resolved).queryByRole("button", { name: "Discard this copy" }),
    ).not.toBeInTheDocument();
  });
});

describe("confirming a quarantine", () => {
  it("discards the copy and moves the record out of the queue", async () => {
    const user = userEvent.setup();
    renderInbox();
    await screen.findByText("Suspected duplicate");

    const card = cardFor("wahoo-2026-08-05.fit");
    expect(within(card).getByText("Waiting on you")).toBeInTheDocument();

    // Two clicks, because discarding is irreversible.
    await user.click(
      within(card).getByRole("button", { name: "Discard this copy" }),
    );
    await user.click(within(card).getByRole("button", { name: "Discard" }));

    await waitFor(() => {
      expect(
        within(cardFor("wahoo-2026-08-05.fit")).getByText("Discarded"),
      ).toBeInTheDocument();
    });
    // And the server really flipped it: the queue count follows.
    expect(await screen.findByText("1 waiting")).toBeInTheDocument();
  });

  it("prints the server's refusal when the record was already resolved", async () => {
    // The reachable 409: the page was opened before something else — a second
    // tab, an MCP tool — answered this record.
    server.use(
      http.post(
        "/api/v1/ingest/quarantine/{record_id}/confirm",
        ({ response }) =>
          response(409).json({
            detail: `Quarantine record ${QUARANTINE_IDS.duplicate} is already resolved`,
          }),
      ),
    );
    const user = userEvent.setup();
    renderInbox();
    await screen.findByText("Suspected duplicate");

    const card = cardFor("wahoo-2026-08-05.fit");
    await user.click(
      within(card).getByRole("button", { name: "Discard this copy" }),
    );
    await user.click(within(card).getByRole("button", { name: "Discard" }));

    const alert = await within(cardFor("wahoo-2026-08-05.fit")).findByRole(
      "alert",
    );
    expect(alert).toHaveTextContent(/already resolved/);
    // The refusal lands on the record it was about, and nowhere else.
    expect(
      within(cardFor("corrupt-export.fit")).queryByRole("alert"),
    ).not.toBeInTheDocument();
  });
});

describe("rejecting a quarantine", () => {
  it("ingests the file as its own session and logs it", async () => {
    const user = userEvent.setup();
    renderInbox();
    await screen.findByText("Suspected duplicate");

    const card = cardFor("wahoo-2026-08-05.fit");
    await user.click(
      within(card).getByRole("button", { name: "Not a duplicate" }),
    );
    // The safe answer is the one that takes focus; the ingest is the second.
    expect(
      screen.getByRole("alertdialog", {
        name: "Ingest this file as its own session?",
      }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ingest it" }));

    await waitFor(() => {
      expect(
        within(cardFor("wahoo-2026-08-05.fit")).getByText("Ingested anyway"),
      ).toBeInTheDocument();
    });
    // The log is the other half of what happened.
    const log = screen.getByRole("table");
    expect(
      within(log).getAllByRole("link", { name: "wahoo-2026-08-05.fit" }).length,
    ).toBeGreaterThan(0);
  });

  it("prints the server's refusal rather than swallowing it", async () => {
    server.use(
      http.post(
        "/api/v1/ingest/quarantine/{record_id}/reject",
        ({ response }) =>
          response(409).json({
            detail:
              "Only a suspected duplicate can be rejected; this file could not be read",
          }),
      ),
    );
    const user = userEvent.setup();
    renderInbox();
    await screen.findByText("Suspected duplicate");

    const card = cardFor("wahoo-2026-08-05.fit");
    await user.click(
      within(card).getByRole("button", { name: "Not a duplicate" }),
    );
    await user.click(screen.getByRole("button", { name: "Ingest it" }));

    expect(
      await within(cardFor("wahoo-2026-08-05.fit")).findByRole("alert"),
    ).toHaveTextContent(/Only a suspected duplicate can be rejected/);
    // Still pending: a refused reject decided nothing.
    expect(
      within(cardFor("wahoo-2026-08-05.fit")).getByText("Waiting on you"),
    ).toBeInTheDocument();
  });

  it("lets the athlete think better of it", async () => {
    const user = userEvent.setup();
    renderInbox();
    await screen.findByText("Suspected duplicate");

    await user.click(
      within(cardFor("wahoo-2026-08-05.fit")).getByRole("button", {
        name: "Not a duplicate",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Keep waiting" }));

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(
      within(cardFor("wahoo-2026-08-05.fit")).getByText("Waiting on you"),
    ).toBeInTheDocument();
  });
});

describe("uploading a file", () => {
  it("branches on the outcome, not on the status code", async () => {
    const user = userEvent.setup();
    renderInbox();
    await screen.findByText("Suspected duplicate");

    await upload(user, activityFile("evening-ride.fit", "FIT ride bytes"));

    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("evening-ride.fit was ingested.");
    expect(
      within(notice).getByRole("link", { name: "Open the session" }),
    ).toHaveAttribute("href", expect.stringMatching(/^\/sessions\//));
  });

  it("says so when the same bytes arrive twice", async () => {
    const user = userEvent.setup();
    renderInbox();
    await screen.findByText("Suspected duplicate");

    const bytes = "FIT the very same ride";
    await upload(user, activityFile("ride.fit", bytes));
    await screen.findByRole("status");
    // A different name, the same content: the pipeline dedups on the digest.
    await upload(user, activityFile("ride-copy.fit", bytes));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        "ride-copy.fit was already ingested — nothing changed.",
      );
    });
  });

  it("reports a quarantined upload as the result it is", async () => {
    // The control's `accept` list is a hint to the file picker, not a guard:
    // every browser offers "All files" beside it, so a file no parser reads
    // is a path the athlete can genuinely reach — and the server's answer to
    // it is what this asserts.
    const user = userEvent.setup({ applyAccept: false });
    renderInbox();
    await screen.findByText("Suspected duplicate");

    // 200 with `outcome: "quarantined"` (D97) — a client reading the status
    // instead would call this a success and show nothing.
    await upload(user, activityFile("ride.csv", "date,power\n"));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "ride.csv was quarantined: it is waiting on you below.",
    );
    // And it joins the queue, which the page refetched.
    await waitFor(() => {
      expect(
        within(cardFor("ride.csv")).getByText("no parser for '.csv'"),
      ).toBeInTheDocument();
    });
  });

  it("prints why an unusable upload was refused", async () => {
    const user = userEvent.setup();
    renderInbox();
    await screen.findByText("Suspected duplicate");

    await upload(user, activityFile("empty.fit", ""));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The uploaded file is empty",
    );
  });
});

describe("the ingest log", () => {
  it("pages through the files the pipeline has seen", async () => {
    const user = userEvent.setup();
    renderInbox();

    expect(await screen.findByText("1–20 of 26")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Newer" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Older" }));

    expect(await screen.findByText("21–26 of 26")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Older" })).toBeDisabled();
  });

  it("links a logged file to the session it became", async () => {
    renderInbox();
    await screen.findByText("1–20 of 26");

    const log = screen.getByRole("table");
    expect(
      within(log).getAllByRole("link", {
        name: "2026-08-05-morning-ride.fit",
      })[0],
    ).toHaveAttribute("href", `/sessions/${ACTIVITY_IDS.outdoorRide}`);
    // "Already had it" is a success, and reads like one.
    expect(within(log).getAllByText("Already had it").length).toBeGreaterThan(
      0,
    );
  });
});

describe("an inbox with nothing in it", () => {
  it("names the missing input and the action that supplies it", async () => {
    server.use(
      http.get("/api/v1/ingest/quarantine", ({ response }) =>
        response(200).json({ items: [], total: 0, offset: 0, limit: 50 }),
      ),
      http.get("/api/v1/ingest/events", ({ response }) =>
        response(200).json({ items: [], total: 0, offset: 0, limit: 20 }),
      ),
    );
    renderInbox();

    expect(await screen.findByText("nothing waiting")).toBeInTheDocument();
    expect(
      screen.getByText(/Drop FIT, TCX or GPX files into the inbox folder/),
    ).toBeInTheDocument();
    // The remedy is a control, not a sentence about one.
    expect(screen.getByLabelText("Activity file")).toBeInTheDocument();
    expect(
      screen.getByText(/The pipeline has not seen a file yet/),
    ).toBeInTheDocument();
  });

  it("says so when the queue cannot be reached at all", async () => {
    server.use(
      http.get("/api/v1/ingest/quarantine", ({ response }) =>
        response(401).json({ detail: "No valid session" }),
      ),
    );
    renderInbox();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the queue",
    );
  });
});
