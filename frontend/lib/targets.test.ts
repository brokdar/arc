import { describe, expect, it } from "vitest";

import type { components } from "@/generated/api/schema";
import {
  type ChannelBand,
  channelBands,
  channelLabel,
  describePrescribed,
  describeSpan,
  profileLegend,
  resolveBand,
} from "@/lib/targets";
import type { EnduranceStructure } from "@/lib/workout-profile";

type Schemas = components["schemas"];

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

  /**
   * The bug this pins: `85 % LTHR` and `75 % max HR` are both heart rate, and
   * unioning them produced "75–85 % of LTHR" — a band the plan never states,
   * attributing one step's percentage to the other step's anchor. Two anchors
   * are two bands, always.
   */
  it("never unions two anchors into one band", () => {
    const bands = channelBands({
      discipline: "cycling",
      steps: [
        {
          kind: "steady",
          role: "work",
          name: null,
          duration_s: 1200,
          distance_m: null,
          targets: {
            hr: {
              kind: "percent_of_anchor",
              anchor_type: "lthr",
              pct_low: 0.85,
              pct_high: 0.85,
            },
          },
        },
        {
          kind: "steady",
          role: "recovery",
          name: null,
          duration_s: 600,
          distance_m: null,
          targets: {
            hr: {
              kind: "percent_of_anchor",
              anchor_type: "max_hr",
              pct_low: 0.75,
              pct_high: 0.75,
            },
          },
        },
      ],
    });

    expect(bands.map((band) => band.anchorType)).toEqual(["lthr", "max_hr"]);
    expect(bands.map(describePrescribed)).toEqual([
      "85% of LTHR",
      "75% of max HR",
    ]);
  });

  it("keeps a percentage and an absolute range on one channel apart", () => {
    const bands = channelBands({
      discipline: "cycling",
      steps: [
        {
          kind: "steady",
          role: "work",
          name: null,
          duration_s: 600,
          distance_m: null,
          targets: {
            power: {
              kind: "percent_of_anchor",
              anchor_type: "ftp",
              pct_low: 0.9,
              pct_high: 1,
            },
          },
        },
        {
          kind: "steady",
          role: "work",
          name: null,
          duration_s: 600,
          distance_m: null,
          targets: {
            power: { kind: "absolute", unit: "W", low: 300, high: 320 },
          },
        },
      ],
    });

    expect(bands.map(describePrescribed)).toEqual([
      "90–100% of FTP",
      "300–320 W",
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

describe("describePrescribed", () => {
  it("says a percentage band the way the plan writes it", () => {
    expect(describePrescribed(band("power"))).toBe("50–76% of FTP");
  });

  it("leaves an absolute band alone", () => {
    expect(describePrescribed(band("hr"))).toBe("120–148 bpm");
  });

  it("collapses a band with one value", () => {
    expect(
      describePrescribed({
        channel: "power",
        mode: "absolute",
        anchorType: null,
        low: 200,
        high: 200,
      }),
    ).toBe("200 W");
  });
});

const FTP_VERSION = "0199a000-0000-7000-8000-0000000000f1";

const PINNED_FTP: Schemas["PinnedAnchorRead"] = {
  anchor_type: "ftp",
  anchor_version_id: FTP_VERSION,
  value: 250,
  unit: "W",
  provenance: "estimated",
  effective_date: "2026-06-01",
};

function step(
  index: number,
  targets: Schemas["ResolvedTargetRead"][],
): Schemas["ResolvedStepRead"] {
  return {
    index,
    role: "work",
    name: null,
    duration_s: 600,
    distance_m: null,
    is_ramp: false,
    start_targets: targets,
    end_targets: targets,
  };
}

describe("resolveBand", () => {
  const steps: Schemas["ResolvedStepRead"][] = [
    step(0, [
      {
        channel: "power",
        prescribed: "50–60 % FTP",
        resolved_low: 125,
        resolved_high: 150,
        unit: "W",
        anchor_version_id: FTP_VERSION,
      },
    ]),
    step(1, [
      {
        channel: "power",
        prescribed: "66–76 % FTP",
        resolved_low: 165,
        resolved_high: 190,
        unit: "W",
        anchor_version_id: FTP_VERSION,
      },
      {
        channel: "hr",
        prescribed: "120–148 bpm",
        resolved_low: 120,
        resolved_high: 148,
        unit: "bpm",
        anchor_version_id: null,
      },
    ]),
  ];

  it("spans the numbers the API already resolved against the pins", () => {
    const span = resolveBand(band("power"), [PINNED_FTP], steps);
    expect(span && describeSpan(span)).toBe("125–190 W");
  });

  it("passes an absolute band through, anchor or no anchor", () => {
    const span = resolveBand(band("hr"), [PINNED_FTP], steps);
    expect(span && describeSpan(span)).toBe("120–148 bpm");
  });

  it("resolves to nothing when the session pinned no such anchor", () => {
    expect(resolveBand(band("power"), [], steps)).toBeNull();
  });

  it("resolves to nothing when the steps carry no such channel", () => {
    expect(resolveBand(band("cadence"), [PINNED_FTP], steps)).toBeNull();
  });
});

describe("profileLegend", () => {
  it("gives one row per band the plot actually draws, in intensity order", () => {
    const legend = profileLegend(RIDE);

    const [first] = legend;
    expect(legend.map((entry) => entry.zoneLabel)).toEqual(["Z2"]);
    expect(first && describePrescribed(first)).toBe("50–76% of FTP");
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
