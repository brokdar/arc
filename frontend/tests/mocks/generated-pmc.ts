// GENERATED FILE — do not hand-edit.
//
// Produced by `backend/scripts/emit_pmc_fixture.py` (`just pmc-fixture`) by
// running `app.domain.pmc.fitness_trend` over a stored forty-day history and
// projecting the result through `app.api.routes.fitness.to_fitness_read` — the
// same function the endpoint uses. Every CTL, ATL, form value and maturity
// verdict below is therefore the domain's own, which a hand-typed fixture
// cannot promise.
//
// Regenerate after changing app/domain/pmc.py or the fitness read shape, and
// commit the result.

import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

/**
 * Forty days of training read on its last day: CTL maturing (day 40 of 97),
 * ATL mature, rest days decaying, and one `load_incomplete` day where a gym
 * session could not be priced.
 *
 * The dates end on a fixed day so the file is stable in git;
 * `tests/mocks/handlers-fitness.ts` rebases the whole document onto the
 * athlete's today, which moves the calendar and none of the numbers.
 */
export const FITNESS_TREND: Schemas["FitnessTrendRead"] = {
  explanations: {
    atl: {
      assumptions: [
        "every discipline feeds fatigue; strength through its heart-rate load, which is a low-trust estimate",
        "a day with no session is a load of 0 and the average decays",
        "a session neither load model could price is left out and its day is marked load_incomplete",
        "maturing until the seed's weight (1 − α)ⁿ falls to 10%: day 17",
      ],
      citation:
        "Banister EW et al. (1975), A systems model of training for athletic performance; Coggan's performance-management chart",
      display_only: true,
      formula: "ATL_d = ATL_{d−1} + α·(load_d − ATL_{d−1}), α = 1 − e^(−1/7)",
      inputs: {
        seed: "0",
        α: "0.133122",
        τ: "7 days",
      },
    },
    ctl: {
      assumptions: [
        "only cycling load feeds fitness",
        "a day with no session is a load of 0 and the average decays",
        "a session neither load model could price is left out and its day is marked load_incomplete",
        "maturing until the seed's weight (1 − α)ⁿ falls to 10%: day 97",
      ],
      citation:
        "Banister EW et al. (1975), A systems model of training for athletic performance; Coggan's performance-management chart",
      display_only: true,
      formula:
        "CTL_d = CTL_{d−1} + α·(cycling load_d − CTL_{d−1}), α = 1 − e^(−1/42)",
      inputs: {
        seed: "0",
        α: "0.023528",
        τ: "42 days",
      },
    },
    tsb: {
      assumptions: [
        "the lag is a declared parameter: TrainingPeaks reads yesterday's balance (L = 1), and intervals.icu's convention is unconfirmed",
      ],
      citation:
        "Banister EW et al. (1975), A systems model of training for athletic performance; Coggan's performance-management chart",
      display_only: true,
      formula: "TSB_d = CTL_{d−L} − ATL_{d−L}",
      inputs: {
        L: "0 days",
      },
    },
  },
  form_lag_days: 0,
  maturity: {
    as_of: "2026-08-15",
    atl: {
      display_only: true,
      mature: true,
      matures_on: "2026-07-23",
      matures_on_day: 17,
      seeded: false,
    },
    ctl: {
      display_only: true,
      mature: false,
      matures_on: "2026-10-11",
      matures_on_day: 97,
      seeded: false,
    },
    day: 40,
    first_session: "2026-07-07",
  },
  points: [
    {
      atl: {
        display_only: true,
        value: 13.578454225481478,
      },
      ctl: {
        display_only: true,
        value: 1.694038561038485,
      },
      day: 1,
      fitness_load: 72.0,
      load: 102.0,
      load_incomplete: false,
      local_date: "2026-07-07",
      n: 2,
      tsb: {
        display_only: true,
        value: -11.884415664442994,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 19.19907507477923,
      },
      ctl: {
        display_only: true,
        value: 2.9670605757560145,
      },
      day: 2,
      fitness_load: 55.8,
      load: 55.8,
      load_incomplete: false,
      local_date: "2026-07-08",
      n: 1,
      tsb: {
        display_only: true,
        value: -16.232014499023215,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 32.56465706784896,
      },
      ctl: {
        display_only: true,
        value: 5.005387520766854,
      },
      day: 3,
      fitness_load: 89.6,
      load: 119.6,
      load_incomplete: false,
      local_date: "2026-07-09",
      n: 2,
      tsb: {
        display_only: true,
        value: -27.55926954708211,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 28.229581525061814,
      },
      ctl: {
        display_only: true,
        value: 4.8876191947513,
      },
      day: 4,
      fitness_load: 0.0,
      load: 0.0,
      load_incomplete: false,
      local_date: "2026-07-10",
      n: 0,
      tsb: {
        display_only: true,
        value: -23.341962330310515,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 44.2002956002952,
      },
      ctl: {
        display_only: true,
        value: 8.25951779695023,
      },
      day: 5,
      fitness_load: 148.2,
      load: 148.2,
      load_incomplete: false,
      local_date: "2026-07-11",
      n: 1,
      tsb: {
        display_only: true,
        value: -35.94077780334497,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 51.62846944330293,
      },
      ctl: {
        display_only: true,
        value: 10.418016608897885,
      },
      day: 6,
      fitness_load: 100.0,
      load: 100.0,
      load_incomplete: false,
      local_date: "2026-07-12",
      n: 1,
      tsb: {
        display_only: true,
        value: -41.21045283440505,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 44.755579158326874,
      },
      ctl: {
        display_only: true,
        value: 10.172898249661602,
      },
      day: 7,
      fitness_load: 0.0,
      load: 0.0,
      load_incomplete: false,
      local_date: "2026-07-13",
      n: 0,
      tsb: {
        display_only: true,
        value: -34.582680908665274,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 53.12156044975386,
      },
      ctl: {
        display_only: true,
        value: 11.75934422777464,
      },
      day: 8,
      fitness_load: 77.6,
      load: 107.6,
      load_incomplete: false,
      local_date: "2026-07-14",
      n: 2,
      tsb: {
        display_only: true,
        value: -41.36221622197922,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 54.223603709473785,
      },
      ctl: {
        display_only: true,
        value: 12.927305131571687,
      },
      day: 9,
      fitness_load: 61.4,
      load: 61.4,
      load_incomplete: false,
      local_date: "2026-07-15",
      n: 1,
      tsb: {
        display_only: true,
        value: -41.2962985779021,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 63.672130651832056,
      },
      ctl: {
        display_only: true,
        value: 14.863042876400446,
      },
      day: 10,
      fitness_load: 95.2,
      load: 125.2,
      load_incomplete: false,
      local_date: "2026-07-16",
      n: 2,
      tsb: {
        display_only: true,
        value: -48.80908777543161,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 55.195962892079336,
      },
      ctl: {
        display_only: true,
        value: 14.513340546303352,
      },
      day: 11,
      fitness_load: 0.0,
      load: 0.0,
      load_incomplete: false,
      local_date: "2026-07-17",
      n: 0,
      tsb: {
        display_only: true,
        value: -40.68262234577598,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 68.32233940499677,
      },
      ctl: {
        display_only: true,
        value: 17.79052071509221,
      },
      day: 12,
      fitness_load: 153.8,
      load: 153.8,
      load_incomplete: false,
      local_date: "2026-07-18",
      n: 1,
      tsb: {
        display_only: true,
        value: -50.531818689904554,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 73.28481987580349,
      },
      ctl: {
        display_only: true,
        value: 19.856529658610874,
      },
      day: 13,
      fitness_load: 105.6,
      load: 105.6,
      load_incomplete: false,
      local_date: "2026-07-19",
      n: 1,
      tsb: {
        display_only: true,
        value: -53.42829021719262,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 63.528990737506895,
      },
      ctl: {
        display_only: true,
        value: 19.389339006804054,
      },
      day: 14,
      fitness_load: 0.0,
      load: 0.0,
      load_incomplete: false,
      local_date: "2026-07-20",
      n: 0,
      tsb: {
        display_only: true,
        value: -44.13965173070284,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 70.14129981205816,
      },
      ctl: {
        display_only: true,
        value: 20.890696233579447,
      },
      day: 15,
      fitness_load: 83.2,
      load: 113.2,
      load_incomplete: false,
      local_date: "2026-07-21",
      n: 2,
      tsb: {
        display_only: true,
        value: -49.25060357847872,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 69.72312338356262,
      },
      ctl: {
        display_only: true,
        value: 21.97557038084269,
      },
      day: 16,
      fitness_load: 67.0,
      load: 67.0,
      load_incomplete: false,
      local_date: "2026-07-22",
      n: 1,
      tsb: {
        display_only: true,
        value: -47.74755300271993,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 77.85380547544179,
      },
      ctl: {
        display_only: true,
        value: 23.83017626038042,
      },
      day: 17,
      fitness_load: 100.8,
      load: 130.8,
      load_incomplete: false,
      local_date: "2026-07-23",
      n: 2,
      tsb: {
        display_only: true,
        value: -54.02362921506137,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 67.48974337811016,
      },
      ctl: {
        display_only: true,
        value: 23.269492406193915,
      },
      day: 18,
      fitness_load: 0.0,
      load: 0.0,
      load_incomplete: false,
      local_date: "2026-07-24",
      n: 0,
      tsb: {
        display_only: true,
        value: -44.22025097191624,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 79.72502977411592,
      },
      ctl: {
        display_only: true,
        value: 26.472413645050164,
      },
      day: 19,
      fitness_load: 159.4,
      load: 159.4,
      load_incomplete: false,
      local_date: "2026-07-25",
      n: 1,
      tsb: {
        display_only: true,
        value: -53.25261612906576,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 83.91504391588612,
      },
      ctl: {
        display_only: true,
        value: 28.46591084600854,
      },
      day: 20,
      fitness_load: 111.2,
      load: 111.2,
      load_incomplete: false,
      local_date: "2026-07-26",
      n: 1,
      tsb: {
        display_only: true,
        value: -55.449133069877576,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 72.7440970272476,
      },
      ctl: {
        display_only: true,
        value: 27.796155975894344,
      },
      day: 21,
      fitness_load: 0.0,
      load: 0.0,
      load_incomplete: false,
      local_date: "2026-07-27",
      n: 0,
      tsb: {
        display_only: true,
        value: -44.94794105135326,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 78.87515555988226,
      },
      ctl: {
        display_only: true,
        value: 29.231473533511178,
      },
      day: 22,
      fitness_load: 88.8,
      load: 118.8,
      load_incomplete: false,
      local_date: "2026-07-28",
      n: 2,
      tsb: {
        display_only: true,
        value: -49.64368202637108,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 78.03979367235641,
      },
      ctl: {
        display_only: true,
        value: 30.251861813645206,
      },
      day: 23,
      fitness_load: 72.6,
      load: 72.6,
      load_incomplete: false,
      local_date: "2026-07-29",
      n: 1,
      tsb: {
        display_only: true,
        value: -47.787931858711204,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 81.81516390221051,
      },
      ctl: {
        display_only: true,
        value: 32.04349906974204,
      },
      day: 24,
      fitness_load: 106.4,
      load: 106.4,
      load_incomplete: true,
      local_date: "2026-07-30",
      n: 1,
      tsb: {
        display_only: true,
        value: -49.771664832468474,
      },
      uncounted: 1,
    },
    {
      atl: {
        display_only: true,
        value: 70.92375745126512,
      },
      ctl: {
        display_only: true,
        value: 31.289569582870598,
      },
      day: 25,
      fitness_load: 0.0,
      load: 0.0,
      load_incomplete: false,
      local_date: "2026-07-31",
      n: 0,
      tsb: {
        display_only: true,
        value: -39.63418786839452,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 83.44738444296404,
      },
      ctl: {
        display_only: true,
        value: 34.435550487588245,
      },
      day: 26,
      fitness_load: 165.0,
      load: 165.0,
      load_incomplete: false,
      local_date: "2026-08-01",
      n: 1,
      tsb: {
        display_only: true,
        value: -49.01183395537579,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 87.88735467474143,
      },
      ctl: {
        display_only: true,
        value: 36.37344706443176,
      },
      day: 27,
      fitness_load: 116.8,
      load: 116.8,
      load_incomplete: false,
      local_date: "2026-08-02",
      n: 1,
      tsb: {
        display_only: true,
        value: -51.51390761030967,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 76.18760543503916,
      },
      ctl: {
        display_only: true,
        value: 35.517641204361766,
      },
      day: 28,
      fitness_load: 0.0,
      load: 0.0,
      load_incomplete: false,
      local_date: "2026-08-03",
      n: 0,
      tsb: {
        display_only: true,
        value: -40.669964230677394,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 82.60574065759968,
      },
      ctl: {
        display_only: true,
        value: 36.903043792760585,
      },
      day: 29,
      fitness_load: 94.4,
      load: 124.4,
      load_incomplete: false,
      local_date: "2026-08-04",
      n: 2,
      tsb: {
        display_only: true,
        value: -45.7026968648391,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 82.019239208104,
      },
      ctl: {
        display_only: true,
        value: 37.8746915187131,
      },
      day: 30,
      fitness_load: 78.2,
      load: 78.2,
      load_incomplete: false,
      local_date: "2026-08-05",
      n: 1,
      tsb: {
        display_only: true,
        value: -44.1445476893909,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 90.00400405930316,
      },
      ctl: {
        display_only: true,
        value: 39.61873500365995,
      },
      day: 31,
      fitness_load: 112.0,
      load: 142.0,
      load_incomplete: false,
      local_date: "2026-08-06",
      n: 2,
      tsb: {
        display_only: true,
        value: -50.38526905564321,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 78.02248200803554,
      },
      ctl: {
        display_only: true,
        value: 38.6865729920521,
      },
      day: 32,
      fitness_load: 0.0,
      load: 0.0,
      load_incomplete: false,
      local_date: "2026-08-07",
      n: 0,
      tsb: {
        display_only: true,
        value: -39.33590901598344,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 90.3465956390412,
      },
      ctl: {
        display_only: true,
        value: 41.790273437471534,
      },
      day: 33,
      fitness_load: 170.6,
      load: 170.6,
      load_incomplete: false,
      local_date: "2026-08-08",
      n: 1,
      tsb: {
        display_only: true,
        value: -48.556322201569664,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 94.61361214772872,
      },
      ctl: {
        display_only: true,
        value: 43.686884342911696,
      },
      day: 34,
      fitness_load: 122.4,
      load: 122.4,
      load_incomplete: false,
      local_date: "2026-08-09",
      n: 1,
      tsb: {
        display_only: true,
        value: -50.92672780481702,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 82.01844938640134,
      },
      ctl: {
        display_only: true,
        value: 42.65900563890446,
      },
      day: 35,
      fitness_load: 0.0,
      load: 0.0,
      load_incomplete: false,
      local_date: "2026-08-10",
      n: 0,
      tsb: {
        display_only: true,
        value: -39.35944374749688,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 88.40585417732656,
      },
      ctl: {
        display_only: true,
        value: 44.00814252190427,
      },
      day: 36,
      fitness_load: 100.0,
      load: 130.0,
      load_incomplete: false,
      local_date: "2026-08-11",
      n: 2,
      tsb: {
        display_only: true,
        value: -44.39771165542229,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 87.79271319579645,
      },
      ctl: {
        display_only: true,
        value: 44.94437781333818,
      },
      day: 37,
      fitness_load: 83.8,
      load: 83.8,
      load_incomplete: false,
      local_date: "2026-08-12",
      n: 1,
      tsb: {
        display_only: true,
        value: -42.848335382458274,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 95.75438482541527,
      },
      ctl: {
        display_only: true,
        value: 46.65384205862218,
      },
      day: 38,
      fitness_load: 117.6,
      load: 147.6,
      load_incomplete: false,
      local_date: "2026-08-13",
      n: 2,
      tsb: {
        display_only: true,
        value: -49.10054276679309,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 83.00736000932665,
      },
      ctl: {
        display_only: true,
        value: 45.556155843790165,
      },
      day: 39,
      fitness_load: 0.0,
      load: 0.0,
      load_incomplete: false,
      local_date: "2026-08-14",
      n: 0,
      tsb: {
        display_only: true,
        value: -37.45120416553648,
      },
      uncounted: 0,
    },
    {
      atl: {
        display_only: true,
        value: 95.4133599727103,
      },
      ctl: {
        display_only: true,
        value: 48.62998514605297,
      },
      day: 40,
      fitness_load: 176.2,
      load: 176.2,
      load_incomplete: false,
      local_date: "2026-08-15",
      n: 1,
      tsb: {
        display_only: true,
        value: -46.78337482665733,
      },
      uncounted: 0,
    },
  ],
};

/** The same read over no sessions at all: no points, and a maturity. */
export const FITNESS_TREND_EMPTY: Schemas["FitnessTrendRead"] = {
  explanations: {
    atl: {
      assumptions: [
        "every discipline feeds fatigue; strength through its heart-rate load, which is a low-trust estimate",
        "a day with no session is a load of 0 and the average decays",
        "a session neither load model could price is left out and its day is marked load_incomplete",
        "maturing until the seed's weight (1 − α)ⁿ falls to 10%: day 17",
      ],
      citation:
        "Banister EW et al. (1975), A systems model of training for athletic performance; Coggan's performance-management chart",
      display_only: true,
      formula: "ATL_d = ATL_{d−1} + α·(load_d − ATL_{d−1}), α = 1 − e^(−1/7)",
      inputs: {
        seed: "0",
        α: "0.133122",
        τ: "7 days",
      },
    },
    ctl: {
      assumptions: [
        "only cycling load feeds fitness",
        "a day with no session is a load of 0 and the average decays",
        "a session neither load model could price is left out and its day is marked load_incomplete",
        "maturing until the seed's weight (1 − α)ⁿ falls to 10%: day 97",
      ],
      citation:
        "Banister EW et al. (1975), A systems model of training for athletic performance; Coggan's performance-management chart",
      display_only: true,
      formula:
        "CTL_d = CTL_{d−1} + α·(cycling load_d − CTL_{d−1}), α = 1 − e^(−1/42)",
      inputs: {
        seed: "0",
        α: "0.023528",
        τ: "42 days",
      },
    },
    tsb: {
      assumptions: [
        "the lag is a declared parameter: TrainingPeaks reads yesterday's balance (L = 1), and intervals.icu's convention is unconfirmed",
      ],
      citation:
        "Banister EW et al. (1975), A systems model of training for athletic performance; Coggan's performance-management chart",
      display_only: true,
      formula: "TSB_d = CTL_{d−L} − ATL_{d−L}",
      inputs: {
        L: "0 days",
      },
    },
  },
  form_lag_days: 0,
  maturity: {
    as_of: "2026-08-15",
    atl: {
      display_only: true,
      mature: false,
      matures_on: null,
      matures_on_day: 17,
      seeded: false,
    },
    ctl: {
      display_only: true,
      mature: false,
      matures_on: null,
      matures_on_day: 97,
      seeded: false,
    },
    day: null,
    first_session: null,
  },
  points: [],
};
