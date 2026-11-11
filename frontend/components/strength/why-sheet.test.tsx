import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { WhySheet } from "@/components/strength/why-sheet";
import { LocaleProvider } from "@/lib/i18n/provider";
import {
  CAPPED_DOWN,
  EASIER_TWICE,
  FIRST_TIME,
} from "@/tests/mocks/generated-progression";

/**
 * AC-73: the reason chip and the Why sheet on the logging screen. Every
 * suggestion is the generated fixture — `app.domain.progression.suggest` run
 * over stored sessions and projected through the packet's `SuggestionRead` —
 * so "102.5 kg, computed 102.6" is the domain's arithmetic, not this file's.
 */
function renderSheet(
  suggestion: Parameters<typeof WhySheet>[0]["suggestion"],
  locale = "en",
) {
  return render(
    <LocaleProvider locale={locale}>
      <WhySheet suggestion={suggestion} fallback="Same as last time" />
    </LocaleProvider>,
  );
}

describe("the reason chip", () => {
  it("names the change and why for two easier sessions", () => {
    renderSheet(EASIER_TWICE);

    expect(
      screen.getByText("Up 2.5 kg: two sessions felt easier"),
    ).toBeInTheDocument();
  });

  it("names a capped drop by what was logged", () => {
    renderSheet(CAPPED_DOWN);

    expect(
      screen.getByText("Down 10 kg: last session was much harder"),
    ).toBeInTheDocument();
  });

  it("shows No history yet for a first-time exercise, with no Why", () => {
    renderSheet(FIRST_TIME);

    expect(screen.getByText("No history yet")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Why this weight?" }),
    ).not.toBeInTheDocument();
  });

  it("falls back to the pre-fill's own reason without a suggestion", () => {
    renderSheet(null);

    expect(screen.getByText("Same as last time")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Why this weight?" }),
    ).not.toBeInTheDocument();
  });

  it("speaks German when the athlete does", () => {
    renderSheet(EASIER_TWICE, "de");

    expect(
      screen.getByText("2,5 kg mehr: zweimal leichter als der Plan"),
    ).toBeInTheDocument();
  });
});

describe("the Why sheet", () => {
  it("shows the three lines from the explanation", async () => {
    const user = userEvent.setup();
    renderSheet(EASIER_TWICE);

    await user.click(screen.getByRole("button", { name: "Why this weight?" }));

    const sheet = screen.getByRole("dialog", { name: "Why 102.5 kg?" });
    expect(
      within(sheet).getByText(
        "Your last two sessions: 100 kg × 6, with 3 reps left each time. The plan aims for 2 reps left.",
      ),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText(
        "So there's a little more in you — 102.5 kg today.",
      ),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText(
        "You can change it. arc uses what you actually lift.",
      ),
    ).toBeInTheDocument();
    // The arithmetic waits behind Details.
    expect(within(sheet).queryByText(/÷ 30/)).not.toBeInTheDocument();
  });

  it("shows the arithmetic, the rounding and the rule under Details", async () => {
    const user = userEvent.setup();
    renderSheet(EASIER_TWICE);

    await user.click(screen.getByRole("button", { name: "Why this weight?" }));
    const sheet = screen.getByRole("dialog");
    await user.click(within(sheet).getByRole("button", { name: "Details" }));

    expect(
      within(sheet).getByText(
        "100 kg × (1 + (6 + 3) ÷ 30) ÷ (1 + (6 + 2) ÷ 30) = 102.6 kg",
      ),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText(
        "→ nearest loadable weight with your plates = 102.5 kg.",
      ),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText(
        "Rule: weight from effort · Coaching practice, built on a standard strength estimate",
      ),
    ).toBeInTheDocument();
  });

  it("names the limit that held a big drop back", async () => {
    const user = userEvent.setup();
    renderSheet(CAPPED_DOWN);

    await user.click(screen.getByRole("button", { name: "Why this weight?" }));
    const sheet = screen.getByRole("dialog", { name: "Why 90 kg?" });
    await user.click(within(sheet).getByRole("button", { name: "Details" }));

    expect(
      within(sheet).getByText(
        "100 kg × (1 + (5 + 0) ÷ 30) ÷ (1 + (8 + 2) ÷ 30) = 87.5 kg",
      ),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText("→ held at the most a lift may drop in one go"),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText(
        "→ nearest loadable weight with your plates = 90 kg.",
      ),
    ).toBeInTheDocument();
  });
});
