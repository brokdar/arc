import { render } from "@testing-library/react";
import { act } from "react";
import { describe, expect, it } from "vitest";

import { StreamCharts } from "@/components/sessions/stream-charts";
import { RIDE_STREAMS } from "@/tests/mocks/fixtures";

/** Node **identity**, not shape: two fresh canvases are deeply equal. */
function canvases(container: HTMLElement): HTMLCanvasElement[] {
  return [...container.querySelectorAll("canvas")];
}

function expectSameNodes(
  after: readonly HTMLCanvasElement[],
  before: readonly HTMLCanvasElement[],
): void {
  expect(after).toHaveLength(before.length);
  for (const [index, node] of after.entries()) {
    expect(node).toBe(before[index]);
  }
}

/**
 * The one thing a component test can prove about a canvas: it is not being
 * rebuilt.
 *
 * uPlot owns its canvas, and creating one over 14 400 points is expensive.
 * Worse, destroying one mid-gesture throws away the zoom and the drag the
 * athlete is performing. So the panels must be built **once per data
 * identity** — and the way that regresses is somebody deriving a prop inline
 * at the call site (`repairs.filter(...)`), which mints a new array on every
 * render and lands in the create-effect's dependency list.
 *
 * A re-render with identical props is exactly that scenario, minus the mouse:
 * if the effect re-runs, uPlot is destroyed and a *new* canvas element takes
 * the old one's place, and the node identity check below fails.
 */
describe("the stream charts", () => {
  it("keeps its canvases across a re-render with the same data", () => {
    const { container, rerender } = render(
      <StreamCharts streams={RIDE_STREAMS} ftpWatts={262} />,
    );
    const before = canvases(container);
    expect(before.length).toBeGreaterThan(1);

    rerender(<StreamCharts streams={RIDE_STREAMS} ftpWatts={262} />);
    rerender(<StreamCharts streams={RIDE_STREAMS} ftpWatts={262} />);

    // Same nodes, not merely the same count: a rebuild replaces them.
    expectSameNodes(canvases(container), before);
  });

  it("keeps them while the cursor moves", () => {
    const { container } = render(<StreamCharts streams={RIDE_STREAMS} />);
    const before = canvases(container);

    // uPlot publishes the cursor through a mousemove on its own overlay; the
    // charts route that into a live value rather than component state, so
    // this must not re-render — let alone rebuild — the panels.
    const over = container.querySelector(".u-over");
    expect(over).not.toBeNull();
    for (const clientX of [40, 80, 120, 160]) {
      act(() => {
        over?.dispatchEvent(
          new MouseEvent("mousemove", { bubbles: true, clientX, clientY: 20 }),
        );
      });
    }

    expectSameNodes(canvases(container), before);
  });

  it("rebuilds when the samples it is plotting change", () => {
    // The other half of the contract: a genuinely different ride *must*
    // replace the canvas, or the page would go on showing the previous one.
    const { container, rerender } = render(
      <StreamCharts streams={RIDE_STREAMS} />,
    );
    const before = canvases(container);

    rerender(
      <StreamCharts
        streams={{
          ...RIDE_STREAMS,
          recording_id: "other",
          anomalies: [],
          channels: RIDE_STREAMS.channels.map((channel) => ({
            ...channel,
            values: channel.values.map((value) =>
              value === null ? null : value + 1,
            ),
          })),
        }}
      />,
    );

    expect(canvases(container)[0]).not.toBe(before[0]);
  });

  it("draws only the channels the recording carried", () => {
    const { container } = render(
      <StreamCharts
        streams={{
          ...RIDE_STREAMS,
          channels: RIDE_STREAMS.channels.filter(
            (channel) => channel.channel === "power",
          ),
        }}
      />,
    );

    expect(container.querySelectorAll("canvas")).toHaveLength(1);
  });
});
