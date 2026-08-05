"""The athlete profile: an empty one is legal, a nonsensical one is not."""

import datetime as dt

import pytest

from app.domain.athlete import AthleteProfile, Discipline, Sex


def test_an_empty_profile_is_legal() -> None:
    # The profile is bootstrapped before anything is known about the athlete,
    # so "nothing filled in" must not be an error state.
    profile = AthleteProfile()

    assert profile.name is None
    assert profile.sex is Sex.UNSPECIFIED
    assert profile.capabilities == {}


def test_a_blank_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        AthleteProfile(name="   ")


@pytest.mark.parametrize("height", [17.0, 1750.0])
def test_implausible_heights_are_rejected(height: float) -> None:
    with pytest.raises(ValueError, match="height_cm must be between"):
        AthleteProfile(height_cm=height)


def test_a_birth_year_before_1900_is_a_typo() -> None:
    with pytest.raises(ValueError, match="must be in or after 1900"):
        AthleteProfile(date_of_birth=dt.date(1089, 5, 4))


def test_age_counts_whole_years() -> None:
    profile = AthleteProfile(date_of_birth=dt.date(1990, 6, 15))

    assert profile.age_on(dt.date(2026, 6, 14)) == 35, "the day before counts as 35"
    assert profile.age_on(dt.date(2026, 6, 15)) == 36, "the birthday itself counts"
    assert profile.age_on(dt.date(2026, 6, 16)) == 36


def test_leap_day_births_age_on_the_first_of_march() -> None:
    profile = AthleteProfile(date_of_birth=dt.date(2000, 2, 29))

    assert profile.age_on(dt.date(2026, 2, 28)) == 25
    assert profile.age_on(dt.date(2026, 3, 1)) == 26


def test_age_is_unknown_without_a_birth_date() -> None:
    assert AthleteProfile().age_on(dt.date(2026, 6, 15)) is None


def test_asking_for_an_age_before_birth_is_an_error() -> None:
    profile = AthleteProfile(date_of_birth=dt.date(1990, 6, 15))

    with pytest.raises(ValueError, match="precedes the date of birth"):
        profile.age_on(dt.date(1989, 1, 1))


def test_capabilities_are_free_form_per_discipline() -> None:
    profile = AthleteProfile(
        capabilities={"cycling": {"weekly_hours": 8}, "strength": {"level": "novice"}}
    )

    assert profile.capability(Discipline.CYCLING) == {"weekly_hours": 8}
    assert profile.capability(Discipline.STRENGTH) == {"level": "novice"}


def test_a_missing_or_malformed_capability_reads_as_empty() -> None:
    # Free-form means the MVP stores whatever the client sends; a reader must
    # not crash on a shape it did not expect.
    profile = AthleteProfile(capabilities={"cycling": "not a mapping"})

    assert profile.capability(Discipline.CYCLING) == {}
    assert profile.capability(Discipline.STRENGTH) == {}


def test_profiles_are_immutable() -> None:
    with pytest.raises(AttributeError):
        AthleteProfile().name = "new"  # type: ignore[misc]
