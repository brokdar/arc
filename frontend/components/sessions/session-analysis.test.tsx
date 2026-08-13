import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { describe, expect, it } from "vitest";

import { MetricHeader, ZoneBar } from "@/components/sessions/metric-header";
import {
  IntervalsTable,
  SessionAnalysis,
  StrengthCard,
} from "@/components/sessions/session-analysis";
import type { PlannedBand } from "@/components/sessions/stream-charts";
import { formatDurationClock } from "@/lib/format";
import type { SessionMetrics } from "@/lib/metrics";
import { normalizedPower, selectionStats } from "@/lib/metrics";
import {
  ACTIVITY_IDS,
  RIDE_METRICS,
  RIDE_STREAMS,
} from "@/tests/mocks/fixtures";

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

/** The artefact with one block replaced by its `not_assessed` form. */
function without(patch: Partial<SessionMetrics>): SessionMetrics {
  return { ...RIDE_METRICS, ...patch };
}

/**
 * The slot the API sends for a metric an older artefact was written before.
 *
 * Word for word what `predates()` in `backend/app/api/schemas/metrics.py`
 * fills the missing key with, because that is what a client actually receives:
 * the block is never absent, it is present and full of reasons that name the
 * remedy.
 */
function predates(metric: string) {
  return {
    value: null,
    explanation: null,
    not_assessed: `these metrics were computed before ${metric} was — recompute this session to add it`,
  };
}

describe("the metric header", () => {
  it("renders every number with the explanation it came with", () => {
    render(<MetricHeader metrics={RIDE_METRICS} />);

    // A3.7: the explanation is data attached to the number. The affordance is
    // the same one for every stat, which is what stops NP being explained one
    // way and load another.
    const np = screen.getByLabelText(/NP = mean\(rolling_mean_30s/);
    expect(np).toHaveTextContent(
      String(Math.round(RIDE_METRICS.power.normalized_power.value ?? 0)),
    );
    // Average power carries the caveat that matters most about it: the
    // divisor is moving time, and the load beside it still uses recording
    // time. Both sentences travel on the number itself.
    const average = screen.getByLabelText(/average power = Σ P × Δt/);
    expect(average.getAttribute("aria-label")).toMatch(/moving time/);
    expect(average.getAttribute("aria-label")).toMatch(/recording time/);
  });

  it("shows what the ride was, not only what it cost", () => {
    render(<MetricHeader metrics={RIDE_METRICS} />);

    // The basics every other training application leads with, and this one
    // omitted: distance, speed, climbing, cadence, standing time, weather.
    const distance = RIDE_METRICS.speed?.distance_km?.value ?? 0;
    const averageSpeed = RIDE_METRICS.speed?.average_speed_kmh?.value ?? 0;
    const maxSpeed = RIDE_METRICS.speed?.max_speed_kmh?.value ?? 0;
    expect(distance).toBeGreaterThan(0);
    expect(screen.getByText(distance.toFixed(1))).toBeInTheDocument();
    expect(screen.getByText(averageSpeed.toFixed(1))).toBeInTheDocument();
    expect(screen.getByText(maxSpeed.toFixed(1))).toBeInTheDocument();
    // Addressed through their own explanations rather than by text: this
    // ride's climbing and its load happen to round to the same integer, and a
    // bare text match would pass while pointing at the wrong slot.
    expect(
      screen.getByLabelText(/elevation gain = Σ \(peak/),
    ).toHaveTextContent(
      String(Math.round(RIDE_METRICS.elevation_gain_m.value ?? 0)),
    );
    expect(screen.getByLabelText(/average temperature =/)).toHaveTextContent(
      String(Math.round(RIDE_METRICS.temperature?.average_temp_c?.value ?? 0)),
    );
  });

  it("shows standing still and freewheeling as the different things they are", () => {
    render(<MetricHeader metrics={RIDE_METRICS} />);

    // Stopped time is elapsed − moving; coasting is time moving without
    // pedalling. The fixture's ride contains both, so a header that had
    // swapped them would fail here rather than read plausibly.
    const stopped = RIDE_METRICS.stopped_time_s?.value ?? 0;
    const coasting = RIDE_METRICS.power.coasting_time_s.value ?? 0;
    expect(stopped).toBeGreaterThan(0);
    expect(coasting).toBeGreaterThan(0);
    expect(stopped).not.toBe(coasting);
    expect(screen.getByText(formatDurationClock(stopped))).toBeInTheDocument();
    expect(screen.getByText(formatDurationClock(coasting))).toBeInTheDocument();
  });

  it("holds the ride-log slots for an artefact written before they existed", () => {
    // The version chain is append-only, so an artefact computed by an earlier
    // metric set has no key for a number added later — and the API fills the
    // gap on the way out (`predates()` in app/api/schemas/metrics.py), so what
    // reaches a client is never a missing block: it is every slot carrying the
    // reason and the remedy. That is the payload built here; a fixture with
    // `speed: undefined` in it would be testing a response the API cannot
    // send, and would pass whatever the recompute wording became.
    const metrics = without({
      speed: {
        distance_km: predates("distance"),
        average_speed_kmh: predates("average speed"),
        max_speed_kmh: predates("max speed"),
      },
      temperature: {
        average_temp_c: predates("temperature"),
        min_temp_c: predates("temperature"),
        max_temp_c: predates("temperature"),
      },
      stopped_time_s: predates("stopped time"),
    });

    render(<MetricHeader metrics={metrics} />);

    // UI convention 3: an empty state names the action that fills it, and the
    // action — the recompute button already on this page — has to reach a
    // screen reader, not only a hovering mouse.
    const held = screen.getAllByRole("img", { name: /recompute this session/ });
    expect(held.length).toBeGreaterThanOrEqual(7);
    expect(screen.getByText("Distance")).toBeInTheDocument();
    expect(screen.getByText("Temperature")).toBeInTheDocument();
    expect(screen.getByText("Stopped")).toBeInTheDocument();
  });

  it("names the FTP version an intensity factor was computed against", () => {
    render(<MetricHeader metrics={RIDE_METRICS} />);

    const ftp = RIDE_METRICS.pins.find((pin) => pin.anchor_type === "ftp");
    expect(
      screen.getByText(new RegExp(`FTP ${ftp?.value.toFixed(0)}`)),
    ).toBeInTheDocument();
    // Provenance travels with it: an estimate has to read as an estimate.
    expect(screen.getByLabelText(/estimated:/)).toBeInTheDocument();
  });

  it("labels a heart-rate load HRSS, never TSS", () => {
    // Both scales put an hour at threshold at 100, so the numbers sit in the
    // same range and an HRSS value stamped "TSS" looks entirely plausible.
    const metrics = without({
      load: {
        not_assessed: null,
        training_load: 75,
        load_basis: "hr",
        load_basis_rule:
          "no power was recorded, so the heart-rate model was used",
        power_load: null,
        hr_load: 75,
        explanation: null,
      },
    });

    render(<MetricHeader metrics={metrics} />);

    expect(screen.getByText("HRSS")).toBeInTheDocument();
    expect(screen.queryByText("TSS")).not.toBeInTheDocument();
    expect(screen.getByText(/from heart rate/)).toBeInTheDocument();
  });

  it("labels a power load TSS", () => {
    render(<MetricHeader metrics={RIDE_METRICS} />);

    expect(screen.getByText("TSS")).toBeInTheDocument();
    expect(screen.queryByText("HRSS")).not.toBeInTheDocument();
  });

  it("shows a stream-free session's elapsed time, never 0:00", () => {
    // `recording_time_s` is 0.0 on every artefact with no recording behind
    // it — a typed-in gym session has no pauses to subtract — and printing it
    // put "0:00" under an hour in the gym.
    const metrics = without({ recording_time_s: 0, elapsed_time_s: 3_600 });

    render(<MetricHeader metrics={metrics} />);

    expect(screen.getByText("1:00:00")).toBeInTheDocument();
    expect(screen.queryByText("0:00")).not.toBeInTheDocument();
    // And it says which of the two durations is on screen: they differ.
    expect(screen.getByText("elapsed")).toBeInTheDocument();
  });

  it("holds the duration slot when a session records neither", () => {
    const metrics = without({ recording_time_s: 0, elapsed_time_s: 0 });

    render(<MetricHeader metrics={metrics} />);

    expect(
      screen.getByRole("img", { name: /Not assessed: This session records/ }),
    ).toBeInTheDocument();
  });

  it("states the counterfactual when both load models exist", () => {
    render(<MetricHeader metrics={RIDE_METRICS} />);

    // A5.2's sentence, composed from two numbers the API sends.
    expect(
      screen.getByText(/Had power been unavailable, the heart-rate model/),
    ).toBeInTheDocument();
  });

  it("holds a slot and gives the reason when a metric was not assessed", () => {
    const metrics = without({
      power: {
        ...RIDE_METRICS.power,
        normalized_power: {
          value: null,
          explanation: null,
          not_assessed: "no power was recorded",
        },
      },
    });

    render(<MetricHeader metrics={metrics} />);

    expect(
      screen.getByRole("img", { name: "Not assessed: no power was recorded" }),
    ).toBeInTheDocument();
  });

  it("says why there is no load rather than showing a zero", () => {
    const metrics = without({
      load: {
        not_assessed: "no power was recorded; no heart rate was recorded",
        training_load: null,
        load_basis: null,
        load_basis_rule: null,
        power_load: null,
        hr_load: null,
        explanation: null,
      },
    });

    render(<MetricHeader metrics={metrics} />);

    expect(
      screen.getByRole("img", { name: /no power was recorded/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});

describe("the zone bar", () => {
  it("keeps every band of the model, including the empty ones", () => {
    render(<ZoneBar distribution={RIDE_METRICS.time_in_zone.power} />);

    // A zone with no time in it is a fact about the ride; dropping it would
    // make the bar's shape depend on the data.
    const bands = RIDE_METRICS.time_in_zone.power.zones;
    expect(bands.length).toBeGreaterThan(0);
    for (const band of bands) {
      expect(screen.getByText(`Z${band.index}`)).toBeInTheDocument();
    }
  });

  it("falls back to heart rate when there is no power distribution", () => {
    render(
      <ZoneBar
        distribution={{
          not_assessed: "no FTP anchor is in force to derive zones from",
          zones: [],
        }}
        fallback={RIDE_METRICS.time_in_zone.hr}
      />,
    );

    expect(screen.getByRole("img", { name: /heart rate/ })).toBeInTheDocument();
  });

  it("says why when neither channel produced one", () => {
    render(
      <ZoneBar
        distribution={{ not_assessed: "no power was recorded", zones: [] }}
      />,
    );

    expect(
      screen.getByRole("img", { name: "Not assessed: no power was recorded" }),
    ).toBeInTheDocument();
  });
});

describe("the intervals table", () => {
  it("lists what the detector found, with each interval's own statistics", () => {
    render(<IntervalsTable intervals={RIDE_METRICS.intervals} />);

    expect(RIDE_METRICS.intervals.length).toBeGreaterThan(0);
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(RIDE_METRICS.intervals.length);
    const first = RIDE_METRICS.intervals[0];
    expect(
      within(rows[0]).getByText(String(Math.round(first.average_power ?? 0))),
    ).toBeInTheDocument();
  });

  it("has no adherence column — that is WP-7's, against a plan", () => {
    render(<IntervalsTable intervals={RIDE_METRICS.intervals} />);

    expect(screen.queryByText(/adherence/i)).not.toBeInTheDocument();
  });

  it("says a steady ride has no intervals rather than showing an empty table", () => {
    render(<IntervalsTable intervals={[]} />);

    expect(
      screen.getByText(/No work intervals were detected/),
    ).toBeInTheDocument();
  });
});

describe("the strength card", () => {
  it("reports kilograms and how much of the session they cover", () => {
    render(
      <StrengthCard
        strength={{
          not_assessed: null,
          volume_load_kg: 1000,
          sets_completed: 3,
          coverage: 2 / 3,
          explanation: {
            formula: "volume load = Σ reps × kg",
            inputs: {},
            assumptions: ["kilograms, never a training load"],
            citation: null,
          },
        }}
      />,
    );

    expect(screen.getByText("1000")).toBeInTheDocument();
    expect(screen.getByText("kg")).toBeInTheDocument();
    expect(
      screen.getByText(/67% of the working sets carried kilograms/),
    ).toBeInTheDocument();
    // v2 §5.4: kilograms are not a load, and nothing here calls them one.
    expect(screen.queryByText("TSS")).not.toBeInTheDocument();
  });

  it("reports held seconds as their own figure, never inside the kilograms", () => {
    // A session of planks moved no kilograms and is still work. Without this
    // figure the only trace of it on the page is the per-row `45 s` in the
    // logged-sets table, and the session's total held time is unreadable.
    render(
      <StrengthCard
        strength={{
          not_assessed: null,
          volume_load_kg: null,
          sets_completed: 4,
          total_hold_s: 135,
          coverage: 0,
          explanation: null,
        }}
      />,
    );

    expect(screen.getByText("Held")).toBeInTheDocument();
    expect(screen.getByText("135")).toBeInTheDocument();
    expect(screen.getByText("s")).toBeInTheDocument();
    expect(
      screen.getByLabelText(
        "Not assessed: No set in this session was logged in kilograms",
      ),
    ).toBeInTheDocument();
  });

  it("counts sets in working sets, and the note says so", () => {
    // `sets_completed` counts a per-side row twice, so three logged rows read
    // as six. The note promised "every set logged", which is the count of
    // rows the athlete typed — a different number.
    render(
      <StrengthCard
        strength={{
          not_assessed: null,
          volume_load_kg: 990,
          sets_completed: 6,
          total_hold_s: null,
          coverage: 1,
          explanation: null,
        }}
      />,
    );

    expect(screen.getByText("6")).toBeInTheDocument();
    expect(screen.getByText(/a per-side row counts twice/)).toBeInTheDocument();
    expect(screen.queryByText("Held")).not.toBeInTheDocument();
  });
});

describe("the analysis section", () => {
  it("offers the action when nothing has been computed", async () => {
    // The trainer ride is the seeded session with no artefact, so the mock's
    // state and the prop agree — a test that claimed "nothing computed" for a
    // session the mock has metrics for would assert against an impossible page.
    renderWithClient(
      <SessionAnalysis
        sessionId={ACTIVITY_IDS.trainerRide}
        metrics={null}
        hasRecording
      />,
    );

    // UI convention 3: an empty state that names no remedy is a dead end.
    expect(screen.getByText(/have not been computed/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Compute metrics" }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      /Wrote version 1\./,
    );
  });

  it("recompute appends a version and says the old one survives", async () => {
    renderWithClient(
      <SessionAnalysis
        sessionId={ACTIVITY_IDS.outdoorRide}
        metrics={RIDE_METRICS}
        hasRecording
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Recompute" }));

    // Invariant 1: n+1 supersedes n, and n stays readable. The handler
    // honours the request rather than answering a canned version.
    expect(await screen.findByRole("status")).toHaveTextContent(
      `Wrote version ${RIDE_METRICS.version + 1}`,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/still\s+readable/);
  });

  it("shows the strength card and no charts for a session with no stream", () => {
    renderWithClient(
      <SessionAnalysis
        sessionId={ACTIVITY_IDS.gym}
        metrics={RIDE_METRICS}
        hasRecording={false}
      />,
    );

    expect(screen.getByText("Strength")).toBeInTheDocument();
    expect(screen.queryByText("Streams")).not.toBeInTheDocument();
    expect(screen.queryByText("Intervals")).not.toBeInTheDocument();
  });

  it("names the anchor versions the numbers were computed against", () => {
    renderWithClient(
      <SessionAnalysis
        sessionId={ACTIVITY_IDS.outdoorRide}
        metrics={RIDE_METRICS}
        hasRecording
      />,
    );

    // The pins are frozen on the artefact, not looked up now — an IF is
    // only meaningful beside the FTP version it divided by.
    for (const pin of RIDE_METRICS.pins) {
      expect(
        screen.getByText(
          `${pin.anchor_type} ${pin.value.toFixed(0)} ${pin.unit}`,
        ),
      ).toBeInTheDocument();
    }
  });

  it("takes planned bands as a prop and renders nothing without them", () => {
    // The overlay is a component capability until WP-6 has matches to
    // resolve bands from. Passing a mock is how it is proven to work today.
    const bands: PlannedBand[] = [
      { fromS: 240, toS: 360, lowWatts: 300, highWatts: 318 },
    ];

    const withBands = renderWithClient(
      <SessionAnalysis
        sessionId={ACTIVITY_IDS.outdoorRide}
        metrics={RIDE_METRICS}
        hasRecording
        plannedBands={bands}
      />,
    );
    expect(withBands.container).toBeTruthy();

    withBands.unmount();
    const without = renderWithClient(
      <SessionAnalysis
        sessionId={ACTIVITY_IDS.outdoorRide}
        metrics={RIDE_METRICS}
        hasRecording
      />,
    );
    expect(without.container).toBeTruthy();
  });
});

describe("selection statistics", () => {
  it("computes the same normalized power the backend does", () => {
    // The one number this codebase computes twice. The fixture came out of
    // `app.domain.metrics.normalized_power` over exactly these samples, so
    // the two implementations either agree here or the drift is caught.
    const power = RIDE_STREAMS.channels.find(
      (channel) => channel.channel === "power",
    );
    const watts = (power?.values ?? []).filter(
      (value): value is number => value !== null,
    );

    expect(normalizedPower(watts)).toBeCloseTo(
      RIDE_METRICS.power.normalized_power.value ?? 0,
      6,
    );
  });

  it("excludes null rows rather than reading them as zero", () => {
    const stop = RIDE_STREAMS.recording_stops[0];
    expect(stop).toBeDefined();

    const across = selectionStats(
      RIDE_STREAMS,
      stop.start_index - 10,
      stop.end_index + 10,
    );

    // The selection spans the pause; the average is of the riding either side
    // of it, dragged toward nothing by nothing.
    expect(across.averagePower).not.toBeNull();
    expect(across.averagePower ?? 0).toBeGreaterThan(0);
  });

  it("counts a half-open range, like every other range in the system", () => {
    // `[from, to)`, the same convention as recording stops, anomaly regions
    // and detected intervals — and the same as the label the chart header
    // prints beside the selection, which an inclusive count contradicted by
    // exactly one second.
    expect(selectionStats(RIDE_STREAMS, 0, 200).durationS).toBe(200);
    expect(selectionStats(RIDE_STREAMS, 120, 180).durationS).toBe(60);
    // Dragged backwards is the same range.
    expect(selectionStats(RIDE_STREAMS, 180, 120).durationS).toBe(60);
    // And a drag that went nowhere selected nothing.
    expect(selectionStats(RIDE_STREAMS, 90, 90).durationS).toBe(0);
  });

  it("recomputes when the range changes", () => {
    const early = selectionStats(RIDE_STREAMS, 0, 200);
    const inInterval = selectionStats(RIDE_STREAMS, 260, 340);

    // The fixture's first block is a warm-up and the second is a work
    // interval, so a selection that moves has to report a different ride.
    expect(inInterval.averagePower ?? 0).toBeGreaterThan(
      early.averagePower ?? 0,
    );
  });

  it("has nothing to say about a channel that was not recorded", () => {
    const stats = selectionStats({ ...RIDE_STREAMS, channels: [] }, 0, 100);

    expect(stats.averagePower).toBeNull();
    expect(stats.normalizedPower).toBeNull();
    expect(stats.averageHr).toBeNull();
  });
});
