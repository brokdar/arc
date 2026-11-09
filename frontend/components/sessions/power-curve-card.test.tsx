import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { PowerCurveCard } from "@/components/sessions/power-curve-card";
import { SessionDetail } from "@/components/sessions/session-detail";
import { AthleteClock } from "@/lib/clock";
import {
  ACTIVITY_IDS,
  ATHLETE_TIMEZONE,
  ingestState,
} from "@/tests/mocks/fixtures";
import {
  LONG_RIDE_POWER_CURVE,
  NO_POWER_CURVE,
  RIDE_POWER_CURVE,
} from "@/tests/mocks/generated-power-curve";
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

function withClient(node: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>,
  );
}

type Curve = typeof LONG_RIDE_POWER_CURVE;

/** The fixture's watts at one duration, as the readout prints them. */
function watts(curve: Curve, durationS: number): string {
  const point = curve.points.find((one) => one.duration_s === durationS);
  if (!point) {
    throw new Error(`the fixture has no ${durationS} s point`);
  }
  return `${Math.round(point.watts)} W`;
}

/** The value rendered under one readout label. */
function readout(label: string): HTMLElement {
  const card = screen.getByRole("region", { name: "Power curve" });
  const term = within(card)
    .getAllByText(label)
    .find((node) => node.closest("dt"));
  const value = term?.closest("dt")?.nextElementSibling;
  if (!value) {
    throw new Error(`no readout labelled ${label}`);
  }
  return value as HTMLElement;
}

/** Serve the two-hour fixture for the outdoor ride, honouring the id. */
function serveLongRide(): void {
  server.use(
    http.get(
      "/api/v1/sessions/{session_id}/power-curve",
      ({ params, response }) =>
        params.session_id === ACTIVITY_IDS.outdoorRide
          ? response(200).json(LONG_RIDE_POWER_CURVE)
          : response(404).json({
              detail: `Session ${params.session_id} not found`,
            }),
    ),
  );
}

const READOUT: readonly [string, number][] = [
  ["5 s", 5],
  ["1 min", 60],
  ["5 min", 300],
  ["20 min", 1200],
  ["60 min", 3600],
];

describe("the power curve card", () => {
  it("is on the session page", async () => {
    // Under the athlete's clock, as the app shell provides it: the coach
    // notes further down the page render times on it.
    withClient(
      <AthleteClock timezone={ATHLETE_TIMEZONE}>
        <SessionDetail sessionId={ACTIVITY_IDS.outdoorRide} />
      </AthleteClock>,
    );

    const card = await screen.findByRole("region", { name: "Power curve" });

    expect(
      await within(card).findByRole("img", { name: /^Power curve:/ }),
    ).toBeInTheDocument();
  });

  it("reads out the five durations from the computed fixture", async () => {
    serveLongRide();
    withClient(<PowerCurveCard sessionId={ACTIVITY_IDS.outdoorRide} />);

    await screen.findByRole("img", { name: /^Power curve:/ });

    for (const [label, duration] of READOUT) {
      expect(readout(label)).toHaveTextContent(
        watts(LONG_RIDE_POWER_CURVE, duration),
      );
    }
  });

  it("draws the curve on a logarithmic duration axis", async () => {
    serveLongRide();
    const { container } = withClient(
      <PowerCurveCard sessionId={ACTIVITY_IDS.outdoorRide} />,
    );

    await screen.findByRole("img", { name: /^Power curve:/ });

    const plot = container.querySelector("[data-slot='power-curve-plot']");
    expect(plot).toHaveAttribute("data-x-scale", "log");
    expect(plot?.querySelector("canvas")).not.toBeNull();
  });

  it("gives the chart a text alternative listing the readout", async () => {
    serveLongRide();
    withClient(<PowerCurveCard sessionId={ACTIVITY_IDS.outdoorRide} />);

    const chart = await screen.findByRole("img", { name: /^Power curve:/ });

    const name = chart.getAttribute("aria-label") ?? "";
    for (const [label, duration] of READOUT) {
      expect(name).toContain(
        `${label} ${watts(LONG_RIDE_POWER_CURVE, duration)}`,
      );
    }
  });

  it("leaves out durations longer than the ride rather than dashing them", async () => {
    // The default ride is twenty minutes long: it has a 20 min best and no
    // hour, and the hour's slot is absent rather than a placeholder.
    withClient(<PowerCurveCard sessionId={ACTIVITY_IDS.outdoorRide} />);

    const chart = await screen.findByRole("img", { name: /^Power curve:/ });
    const card = screen.getByRole("region", { name: "Power curve" });

    expect(readout("20 min")).toHaveTextContent(watts(RIDE_POWER_CURVE, 1200));
    expect(within(card).queryByText("60 min")).not.toBeInTheDocument();
    expect(chart.getAttribute("aria-label")).not.toContain("60 min");
    expect(
      card.querySelector("[data-slot='not-assessed']"),
    ).not.toBeInTheDocument();
  });

  it("shows why a session without power has no curve", async () => {
    // A recording with no power channel: the handler answers the way the
    // service does, from the recording's own channel list.
    const ride = ingestState().sessions.find(
      (row) => row.id === ACTIVITY_IDS.outdoorRide,
    );
    for (const recording of ride?.recordings ?? []) {
      recording.channels = recording.channels.filter(
        (channel) => channel !== "power",
      );
    }
    withClient(<PowerCurveCard sessionId={ACTIVITY_IDS.outdoorRide} />);

    const placeholder = await screen.findByRole("img", {
      name: `Not assessed: ${NO_POWER_CURVE.reason}`,
    });

    expect(placeholder).toHaveAttribute("data-slot", "not-assessed");
    expect(screen.getByText(NO_POWER_CURVE.reason)).toBeInTheDocument();
    expect(screen.queryByText(/\d+ W/)).not.toBeInTheDocument();
  });
});
