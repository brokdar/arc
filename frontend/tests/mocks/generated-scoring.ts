// GENERATED FILE — do not hand-edit.
//
// Produced by `backend/scripts/emit_scoring_fixture.py` (`just
// scoring-fixture`) by running `app.domain.alignment.align` and
// `app.domain.scoring.score_session` over the VO₂ prescription
// `PLANNED_IDS.vo2` carries and the very stream `generated-metrics.ts` was
// computed from. Every axis value, every explanation, every criterion outcome
// and the suggested verdict with its rule are therefore the domain's own —
// which is what a hand-typed score cannot promise, since the suggestion is
// the output of a nine-row table read in order over numbers derived from the
// stream.
//
// Regenerate after changing app/domain/scoring.py, app/domain/alignment.py or
// the fixtures the script states its two sides against, and commit the result.

import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

/** The computed half of a score: what the mock stores, without its version. */
export type ScorePayload = Omit<
  Schemas["SessionScoreRead"],
  | "alignment_version_id"
  | "computed_at"
  | "intent_version"
  | "metrics_version_id"
  | "pinned_anchor_versions"
  | "planned_session_id"
  | "recompute_reason"
  | "version"
>;

/** The stored half of an alignment: the pairing, without its version. */
export type AlignmentPayload = Omit<
  Schemas["SessionAlignmentRead"],
  "computed_at" | "planned_session_id" | "recompute_reason" | "version"
>;

/** The FTP version the emitted score resolved its targets against. */
export const SCORED_FTP_VERSION_ID = "0199a000-0000-7000-8000-0000000000f1";

/**
 * The offsets the mock can answer for, in seconds.
 *
 * The offset control is functional (A7.1): sliding the planned timeline moves
 * which detected effort answers which prescribed step, so it moves the score.
 * A test may only send one of these — see `statedScoring`.
 */
export const SCORED_OFFSETS: readonly number[] = [-1200, 0, 600];

/**
 * Every score the mock API can answer with, keyed
 * `"<session_id>|<planned_session_id>|<offset_s>"`.
 *
 * The mock **states** its scores rather than computing them, for the reason
 * `MATCH_BREAKDOWNS` states its similarities: a mock that derived the answer
 * would be reimplementing the domain, and one that invented a number would be
 * asserting against a session no service could have scored. A key that is not
 * in here makes the handler throw — refusing to make one up is the point.
 */
export const SCORED_PAIRS: Readonly<
  Record<
    string,
    { readonly score: ScorePayload; readonly alignment: AlignmentPayload }
  >
> = {
  "0199a000-0000-7000-8000-000000000101|0199a000-0000-7000-8000-000000000501|-1200":
    {
      alignment: {
        offset_s: -1200,
        aligned: [
          {
            step_index: 3,
            interval_index: 0,
            confidence: 0.6443590622613782,
          },
          {
            step_index: 5,
            interval_index: 1,
            confidence: 0.6427236669289786,
          },
          {
            step_index: 9,
            interval_index: 3,
            confidence: 0.6430021198309832,
          },
        ],
        excluded: [
          {
            step_index: 7,
            interval_index: 2,
            confidence: 0.4247200177010503,
            reason: "alignment_low_confidence",
          },
        ],
        unmatched_steps: [1],
        unmatched_intervals: [],
      },
      score: {
        purpose: "vo2max",
        standalone: false,
        suggested_verdict: "abandoned",
        verdict_rule: "completion_below_floor",
        verdict_rationale:
          "only 33% of the prescription was completed, below the 50% floor",
        axes: [
          {
            axis: "completion",
            value: 0.3333333333333333,
            explanation: {
              formula:
                "completion = min(1, recording time / prescribed duration)",
              inputs: {
                "prescribed duration": "3420 s",
                "recording time": "1140 s",
                ratio: "0.333",
              },
              assumptions: [
                "recording time is the duration training load is computed over (A5.1), not elapsed time",
                "a ride longer than prescribed is 100 % complete, not more",
              ],
              citation: null,
            },
            not_assessed: null,
            criteria: [
              {
                index: 2,
                kind: "duration_floor",
                passed: false,
                observed: 1140.0,
                required: 3600.0,
                detail: "1140 s recorded against a floor of 3600 s",
                not_assessed: null,
              },
            ],
          },
          {
            axis: "adherence",
            value: 0.7805555555555556,
            explanation: {
              formula:
                "adherence = \u03a3(fraction in band \u00d7 seconds covered) / \u03a3(seconds covered), over the time-in-band criteria",
              inputs: {
                "criteria evaluated": "1",
                "seconds covered": "360",
                "seconds in band": "281",
                "seconds below band": "79",
                "seconds above band": "0",
              },
              assumptions: [
                "only work steps the alignment kept are scored; excluded (alignment_low_confidence) and unmatched steps are left out",
                "each band is compared through the trailing window frozen into the criterion, not one chosen by the scorer",
              ],
              citation: null,
            },
            not_assessed: null,
            criteria: [
              {
                index: 0,
                kind: "time_in_band",
                passed: true,
                observed: 0.7805555555555556,
                required: 0.75,
                detail:
                  "78% of 360 s inside 95%\u2013105% of the prescribed power, against a floor of 75%",
                not_assessed: null,
              },
            ],
          },
          {
            axis: "pacing",
            value: 1.0,
            explanation: {
              formula:
                "pacing = 1 while fade \u2264 5 %, falling to 0 at 25 % fade, where fade = 1 \u2212 NP(last rep) / NP(first rep)",
              inputs: {
                "repetitions aligned": "3",
                "NP of the first repetition": "301 W",
                "NP of the last repetition": "291 W",
                ratio: "0.967",
              },
              assumptions: [
                "a repetition ridden harder than the first is not penalised",
                "only repetitions the alignment kept are compared",
              ],
              citation:
                "Allen & Coggan, Training and Racing with a Power Meter",
            },
            not_assessed: null,
            criteria: [],
          },
        ],
        other_criteria: [
          {
            index: 1,
            kind: "ceiling",
            passed: true,
            observed: 0.0,
            required: 360.0,
            detail: "0 s above 178 hr, against an allowance of 360 s",
            not_assessed: null,
          },
        ],
      },
    },
  "0199a000-0000-7000-8000-000000000101|0199a000-0000-7000-8000-000000000501|0":
    {
      alignment: {
        offset_s: 0,
        aligned: [
          {
            step_index: 1,
            interval_index: 0,
            confidence: 0.6443590622613782,
          },
          {
            step_index: 3,
            interval_index: 1,
            confidence: 0.6427236669289786,
          },
          {
            step_index: 7,
            interval_index: 3,
            confidence: 0.6430021198309832,
          },
        ],
        excluded: [
          {
            step_index: 5,
            interval_index: 2,
            confidence: 0.4247200177010503,
            reason: "alignment_low_confidence",
          },
        ],
        unmatched_steps: [9],
        unmatched_intervals: [],
      },
      score: {
        purpose: "vo2max",
        standalone: false,
        suggested_verdict: "abandoned",
        verdict_rule: "completion_below_floor",
        verdict_rationale:
          "only 33% of the prescription was completed, below the 50% floor",
        axes: [
          {
            axis: "completion",
            value: 0.3333333333333333,
            explanation: {
              formula:
                "completion = min(1, recording time / prescribed duration)",
              inputs: {
                "prescribed duration": "3420 s",
                "recording time": "1140 s",
                ratio: "0.333",
              },
              assumptions: [
                "recording time is the duration training load is computed over (A5.1), not elapsed time",
                "a ride longer than prescribed is 100 % complete, not more",
              ],
              citation: null,
            },
            not_assessed: null,
            criteria: [
              {
                index: 2,
                kind: "duration_floor",
                passed: false,
                observed: 1140.0,
                required: 3600.0,
                detail: "1140 s recorded against a floor of 3600 s",
                not_assessed: null,
              },
            ],
          },
          {
            axis: "adherence",
            value: 0.7805555555555556,
            explanation: {
              formula:
                "adherence = \u03a3(fraction in band \u00d7 seconds covered) / \u03a3(seconds covered), over the time-in-band criteria",
              inputs: {
                "criteria evaluated": "1",
                "seconds covered": "360",
                "seconds in band": "281",
                "seconds below band": "79",
                "seconds above band": "0",
              },
              assumptions: [
                "only work steps the alignment kept are scored; excluded (alignment_low_confidence) and unmatched steps are left out",
                "each band is compared through the trailing window frozen into the criterion, not one chosen by the scorer",
              ],
              citation: null,
            },
            not_assessed: null,
            criteria: [
              {
                index: 0,
                kind: "time_in_band",
                passed: true,
                observed: 0.7805555555555556,
                required: 0.75,
                detail:
                  "78% of 360 s inside 95%\u2013105% of the prescribed power, against a floor of 75%",
                not_assessed: null,
              },
            ],
          },
          {
            axis: "pacing",
            value: 1.0,
            explanation: {
              formula:
                "pacing = 1 while fade \u2264 5 %, falling to 0 at 25 % fade, where fade = 1 \u2212 NP(last rep) / NP(first rep)",
              inputs: {
                "repetitions aligned": "3",
                "NP of the first repetition": "301 W",
                "NP of the last repetition": "291 W",
                ratio: "0.967",
              },
              assumptions: [
                "a repetition ridden harder than the first is not penalised",
                "only repetitions the alignment kept are compared",
              ],
              citation:
                "Allen & Coggan, Training and Racing with a Power Meter",
            },
            not_assessed: null,
            criteria: [],
          },
        ],
        other_criteria: [
          {
            index: 1,
            kind: "ceiling",
            passed: true,
            observed: 0.0,
            required: 360.0,
            detail: "0 s above 178 hr, against an allowance of 360 s",
            not_assessed: null,
          },
        ],
      },
    },
  "0199a000-0000-7000-8000-000000000101|0199a000-0000-7000-8000-000000000501|600":
    {
      alignment: {
        offset_s: 600,
        aligned: [
          {
            step_index: 1,
            interval_index: 0,
            confidence: 0.6443590622613782,
          },
          {
            step_index: 3,
            interval_index: 1,
            confidence: 0.6427236669289786,
          },
          {
            step_index: 7,
            interval_index: 3,
            confidence: 0.6430021198309832,
          },
        ],
        excluded: [
          {
            step_index: 5,
            interval_index: 2,
            confidence: 0.4247200177010503,
            reason: "alignment_low_confidence",
          },
        ],
        unmatched_steps: [9],
        unmatched_intervals: [],
      },
      score: {
        purpose: "vo2max",
        standalone: false,
        suggested_verdict: "abandoned",
        verdict_rule: "completion_below_floor",
        verdict_rationale:
          "only 33% of the prescription was completed, below the 50% floor",
        axes: [
          {
            axis: "completion",
            value: 0.3333333333333333,
            explanation: {
              formula:
                "completion = min(1, recording time / prescribed duration)",
              inputs: {
                "prescribed duration": "3420 s",
                "recording time": "1140 s",
                ratio: "0.333",
              },
              assumptions: [
                "recording time is the duration training load is computed over (A5.1), not elapsed time",
                "a ride longer than prescribed is 100 % complete, not more",
              ],
              citation: null,
            },
            not_assessed: null,
            criteria: [
              {
                index: 2,
                kind: "duration_floor",
                passed: false,
                observed: 1140.0,
                required: 3600.0,
                detail: "1140 s recorded against a floor of 3600 s",
                not_assessed: null,
              },
            ],
          },
          {
            axis: "adherence",
            value: 0.7805555555555556,
            explanation: {
              formula:
                "adherence = \u03a3(fraction in band \u00d7 seconds covered) / \u03a3(seconds covered), over the time-in-band criteria",
              inputs: {
                "criteria evaluated": "1",
                "seconds covered": "360",
                "seconds in band": "281",
                "seconds below band": "79",
                "seconds above band": "0",
              },
              assumptions: [
                "only work steps the alignment kept are scored; excluded (alignment_low_confidence) and unmatched steps are left out",
                "each band is compared through the trailing window frozen into the criterion, not one chosen by the scorer",
              ],
              citation: null,
            },
            not_assessed: null,
            criteria: [
              {
                index: 0,
                kind: "time_in_band",
                passed: true,
                observed: 0.7805555555555556,
                required: 0.75,
                detail:
                  "78% of 360 s inside 95%\u2013105% of the prescribed power, against a floor of 75%",
                not_assessed: null,
              },
            ],
          },
          {
            axis: "pacing",
            value: 1.0,
            explanation: {
              formula:
                "pacing = 1 while fade \u2264 5 %, falling to 0 at 25 % fade, where fade = 1 \u2212 NP(last rep) / NP(first rep)",
              inputs: {
                "repetitions aligned": "3",
                "NP of the first repetition": "301 W",
                "NP of the last repetition": "291 W",
                ratio: "0.967",
              },
              assumptions: [
                "a repetition ridden harder than the first is not penalised",
                "only repetitions the alignment kept are compared",
              ],
              citation:
                "Allen & Coggan, Training and Racing with a Power Meter",
            },
            not_assessed: null,
            criteria: [],
          },
        ],
        other_criteria: [
          {
            index: 1,
            kind: "ceiling",
            passed: true,
            observed: 0.0,
            required: 360.0,
            detail: "0 s above 178 hr, against an allowance of 360 s",
            not_assessed: null,
          },
        ],
      },
    },
};
