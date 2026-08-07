import { describe, expect, it } from "vitest";

import {
  type ChannelBand,
  channelBands,
  channelLabel,
  describeBand,
  describeBandSource,
  profileLegend,
} from "@/lib/targets";
import type { EnduranceStructure } from "@/lib/workout-profile";

const RIDE: EnduranceStructure = {
  discipline: "cycling",
  steps: [
    {
      kind: "steady",
      role: "warmup",
      name: null,
      duration_s: 900,
      distance_m: null,
      targets: {
        power: {
          kind: "percent_of_anchor",
          anchor_type: "ftp",
          pct_low: 0.5,
          pct_high: 0.6,
        },
      },
    },
    {
      kind: "steady",
      role: "work",
      name: null,
      duration_s: 5400,
      distance_m: null,
      targets: {
        power: {
          kind: "percent_of_anchor",
          anchor_type: "ftp",
          pct_low: 0.66,
          pct_high: 0.76,
        },
        hr: { kind: "absolute", unit: "bpm", low: 120, high: 148 },
        cadence: { kind: "absolute", unit: "rpm", low: 85, high: 95 },
      },
    },
  ],
};

describe("channelBands", () => {
  it("spans the widest band each channel is prescribed anywhere", () => {
    expect(channelBands(RIDE)).toEqual([
      {
        channel: "power",
        mode: "percent",
        anchorType: "ftp",
        low: 0.5,
        high: 0.76,
      },
      {
        channel: "hr",
        mode: "absolute",
        anchorType: null,
        low: 120,
        high: 148,
      },
      {
        channel: "cadence",
        mode: "absolute",
        anchorType: null,
        low: 85,
        high: 95,
      },
    ]);
  });

  it("has nothing to say about a strength prescription", () => {
    expect(channelBands({ discipline: "strength", groups: [] })).toEqual([]);
  });
});

/** One channel's band out of the fold, or a failure that names the channel. */
function band(channel: "power" | "hr" | "cadence"): ChannelBand {
  const found = channelBands(RIDE).find((entry) => entry.channel === channel);
  if (!found) {
    throw new Error(`the fixture prescribes no ${channel}`);
  }
  return found;
}

describe("describeBand", () => {
  const power = band("power");
  const hr = band("hr");

  it("resolves a percentage against the anchor in force", () => {
    expect(describeBand(power, { ftp: 250 })).toBe("125–190 W");
    expect(describeBandSource(power, { ftp: 250 })).toBe("50–76% of FTP");
  });

  it("stays in percentages when nobody has entered the anchor", () => {
    expect(describeBand(power, {})).toBe("50–76% of FTP");
    expect(describeBandSource(power, {})).toBeNull();
  });

  it("leaves an absolute band alone", () => {
    expect(describeBand(hr, { ftp: 250 })).toBe("120–148 bpm");
  });

  it("collapses a band with one value", () => {
    expect(
      describeBand(
        {
          channel: "power",
          mode: "absolute",
          anchorType: null,
          low: 200,
          high: 200,
        },
        {},
      ),
    ).toBe("200 W");
  });
});

describe("profileLegend", () => {
  it("gives one row per band the plot actually draws, in intensity order", () => {
    const legend = profileLegend(RIDE);

    const [first] = legend;
    expect(legend.map((entry) => entry.zoneLabel)).toEqual(["Z2"]);
    expect(first && describeBand(first, { ftp: 250 })).toBe("125–190 W");
  });

  it("splits the bands of an interval session by zone", () => {
    const legend = profileLegend({
      discipline: "cycling",
      steps: [
        {
          kind: "repeat",
          times: 4,
          children: [
            {
              kind: "steady",
              role: "work",
              name: null,
              duration_s: 240,
              distance_m: null,
              targets: {
                power: {
                  kind: "percent_of_anchor",
                  anchor_type: "ftp",
                  pct_low: 1.14,
                  pct_high: 1.2,
                },
              },
            },
            {
              kind: "steady",
              role: "rest",
              name: null,
              duration_s: 180,
              distance_m: null,
              targets: {
                power: {
                  kind: "percent_of_anchor",
                  anchor_type: "ftp",
                  pct_low: 0.4,
                  pct_high: 0.5,
                },
              },
            },
          ],
        },
      ],
    });

    expect(legend.map((entry) => entry.zoneLabel)).toEqual(["Z1", "Z5"]);
  });
});

describe("channelLabel", () => {
  it("names the channels for the panel's left column", () => {
    expect(channelLabel("hr")).toBe("Heart rate");
  });
});
