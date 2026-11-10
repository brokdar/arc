"""The first-run answer and the mode it sets, as pure rules.

`mode_for_answer` is the whole of "which mode does a new athlete get": nobody
is asked to pick a mode by name, because *Simple / Standard / Full control* is
a question about arc, and "have you lifted before?" is a question about them.
"""

import pytest

from app.domain.training_mode import (
    AskFrequency,
    LiftedBefore,
    Locale,
    TrainingMode,
    TrainingSettings,
    Units,
    mode_for_answer,
    with_changes,
)


@pytest.mark.parametrize(
    ("answer", "mode"),
    [
        (LiftedBefore.NEVER, TrainingMode.SIMPLE),
        (None, TrainingMode.SIMPLE),
        (LiftedBefore.A_LITTLE, TrainingMode.STANDARD),
        (LiftedBefore.REGULARLY, TrainingMode.STANDARD),
    ],
)
def test_never_or_skipping_is_simple_and_any_lifting_is_standard(
    answer: LiftedBefore | None, mode: TrainingMode
) -> None:
    assert mode_for_answer(answer) is mode


def test_no_answer_ever_leads_to_full_control() -> None:
    """Full control is only ever chosen, never inferred."""
    assert TrainingMode.FULL not in {
        mode_for_answer(answer) for answer in [None, *LiftedBefore]
    }


def test_the_defaults_are_a_newcomer_in_kilograms_and_english() -> None:
    assert TrainingSettings() == TrainingSettings(
        mode=TrainingMode.SIMPLE,
        plain_logbook=False,
        ask_frequency=AskFrequency.ALWAYS,
        units=Units.KG,
        locale=Locale.EN,
        high_contrast=False,
        before_you_start_seen=False,
        first_run_seen=False,
        lifted_before=None,
    )


def test_answering_sets_the_mode_and_marks_the_question_seen() -> None:
    changed = with_changes(
        TrainingSettings(), {"lifted_before": LiftedBefore.REGULARLY}
    )

    assert changed.mode is TrainingMode.STANDARD
    assert changed.lifted_before is LiftedBefore.REGULARLY
    assert changed.first_run_seen is True


def test_a_mode_named_in_the_same_change_wins_over_the_answer() -> None:
    changed = with_changes(
        TrainingSettings(),
        {"lifted_before": LiftedBefore.NEVER, "mode": TrainingMode.FULL},
    )

    assert changed.mode is TrainingMode.FULL


def test_an_answer_does_not_take_full_control_away() -> None:
    chosen = TrainingSettings(mode=TrainingMode.FULL, first_run_seen=True)

    changed = with_changes(chosen, {"lifted_before": LiftedBefore.NEVER})

    assert changed.mode is TrainingMode.FULL


def test_repeating_the_same_answer_leaves_a_chosen_mode_alone() -> None:
    chosen = TrainingSettings(
        mode=TrainingMode.SIMPLE,
        lifted_before=LiftedBefore.REGULARLY,
        first_run_seen=True,
    )

    changed = with_changes(chosen, {"lifted_before": LiftedBefore.REGULARLY})

    assert changed == chosen


def test_skipping_marks_the_question_seen_and_leaves_the_answer_empty() -> None:
    changed = with_changes(TrainingSettings(), {"first_run_seen": True})

    assert changed.lifted_before is None
    assert changed.mode is TrainingMode.SIMPLE
    assert changed.first_run_seen is True


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        with_changes(TrainingSettings(), {"timezone": "Europe/Berlin"})


def test_a_null_is_refused_naming_the_field() -> None:
    with pytest.raises(ValueError, match="units"):
        with_changes(TrainingSettings(), {"units": None})
