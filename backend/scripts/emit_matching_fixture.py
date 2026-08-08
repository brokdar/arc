"""Generate the frontend's match-breakdown fixtures by running the real domain.

A fixture must be a payload the real API could produce (the repo's testing
strategy, rule 3). A similarity breakdown is one of the shapes where
type-checking proves almost nothing: the weights on the assessed components are
**renormalised** over the ones that could be assessed, the score is their
weighted mean, and every unassessed component carries a sentence the domain
words itself. A hand-typed breakdown where three components each score 0.6 and
the total says 0.6 with two of them missing type-checks perfectly and describes
a link `app.domain.matching.similarity` cannot produce — and the component test
then agrees with the fixture instead of with the application.

So the breakdowns are generated: this script states, for each pair the frontend
mock knows about, exactly the evidence the service would read off those two
rows, runs `similarity` over it, and emits `similarity_to_json` — the very
document the API stores on the link and hands back on
`GET /matches/{id}.breakdown`.

**The evidence is stated, not invented.** Every number below is annotated with
the frontend fixture it comes from (`frontend/tests/mocks/fixtures.ts` and the
generated `generated-metrics.ts`), so the pair of rows the mock joins and the
breakdown it answers with describe the same two sessions.

Run it with ``just matching-fixture`` after changing `app/domain/matching.py`
or the fixtures it is stated against, and commit the result.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from app.domain.matching import (
    IntensityBasis,
    MatchEvidence,
    StructureBasis,
    similarity,
    similarity_to_json,
)

#: Where the generated module lands.
DESTINATION = (
    Path(__file__).parents[2] / "frontend" / "tests" / "mocks" / "generated-matching.ts"
)

# --- the two sides, as `frontend/tests/mocks/fixtures.ts` states them ---------

#: `ACTIVITY_IDS` in the frontend fixtures.
OUTDOOR_RIDE = "0199a000-0000-7000-8000-000000000101"
TRAINER_RIDE = "0199a000-0000-7000-8000-000000000102"
GYM = "0199a000-0000-7000-8000-000000000103"

#: `PLANNED_IDS` in the frontend fixtures — the planned sessions dated around
#: the three recordings above, which is what makes them candidates at all.
PLANNED_VO2 = "0199a000-0000-7000-8000-000000000501"
PLANNED_LONG = "0199a000-0000-7000-8000-000000000502"
PLANNED_STRENGTH = "0199a000-0000-7000-8000-000000000503"
PLANNED_CORE = "0199a000-0000-7000-8000-000000000504"
PLANNED_THRESHOLD = "0199a000-0000-7000-8000-000000000505"

#: The FTP every cycling fixture's percentages resolve against (`FTP_WATTS`).
FTP = 250.0

#: `VO2_PREDICTED_LOAD.intensity_factor` and `LONG_PREDICTED_LOAD` — the
#: predictions the fixtures carry, computed by `predict_endurance_load` over
#: those exact structures. Planned NP is `IF × pinned FTP`, which is what
#: `app.domain.matching.planned_power_intensity` recovers.
VO2_INTENSITY_FACTOR = 0.9077207593641538
LONG_INTENSITY_FACTOR = 0.6515048505794672

#: `RIDE_METRICS.power.normalized_power.value` and the length of
#: `RIDE_METRICS.intervals` — the artefact the outdoor ride carries.
OUTDOOR_NP = 231.38976285327033
OUTDOOR_INTERVALS = 4

#: `session_duration_s`: recording time for a device session (the outdoor
#: ride's 9540 s elapsed less its 600 s coffee stop, and the trainer hour),
#: wall clock for the typed-in gym session.
OUTDOOR_DURATION_S = 8940.0
TRAINER_DURATION_S = 3600.0
GYM_DURATION_S = 3600.0

#: `total_duration_s` over each endurance prescription, and
#: `planned_work_steps` over the same document: the VO₂ session's 5 × 4′ inside
#: a 57-minute whole, and the long ride's single steady block.
VO2_PLANNED_S = 3420.0
VO2_WORK_STEPS = 5
LONG_PLANNED_S = 11400.0
LONG_WORK_STEPS = 1
THRESHOLD_PLANNED_S = 3900.0
THRESHOLD_WORK_STEPS = 2

#: `predict_strength_volume(...).total_sets` over each strength prescription,
#: and `len(session.logged_sets)` on the gym session.
STRENGTH_PLANNED_SETS = 5
CORE_PLANNED_SETS = 4
GYM_LOGGED_SETS = 3


@dataclass(frozen=True, slots=True)
class Pair:
    """One session against one planned session, and what would be compared."""

    session_id: str
    planned_session_id: str
    note: str
    evidence: MatchEvidence


def endurance(
    *,
    planned_s: float,
    actual_s: float,
    planned_np: float | None,
    actual_np: float | None,
    planned_steps: int,
    performed_intervals: int | None,
) -> MatchEvidence:
    """The endurance half of `app.services.matching._evidence`, stated.

    The two absences it models are the real ones: a session with no metric
    artefact has neither a normalized power to compare nor a detected interval
    count to count, and the domain reports both as unassessed rather than as
    zero.
    """
    return MatchEvidence(
        planned_duration_s=planned_s,
        actual_duration_s=actual_s,
        planned_intensity=planned_np,
        actual_intensity=actual_np,
        intensity_basis=(
            IntensityBasis.POWER
            if planned_np is not None and actual_np is not None
            else None
        ),
        planned_units=planned_steps,
        performed_units=performed_intervals,
        structure_basis=StructureBasis.INTERVALS,
    )


def strength(*, planned_sets: int, logged_sets: int, actual_s: float) -> MatchEvidence:
    """The strength half of it: sets against sets, and no seconds either side."""
    return MatchEvidence(
        planned_units=planned_sets,
        performed_units=logged_sets,
        structure_basis=StructureBasis.SETS,
        actual_duration_s=actual_s,
    )


PAIRS: tuple[Pair, ...] = (
    Pair(
        session_id=GYM,
        planned_session_id=PLANNED_STRENGTH,
        note=(
            "The seeded **pending** proposal: three sets logged against five "
            "prescribed. A strength prescription states no duration and shares "
            "no channel with a typed-in session, so two of the three "
            "components carry a reason instead of a number and the structure "
            "term is the whole score."
        ),
        evidence=strength(
            planned_sets=STRENGTH_PLANNED_SETS,
            logged_sets=GYM_LOGGED_SETS,
            actual_s=GYM_DURATION_S,
        ),
    ),
    Pair(
        session_id=GYM,
        planned_session_id=PLANNED_CORE,
        note="Where the proposal above is retargeted: four sets prescribed, three done.",
        evidence=strength(
            planned_sets=CORE_PLANNED_SETS,
            logged_sets=GYM_LOGGED_SETS,
            actual_s=GYM_DURATION_S,
        ),
    ),
    Pair(
        session_id=OUTDOOR_RIDE,
        planned_session_id=PLANNED_VO2,
        note=(
            "The other seeded proposal, and the one breakdown with all three "
            "components assessed: the intervals were ridden almost exactly at "
            "the prescribed intensity and four of five were detected, but the "
            "ride ran two and a half hours where 57 minutes were planned — "
            "which is what pulls the whole score down into the band where arc "
            "asks instead of deciding."
        ),
        evidence=endurance(
            planned_s=VO2_PLANNED_S,
            actual_s=OUTDOOR_DURATION_S,
            planned_np=VO2_INTENSITY_FACTOR * FTP,
            actual_np=OUTDOOR_NP,
            planned_steps=VO2_WORK_STEPS,
            performed_intervals=OUTDOOR_INTERVALS,
        ),
    ),
    Pair(
        session_id=OUTDOOR_RIDE,
        planned_session_id=PLANNED_LONG,
        note=(
            "The swap target for the link above. A steady endurance ride is "
            "one work step, which is fewer than a structure hint can mean "
            "anything with, so that component is dropped and the other two "
            "are scaled up between them."
        ),
        evidence=endurance(
            planned_s=LONG_PLANNED_S,
            actual_s=OUTDOOR_DURATION_S,
            planned_np=LONG_INTENSITY_FACTOR * FTP,
            actual_np=OUTDOOR_NP,
            planned_steps=LONG_WORK_STEPS,
            performed_intervals=OUTDOOR_INTERVALS,
        ),
    ),
    Pair(
        session_id=TRAINER_RIDE,
        planned_session_id=PLANNED_THRESHOLD,
        note=(
            "What re-running matching over the trainer ride finds: the "
            "threshold session planned for the same evening, an hour "
            "prescribed against the hour recorded. Nothing was computed over "
            "the file, so the duration is the only term — and it agrees well "
            "enough that the link is made without asking (and can still be "
            "undone). The prescription's watts are beside the point here: "
            "with no artefact there is no recorded intensity to compare them "
            "with, whatever they say."
        ),
        evidence=endurance(
            planned_s=THRESHOLD_PLANNED_S,
            actual_s=TRAINER_DURATION_S,
            planned_np=None,
            actual_np=None,
            planned_steps=THRESHOLD_WORK_STEPS,
            performed_intervals=None,
        ),
    ),
    Pair(
        session_id=TRAINER_RIDE,
        planned_session_id=PLANNED_LONG,
        note=(
            "The **displaced** case: an hour on the trainer where three hours "
            "outdoors were planned. Nothing was computed over the file yet, so "
            "only the duration could be compared — and at 0.32 the machine "
            "proposes nothing. Linking it is the athlete saying 'I trained, "
            "and it was not this'."
        ),
        evidence=endurance(
            planned_s=LONG_PLANNED_S,
            actual_s=TRAINER_DURATION_S,
            planned_np=LONG_INTENSITY_FACTOR * FTP,
            actual_np=None,
            planned_steps=LONG_WORK_STEPS,
            performed_intervals=None,
        ),
    ),
)


HEADER = """// GENERATED FILE — do not hand-edit.
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
> = """


def main() -> None:
    """Write the generated module."""
    entries: dict[str, object] = {}
    notes: dict[str, str] = {}
    for pair in PAIRS:
        key = f"{pair.session_id}|{pair.planned_session_id}"
        entries[key] = similarity_to_json(similarity(pair.evidence))
        notes[key] = pair.note

    body = json.dumps(entries, indent=2)
    # The note for each pair, above the key it explains: a breakdown is read to
    # find out *why* a link scored what it did, and the generated file is the
    # only place the two rows behind it are named.
    for key, note in notes.items():
        wrapped = "\n".join(f"  //   {line}" for line in _wrap(note))
        body = body.replace(f'  "{key}"', f'  //\n{wrapped}\n  "{key}"', 1)

    DESTINATION.write_text(HEADER + body + ";\n", encoding="utf-8")
    print(f"wrote {DESTINATION.relative_to(Path(__file__).parents[2])}")


def _wrap(text: str, width: int = 68) -> list[str]:
    """Break a note into comment-width lines without a dependency."""
    lines: list[str] = []
    current = ""
    for word in text.replace("**", "").split():
        if current and len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    main()
