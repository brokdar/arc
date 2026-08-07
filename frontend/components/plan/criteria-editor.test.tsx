import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { CriteriaEditor } from "@/components/plan/criteria-editor";
import type { SuccessCriterion } from "@/lib/criteria";

/** A host that keeps the list, the way the session form does. */
function Host({
  initial,
  discipline = "cycling",
  onChange,
}: {
  initial: SuccessCriterion[];
  discipline?: "cycling" | "strength";
  onChange?: (criteria: readonly SuccessCriterion[]) => void;
}) {
  const [criteria, setCriteria] =
    useState<readonly SuccessCriterion[]>(initial);
  return (
    <CriteriaEditor
      criteria={criteria}
      discipline={discipline}
      onChange={(next) => {
        setCriteria(next);
        onChange?.(next);
      }}
    />
  );
}

const TIME_IN_BAND: SuccessCriterion = {
  kind: "time_in_band",
  band: { channel: "power", low: 0.95, high: 1.05, smoothing_s: 30 },
  min_fraction: 0.75,
  selector: { kind: "role", role: "work", index: null },
};

describe("CriteriaEditor", () => {
  it("shows each criterion as the sentence the rest of the app shows", () => {
    render(<Host initial={[TIME_IN_BAND]} />);

    expect(
      screen.getByText(
        "75% of the work steps' time within 95%–105% of the prescribed power, 30 s average",
      ),
    ).toBeInTheDocument();
  });

  it("edits the numbers behind the sentence, and the sentence follows", async () => {
    render(<Host initial={[TIME_IN_BAND]} />);
    const card = screen.getByTestId("criterion");

    await userEvent.clear(within(card).getByLabelText(/At least/));
    await userEvent.type(within(card).getByLabelText(/At least/), "90");

    expect(
      screen.getByText(
        "90% of the work steps' time within 95%–105% of the prescribed power, 30 s average",
      ),
    ).toBeInTheDocument();
  });

  it("widens a criterion from the work steps to the whole ride", async () => {
    render(<Host initial={[TIME_IN_BAND]} />);

    await userEvent.selectOptions(screen.getByLabelText(/Applies to/), "all");

    expect(
      screen.getByText(
        "75% of the session's time within 95%–105% of the prescribed power, 30 s average",
      ),
    ).toBeInTheDocument();
  });

  it("adds a criterion of the kind that was chosen", async () => {
    const onChange = vi.fn();
    render(<Host initial={[]} onChange={onChange} />);

    await userEvent.selectOptions(
      screen.getByLabelText("Add a criterion"),
      "ceiling",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(screen.getAllByTestId("criterion")).toHaveLength(1);
    expect(
      screen.getByText(
        "No more than 5:00 with heart rate above 100% of LTHR, raw samples",
      ),
    ).toBeInTheDocument();
    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ kind: "ceiling" }),
    ]);
  });

  it("edits a ceiling's duration in mm:ss", async () => {
    render(
      <Host
        initial={[
          {
            kind: "ceiling",
            channel: "hr",
            limit: { kind: "absolute", unit: "bpm", value: 178 },
            max_seconds_above: 300,
            smoothing_s: 0,
          },
        ]}
      />,
    );

    await userEvent.clear(screen.getByLabelText(/For no more than/));
    await userEvent.type(screen.getByLabelText(/For no more than/), "6:00");

    expect(
      screen.getByText(
        "No more than 6:00 with heart rate above 178 bpm, raw samples",
      ),
    ).toBeInTheDocument();
  });

  it("removes a criterion", async () => {
    render(
      <Host
        initial={[TIME_IN_BAND, { kind: "duration_floor", min_seconds: 3600 }]}
      />,
    );

    await userEvent.click(
      within(screen.getAllByTestId("criterion")[0] as HTMLElement).getByRole(
        "button",
        { name: "Remove criterion" },
      ),
    );

    expect(screen.getAllByTestId("criterion")).toHaveLength(1);
    expect(screen.getByText("Lasts at least 1:00:00")).toBeInTheDocument();
  });

  it("offers a lifting session only the criteria a gym can be judged by", async () => {
    render(
      <Host
        discipline="strength"
        initial={[{ kind: "sets_completed", min_fraction: 0.9 }]}
      />,
    );

    const menu = screen.getByLabelText("Add a criterion");
    expect(within(menu).queryByText("Time in band")).toBeNull();
    expect(within(menu).getByText("Sets completed")).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText(/At least/));
    await userEvent.type(screen.getByLabelText(/At least/), "100");

    expect(
      screen.getByText("100% of the prescribed sets completed"),
    ).toBeInTheDocument();
  });

  it("says what to do when there are no criteria at all", () => {
    render(<Host initial={[]} />);

    expect(screen.getByText(/No criteria\./)).toBeInTheDocument();
  });
});
