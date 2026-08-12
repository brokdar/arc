import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { Inbox } from "@/components/ingest/inbox";
import {
  ACTIVITY_IDS,
  DETAILS,
  longQuarantineFixture,
  QUARANTINE_IDS,
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
      within(cardFor("corrupt-export.fit")).getByText(DETAILS.unreadableFit),
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

    // The API answers 409 for every reason but the two it lets you overrule,
    // so the unreadable file is offered the one answer that exists.
    expect(
      within(cardFor("wahoo-2026-08-05.fit")).getByRole("button", {
        name: "Not a duplicate",
      }),
    ).toBeInTheDocument();
    const corrupt = within(cardFor("corrupt-export.fit"));
    expect(
      corrupt.queryByRole("button", { name: "Not a duplicate" }),
    ).not.toBeInTheDocument();
    expect(
      corrupt.queryByRole("button", { name: "Ingest it anyway" }),
    ).not.toBeInTheDocument();
    expect(
      corrupt.getByRole("button", { name: "Discard this copy" }),
    ).toBeInTheDocument();
  });

  it("offers a broken channel the ingest that blanks it, and discard too", async () => {
    // D107 generalised D98: the cleaner nulls what it cannot believe, so a
    // good ride with a bad strap is recoverable through the product instead of
    // being confirm-and-discard only.
    server.use(
      http.get("/api/v1/ingest/quarantine", ({ response }) =>
        response(200).json({
          items: [
            {
              id: QUARANTINE_IDS.duplicate,
              original_filename: "strap-2026-08-06.fit",
              file_hash: "ab".repeat(32),
              file_sport_index: 0,
              reason: "implausible_channel",
              detail:
                "41% of the hr channel is outside 20-230 bpm; a mis-paired sensor, not a spike",
              status: "pending",
              suspected_session_id: null,
              created_at: "2026-08-06T06:12:31Z",
              resolved_at: null,
            },
          ],
          total: 1,
          offset: 0,
          limit: 50,
        }),
      ),
    );
    const user = userEvent.setup();
    renderInbox();

    const card = within(await screen.findByTestId("quarantine-record"));
    // The copy says what the button does, not which enum member it is: "Not a
    // duplicate" would describe the other overrulable verdict, not this one.
    expect(
      card.getByRole("button", { name: "Ingest it anyway" }),
    ).toBeInTheDocument();
    expect(
      card.queryByRole("button", { name: "Not a duplicate" }),
    ).not.toBeInTheDocument();
    // The confirm side still offers discard: overruling is the *second* answer.
    expect(
      card.getByRole("button", { name: "Discard this copy" }),
    ).toBeInTheDocument();
    expect(card.getByText(/ingest it anyway/)).toBeInTheDocument();

    await user.click(card.getByRole("button", { name: "Ingest it anyway" }));

    expect(
      screen.getByRole("alertdialog", {
        name: "Ingest it anyway — the broken channel arrives blanked?",
      }),
    ).toBeInTheDocument();
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

describe("a queue longer than one request", () => {
  /** 55 pending laps and 3 already discarded, in the API's own order. */
  function longQueue(): void {
    const records = longQuarantineFixture(55, 3);
    server.use(
      http.get("/api/v1/ingest/quarantine", ({ query, response }) => {
        const offset = Number(query.get("offset") ?? 0);
        const limit = Number(query.get("limit") ?? 50);
        return response(200).json({
          items: records.slice(offset, offset + limit),
          total: records.length,
          offset,
          limit,
        });
      }),
    );
  }

  it("reaches the records past the first page", async () => {
    // Before the pager, the 51st record onwards could not be answered at all:
    // the queue asked for 50 and had no way to ask for the next 50.
    longQueue();
    const user = userEvent.setup();
    renderInbox();

    expect(await screen.findByText("1–50 of 58")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Newer quarantine records" }),
    ).toBeDisabled();
    expect(screen.getAllByTestId("quarantine-record")).toHaveLength(50);

    await user.click(
      screen.getByRole("button", { name: "Older quarantine records" }),
    );

    expect(await screen.findByText("51–58 of 58")).toBeInTheDocument();
    expect(screen.getAllByTestId("quarantine-record")).toHaveLength(8);
    expect(
      screen.getByRole("button", { name: "Older quarantine records" }),
    ).toBeDisabled();
    // The tail of the queue is where the resolved records are, so the second
    // band appears only here.
    expect(screen.getByText("Already decided")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Newer quarantine records" }),
    );
    expect(await screen.findByText("1–50 of 58")).toBeInTheDocument();
  });

  it("will not claim a waiting count it cannot see the end of", async () => {
    longQueue();
    const user = userEvent.setup();
    renderInbox();

    // 50 pending on a 50-record page, 58 records behind it: the ones past the
    // cut may be pending too. `total` is every record, not every pending one,
    // so neither number is an answer — and the label says so.
    expect(await screen.findByText("at least 50 waiting")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Older quarantine records" }),
    );

    // Past the first page the question is not answerable at all: the pending
    // records are behind us, so the label reports the page it is on.
    expect(
      await screen.findByText("5 waiting on this page"),
    ).toBeInTheDocument();
  });

  it("counts exactly when the whole queue fits on the page", async () => {
    // The seed: two pending and one discarded, all three in one request.
    renderInbox();

    expect(await screen.findByText("2 waiting")).toBeInTheDocument();
    expect(screen.getByText("1–3 of 3")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Older quarantine records" }),
    ).toBeDisabled();
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

  it("cannot be answered twice while the first answer is in flight", async () => {
    // The second click is the same decision twice, and it is the one that
    // loses: the record is already resolved by then, so its 409 lands *after*
    // the success and paints a refusal over a reject that went through.
    let calls = 0;
    server.use(
      http.post(
        "/api/v1/ingest/quarantine/{record_id}/reject",
        async ({ params, response }) => {
          calls += 1;
          await new Promise((resolve) => setTimeout(resolve, 30));
          return response(409).json({
            detail: `Quarantine record ${params.record_id} is already resolved`,
          });
        },
      ),
    );
    const user = userEvent.setup();
    renderInbox();
    await screen.findByText("Suspected duplicate");

    await user.click(
      within(cardFor("wahoo-2026-08-05.fit")).getByRole("button", {
        name: "Not a duplicate",
      }),
    );
    const confirm = screen.getByRole("button", { name: "Ingest it" });
    await user.click(confirm);

    expect(confirm).toBeDisabled();
    await waitFor(() => {
      expect(
        within(cardFor("wahoo-2026-08-05.fit")).queryByRole("alert"),
      ).toBeInTheDocument();
    });
    expect(calls).toBe(1);
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

    // 200 with `outcome: "quarantined"` — a client reading the status
    // instead would call this a success and show nothing.
    await upload(user, activityFile("ride.csv", "date,power\n"));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "ride.csv was quarantined: it is waiting on you below.",
    );
    // And it joins the queue, which the page refetched — carrying the
    // parser's own sentence, not a shorter one invented for the fixture.
    await waitFor(() => {
      expect(
        within(cardFor("ride.csv")).getByText(DETAILS.noParser("ride.csv")),
      ).toBeInTheDocument();
    });
  });

  it("names each session a multisport file became", async () => {
    const first = "0199a000-0000-7000-8000-000000000a01";
    const second = "0199a000-0000-7000-8000-000000000a02";
    server.use(
      http.post("/api/v1/ingest/upload", ({ response }) =>
        response(200).json({
          filename: "brick.fit",
          file_hash: "b7".repeat(32),
          outcome: "ingested",
          detail: "2 session(s) ingested, 0 quarantined",
          session_ids: [first, second],
          quarantine_ids: [],
        }),
      ),
    );
    const user = userEvent.setup();
    renderInbox();
    await screen.findByText("Suspected duplicate");

    await upload(user, activityFile("brick.fit", "FIT ride then run"));

    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("brick.fit was ingested as 2 sessions.");
    // Two links, told apart: "Open the session" twice is two controls a
    // screen reader announces identically.
    expect(
      within(notice).getByRole("link", { name: "Open session 1" }),
    ).toHaveAttribute("href", `/sessions/${first}`);
    expect(
      within(notice).getByRole("link", { name: "Open session 2" }),
    ).toHaveAttribute("href", `/sessions/${second}`);
    expect(
      within(notice).queryByRole("link", { name: "Open the session" }),
    ).not.toBeInTheDocument();
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
    expect(
      screen.getByRole("button", { name: "Newer ingest log rows" }),
    ).toBeDisabled();

    await user.click(
      screen.getByRole("button", { name: "Older ingest log rows" }),
    );

    expect(await screen.findByText("21–26 of 26")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Older ingest log rows" }),
    ).toBeDisabled();
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

  it("names logging in as the remedy when the session has expired", async () => {
    // A 401 is an *answer*: the API was reachable and said no. "Is the API
    // reachable?" sends the athlete to check a network that is fine.
    server.use(
      http.get("/api/v1/ingest/quarantine", ({ response }) =>
        response(401).json({ detail: "Not authenticated" }),
      ),
    );
    renderInbox();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your session has expired. Log in again to see the queue.",
    );
  });

  it("asks about the network when the network is the open question", async () => {
    server.use(
      // Untyped: a transport failure is not a response the schema describes.
      http.untyped.get("http://localhost:8000/api/v1/ingest/quarantine", () =>
        HttpResponse.error(),
      ),
    );
    renderInbox();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the queue. Is the API reachable?",
    );
  });
});
