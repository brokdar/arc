"""The sync conflict rule, as pure functions (`app.domain.sync`).

One test per row of the direction's §13.2 conflict table at the level of the
rule itself; `tests/integration/test_sync_conflicts.py` holds the same rows
against Postgres through the service.
"""

import datetime as dt

import pytest

from app.domain.sync import (
    EXISTS,
    MAX_SCHEMA_VERSION,
    MIN_SCHEMA_VERSION,
    Stamp,
    decode_clocks,
    encode_clocks,
    is_alive,
    merge,
    schema_version_supported,
    stamp_for,
)

T10 = dt.datetime(2026, 11, 10, 10, 0, tzinfo=dt.UTC)


def at(minutes: int, received: int) -> Stamp:
    return Stamp(edit_time=T10 + dt.timedelta(minutes=minutes), received=received)


def test_a_later_edit_wins_per_field_and_leaves_the_others() -> None:
    """(a) Each field keeps the edit with the latest client edit time."""
    values, clocks = {}, {}
    first = merge(values, clocks, {"reps": 5, "load_kg": 100.0}, at(0, 1))
    coach = merge(first.values, first.clocks, {"load_kg": 102.5}, at(5, 2))
    phone_late = merge(
        coach.values, coach.clocks, {"reps": 6, "load_kg": 97.5}, at(3, 3)
    )

    assert phone_late.values == {"reps": 6, "load_kg": 102.5}
    assert phone_late.changed == ("reps",)


def test_identical_edit_times_resolve_by_receive_order() -> None:
    """Same client edit time: the one the server received later wins."""
    first = merge({}, {}, {"reps": 5}, at(0, 1))
    second = merge(first.values, first.clocks, {"reps": 8}, at(0, 2))
    assert second.values == {"reps": 8}

    # And the order is the receive sequence, never which value is bigger.
    stale = merge(second.values, second.clocks, {"reps": 3}, at(0, 1))
    assert stale.values == {"reps": 8}
    assert stale.changed == ()


def test_a_delete_newer_than_an_edit_leaves_a_tombstone() -> None:
    """(d) A delete newer than the edit wins; the row stays gone."""
    created = merge({}, {}, {EXISTS: True, "reps": 5}, at(0, 1))
    deleted = merge(created.values, created.clocks, {EXISTS: False}, at(10, 2))
    older_edit = merge(
        deleted.values, deleted.clocks, {EXISTS: True, "reps": 7}, at(5, 3)
    )

    assert not is_alive(older_edit.values)
    # The field itself is newer than its last write and still merges, so a
    # later revival carries it.
    assert older_edit.values["reps"] == 7


def test_an_edit_newer_than_a_delete_revives_the_row() -> None:
    """(d) An edit newer than the delete brings the row back."""
    created = merge({}, {}, {EXISTS: True, "reps": 5}, at(0, 1))
    deleted = merge(created.values, created.clocks, {EXISTS: False}, at(5, 2))
    revived = merge(deleted.values, deleted.clocks, {EXISTS: True, "reps": 9}, at(8, 3))

    assert is_alive(revived.values)
    assert revived.values["reps"] == 9


def test_an_offline_edit_after_the_upload_applies_only_when_newer() -> None:
    """(c) An edit made after the original uploaded is applied if newer."""
    original = merge({}, {}, {"rir": 2}, at(0, 1))
    newer = merge(original.values, original.clocks, {"rir": 1}, at(20, 2))
    older = merge(original.values, original.clocks, {"rir": 4}, at(-20, 2))

    assert newer.values == {"rir": 1}
    assert older.values == {"rir": 2}


def test_the_latest_dont_learn_toggle_wins() -> None:
    """(e) Toggled offline on two devices: the latest toggle stands."""
    on = merge({}, {}, {"dont_learn": True}, at(10, 1))
    off_earlier = merge(on.values, on.clocks, {"dont_learn": False}, at(5, 2))
    assert off_earlier.values == {"dont_learn": True}
    off_later = merge(on.values, on.clocks, {"dont_learn": False}, at(15, 3))
    assert off_later.values == {"dont_learn": False}
    assert off_later.changed == ("dont_learn",)


def test_a_coach_edit_is_stamped_with_server_time() -> None:
    """The coach's edit time is its server time, whatever it claimed."""
    server_now = T10 + dt.timedelta(minutes=5)
    claimed = T10 - dt.timedelta(days=1)

    assert stamp_for(
        agent=True, client_edit_time=claimed, server_now=server_now, received=4
    ) == Stamp(edit_time=server_now, received=4)
    assert stamp_for(
        agent=False, client_edit_time=claimed, server_now=server_now, received=4
    ) == Stamp(edit_time=claimed, received=4)


def test_clocks_round_trip_through_json() -> None:
    clocks = {"reps": at(3, 7), EXISTS: at(0, 1)}
    assert decode_clocks(encode_clocks(clocks)) == clocks


def test_edit_times_in_different_offsets_compare_as_instants() -> None:
    plus_two = dt.timezone(dt.timedelta(hours=2))
    later_instant_in_another_offset = Stamp(
        edit_time=dt.datetime(2026, 11, 10, 12, 1, tzinfo=plus_two), received=1
    )
    assert later_instant_in_another_offset > at(0, 2)


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        (MIN_SCHEMA_VERSION, True),
        (MAX_SCHEMA_VERSION, True),
        (MAX_SCHEMA_VERSION + 1, False),
        (MIN_SCHEMA_VERSION - 1, False),
    ],
)
def test_schema_versions_outside_the_window_are_unsupported(
    version: int, supported: bool
) -> None:
    assert schema_version_supported(version) is supported
