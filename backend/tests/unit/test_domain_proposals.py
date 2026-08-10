"""The proposal state machine, the change union, and the red-flag rule.

Pure domain: no database, no service. What is pinned here is the shape of
invariant 6 — a proposal is answered exactly once, and the "adds or
intensifies" rule is deterministic and says why.
"""

import datetime as dt
import uuid

import pytest

from app.domain.athlete import Discipline
from app.domain.proposals import (
    LEGAL_TRANSITIONS,
    MAX_CHANGES,
    PURPOSE_INTENSITY,
    TERMINAL_STATUSES,
    ChangeKind,
    CreateChange,
    DeleteChange,
    MoveChange,
    ProposalStatus,
    UpdateChange,
    can_transition,
    change_from_json,
    changes_from_json,
    changes_to_json,
    check_transition,
    intensifies,
    kind_of,
    purpose_intensity,
)
from app.domain.purpose import Purpose
from app.domain.purpose import discipline_of as purpose_discipline

SESSION_ID = uuid.UUID("018f0000-0000-7000-8000-000000000001")


# --- the state machine ----------------------------------------------------------


def test_transition_table_is_total() -> None:
    # Total rather than "absent means illegal": adding a member has to be a
    # decision about what it may become, not a silent dead end.
    assert set(LEGAL_TRANSITIONS) == set(ProposalStatus)


def test_pending_is_the_only_state_anything_leaves() -> None:
    assert set(ProposalStatus) - {ProposalStatus.PENDING} == TERMINAL_STATUSES


@pytest.mark.parametrize(
    "target",
    [
        ProposalStatus.ACCEPTED,
        ProposalStatus.REJECTED,
        ProposalStatus.LAPSED,
        ProposalStatus.SUPERSEDED,
        ProposalStatus.RESOLVED_BY_REALITY,
    ],
)
def test_every_exit_from_pending_is_legal(target: ProposalStatus) -> None:
    assert can_transition(ProposalStatus.PENDING, target)
    check_transition(ProposalStatus.PENDING, target)


@pytest.mark.parametrize("source", sorted(TERMINAL_STATUSES))
@pytest.mark.parametrize("target", sorted(ProposalStatus))
def test_nothing_leaves_a_terminal_state(
    source: ProposalStatus, target: ProposalStatus
) -> None:
    assert not can_transition(source, target)
    with pytest.raises(ValueError, match=f"already {source.value}"):
        check_transition(source, target)


def test_pending_cannot_transition_to_itself() -> None:
    # Answering a proposal with "still pending" is not an answer, and letting
    # it through would make `resolved_at` and the audit row lies.
    assert not can_transition(ProposalStatus.PENDING, ProposalStatus.PENDING)


# --- the change union -----------------------------------------------------------


def test_every_kind_round_trips_through_json() -> None:
    changes = (
        CreateChange(
            date=dt.date(2026, 8, 12),
            purpose=Purpose.ENDURANCE,
            structure={"discipline": "cycling", "steps": []},
            intent_text="easy",
        ),
        UpdateChange(
            planned_session_id=SESSION_ID,
            expected_intent_version=2,
            updates={"purpose": "recovery"},
        ),
        MoveChange(
            planned_session_id=SESSION_ID,
            expected_intent_version=2,
            date=dt.date(2026, 8, 13),
        ),
        DeleteChange(planned_session_id=SESSION_ID, expected_intent_version=2),
    )

    assert changes_from_json(changes_to_json(changes)) == changes


def test_the_tag_names_the_kind() -> None:
    assert [
        kind_of(change)
        for change in changes_from_json(
            [
                {"kind": "create", "date": "2026-08-12", "purpose": "endurance"},
                {
                    "kind": "delete",
                    "planned_session_id": str(SESSION_ID),
                    "expected_intent_version": 1,
                },
            ]
        )
    ] == [ChangeKind.CREATE, ChangeKind.DELETE]


def test_an_unknown_kind_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="unknown change kind 'rename'"):
        change_from_json({"kind": "rename"})


@pytest.mark.parametrize("kind", ["update", "move", "delete"])
def test_a_targeted_change_needs_a_concurrency_token(kind: str) -> None:
    # The token is what makes a proposal safe to hold overnight; a change
    # without one could apply to a session it was never computed against.
    with pytest.raises(ValueError, match="expected_intent_version"):
        change_from_json(
            {"kind": kind, "planned_session_id": str(SESSION_ID), "date": "2026-08-12"}
        )


def test_an_update_with_no_fields_is_refused() -> None:
    with pytest.raises(ValueError, match="non-empty updates"):
        change_from_json(
            {
                "kind": "update",
                "planned_session_id": str(SESSION_ID),
                "expected_intent_version": 1,
                "updates": {},
            }
        )


def test_the_position_of_a_bad_change_is_named() -> None:
    with pytest.raises(ValueError, match="change 1: unknown purpose"):
        changes_from_json(
            [
                {"kind": "create", "date": "2026-08-12", "purpose": "endurance"},
                {"kind": "create", "date": "2026-08-12", "purpose": "sprinting"},
            ]
        )


def test_a_proposal_with_no_changes_is_not_a_proposal() -> None:
    with pytest.raises(ValueError, match="at least one change"):
        changes_from_json([])


def test_a_proposal_is_bounded_in_size() -> None:
    one = {"kind": "create", "date": "2026-08-12", "purpose": "endurance"}
    with pytest.raises(ValueError, match=f"at most {MAX_CHANGES} changes"):
        changes_from_json([one] * (MAX_CHANGES + 1))


# --- the red-flag rule ----------------------------------------------------------


def test_every_purpose_has_an_intensity_rank() -> None:
    # Total, because the rule has to be answerable about any revision — a
    # purpose with no rank would silently pass the guardrail.
    assert set(PURPOSE_INTENSITY) == set(Purpose)


def test_the_ranks_run_from_restorative_to_maximal() -> None:
    for discipline, order in (
        (
            Discipline.CYCLING,
            [Purpose.RECOVERY, Purpose.ENDURANCE, Purpose.THRESHOLD, Purpose.TEST],
        ),
        (
            Discipline.STRENGTH,
            [Purpose.MOBILITY, Purpose.CONDITIONING, Purpose.MAX_STRENGTH],
        ),
    ):
        assert all(purpose_discipline(purpose) is discipline for purpose in order)
        ranks = [purpose_intensity(purpose) for purpose in order]
        assert ranks == sorted(ranks)
        assert len(set(ranks)) == len(ranks)


def test_raising_the_purpose_intensifies() -> None:
    reason = intensifies(before_purpose=Purpose.ENDURANCE, after_purpose=Purpose.VO2MAX)
    assert reason is not None
    assert "endurance to vo2max" in reason


def test_lowering_the_purpose_does_not() -> None:
    assert (
        intensifies(before_purpose=Purpose.VO2MAX, after_purpose=Purpose.RECOVERY)
        is None
    )


def test_the_same_purpose_with_more_load_intensifies() -> None:
    reason = intensifies(
        before_purpose=Purpose.ENDURANCE,
        after_purpose=Purpose.ENDURANCE,
        before_load=60.0,
        after_load=95.0,
    )
    assert reason is not None
    assert "60.0 to 95.0 TSS" in reason


def test_the_same_purpose_with_less_load_does_not() -> None:
    assert (
        intensifies(
            before_purpose=Purpose.ENDURANCE,
            after_purpose=Purpose.ENDURANCE,
            before_load=95.0,
            after_load=60.0,
        )
        is None
    )


@pytest.mark.parametrize(
    ("before_load", "after_load"), [(None, 500.0), (60.0, None), (None, None)]
)
def test_an_unpredictable_side_falls_back_to_the_purpose_rank(
    before_load: float | None, after_load: float | None
) -> None:
    # "We could not compute it" is not evidence of an increase either way, so
    # the rank decides alone — here it says the session got easier.
    assert (
        intensifies(
            before_purpose=Purpose.THRESHOLD,
            after_purpose=Purpose.RECOVERY,
            before_load=before_load,
            after_load=after_load,
        )
        is None
    )


def test_a_strength_increase_is_reported_in_kilograms() -> None:
    reason = intensifies(
        before_purpose=Purpose.HYPERTROPHY,
        after_purpose=Purpose.HYPERTROPHY,
        before_load=1000.0,
        after_load=1500.0,
    )
    assert reason is not None
    assert reason.endswith("kg")


def test_swapping_discipline_intensifies_because_it_cannot_be_shown_not_to() -> None:
    # The two rank scales are unrelated, so a ride becoming a lift cannot be
    # *shown* to be a reduction — and the rule refuses what it cannot show
    # while the athlete is ill.
    reason = intensifies(before_purpose=Purpose.VO2MAX, after_purpose=Purpose.MOBILITY)
    assert reason is not None
    assert "changes the discipline" in reason
