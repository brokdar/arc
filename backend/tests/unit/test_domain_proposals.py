"""The proposal state machine, the change union, and the red-flag rule.

Pure domain: no database, no service. What is pinned here is the shape of
invariant 6 — a proposal is answered exactly once, and the "adds or
intensifies" rule is deterministic and says why.
"""

import datetime as dt
import uuid
from typing import Any

import pytest

from app.domain.athlete import Discipline
from app.domain.proposals import (
    LEGAL_TRANSITIONS,
    MAX_CHANGES,
    PURPOSE_INTENSITY,
    TERMINAL_STATUSES,
    UPDATE_FIELDS,
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
from app.domain.sessions import MAX_INTENT_CHARS

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


def test_an_update_may_not_carry_a_status() -> None:
    # D174: a planned session's status is derived from what the athlete did,
    # so it is not something anyone proposes — and it is named rather than
    # lumped in with the typos, because it *is* a planned-session field.
    assert "status" not in UPDATE_FIELDS
    with pytest.raises(ValueError, match="status is not a proposable field"):
        change_from_json(
            {
                "kind": "update",
                "planned_session_id": str(SESSION_ID),
                "expected_intent_version": 1,
                "updates": {"status": "completed"},
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


#: Every loosely-typed field in the union, with a value of the wrong kind.
#: These used to reach `dict(...)` or a comprehension over a non-iterable and
#: leave as `TypeError` — which no caller catches, so the MCP adapter reported
#: "the server failed" and the agent retried a call that can never work.
WRONG_TYPES: tuple[tuple[dict[str, object], str], ...] = (
    (
        {
            "kind": "create",
            "date": "2026-08-12",
            "purpose": "endurance",
            "structure": 5,
        },
        "structure",
    ),
    (
        {
            "kind": "create",
            "date": "2026-08-12",
            "purpose": "endurance",
            "success_criteria": [1, 2],
        },
        "success_criteria",
    ),
    (
        {
            "kind": "create",
            "date": "2026-08-12",
            "purpose": "endurance",
            "intent_text": ["a"],
        },
        "intent_text",
    ),
    (
        {
            "kind": "create",
            "date": "2026-08-12",
            "purpose": "endurance",
            "coach_notes": 7,
        },
        "coach_notes",
    ),
    ({"kind": "create", "date": 5, "purpose": "endurance"}, "date"),
    (
        {
            "kind": "update",
            "planned_session_id": str(SESSION_ID),
            "expected_intent_version": 1,
            "updates": {"structure": 5},
        },
        "structure",
    ),
    (
        {
            "kind": "update",
            "planned_session_id": str(SESSION_ID),
            "expected_intent_version": 1,
            "updates": {"success_criteria": "nope"},
        },
        "success_criteria",
    ),
)


@pytest.mark.parametrize(("document", "field"), WRONG_TYPES)
def test_a_wrong_typed_field_is_refused_by_name(
    document: dict[str, object], field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        change_from_json(document)


def test_a_wrong_typed_field_names_its_change() -> None:
    # The whole point of the ValueError: an agent that sent five changes has
    # to be told which one, and which field of it, before it can fix anything.
    with pytest.raises(ValueError, match="change 1: structure must be an object"):
        changes_from_json(
            [
                {"kind": "create", "date": "2026-08-12", "purpose": "endurance"},
                {
                    "kind": "create",
                    "date": "2026-08-12",
                    "purpose": "endurance",
                    "structure": 5,
                },
            ]
        )


def test_a_two_character_string_is_not_a_prescription() -> None:
    # `dict("ab")` raises, but `dict(["ab"])` is `{"a": "b"}` — the wrong type
    # that silently *succeeds*, which is worse than the one that raises.
    with pytest.raises(ValueError, match="structure must be an object"):
        change_from_json(
            {
                "kind": "create",
                "date": "2026-08-12",
                "purpose": "endurance",
                "structure": ["ab"],
            }
        )


def test_a_change_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(ValueError, match="change 0: a change must be an object"):
        changes_from_json([5])  # pyrefly: ignore[bad-argument-type]


def test_a_non_string_update_key_is_refused_rather_than_crashing() -> None:
    # The refusal itself used to raise: `', '.join(sorted(unknown))` is a
    # TypeError when a key is not a string.
    with pytest.raises(ValueError, match="unknown planned-session field"):
        change_from_json(
            {
                "kind": "update",
                "planned_session_id": str(SESSION_ID),
                "expected_intent_version": 1,
                "updates": {1: "x"},
            }
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
    # A rank is a label, not an amount, so it clears nothing on its own: the
    # session has to be shown to be no bigger as well (here, the same hour).
    assert (
        intensifies(
            before_purpose=Purpose.VO2MAX,
            after_purpose=Purpose.RECOVERY,
            before_duration_s=3_600,
            after_duration_s=3_600,
        )
        is None
    )


def test_a_lower_purpose_alone_does_not_clear_a_change() -> None:
    # D186: the flag is up, nothing about the amount of work can be compared,
    # and a purpose named `recovery` is a promise rather than a measurement.
    reason = intensifies(before_purpose=Purpose.VO2MAX, after_purpose=Purpose.RECOVERY)
    assert reason is not None
    assert "cannot be shown not to add work" in reason


def test_more_sets_intensify_when_neither_side_has_kilograms() -> None:
    # Bodyweight, RPE or %e1RM strength prices at no kilograms on either side.
    # 3x5 becoming 30x5 is obviously more work, and the guard has to say so.
    reason = intensifies(
        before_purpose=Purpose.STRENGTH_ENDURANCE,
        after_purpose=Purpose.STRENGTH_ENDURANCE,
        before_sets=3,
        after_sets=30,
    )
    assert reason is not None
    assert "prescribed sets from 3 to 30" in reason


def test_a_longer_ride_intensifies_when_neither_side_has_a_load() -> None:
    # Unstructured riding carries no power target, so no TSS can be predicted
    # on either side. Ten minutes becoming six hours is still six hours.
    reason = intensifies(
        before_purpose=Purpose.UNSTRUCTURED,
        after_purpose=Purpose.UNSTRUCTURED,
        before_duration_s=600,
        after_duration_s=21_600,
    )
    assert reason is not None
    assert "prescribed duration from 10 to 360 minutes" in reason


def test_the_same_size_session_is_cleared() -> None:
    assert (
        intensifies(
            before_purpose=Purpose.HYPERTROPHY,
            after_purpose=Purpose.HYPERTROPHY,
            before_sets=12,
            after_sets=12,
        )
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
def test_an_unpredictable_side_is_refused_rather_than_waved_through(
    before_load: float | None, after_load: float | None
) -> None:
    # D186, superseding D170's fail-open half: "we could not compute it" is
    # not evidence of a reduction, and while the athlete is ill the benefit of
    # that doubt goes to the athlete. The rank says the session got easier and
    # that is not enough on its own.
    reason = intensifies(
        before_purpose=Purpose.THRESHOLD,
        after_purpose=Purpose.RECOVERY,
        before_load=before_load,
        after_load=after_load,
    )
    assert reason is not None
    assert "cannot be shown not to add work" in reason


@pytest.mark.parametrize(
    ("before_load", "after_load"), [(None, 500.0), (60.0, None), (None, None)]
)
def test_an_unpredictable_cost_still_clears_on_a_comparable_size(
    before_load: float | None, after_load: float | None
) -> None:
    # The cost is the best signal, not the only one: a ride that cannot be
    # priced can still be shown to be shorter.
    assert (
        intensifies(
            before_purpose=Purpose.THRESHOLD,
            after_purpose=Purpose.RECOVERY,
            before_load=before_load,
            after_load=after_load,
            before_duration_s=7_200,
            after_duration_s=1_800,
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


# --- bounds on the free text an agent may propose --------------------------------


@pytest.mark.parametrize("field", ["intent_text", "coach_notes"])
def test_an_over_long_revision_text_is_refused_when_the_change_is_built(
    field: str,
) -> None:
    # The intent applies this cap when the change is *applied*, which is hours
    # later: a proposal carrying 200 000 characters otherwise stores happily,
    # sits in the inbox looking answerable, and refuses every accept until it
    # lapses. The agent has to be told now, by its own call.
    with pytest.raises(ValueError, match=f"{field} must be at most"):
        UpdateChange(
            planned_session_id=SESSION_ID,
            expected_intent_version=1,
            updates={field: "x" * (MAX_INTENT_CHARS + 1)},
        )


def test_an_over_long_create_text_is_refused_too() -> None:
    too_long = "x" * (MAX_INTENT_CHARS + 1)
    body: dict[str, Any] = {"discipline": "cycling", "steps": []}

    with pytest.raises(ValueError, match="intent_text must be at most"):
        CreateChange(
            date=dt.date(2026, 8, 10),
            purpose=Purpose.ENDURANCE,
            structure=body,
            intent_text=too_long,
        )
    with pytest.raises(ValueError, match="coach_notes must be at most"):
        CreateChange(
            date=dt.date(2026, 8, 10),
            purpose=Purpose.ENDURANCE,
            structure=body,
            coach_notes=too_long,
        )


def test_text_at_exactly_the_cap_is_accepted() -> None:
    change = UpdateChange(
        planned_session_id=SESSION_ID,
        expected_intent_version=1,
        updates={"intent_text": "x" * MAX_INTENT_CHARS},
    )

    assert len(change.updates["intent_text"]) == MAX_INTENT_CHARS


def test_an_over_long_text_is_refused_by_position_when_parsed_from_json() -> None:
    with pytest.raises(ValueError, match="change 0"):
        changes_from_json(
            [
                {
                    "kind": "update",
                    "planned_session_id": str(SESSION_ID),
                    "expected_intent_version": 1,
                    "updates": {"coach_notes": "x" * (MAX_INTENT_CHARS + 1)},
                }
            ]
        )
