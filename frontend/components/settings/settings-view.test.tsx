import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { SettingsView } from "@/components/settings/settings-view";
import { AthleteClock } from "@/lib/clock";
import { addDays } from "@/lib/dates";
import {
  ATHLETE_TIMEZONE,
  anchorHistory,
  appendAnchorVersion,
  athleteRecord,
  athleteToday,
} from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

function renderSettings() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <AthleteClock timezone={ATHLETE_TIMEZONE}>
          <SettingsView />
        </AthleteClock>
      </QueryClientProvider>,
    ),
  };
}

/** The card for one anchor in the "in force now" grid. */
function slot(anchorType: string): HTMLElement {
  return screen.getByTestId(`current-${anchorType}`);
}

/**
 * Fill in the append form. Only what is named is touched, so a test can say
 * what it is about — a missing protocol, a back-dated correction — without
 * restating the whole form each time.
 */
async function fillAppend(
  user: ReturnType<typeof userEvent.setup>,
  fields: {
    anchor?: string;
    value?: string;
    provenance?: string;
    protocol?: string;
    effectiveDate?: string;
    ciLow?: string;
    ciHigh?: string;
  },
) {
  if (fields.anchor) {
    await user.selectOptions(screen.getByLabelText("Anchor"), fields.anchor);
  }
  if (fields.provenance) {
    await user.selectOptions(
      screen.getByLabelText(/Provenance/),
      fields.provenance,
    );
  }
  await retype(user, /Value/, fields.value);
  await retype(user, /Protocol/, fields.protocol);
  await retype(user, /Effective from/, fields.effectiveDate);
  await retype(user, /CI low/, fields.ciLow);
  await retype(user, /CI high/, fields.ciHigh);
}

/** Replace a field's contents; an empty string empties it. */
async function retype(
  user: ReturnType<typeof userEvent.setup>,
  label: RegExp,
  text: string | undefined,
) {
  if (text === undefined) {
    return;
  }
  await user.clear(screen.getByLabelText(label));
  if (text !== "") {
    await user.type(screen.getByLabelText(label), text);
  }
}

async function submitAppend(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Append version" }));
}

describe("the anchors in force", () => {
  it("shows each value with the provenance and the date it applies from", async () => {
    renderSettings();

    const ftp = screen.getByTestId("current-ftp");
    expect(await within(ftp).findByText("265")).toBeInTheDocument();
    expect(within(ftp).getByText("W")).toBeInTheDocument();
    // The provenance is the whole point of an anchor: 265 W measured is a
    // different claim from 265 W guessed, and everything resolved from it
    // inherits whichever it was.
    expect(within(ftp).getByText("tested")).toHaveAttribute(
      "data-provenance",
      "tested",
    );
    expect(within(ftp).getByText(/effective 15\.07\.2026/)).toBeInTheDocument();
    expect(within(ftp).getByText(/CI 258–272 W/)).toBeInTheDocument();
    expect(within(ftp).getByText("20 min × 0.95")).toBeInTheDocument();

    // An estimate is marked as one, not merely spelled differently.
    expect(within(slot("max_hr")).getByText("estimated")).toHaveAttribute(
      "data-untested",
      "true",
    );
    expect(within(slot("lthr")).getByText("162")).toBeInTheDocument();
  });

  it("keeps the slot of an anchor nobody has entered, and names the remedy", async () => {
    renderSettings();

    const resting = screen.getByTestId("current-resting_hr");
    // The grid holds its positions (UI convention 4): the slot is there with
    // a placeholder that says why, not a hole where a card would be.
    expect(
      await within(resting).findByRole("img", {
        name: /Not assessed: No resting HR version is in force/,
      }),
    ).toBeInTheDocument();
    expect(within(resting).getByText(/No resting HR yet/)).toBeInTheDocument();
    expect(
      within(resting).getByRole("button", { name: "Add resting HR" }),
    ).toBeInTheDocument();
  });

  it("points the append form at the anchor whose slot asked for one", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.click(
      await within(await screen.findByTestId("current-resting_hr")).findByRole(
        "button",
        { name: "Add resting HR" },
      ),
    );

    // The remedy is the form, already set to the anchor that is missing and
    // with the cursor in it — not a scroll to a form still to be configured.
    expect(screen.getByLabelText("Anchor")).toHaveValue("resting_hr");
    expect(screen.getByLabelText(/Value/)).toHaveFocus();
  });
});

describe("appending a version", () => {
  it("appends, and the new version is the one in force", async () => {
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId("current-ftp");

    await fillAppend(user, {
      value: "272",
      provenance: "tested",
      protocol: "ramp test",
      ciLow: "266",
      ciHigh: "279",
    });
    await submitAppend(user);

    expect(await within(slot("ftp")).findByText("272")).toBeInTheDocument();
    expect(within(slot("ftp")).getByText(/CI 266–279 W/)).toBeInTheDocument();
    // And it is in the history, alongside — not instead of — the version it
    // corrected.
    const history = await screen.findAllByTestId("anchor-version");
    expect(history[0]).toHaveTextContent("272 W");
    expect(anchorHistory("ftp").map((version) => version.value)).toEqual([
      272, 265, 250,
    ]);
  });

  it("refuses a tested value with no protocol before it is sent", async () => {
    const user = userEvent.setup();
    const posted = vi.fn();
    server.use(
      http.post("/api/v1/anchors", async ({ request, response }) => {
        posted(await request.json());
        return response(422).json({ detail: "should never be reached" });
      }),
    );
    renderSettings();
    await screen.findByTestId("current-ftp");

    await fillAppend(user, {
      value: "280",
      provenance: "tested",
      protocol: "",
    });
    await submitAppend(user);

    expect(screen.getByRole("alert")).toHaveTextContent(
      /Say how it was tested/,
    );
    // Refused in front of the athlete, not bounced off a 422.
    expect(posted).not.toHaveBeenCalled();
  });

  it("refuses a confidence bound that is not a number rather than sending none", async () => {
    const user = userEvent.setup();
    const posted = vi.fn();
    server.use(
      http.post("/api/v1/anchors", async ({ request, response }) => {
        posted(await request.json());
        return response(422).json({ detail: "should never be reached" });
      }),
    );
    renderSettings();
    await screen.findByTestId("current-ftp");

    await fillAppend(user, {
      value: "272",
      provenance: "estimated",
      protocol: "",
      ciLow: "26o",
    });
    await submitAppend(user);

    expect(screen.getByRole("alert")).toHaveTextContent(
      /confidence bound is a number in W/,
    );
    // The whole point: "26o" parses to null, and null is also how this form
    // says "no interval". Sent, it would append a version with the confidence
    // interval quietly dropped and report it as a success.
    expect(posted).not.toHaveBeenCalled();
  });

  it("drops the server's last refusal when it refuses the next one itself", async () => {
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId("current-ftp");

    await fillAppend(user, {
      value: "2650",
      provenance: "estimated",
      protocol: "",
    });
    await submitAppend(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /must be between 30 and 700/,
    );

    // react-query holds the mutation's error until the next `mutate()`, and
    // this submit never reaches one — so without a reset the athlete is told
    // two things at once, one of them about a payload that no longer exists.
    await fillAppend(user, { value: "" });
    await submitAppend(user);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getAllByRole("listitem")).toHaveLength(1);
    expect(alert).toHaveTextContent("Enter the FTP value in W.");
  });

  it("puts the effective date back to today once the version is appended", async () => {
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId("current-ftp");

    await fillAppend(user, {
      value: "240",
      provenance: "estimated",
      protocol: "",
      effectiveDate: "2026-06-15",
    });
    await submitAppend(user);
    await screen.findByRole("status");

    // A back-date is a one-off. Left where the correction put it, the next
    // value — a test ridden today — would be dated to June as well.
    expect(screen.getByLabelText(/Effective from/)).toHaveValue(athleteToday());
  });

  it("says a future-dated version is not in force yet, in both places it shows", async () => {
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId("current-ftp");

    await fillAppend(user, {
      value: "280",
      provenance: "estimated",
      protocol: "",
      effectiveDate: addDays(athleteToday(), 30),
    });
    await submitAppend(user);

    // Otherwise this is a success message beside a card that did not change
    // and a history whose top row is not the value in force — three panels
    // disagreeing, with nothing on the page saying why.
    expect(await screen.findByRole("status")).toHaveTextContent(
      "It is not in force yet",
    );
    expect(within(slot("ftp")).getByText("265")).toBeInTheDocument();
    const rows = await screen.findAllByTestId("anchor-version");
    expect(rows[0]).toHaveTextContent("280 W");
    expect(rows[0]).toHaveTextContent("not in force yet");
  });

  it("prints the API's own refusal for a value the domain will not take", async () => {
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId("current-ftp");

    // A typo'd extra digit, which is exactly what the plausibility bounds
    // exist to catch (`ANCHOR_BOUNDS`).
    await fillAppend(user, {
      value: "2650",
      provenance: "estimated",
      protocol: "",
    });
    await submitAppend(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /ftp value must be between 30 and 700 W/,
    );
    expect(within(slot("ftp")).getByText("265")).toBeInTheDocument();
  });

  it("back-dates a correction without displacing a newer version", async () => {
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId("current-ftp");

    await fillAppend(user, {
      value: "240",
      provenance: "estimated",
      protocol: "",
      effectiveDate: "2026-06-15",
    });
    await submitAppend(user);

    expect(
      await screen.findByText(/240 W/, { selector: "span" }),
    ).toBeInTheDocument();
    // "In force" is effective-date order, not insertion order: the July test
    // still governs, even though June's correction was appended after it.
    await waitFor(() =>
      expect(within(slot("ftp")).getByText("265")).toBeInTheDocument(),
    );
  });

  // The confidence interval's own round trip is the first test in this block;
  // this one is about the fields that are not typed as numbers.
  it("sends the value, the date and the provenance it was given, and no unit", async () => {
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId("current-ftp");

    await fillAppend(user, {
      anchor: "lthr",
      value: "164",
      provenance: "athlete_reported",
      effectiveDate: "2026-08-01",
    });
    await submitAppend(user);

    await waitFor(() =>
      expect(anchorHistory("lthr")[0]).toMatchObject({
        anchor_type: "lthr",
        value: 164,
        // The unit was never sent: the API stamps the anchor type's own.
        unit: "bpm",
        provenance: "athlete_reported",
        effective_date: "2026-08-01",
        protocol: null,
        source: "athlete",
      }),
    );
  });
});

describe("the history", () => {
  it("lists every version newest first, with no way to edit or delete one", async () => {
    renderSettings();

    const rows = await screen.findAllByTestId("anchor-version");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("265 W");
    // The version a planned session pinned is still there, months later,
    // which is what makes the watts on that session explicable.
    expect(rows[3]).toHaveTextContent("250 W");
    expect(rows[3]).toHaveTextContent("01.06.2026");

    // The API answers PUT, PATCH and DELETE on a version with 405 by design,
    // so there is nothing here that could ask it to.
    expect(
      screen.queryByRole("button", { name: /edit|delete|remove/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/no edit and no delete/i)).toBeInTheDocument();
  });

  it("filters through the API rather than hiding rows it fetched", async () => {
    const user = userEvent.setup();
    const asked: (string | null)[] = [];
    server.use(
      http.get("/api/v1/anchors", ({ query, response }) => {
        asked.push(query.get("anchor_type"));
        const rows = anchorHistory(query.get("anchor_type") as "lthr" | null);
        return response(200).json({
          items: rows,
          total: rows.length,
          offset: 0,
          limit: 20,
        });
      }),
    );
    renderSettings();
    await screen.findAllByTestId("anchor-version");

    await user.selectOptions(screen.getByLabelText("Filter the history"), [
      "lthr",
    ]);

    await waitFor(() =>
      expect(screen.getAllByTestId("anchor-version")).toHaveLength(1),
    );
    expect(asked).toContain("lthr");
  });

  it("pages a history longer than one page, asking for the page it shows", async () => {
    // Twenty-one more versions through the mock's own append — so the history
    // is twenty-five long: one full page and a short second one.
    for (let index = 0; index < 21; index += 1) {
      appendAnchorVersion({
        anchor_type: "ftp",
        value: 200 + index,
        provenance: "estimated",
        effective_date: `2026-01-${String(index + 1).padStart(2, "0")}`,
      });
    }
    const offsets: (string | null)[] = [];
    server.use(
      http.get("/api/v1/anchors", ({ query, response }) => {
        offsets.push(query.get("offset"));
        const offset = Number(query.get("offset") ?? 0);
        const limit = Number(query.get("limit") ?? 20);
        const rows = anchorHistory(query.get("anchor_type") as null);
        return response(200).json({
          items: rows.slice(offset, offset + limit),
          total: rows.length,
          offset,
          limit,
        });
      }),
    );
    const user = userEvent.setup();
    renderSettings();

    expect(await screen.findByText("1–20 of 25")).toBeInTheDocument();
    expect(screen.getAllByTestId("anchor-version")).toHaveLength(20);
    expect(
      screen.getByRole("button", { name: "Newer anchor versions" }),
    ).toBeDisabled();

    await user.click(
      screen.getByRole("button", { name: "Older anchor versions" }),
    );

    // The last page is short, and the range says how short rather than
    // running on to the page size.
    expect(await screen.findByText("21–25 of 25")).toBeInTheDocument();
    expect(screen.getAllByTestId("anchor-version")).toHaveLength(5);
    expect(
      screen.getByRole("button", { name: "Older anchor versions" }),
    ).toBeDisabled();

    await user.click(
      screen.getByRole("button", { name: "Newer anchor versions" }),
    );
    expect(await screen.findByText("1–20 of 25")).toBeInTheDocument();

    // Every step asked the server for its own page: a pager that sliced the
    // rows it already had would show the same twenty three times.
    expect(offsets).toEqual(["0", "20", "0"]);
  });
});

describe("the zones the anchors produce", () => {
  it("derives power zones from the FTP in force, and says which scheme", async () => {
    renderSettings();

    const power = screen.getByTestId("zones-coggan_7");
    expect(within(power).getByText("Coggan 7 · %FTP")).toBeInTheDocument();
    expect(
      await within(power).findByText(/from FTP 265 W/),
    ).toBeInTheDocument();
    // 55–75 % of 265 W, rounded to whole watts — the API's own numbers, not
    // a percentage this component multiplied out.
    expect(within(power).getByText("146–199 W")).toBeInTheDocument();
    expect(within(power).getByText("55%–75%")).toBeInTheDocument();
    // There is no ceiling on a sprint.
    expect(within(power).getByText("≥ 398 W")).toBeInTheDocument();

    const hr = screen.getByTestId("zones-lthr_5");
    expect(
      await within(hr).findByText(/from LTHR 162 bpm/),
    ).toBeInTheDocument();
    expect(within(hr).getByText("131–146 bpm")).toBeInTheDocument();
  });

  it("names the anchor it is waiting for instead of drawing a guess", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/v1/zones", ({ query, response }) =>
        query.get("anchor_type") === "lthr"
          ? response(404).json({ detail: "No lthr anchor is in force" })
          : response(404).json({ detail: "No ftp anchor is in force" }),
      ),
    );
    renderSettings();

    const hr = screen.getByTestId("zones-lthr_5");
    expect(
      await within(hr).findByText(
        /No LTHR yet — add one to see heart-rate zones/,
      ),
    ).toBeInTheDocument();

    // And the remedy is the form, aimed at the anchor that is missing.
    await user.click(within(hr).getByRole("button", { name: "Add LTHR" }));
    expect(screen.getByLabelText("Anchor")).toHaveValue("lthr");
  });
});

describe("the profile", () => {
  it("shows what the profile holds and saves an edit", async () => {
    const user = userEvent.setup();
    renderSettings();

    const name = await screen.findByLabelText("Name");
    expect(name).toHaveValue("Alex Rider");
    expect(screen.getByLabelText("Date of birth")).toHaveValue("1990-06-15");
    expect(screen.getByLabelText("Sex")).toHaveValue("male");
    expect(screen.getByLabelText(/Height/)).toHaveValue("181.5");

    await user.clear(name);
    await user.type(name, "Alexandra Rider");
    await user.clear(screen.getByLabelText(/Height/));
    await user.type(screen.getByLabelText(/Height/), "179");
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByText("Profile saved.")).toBeInTheDocument();
    expect(athleteRecord()).toMatchObject({
      name: "Alexandra Rider",
      height_cm: 179,
      date_of_birth: "1990-06-15",
      sex: "male",
    });
  });

  it("clears a field it was emptied rather than leaving it alone", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.clear(await screen.findByLabelText("Date of birth"));
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    // An emptied field is an explicit null, which is how this API erases one
    // — a PATCH that omitted it would silently keep the old date.
    await waitFor(() => expect(athleteRecord().date_of_birth).toBeNull());
  });

  it("keeps the fields, and what was typed into them, when a refetch fails", async () => {
    const user = userEvent.setup();
    const { queryClient } = renderSettings();

    const name = await screen.findByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Alexandra Rider");

    server.use(
      // Untyped: an infrastructure failure has no schema to conform to.
      http.untyped.get("http://localhost:8000/api/v1/athlete", () =>
        HttpResponse.json(
          { detail: "The profile store is down." },
          { status: 500 },
        ),
      ),
    );
    await act(() => queryClient.refetchQueries());

    // react-query keeps the last good profile through a failed *background*
    // refetch, so there is still a profile to show — and replacing the form
    // with "could not load" over it would throw away half-typed edits because
    // the network blinked. The failure is said, not swallowed.
    expect(
      await screen.findByText(/Could not refresh the profile/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Alexandra Rider");
  });

  it("prints the field the server refused, and which field it was", async () => {
    const user = userEvent.setup();
    renderSettings();

    await user.clear(await screen.findByLabelText(/Height/));
    await user.type(screen.getByLabelText(/Height/), "17");
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "height_cm: Input should be greater than or equal to 100",
    );
  });
});

describe("the illness flag", () => {
  it("offers the same control Today does, and says what raising it costs", async () => {
    renderSettings();

    expect(
      await screen.findByRole("button", { name: "Report illness or injury" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/stops the coaching agent proposing anything that adds/),
    ).toBeInTheDocument();
  });
});
