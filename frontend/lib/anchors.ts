/**
 * The anchor vocabulary the settings page writes with.
 *
 * `lib/targets.ts` names an anchor for a *prescription* (`anchorLabel`); this
 * module is about the other half — what an anchor is for, which of them the
 * athlete may append, and which zone model derives from which. It is
 * deliberately thin: nothing here computes a zone or decides which version is
 * in force, because both are domain rules the API already owns
 * (`app.domain.zones`, `app.domain.anchors.anchor_as_of`) and a second
 * implementation in the client is a second answer.
 */

import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];
export type AnchorType = Schemas["AnchorType"];
export type AnchorVersion = Schemas["AnchorVersionRead"];
export type Provenance = Schemas["Provenance"];
export type ZoneModel = Schemas["ZoneModel"];

/**
 * The anchor types the API accepts an append for.
 *
 * Taken from the create schema's own literal, not restated: `cp` and `w_prime`
 * are reserved (WP-5), and a form offering them would collect values the
 * service refuses. When the backend widens that literal, the table below stops
 * type-checking until it grows a row.
 */
export type WritableAnchorType = Schemas["AnchorVersionCreate"]["anchor_type"];

interface AnchorCopy {
  /** The unit the API will stamp on the version — shown as a field hint. */
  readonly unit: string;
  /** What the number is for, in the words the athlete needs to answer it. */
  readonly what: string;
}

/**
 * What each writable anchor is for, in the order the page lists them.
 *
 * A `Record` rather than an array, so a new writable anchor type is a
 * type error here rather than a slot silently missing from the page.
 *
 * The unit is a *hint*, not an assertion: the form never sends one, the API
 * stamps the anchor type's own unit (`app.domain.anchors.ANCHOR_UNITS`), and
 * every unit rendered elsewhere on the page comes off the version that was
 * returned.
 */
export const ANCHOR_COPY: Readonly<Record<WritableAnchorType, AnchorCopy>> = {
  ftp: {
    unit: "W",
    what: "Functional threshold power. Power zones and every %FTP target resolve against it.",
  },
  lthr: {
    unit: "bpm",
    what: "Lactate threshold heart rate. The heart-rate zones derive from it.",
  },
  max_hr: {
    unit: "bpm",
    what: "The highest heart rate you have actually seen — the ceiling of the reserve.",
  },
  resting_hr: {
    unit: "bpm",
    what: "Resting heart rate. With max HR it defines the reserve that heart-rate load is measured on.",
  },
};

/** The writable anchors, in the order the page lists them. */
export const WRITABLE_ANCHOR_TYPES = Object.keys(
  ANCHOR_COPY,
) as WritableAnchorType[];

/**
 * How each provenance is offered in the append form.
 *
 * Ordered strongest first, because the option list is also a ranking: the
 * backend orders the same four weakest-to-strongest and compares them when
 * deciding whether a value should displace another (`Provenance`).
 */
export const PROVENANCE_OPTIONS: readonly {
  readonly value: Provenance;
  readonly label: string;
}[] = [
  { value: "tested", label: "Tested — measured in a protocol" },
  { value: "estimated", label: "Estimated — computed from something else" },
  { value: "athlete_reported", label: "Athlete-reported — I know this number" },
  { value: "assumed", label: "Assumed — a placeholder until it is measured" },
];

/** The zone models the page previews, and the anchor each derives from. */
export const ZONE_PREVIEWS: readonly {
  readonly anchorType: WritableAnchorType;
  readonly model: ZoneModel;
  readonly heading: string;
}[] = [
  { anchorType: "ftp", model: "coggan_7", heading: "Power zones" },
  { anchorType: "lthr", model: "lthr_5", heading: "Heart-rate zones" },
];

/** How a zone model is written when the page names the one it used. */
export const ZONE_MODEL_LABELS: Readonly<Record<ZoneModel, string>> = {
  coggan_7: "Coggan 7 · %FTP",
  lthr_5: "5-zone · %LTHR",
};

/** Every cached page of anchor history, whatever filter it was fetched with. */
export const ANCHORS_QUERY_PREFIX = ["get", "/api/v1/anchors"] as const;

/** Every cached "which version is in force", one per anchor type. */
export const CURRENT_ANCHOR_QUERY_PREFIX = [
  "get",
  "/api/v1/anchors/current",
] as const;

/** Every cached zone table — all of them derived from an anchor in force. */
export const ZONES_QUERY_PREFIX = ["get", "/api/v1/zones"] as const;
