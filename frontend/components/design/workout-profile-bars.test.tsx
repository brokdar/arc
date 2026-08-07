import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WorkoutProfileBars } from "@/components/design/workout-profile-bars";
import type { components } from "@/generated/api/schema";

type Endurance = components["schemas"]["EnduranceStructureSchema-Output"];

function steady(
  role: components["schemas"]["StepRole"],
  durationS: number | null,
): components["schemas"]["SteadyStepSchema"] {
  return {
    kind: "steady",
    role,
    name: null,
    duration_s: durationS,
    distance_m: null,
    targets: {},
  };
}

/** 12 min warm-up + 4×(4 min on, 3 min off) + 20 min cool-down = 1:00:00. */
const vo2: Endurance = {
  discipline: "cycling",
  steps: [
    steady("warmup", 720),
    {
      kind: "repeat",
      times: 4,
      children: [steady("work", 240), steady("rest", 180)],
    },
    steady("cooldown", 1200),
  ],
};

function axis() {
  return document.querySelector("[data-slot='workout-profile-axis']");
}

describe("the detail plot's time axis", () => {
  it("runs from 0:00 to the prescription's own total", () => {
    render(<WorkoutProfileBars structure={vo2} size="detail" />);

    const labels = [...(axis()?.children ?? [])].map((el) => el.textContent);
    expect(labels).toEqual(["0:00", "15:00", "30:00", "45:00", "1:00:00"]);
  });

  it("stays off the card-sized strip, which has no room for it", () => {
    render(<WorkoutProfileBars structure={vo2} />);
    expect(axis()).toBeNull();
  });

  it("says nothing when a step is open-ended", () => {
    // A total invented from steps that state no duration would be a number the
    // prescription never gave.
    render(
      <WorkoutProfileBars
        structure={{
          discipline: "cycling",
          steps: [steady("warmup", 600), steady("work", null)],
        }}
        size="detail"
      />,
    );
    expect(axis()).toBeNull();
    expect(
      document.querySelector("[data-slot='workout-profile']"),
    ).not.toBeNull();
  });

  it("draws nothing at all for a strength prescription", () => {
    const { container } = render(
      <WorkoutProfileBars
        structure={{ discipline: "strength", groups: [] }}
        size="detail"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("keeps the plot out of the accessibility tree", () => {
    // The resolved step list beside it carries the same content as text.
    render(<WorkoutProfileBars structure={vo2} size="detail" />);
    expect(screen.queryByText("15:00")).not.toBeVisible;
    expect(axis()).toHaveAttribute("aria-hidden");
  });
});
