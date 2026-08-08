"""Generate the frontend's scoring fixtures by running the real domain.

A fixture must be a payload the real API could produce (the repo's testing
strategy, rule 3). A score is one of the shapes where type-checking proves
almost nothing: every axis is a number derived from the same 1 Hz stream the
metric artefact was derived from, each carries an explanation naming the
inputs it was fed, the criteria under it pass or fail against thresholds the
prescription froze, and the **suggested verdict** is the output of a nine-row
decision table read in order. A hand-typed score where adherence is 0.91 and
the suggestion is ``abandoned`` type-checks perfectly and describes a session
`app.domain.scoring.score_session` cannot produce — and the component test
then agrees with the fixture instead of with the application.

So the score is generated. This script states the two sides exactly as the
frontend mock states them — the VO₂ prescription of `PLANNED_IDS.vo2` and the
synthetic ride `emit_metrics_fixture.py` builds, whose columns are imported
from that script rather than rebuilt, so the stream the axes are computed over
is byte-identical to the one `generated-metrics.ts` carries — runs
`app.domain.alignment.align` and `app.domain.scoring.score_session` over them,
and emits `alignment_to_json` and `score_to_json`: the very documents the API
stores and hands back on ``GET /sessions/{id}/alignment`` and
``GET /sessions/{id}/score``.

**Every offset is generated, not just the one in force.** The offset control
(A7.1) is functional: sliding the planned timeline changes which detected
effort answers which prescribed step, so it changes the alignment *and* the
score. A mock that answered a PUT with the score it already had would let a
component that sent the wrong offset still pass, so each offset the frontend
can send has its own pair below, and the handler throws for one nothing was
generated for rather than inventing an answer.

Run it with ``just scoring-fixture`` after changing `app/domain/scoring.py`,
`app/domain/alignment.py`, or the fixtures the script states its two sides
against, and commit the result.
"""

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from app.domain.alignment import (
    Alignment,
    WorkInterval,
    align,
    alignment_to_json,
    detect_work_intervals,
)
from app.domain.anchors import (
    ANCHOR_UNITS,
    AnchorSource,
    AnchorType,
    AnchorVersion,
    Provenance,
)
from app.domain.criteria import (
    AbsoluteLimit,
    Band,
    Ceiling,
    DurationFloor,
    StepSelector,
    SuccessCriterion,
    TimeInBand,
)
from app.domain.prediction import PinnedAnchor
from app.domain.purpose import Purpose
from app.domain.resolution import resolve_steps
from app.domain.scoring import ScoredStep, ScoringInputs, score_session, score_to_json
from app.domain.templates import ScoringAxis
from app.domain.workout import (
    Channel,
    ChannelUnit,
    EnduranceWorkout,
    FlatStep,
    PercentOfAnchor,
    RepeatBlock,
    SteadyStep,
    StepRole,
    flatten,
    total_duration_s,
)

# The synthetic ride is the metrics fixture's, imported rather than rebuilt:
# the adherence and pacing axes read the same power column the artefact's NP
# came from, and two scripts each building "the same" stream is exactly how
# they stop being the same stream. `scripts/` is not a package, so the sibling
# is reached by putting this file's directory on the path.
sys.path.insert(0, str(Path(__file__).parent))

from emit_metrics_fixture import (  # noqa: E402
    ANCHOR_IDS,
    build_columns,
)

#: Where the generated module lands.
DESTINATION = (
    Path(__file__).parents[2] / "frontend" / "tests" / "mocks" / "generated-scoring.ts"
)

# --- the two sides, as `frontend/tests/mocks/fixtures.ts` states them ---------

#: `ACTIVITY_IDS.outdoorRide` — the recorded session carrying `RIDE_METRICS`.
OUTDOOR_RIDE = "0199a000-0000-7000-8000-000000000101"

#: `PLANNED_IDS.vo2` — the VO₂ session it was proposed against.
PLANNED_VO2 = "0199a000-0000-7000-8000-000000000501"

#: `FTP_WATTS` / `PINNED_FTP`: the FTP the *prescription pinned*, which is
#: deliberately not the one in force. Every percentage below resolves against
#: this and nothing else — that is what keeps a frozen prescription frozen.
FTP = 250.0

#: `FTP_VERSION_ID`, so the emitted score names the version it resolved.
FTP_VERSION_ID = "0199a000-0000-7000-8000-0000000000f1"

#: Offsets the frontend can send, in seconds.
#:
#: Zero is what the ingest path computes. ``-1200`` is the athlete saying the
#: workout had already been running for twenty minutes when the file was
#: started, and it is the one that visibly **re-pairs**: the first prescribed
#: effort goes from answered to never performed, and the low-confidence
#: exclusion moves from the third rep to the fourth. ``600`` is a small
#: correction the other way, and it changes nothing — which is worth having in
#: the fixture too, because "the offset always changes the answer" is not true
#: and a mock that implied it would be lying about the control.
OFFSETS: tuple[int, ...] = (-1200, 0, 600)


def pct(low: float, high: float) -> PercentOfAnchor:
    """A power target as a percentage band of the pinned FTP."""
    return PercentOfAnchor(anchor_type=AnchorType.FTP, pct_low=low, pct_high=high)


#: `VO2_STRUCTURE`, step for step: 12′ warm-up, 5 × (4′ at 114-122 % off 3′),
#: 10′ cool-down. 3420 s, twelve flattened steps, five of them work.
VO2_STRUCTURE = EnduranceWorkout(
    steps=(
        SteadyStep(
            role=StepRole.WARMUP,
            name="Warm-up",
            duration_s=720,
            targets={Channel.POWER: pct(0.5, 0.6)},
        ),
        RepeatBlock(
            times=5,
            children=(
                SteadyStep(
                    role=StepRole.WORK,
                    name="VO₂ block",
                    duration_s=240,
                    targets={Channel.POWER: pct(1.14, 1.22)},
                ),
                SteadyStep(
                    role=StepRole.REST,
                    name="Spin",
                    duration_s=180,
                    targets={Channel.POWER: pct(0.4, 0.5)},
                ),
            ),
        ),
        SteadyStep(role=StepRole.COOLDOWN, name="Cool-down", duration_s=600),
    )
)

#: `VO2_CRITERIA`, criterion for criterion and in the same order — the index
#: of each one is the `CriterionOutcomeRead.index` the panel joins back onto
#: the prescription the athlete is looking at.
VO2_CRITERIA: tuple[SuccessCriterion, ...] = (
    TimeInBand(
        band=Band(channel=Channel.POWER, low=0.95, high=1.05, smoothing_s=30),
        min_fraction=0.75,
        selector=StepSelector.of_role(StepRole.WORK),
    ),
    Ceiling(
        channel=Channel.HR,
        limit=AbsoluteLimit(unit=ChannelUnit.BPM, value=178),
        max_seconds_above=360,
        smoothing_s=0,
    ),
    DurationFloor(min_seconds=3600),
)

#: `purpose_templates.json` for `vo2max`. Stated rather than loaded so the
#: generated file names the axes it carries; a template change that adds one
#: is a diff here, which is where it should be noticed.
VO2_AXES: tuple[ScoringAxis, ...] = (
    ScoringAxis.COMPLETION,
    ScoringAxis.ADHERENCE,
    ScoringAxis.PACING,
)

#: `RIDE_METRICS.recording_time_s`: the seconds the axes were computed over.
#: The *artefact's* number, not the session row's — a score judges the
#: recording it was computed from.
ACTUAL_DURATION_S = 1140.0


def columns() -> dict[Channel, tuple[float | None, ...]]:
    """The metrics fixture's cleaned columns, keyed by prescribable channel.

    `StreamChannel` is the recording vocabulary and `Channel` the prescribing
    one; the members they share carry the same values on purpose, and the ones
    they do not (speed, elevation) are nothing an axis reads.
    """
    prescribable = {channel.value for channel in Channel}
    return {
        Channel(channel.value): column
        for channel, column in build_columns().items()
        if channel.value in prescribable
    }


def pins() -> dict[AnchorType, PinnedAnchor]:
    """The one anchor this prescription pinned, as the resolver wants it.

    Only FTP: the intent pins what its targets refer to, and the criteria's
    heart-rate ceiling is stated in absolute bpm precisely so no LTHR version
    has to be pinned to read it.
    """
    return {
        AnchorType.FTP: PinnedAnchor(
            version_id=ANCHOR_IDS[AnchorType.FTP],
            version=AnchorVersion(
                anchor_type=AnchorType.FTP,
                value=FTP,
                unit=ANCHOR_UNITS[AnchorType.FTP],
                provenance=Provenance.ESTIMATED,
                effective_date=dt.date(2026, 6, 1),
                created_at=dt.datetime(2026, 6, 1, 9, 0, tzinfo=dt.UTC),
                source=AnchorSource.ATHLETE,
            ),
        )
    }


def step_targets(
    body: EnduranceWorkout, anchors: dict[AnchorType, PinnedAnchor]
) -> dict[int, dict[Channel, float]]:
    """`app.services.scoring._targets`, stated: each step's resolved midpoints."""
    midpoints: dict[int, dict[Channel, float]] = {}
    for step in resolve_steps(body, anchors):
        resolved: dict[Channel, float] = {}
        for target in step.start_targets:
            if target.resolved_low is None or target.resolved_high is None:
                continue
            resolved[target.channel] = (target.resolved_low + target.resolved_high) / 2
        midpoints[step.index] = resolved
    return midpoints


def scored_steps(
    steps: tuple[FlatStep, ...],
    alignment: Alignment,
    intervals: tuple[WorkInterval, ...],
    targets: dict[int, dict[Channel, float]],
) -> tuple[ScoredStep, ...]:
    """`app.services.scoring._scored_steps`, stated for one alignment."""
    by_index = {step.index: step for step in steps}
    return tuple(
        ScoredStep(
            step_index=pair.step_index,
            repetition=by_index[pair.step_index].repetition,
            confidence=pair.confidence,
            start_index=intervals[pair.interval_index].start_index,
            end_index=intervals[pair.interval_index].end_index,
            targets=targets.get(pair.step_index, {}),
        )
        for pair in alignment.aligned
    )


def pair_at(offset_s: int) -> dict[str, Any]:
    """The alignment and the score the ride gets at one offset."""
    grid = columns()
    intervals = tuple(
        detect_work_intervals(grid[Channel.POWER], hr_fixed=grid.get(Channel.HR))
    )
    steps = tuple(flatten(VO2_STRUCTURE))
    anchors = pins()
    targets = step_targets(VO2_STRUCTURE, anchors)
    watts = {
        index: channels[Channel.POWER]
        for index, channels in targets.items()
        if Channel.POWER in channels
    }
    alignment = align(steps, intervals, offset_s=offset_s, target_watts=watts)
    score = score_session(
        ScoringInputs(
            purpose=Purpose.VO2MAX,
            axes=VO2_AXES,
            criteria=VO2_CRITERIA,
            steps=steps,
            planned_duration_s=total_duration_s(VO2_STRUCTURE),
            actual_duration_s=ACTUAL_DURATION_S,
            channels=grid,
            scored_steps=scored_steps(steps, alignment, intervals, targets),
            excluded_steps=tuple(one.step_index for one in alignment.excluded),
            unmatched_steps=alignment.unmatched_steps,
            anchors={AnchorType.FTP: FTP},
        )
    )
    return {"alignment": alignment_to_json(alignment), "score": score_to_json(score)}


HEADER = """// GENERATED FILE — do not hand-edit.
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
export const SCORED_FTP_VERSION_ID = "%(ftp_version_id)s";

/**
 * The offsets the mock can answer for, in seconds.
 *
 * The offset control is functional (A7.1): sliding the planned timeline moves
 * which detected effort answers which prescribed step, so it moves the score.
 * A test may only send one of these — see `statedScoring`.
 */
export const SCORED_OFFSETS: readonly number[] = %(offsets)s;

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
  Record<string, { readonly score: ScorePayload; readonly alignment: AlignmentPayload }>
> = """


def main() -> None:
    """Write the generated module."""
    entries = {
        f"{OUTDOOR_RIDE}|{PLANNED_VO2}|{offset}": pair_at(offset) for offset in OFFSETS
    }
    header = HEADER % {
        "ftp_version_id": FTP_VERSION_ID,
        "offsets": json.dumps(list(OFFSETS)),
    }
    DESTINATION.write_text(
        header + json.dumps(entries, indent=2) + ";\n", encoding="utf-8"
    )
    print(f"wrote {DESTINATION.relative_to(Path(__file__).parents[2])}")


if __name__ == "__main__":
    main()
