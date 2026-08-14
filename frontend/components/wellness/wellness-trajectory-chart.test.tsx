import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WellnessTrajectoryChart } from "@/components/wellness/wellness-trajectory-chart";

/**
 * uPlot, captured rather than run.
 *
 * The claim under test is "a date with no reading is a break in the line, not
 * a segment to zero", and uPlot draws that from exactly two things: a `null`
 * at that index of the aligned data, and `spanGaps: false` on the series. It
 * owns its canvas and exposes neither back, so the honest way to assert the
 * claim is to read what it was constructed with — the same artifact the
 * browser draws from.
 *
 * The library itself is not under test here; `stream-charts.test.tsx` renders
 * the real thing.
 */
const constructions: { options: unknown; data: unknown }[] = [];

vi.mock("uplot", () => {
  class FakeUPlot {
    constructor(options: unknown, data: unknown, _target: HTMLElement) {
      constructions.push({ options, data });
    }
    setSize() {}
    setData() {}
    destroy() {}
  }
  return { default: FakeUPlot };
});

/** The y-values the chart handed uPlot, in date order. */
function plotted(): (number | null)[] {
  const [latest] = constructions.slice(-1);
  const data = latest?.data as (number | null)[][];
  return data[1] ?? [];
}

/** Whether the series was told to bridge its gaps. */
function spansGaps(): boolean {
  const [latest] = constructions.slice(-1);
  const options = latest?.options as {
    series: { spanGaps?: boolean }[];
  };
  return options.series[1]?.spanGaps ?? true;
}

const DATES = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"];

function renderChart(values: (number | null)[], band?: [number, number]) {
  return render(
    <WellnessTrajectoryChart
      label="Resting HR"
      unit="bpm"
      dates={DATES.slice(0, values.length)}
      values={values}
      band={band ? { low: band[0], high: band[1] } : null}
    />,
  );
}

describe("the wellness trajectory chart", () => {
  beforeEach(() => {
    constructions.length = 0;
  });

  it("draws a missing day as a break, never as a segment to zero", () => {
    renderChart([50, null, 52, 51]);

    // The gap reaches uPlot as a null. A zero here would draw a line down to
    // the axis and read as a heart that stopped.
    expect(plotted()).toEqual([50, null, 52, 51]);
    expect(plotted()).not.toContain(0);
    // And nothing bridges it: `spanGaps` false is what makes the null a hole
    // rather than a straight line across the missing morning.
    expect(spansGaps()).toBe(false);
  });

  it("keeps a gap at the first date of the range", () => {
    renderChart([null, 51, 52, 51]);

    expect(plotted()[0]).toBeNull();
  });

  it("keeps a gap at the last date of the range", () => {
    renderChart([50, 51, 52, null]);

    expect(plotted().at(-1)).toBeNull();
  });

  it("charts a single-day range", () => {
    renderChart([47]);

    expect(plotted()).toEqual([47]);
  });

  it("does not interpolate a run of missing days", () => {
    renderChart([50, null, null, 56]);

    expect(plotted()).toEqual([50, null, null, 56]);
  });

  it("names the normal band beside the plot when there is one", () => {
    renderChart([50, 51, 52, 51], [48.4, 50.6]);

    expect(screen.getByText(/48\.4/)).toBeInTheDocument();
    expect(screen.getByText(/50\.6/)).toBeInTheDocument();
  });

  it("says nothing about a band it was not given", () => {
    renderChart([50, 51, 52, 51]);

    expect(screen.queryByText(/normal band/i)).not.toBeInTheDocument();
  });
});
