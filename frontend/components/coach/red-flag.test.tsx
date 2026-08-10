import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RedFlagBanner, RedFlagControl } from "@/components/coach/red-flag";
import { athleteRecord, patchAthlete } from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

/**
 * The banner and the control together, the way the shell and Today mount them.
 *
 * Both are rendered because they read the same profile and the point of the
 * banner is that it appears *elsewhere* the moment the control is used — a
 * test that rendered only the dialog could watch a PATCH succeed and never
 * notice that nothing in the application said so afterwards.
 */
function renderRedFlag() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RedFlagBanner />
      <RedFlagControl />
    </QueryClientProvider>,
  );
}

/** Open the dialog from the Today-page control. */
async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  const control = await screen.findByRole("button", {
    name: /Report illness or injury|Red flag up/,
  });
  await user.click(control);
  return await screen.findByRole("dialog");
}

describe("the control", () => {
  it("shows the flag is down before anything is wrong", async () => {
    renderRedFlag();

    expect(
      await screen.findByRole("button", { name: "Report illness or injury" }),
    ).toBeInTheDocument();
    // Nothing else: a banner while the flag is down would cry wolf on every
    // page of the app.
    expect(screen.queryByTestId("red-flag-banner")).not.toBeInTheDocument();
  });

  it("refuses to raise a flag with no severity", async () => {
    const user = userEvent.setup();
    const patched = vi.fn();
    server.use(
      http.patch("/api/v1/athlete", async ({ request, response }) => {
        patched(await request.json());
        return response(200).json(athleteRecord());
      }),
    );
    renderRedFlag();
    const dialog = await openDialog(user);

    await user.click(
      within(dialog).getByRole("checkbox", { name: /Something is wrong/ }),
    );
    await user.click(within(dialog).getByRole("button", { name: /Raise/ }));

    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      /Say how bad it is/,
    );
    // Refused in front of the athlete, not bounced off a 422: the request is
    // never made at all.
    expect(patched).not.toHaveBeenCalled();
  });

  it("raises the flag and banners it everywhere", async () => {
    const user = userEvent.setup();
    renderRedFlag();
    const dialog = await openDialog(user);

    await user.click(
      within(dialog).getByRole("checkbox", { name: /Something is wrong/ }),
    );
    await user.selectOptions(
      within(dialog).getByLabelText(/Severity/),
      "moderate",
    );
    await user.type(
      within(dialog).getByLabelText(/What is wrong/),
      "Chest cold since Tuesday.",
    );
    await user.click(within(dialog).getByRole("button", { name: /Raise/ }));

    const banner = await screen.findByTestId("red-flag-banner");
    expect(banner).toHaveTextContent("Red flag · Moderate");
    expect(banner).toHaveTextContent("Chest cold since Tuesday.");
    // The whole reason the banner is loud: while it stands, the coach is being
    // refused, and a refusal nobody can see looks like a coach with nothing
    // to say.
    expect(banner).toHaveTextContent(/cannot add or intensify sessions/);
    expect(athleteRecord()).toMatchObject({
      red_flag_active: true,
      red_flag_severity: "moderate",
      red_flag_note: "Chest cold since Tuesday.",
    });
  });

  it("names the state on the control once the flag is up", async () => {
    patchAthlete({
      red_flag_active: true,
      red_flag_severity: "mild",
      red_flag_note: "Sore achilles.",
    });
    renderRedFlag();

    expect(
      await screen.findByRole("button", { name: "Red flag up" }),
    ).toBeInTheDocument();
  });
});

describe("lowering the flag", () => {
  it("clears the note and the severity with it", async () => {
    const user = userEvent.setup();
    patchAthlete({
      red_flag_active: true,
      red_flag_severity: "severe",
      red_flag_note: "Broken collarbone.",
    });
    renderRedFlag();
    const banner = await screen.findByTestId("red-flag-banner");

    await user.click(
      within(banner).getByRole("button", { name: "All better" }),
    );

    await waitFor(() => {
      expect(screen.queryByTestId("red-flag-banner")).not.toBeInTheDocument();
    });
    // The story of an illness that is over does not survive it, or the next
    // flag inherits the last one's note.
    expect(athleteRecord()).toMatchObject({
      red_flag_active: false,
      red_flag_severity: null,
      red_flag_note: null,
    });
  });

  it("empties the form's severity and note when the tick comes off", async () => {
    const user = userEvent.setup();
    patchAthlete({
      red_flag_active: true,
      red_flag_severity: "mild",
      red_flag_note: "Sore achilles.",
    });
    renderRedFlag();
    const banner = await screen.findByTestId("red-flag-banner");
    await user.click(within(banner).getByRole("button", { name: "Change" }));
    const dialog = await screen.findByRole("dialog");

    // Loaded with what stands …
    expect(within(dialog).getByLabelText(/Severity/)).toHaveValue("mild");
    await user.click(
      within(dialog).getByRole("checkbox", { name: /Something is wrong/ }),
    );

    // … and emptied on the way down, mirroring what the API does, so the form
    // never shows a severity for a flag that is not up.
    expect(within(dialog).getByLabelText(/Severity/)).toHaveValue("");
    expect(within(dialog).getByLabelText(/What is wrong/)).toHaveValue("");
    expect(within(dialog).getByLabelText(/Severity/)).toBeDisabled();
  });

  it("surfaces a refusal from the server rather than swallowing it", async () => {
    const user = userEvent.setup();
    server.use(
      http.patch("/api/v1/athlete", ({ response }) =>
        response(422).json({ detail: "Severity is required while active." }),
      ),
    );
    renderRedFlag();
    const dialog = await openDialog(user);

    await user.click(
      within(dialog).getByRole("checkbox", { name: /Something is wrong/ }),
    );
    await user.selectOptions(within(dialog).getByLabelText(/Severity/), "mild");
    await user.click(within(dialog).getByRole("button", { name: /Raise/ }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Severity is required while active.",
    );
  });
});
