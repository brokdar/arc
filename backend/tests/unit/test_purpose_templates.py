"""Purpose templates: the bundled file, and what it must guarantee.

The templates are data, so the tests are about the *file* as much as the code:
every purpose in the vocabulary has to have one, every axis it names has to be
applicable to its discipline, and a malformed file has to stop the
application rather than surface at scoring time weeks later.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.athlete import Discipline
from app.domain.criteria import (
    ENDURANCE_ONLY_KINDS,
    STRENGTH_ONLY_KINDS,
    kind_of,
)
from app.domain.purpose import (
    ENDURANCE_PURPOSES,
    PURPOSE_DISCIPLINE,
    STRENGTH_PURPOSES,
    Purpose,
    discipline_of,
    purposes_for,
)
from app.domain.templates import (
    DEFERRED_AXES,
    UNIVERSAL_AXIS,
    PurposeTemplate,
    ScoringAxis,
    parse_templates,
    sorted_templates,
)
from app.services.templates import (
    PURPOSE_TEMPLATES_FILE,
    ResourceError,
    load_purpose_templates,
    purpose_templates,
)


@pytest.fixture(scope="module")
def templates() -> dict[Purpose, PurposeTemplate]:
    """The bundled templates, loaded exactly as the application loads them."""
    return purpose_templates()


# --- the vocabulary -----------------------------------------------------------


def test_the_vocabulary_is_the_one_the_build_plan_states() -> None:
    assert [purpose.value for purpose in ENDURANCE_PURPOSES] == [
        "recovery",
        "endurance",
        "tempo",
        "sweet_spot",
        "threshold",
        "vo2max",
        "anaerobic",
        "neuromuscular",
        "unstructured",
        "technique",
        "test",
    ]
    assert [purpose.value for purpose in STRENGTH_PURPOSES] == [
        "max_strength",
        "strength_endurance",
        "hypertrophy",
        "power",
        "core",
        "mobility",
        "conditioning",
    ]


def test_every_purpose_has_a_discipline() -> None:
    assert set(PURPOSE_DISCIPLINE) == set(Purpose)


def test_purposes_can_be_listed_per_discipline() -> None:
    assert purposes_for(Discipline.CYCLING) == ENDURANCE_PURPOSES
    assert purposes_for(Discipline.STRENGTH) == STRENGTH_PURPOSES


# --- the bundled file ---------------------------------------------------------


def test_every_purpose_has_a_template(
    templates: dict[Purpose, PurposeTemplate],
) -> None:
    # The plan-mandated derivation test: a purpose with no template is
    # plannable and unscoreable.
    assert set(templates) == set(Purpose)


@pytest.mark.parametrize("purpose", list(Purpose), ids=lambda p: p.value)
def test_every_purpose_derives_usable_criteria_and_axes(
    purpose: Purpose, templates: dict[Purpose, PurposeTemplate]
) -> None:
    template = templates[purpose]
    discipline = discipline_of(purpose)

    assert template.purpose is purpose
    assert template.discipline is discipline
    assert UNIVERSAL_AXIS in template.axes
    assert template.default_criteria, "a template with no criteria scores nothing"

    forbidden = (
        STRENGTH_ONLY_KINDS
        if discipline is Discipline.CYCLING
        else ENDURANCE_ONLY_KINDS
    )
    assert not {kind_of(c) for c in template.default_criteria} & forbidden


def test_unstructured_and_recovery_are_not_scored_on_adherence(
    templates: dict[Purpose, PurposeTemplate],
) -> None:
    # The build plan is explicit: adherence is suppressed for `unstructured`
    # (there was no structure to adhere to) and optional for `recovery`, where
    # the point is the ceiling, not the band.
    assert ScoringAxis.ADHERENCE not in templates[Purpose.UNSTRUCTURED].axes
    assert ScoringAxis.ADHERENCE not in templates[Purpose.RECOVERY].axes
    assert ScoringAxis.DISCIPLINE in templates[Purpose.RECOVERY].axes


def test_interval_purposes_are_scored_on_pacing(
    templates: dict[Purpose, PurposeTemplate],
) -> None:
    for purpose in (Purpose.THRESHOLD, Purpose.VO2MAX, Purpose.ANAEROBIC):
        assert ScoringAxis.PACING in templates[purpose].axes


def test_strength_purposes_are_scored_on_sets_and_load(
    templates: dict[Purpose, PurposeTemplate],
) -> None:
    for purpose in (Purpose.MAX_STRENGTH, Purpose.HYPERTROPHY):
        assert ScoringAxis.SETS_LOAD in templates[purpose].axes


def test_the_mvp_templates_claim_no_deferred_axis(
    templates: dict[Purpose, PurposeTemplate],
) -> None:
    # `response` and `fuelling` are in the vocabulary so WP-7's shape exists,
    # but nothing computes them, and a template listing one would promise a
    # score the MVP cannot produce.
    for template in templates.values():
        assert not set(template.axes) & DEFERRED_AXES
        assert template.assessable_axes == template.axes


def test_templates_are_listed_in_vocabulary_order(
    templates: dict[Purpose, PurposeTemplate],
) -> None:
    assert [template.purpose for template in sorted_templates(templates)] == list(
        Purpose
    )


def test_the_file_on_disk_is_what_is_loaded() -> None:
    assert PURPOSE_TEMPLATES_FILE.is_file()
    assert set(load_purpose_templates()) == {purpose.value for purpose in Purpose}


# --- a bad file fails loudly --------------------------------------------------


def complete_document() -> dict[str, Any]:
    """The bundled file, decoded, as a starting point for mutation."""
    return json.loads(Path(PURPOSE_TEMPLATES_FILE).read_text(encoding="utf-8"))


def test_a_missing_purpose_is_refused() -> None:
    document = complete_document()
    del document["templates"]["threshold"]

    with pytest.raises(ValueError, match="no template for purpose\\(s\\): threshold"):
        parse_templates(document)


def test_an_unknown_purpose_is_refused() -> None:
    document = complete_document()
    document["templates"]["gravel"] = {"axes": ["completion"], "default_criteria": []}

    with pytest.raises(ValueError, match="'gravel' is not one of"):
        parse_templates(document)


def test_an_unknown_axis_is_refused() -> None:
    document = complete_document()
    document["templates"]["endurance"]["axes"] = ["completion", "vibes"]

    with pytest.raises(ValueError, match="'vibes' is not one of"):
        parse_templates(document)


def test_a_template_without_the_universal_axis_is_refused() -> None:
    document = complete_document()
    document["templates"]["endurance"]["axes"] = ["adherence"]

    with pytest.raises(ValueError, match="every purpose is scored on completion"):
        parse_templates(document)


def test_an_axis_from_the_other_discipline_is_refused() -> None:
    document = complete_document()
    document["templates"]["hypertrophy"]["axes"] = ["completion", "adherence"]

    with pytest.raises(ValueError, match="cannot be scored on adherence"):
        parse_templates(document)


def test_a_criterion_the_purpose_could_never_evaluate_is_refused() -> None:
    document = complete_document()
    document["templates"]["endurance"]["default_criteria"] = [
        {"kind": "sets_completed", "min_fraction": 0.9}
    ]

    with pytest.raises(ValueError, match="could never be evaluated"):
        parse_templates(document)


def test_a_malformed_criterion_is_refused_with_its_location() -> None:
    document = complete_document()
    document["templates"]["endurance"]["default_criteria"] = [
        {"kind": "duration_floor", "min_seconds": "half an hour"}
    ]

    with pytest.raises(ValueError, match=r"templates.endurance.default_criteria\[0\]"):
        parse_templates(document)


def test_a_broken_file_raises_a_deployment_error_not_an_app_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `ResourceError` deliberately is not an `AppError`: no status code helps
    # a client, and the only correct response is to fail to start.
    broken = tmp_path / "purpose_templates.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(
        "app.services.templates.PURPOSE_TEMPLATES_FILE", broken, raising=True
    )
    load_purpose_templates.cache_clear()

    with pytest.raises(ResourceError, match="not valid JSON"):
        load_purpose_templates()

    load_purpose_templates.cache_clear()
