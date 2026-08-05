"""Anchor rules: what a legal version is, and which one is in force.

"Which anchor was in force" is the question every derived value depends on
(build-plan invariant 2), and it is not "the newest row": versions can be
back-dated and forward-dated, and a value entered today must not retroactively
become what last week's score was looking at.
"""

import datetime as dt

import pytest

from app.domain.anchors import (
    AnchorSource,
    AnchorType,
    AnchorUnit,
    AnchorVersion,
    Provenance,
    StalenessState,
    anchor_as_of,
    current_anchor,
    history_by_type,
    sorted_history,
)

MARCH = dt.date(2026, 3, 1)
NOW = dt.datetime(2026, 3, 15, 12, 0, tzinfo=dt.UTC)
DAY = dt.timedelta(days=1)


def ftp(
    value: float = 250,
    *,
    effective: dt.date = MARCH,
    created: dt.datetime = NOW,
    provenance: Provenance = Provenance.ESTIMATED,
    protocol: str | None = None,
) -> AnchorVersion:
    """An FTP version, varying only what a test is about."""
    return AnchorVersion(
        anchor_type=AnchorType.FTP,
        value=value,
        unit=AnchorUnit.WATT,
        provenance=provenance,
        protocol=protocol,
        effective_date=effective,
        created_at=created,
        source=AnchorSource.ATHLETE,
    )


# --- what a legal version is --------------------------------------------------


def test_a_version_defaults_to_fresh() -> None:
    # The staleness model is deferred; the field is not (build plan WP-1).
    assert ftp().staleness_state is StalenessState.FRESH


def test_implausible_values_are_rejected_per_anchor_type() -> None:
    with pytest.raises(ValueError, match="ftp value must be between"):
        ftp(25_000)


def test_the_unit_must_be_the_anchor_types_own() -> None:
    with pytest.raises(ValueError, match="measured in W"):
        AnchorVersion(
            anchor_type=AnchorType.FTP,
            value=250,
            unit=AnchorUnit.BPM,
            provenance=Provenance.ESTIMATED,
            effective_date=MARCH,
            created_at=NOW,
            source=AnchorSource.ATHLETE,
        )


def test_a_tested_value_must_state_its_protocol() -> None:
    with pytest.raises(ValueError, match="must state its protocol"):
        ftp(provenance=Provenance.TESTED)

    assert ftp(provenance=Provenance.TESTED, protocol="20min x0.95").protocol


def test_estimates_need_no_protocol() -> None:
    assert ftp(provenance=Provenance.ESTIMATED).protocol is None


def test_a_confidence_interval_must_bracket_the_value() -> None:
    with pytest.raises(ValueError, match="ci_low"):
        AnchorVersion(
            anchor_type=AnchorType.FTP,
            value=250,
            unit=AnchorUnit.WATT,
            provenance=Provenance.ESTIMATED,
            effective_date=MARCH,
            created_at=NOW,
            source=AnchorSource.ATHLETE,
            ci_low=260,
        )


def test_naive_creation_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ftp(created=dt.datetime(2026, 3, 15, 12, 0))  # noqa: DTZ001


def test_versions_are_immutable() -> None:
    with pytest.raises(AttributeError):
        ftp().value = 300  # type: ignore[misc]


# --- which version is in force ------------------------------------------------


def test_the_newest_effective_version_is_current() -> None:
    older = ftp(240, effective=MARCH)
    newer = ftp(260, effective=MARCH + 7 * DAY)

    assert current_anchor([older, newer]) is newer


def test_a_future_dated_version_is_not_yet_in_force() -> None:
    today = ftp(250, effective=NOW.date())
    planned = ftp(270, effective=NOW.date() + 30 * DAY)

    assert anchor_as_of([today, planned], NOW) is today


def test_a_correction_appended_later_wins_on_the_same_effective_date() -> None:
    original = ftp(250, effective=MARCH, created=NOW)
    correction = ftp(255, effective=MARCH, created=NOW + dt.timedelta(hours=1))

    in_force = anchor_as_of([original, correction], NOW + dt.timedelta(hours=2))

    assert in_force is correction


def test_a_back_dated_version_does_not_rewrite_the_past() -> None:
    # Appended today, effective from March. A score computed on 10 March was
    # not looking at it, and asking "as of 10 March" must still say so.
    original = ftp(250, effective=MARCH, created=NOW - 10 * DAY)
    back_dated = ftp(230, effective=MARCH, created=NOW)

    at_the_time = anchor_as_of([original, back_dated], NOW - 5 * DAY)
    now = anchor_as_of([original, back_dated], NOW)

    assert at_the_time is original
    assert now is back_dated


def test_no_history_means_nothing_is_in_force() -> None:
    assert current_anchor([]) is None
    assert anchor_as_of([], NOW) is None


def test_as_of_rejects_a_naive_moment() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        anchor_as_of([ftp()], dt.datetime(2026, 3, 15, 12, 0))  # noqa: DTZ001


def test_history_sorts_oldest_first_regardless_of_input_order() -> None:
    first = ftp(240, effective=MARCH)
    second = ftp(250, effective=MARCH + DAY)
    third = ftp(260, effective=MARCH + 2 * DAY)

    assert sorted_history([third, first, second]) == [first, second, third]


def test_history_by_type_keeps_the_anchors_apart() -> None:
    power = ftp(250)
    heart_rate = AnchorVersion(
        anchor_type=AnchorType.LTHR,
        value=168,
        unit=AnchorUnit.BPM,
        provenance=Provenance.TESTED,
        protocol="30min TT",
        effective_date=MARCH,
        created_at=NOW,
        source=AnchorSource.ATHLETE,
    )

    grouped = history_by_type([power, heart_rate])

    assert grouped[AnchorType.FTP] == [power]
    assert grouped[AnchorType.LTHR] == [heart_rate]
