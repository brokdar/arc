"""The versioning primitives every derived artefact will be built on.

Nothing in WP-1 is versioned yet — anchors are an append-only history, not a
version chain — but scores, metrics and alignments all are (build-plan
invariant 1), and these are the two questions they all ask: what is current,
and what was current at time T.
"""

import datetime as dt
import uuid
from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.versioning import (
    FIRST_VERSION,
    Versioned,
    current_version,
    next_version,
    version_as_seen_at,
)

MONDAY = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)
HOUR = dt.timedelta(hours=1)


def _chain(count: int) -> list[Versioned[str]]:
    """A well-formed chain of ``count`` versions, one hour apart."""
    versions = [Versioned.first(f"v{FIRST_VERSION}", as_of=MONDAY)]
    for step in range(1, count):
        closed, successor = versions[-1].recomputed(
            f"v{step + 1}", as_of=MONDAY + step * HOUR, reason="anchor changed"
        )
        versions[-1] = closed
        versions.append(successor)
    return versions


def test_first_starts_a_chain_at_version_one() -> None:
    version = Versioned.first("payload", as_of=MONDAY)

    assert version.version == FIRST_VERSION
    assert version.superseded_by is None
    assert version.recompute_reason is None


def test_recompute_closes_the_old_version_and_opens_the_next() -> None:
    original = Versioned.first("first", as_of=MONDAY)

    closed, successor = original.recomputed(
        "second", as_of=MONDAY + HOUR, reason="intent edited"
    )

    # Same artefact, next version, and the old link points at the new one so a
    # reader holding the old id can walk forward.
    assert successor.artefact_id == original.artefact_id
    assert successor.version == original.version + 1
    assert closed.superseded_by == successor.id
    assert successor.recompute_reason == "intent edited"
    assert closed.payload == "first"


def test_recompute_demands_a_reason() -> None:
    with pytest.raises(ValueError, match="state its reason"):
        Versioned.first("first", as_of=MONDAY).recomputed(
            "second", as_of=MONDAY + HOUR, reason=""
        )


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Versioned.first("payload", as_of=dt.datetime(2026, 3, 2, 9, 0))  # noqa: DTZ001


def test_version_one_cannot_claim_a_recompute_reason() -> None:
    with pytest.raises(ValueError, match="no recompute_reason"):
        Versioned(
            id=uuid.uuid7(),
            artefact_id=uuid.uuid7(),
            version=FIRST_VERSION,
            as_of=MONDAY,
            payload="x",
            recompute_reason="from nowhere",
        )


def test_current_version_is_the_unsuperseded_tip() -> None:
    chain = _chain(3)

    current = current_version(chain)

    assert current is not None
    assert current.version == 3
    assert current.payload == "v3"


def test_current_version_ignores_input_order() -> None:
    chain = _chain(4)

    assert current_version(reversed(chain)) == current_version(chain)


def test_current_version_of_nothing_is_none() -> None:
    assert current_version([]) is None


def test_a_fully_superseded_chain_has_no_current_version() -> None:
    # Every link closed means the tip was lost, which is a bug — better a
    # `None` the caller must handle than a plausible wrong answer.
    chain = _chain(2)
    broken = [chain[0], replace(chain[1], superseded_by=uuid.uuid7())]

    assert current_version(broken) is None


def _payload_seen_at(chain: list[Versioned[str]], moment: dt.datetime) -> str:
    seen = version_as_seen_at(chain, moment)
    assert seen is not None, f"nothing visible at {moment}"
    return seen.payload


def test_version_as_seen_at_returns_what_a_reader_saw_then() -> None:
    chain = _chain(3)  # as_of MONDAY, +1h, +2h

    assert _payload_seen_at(chain, MONDAY + HOUR) == "v2"
    assert _payload_seen_at(chain, MONDAY + dt.timedelta(minutes=90)) == "v2"
    assert _payload_seen_at(chain, MONDAY + 5 * HOUR) == "v3"


def test_version_as_seen_at_before_the_artefact_existed_is_none() -> None:
    assert version_as_seen_at(_chain(3), MONDAY - HOUR) is None


def test_version_as_seen_at_rejects_a_naive_moment() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        version_as_seen_at(_chain(2), dt.datetime(2026, 3, 2, 9, 0))  # noqa: DTZ001


def test_next_version_continues_the_chain() -> None:
    assert next_version([]) == FIRST_VERSION
    assert next_version(_chain(3)) == 4


@given(length=st.integers(min_value=1, max_value=12), offset=st.integers(0, 20))
def test_the_two_helpers_agree_at_the_end_of_time(length: int, offset: int) -> None:
    # The version "seen at" a moment after the last recomputation is the
    # current one — the property every later scoring rescore depends on.
    chain = _chain(length)
    far_future = MONDAY + (length + offset) * HOUR

    assert version_as_seen_at(chain, far_future) == current_version(chain)
