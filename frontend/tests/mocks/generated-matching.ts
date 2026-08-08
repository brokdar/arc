// GENERATED FILE — do not hand-edit.
//
// Produced by `backend/scripts/emit_matching_fixture.py` (`just
// matching-fixture`) by running `app.domain.matching.similarity` over the
// evidence the service would read off each pair of fixture rows. The scores,
// the renormalised weights and the sentences on the unassessed components are
// therefore the domain's own — which is exactly what a hand-typed breakdown
// cannot promise, since the weights on an assessed component depend on which
// other components were assessable.
//
// Regenerate after changing app/domain/matching.py or the fixtures the script
// states its evidence against, and commit the result.

import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

/**
 * Every similarity the mock API can answer with, keyed
 * `"<session_id>|<planned_session_id>"`.
 *
 * The mock **states** its similarities rather than computing them, for the
 * reason `LOCAL_DATES` in the handlers states its dates: a mock that derived
 * the answer would be reimplementing the domain, and one that invented a
 * number would be asserting against a link no service could create. A pair
 * that is not in here makes the handler throw — refusing to make one up is
 * the point.
 */
export const MATCH_BREAKDOWNS: Readonly<
  Record<string, Schemas["MatchBreakdownRead"]>
> = {
  //
  //   The seeded pending proposal: three sets logged against five
  //   prescribed. A strength prescription states no duration and shares no
  //   channel with a typed-in session, so two of the three components
  //   carry a reason instead of a number and the structure term is the
  //   whole score.
  "0199a000-0000-7000-8000-000000000103|0199a000-0000-7000-8000-000000000503": {
    score: 0.6,
    weights: {
      duration: 0.4,
      intensity: 0.3,
      structure: 0.3,
    },
    components: [
      {
        component: "structure",
        score: 0.6,
        weight: 1.0,
        nominal_weight: 0.3,
        planned: 5.0,
        actual: 3.0,
        basis: "sets",
      },
    ],
    not_assessed: [
      {
        component: "duration",
        nominal_weight: 0.4,
        reason: "the prescription states no duration to compare against",
      },
      {
        component: "intensity",
        nominal_weight: 0.3,
        reason:
          "no channel is prescribed and recorded on both sides: the prescription and the recording share neither power nor heart rate",
      },
    ],
  },
  //
  //   Where the proposal above is retargeted: four sets prescribed, three
  //   done.
  "0199a000-0000-7000-8000-000000000103|0199a000-0000-7000-8000-000000000504": {
    score: 0.75,
    weights: {
      duration: 0.4,
      intensity: 0.3,
      structure: 0.3,
    },
    components: [
      {
        component: "structure",
        score: 0.75,
        weight: 1.0,
        nominal_weight: 0.3,
        planned: 4.0,
        actual: 3.0,
        basis: "sets",
      },
    ],
    not_assessed: [
      {
        component: "duration",
        nominal_weight: 0.4,
        reason: "the prescription states no duration to compare against",
      },
      {
        component: "intensity",
        nominal_weight: 0.3,
        reason:
          "no channel is prescribed and recorded on both sides: the prescription and the recording share neither power nor heart rate",
      },
    ],
  },
  //
  //   The other seeded proposal, and the one breakdown with all three
  //   components assessed: the intervals were ridden almost exactly at the
  //   prescribed intensity and four of five were detected, but the ride
  //   ran two and a half hours where 57 minutes were planned — which is
  //   what pulls the whole score down into the band where arc asks instead
  //   of deciding.
  "0199a000-0000-7000-8000-000000000101|0199a000-0000-7000-8000-000000000501": {
    score: 0.6872382366750194,
    weights: {
      duration: 0.4,
      intensity: 0.3,
      structure: 0.3,
    },
    components: [
      {
        component: "duration",
        score: 0.3825503355704698,
        weight: 0.4,
        nominal_weight: 0.4,
        planned: 3420.0,
        actual: 8940.0,
        basis: null,
      },
      {
        component: "intensity",
        score: 0.9807270081561049,
        weight: 0.3,
        nominal_weight: 0.3,
        planned: 226.93018984103844,
        actual: 231.38976285327033,
        basis: "power",
      },
      {
        component: "structure",
        score: 0.8,
        weight: 0.3,
        nominal_weight: 0.3,
        planned: 5.0,
        actual: 4.0,
        basis: "intervals",
      },
    ],
    not_assessed: [],
  },
  //
  //   The swap target for the link above. A steady endurance ride is one
  //   work step, which is fewer than a structure hint can mean anything
  //   with, so that component is dropped and the other two are scaled up
  //   between them.
  "0199a000-0000-7000-8000-000000000101|0199a000-0000-7000-8000-000000000502": {
    score: 0.7497935047551993,
    weights: {
      duration: 0.4,
      intensity: 0.3,
      structure: 0.3,
    },
    components: [
      {
        component: "duration",
        score: 0.7842105263157895,
        weight: 0.5714285714285715,
        nominal_weight: 0.4,
        planned: 11400.0,
        actual: 8940.0,
        basis: null,
      },
      {
        component: "intensity",
        score: 0.7039041426744121,
        weight: 0.4285714285714286,
        nominal_weight: 0.3,
        planned: 162.8762126448668,
        actual: 231.38976285327033,
        basis: "power",
      },
    ],
    not_assessed: [
      {
        component: "structure",
        nominal_weight: 0.3,
        reason:
          "the prescription has 1 work unit(s), fewer than the 2 a structure hint needs to mean anything",
      },
    ],
  },
  //
  //   What re-running matching over the trainer ride finds: the threshold
  //   session planned for the same evening, an hour prescribed against the
  //   hour recorded. Nothing was computed over the file, so the duration
  //   is the only term — and it agrees well enough that the link is made
  //   without asking (and can still be undone). The prescription's watts
  //   are beside the point here: with no artefact there is no recorded
  //   intensity to compare them with, whatever they say.
  "0199a000-0000-7000-8000-000000000102|0199a000-0000-7000-8000-000000000505": {
    score: 0.9230769230769231,
    weights: {
      duration: 0.4,
      intensity: 0.3,
      structure: 0.3,
    },
    components: [
      {
        component: "duration",
        score: 0.9230769230769231,
        weight: 1.0,
        nominal_weight: 0.4,
        planned: 3900.0,
        actual: 3600.0,
        basis: null,
      },
    ],
    not_assessed: [
      {
        component: "intensity",
        nominal_weight: 0.3,
        reason:
          "no channel is prescribed and recorded on both sides: the prescription and the recording share neither power nor heart rate",
      },
      {
        component: "structure",
        nominal_weight: 0.3,
        reason:
          "the prescribed and performed work units are not both countable",
      },
    ],
  },
  //
  //   The displaced case: an hour on the trainer where three hours
  //   outdoors were planned. Nothing was computed over the file yet, so
  //   only the duration could be compared — and at 0.32 the machine
  //   proposes nothing. Linking it is the athlete saying 'I trained, and
  //   it was not this'.
  "0199a000-0000-7000-8000-000000000102|0199a000-0000-7000-8000-000000000502": {
    score: 0.3157894736842105,
    weights: {
      duration: 0.4,
      intensity: 0.3,
      structure: 0.3,
    },
    components: [
      {
        component: "duration",
        score: 0.3157894736842105,
        weight: 1.0,
        nominal_weight: 0.4,
        planned: 11400.0,
        actual: 3600.0,
        basis: null,
      },
    ],
    not_assessed: [
      {
        component: "intensity",
        nominal_weight: 0.3,
        reason:
          "no channel is prescribed and recorded on both sides: the prescription and the recording share neither power nor heart rate",
      },
      {
        component: "structure",
        nominal_weight: 0.3,
        reason:
          "the prescribed and performed work units are not both countable",
      },
    ],
  },
};
