import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { LocalDropCard } from "@/components/settings/integrations/local-drop-card";
import { $api } from "@/lib/api/client";
import { INBOX_PATH, integrationsState } from "@/tests/mocks/fixtures";

/**
 * The card as the panel wires it: fed from `GET /api/v1/integrations`.
 *
 * Not a hand-built prop. AC-20's second edge is that saving re-renders the new
 * interval *without a reload*, which is a claim about the query the panel
 * holds being invalidated and re-read — a fixed prop would re-render whatever
 * the test typed and prove nothing.
 */
function LocalDrop() {
  const integrations = $api.useQuery("get", "/api/v1/integrations");
  const local = integrations.data?.items.find(
    (item) => item.kind === "local_drop",
  );
  return local ? (
    <ul>
      <LocalDropCard integration={local} />
    </ul>
  ) : null;
}

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LocalDrop />
    </QueryClientProvider>,
  );
}

/** The card, by the name the athlete knows the source as. */
function card(): Promise<HTMLElement> {
  return screen.findByRole("region", { name: "Local drop" });
}

describe("the folder, and why it is not editable here", () => {
  it("shows the resolved path with the reason DATA__ROOT is fixed", async () => {
    renderCard();

    const local = await card();
    expect(local).toHaveTextContent(INBOX_PATH);
    // The reason, in the athlete's terms: the same root holds the files arc
    // has already filed, and in Docker it is a mounted volume — so moving it
    // from a settings form would strand them.
    expect(local).toHaveTextContent("DATA__ROOT");
    expect(local).toHaveTextContent("originals/");
    expect(local).toHaveTextContent("streams/");
    expect(local).toHaveTextContent("quarantine/");
    expect(local).toHaveTextContent(/mounted volume/i);
  });

  it("renders no control that would edit the path", async () => {
    renderCard();
    const local = await card();

    // The path is text, never a value in a field: nothing in the card holds
    // it as an editable value, and the only field is the interval's.
    expect(within(local).queryByDisplayValue(INBOX_PATH)).toBeNull();
    expect(within(local).queryAllByRole("textbox")).toHaveLength(0);
    expect(within(local).getAllByRole("spinbutton")).toHaveLength(1);
    for (const control of within(local).getAllByRole("button")) {
      expect(control.textContent ?? "").not.toMatch(/folder|path|move/i);
    }
  });
});

describe("the interval, which is the athlete's", () => {
  it("offers the interval as a control and states what is in force", async () => {
    renderCard();
    const local = await card();

    expect(within(local).getByLabelText(/sweep/i)).toHaveValue(30);
    // Which of the two sources is in force, because they are undone
    // differently: this one is `INGEST__SCAN_INTERVAL_SECONDS` in a file.
    await waitFor(() => {
      expect(local).toHaveTextContent(/INGEST__SCAN_INTERVAL_SECONDS/);
    });
  });

  it("re-renders the new interval after saving, with no reload", async () => {
    const user = userEvent.setup();
    renderCard();
    const local = await card();
    expect(within(local).getByTestId("integration-setup")).toHaveTextContent(
      "30",
    );
    // The environment is in force before the save, so the assertion after it
    // is about the source changing rather than about a line not having loaded.
    await waitFor(() => {
      expect(local).toHaveTextContent(/INGEST__SCAN_INTERVAL_SECONDS/);
    });

    const field = within(local).getByLabelText(/sweep/i);
    await user.clear(field);
    await user.type(field, "120");
    await user.click(within(local).getByRole("button", { name: /save/i }));

    await waitFor(() => {
      expect(within(local).getByTestId("integration-setup")).toHaveTextContent(
        "120",
      );
    });
    // The server holds it, so the sentence is not local optimism.
    expect(integrationsState().scanIntervalSeconds).toBe(120);
    // And it now says the athlete set it, not the environment.
    await waitFor(() => {
      expect(local).not.toHaveTextContent(/INGEST__SCAN_INTERVAL_SECONDS/);
    });
  });

  it("renders the server's refusal and keeps the typed value", async () => {
    const user = userEvent.setup();
    renderCard();
    const local = await card();

    const field = within(local).getByLabelText(/sweep/i);
    await user.clear(field);
    await user.type(field, "1");
    await user.click(within(local).getByRole("button", { name: /save/i }));

    const refusal = await within(local).findByRole("alert");
    // The server's sentence, not a message the component made up.
    expect(refusal).toHaveTextContent(/5/);
    expect(refusal).toHaveTextContent(/86400/);
    // The typed value survives the refusal: retyping a number you already
    // typed is the one thing a rejected form must never ask for.
    expect(field).toHaveValue(1);
    expect(within(local).getByTestId("integration-setup")).toHaveTextContent(
      "30",
    );
    expect(integrationsState().scanIntervalSeconds).toBe(30);
  });
});
