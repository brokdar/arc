"""Laying several recordings on one grid, and the one channel that breaks on it.

`app.ingest.analysis._join` is the seam between a merged session's files and
every metric computed over it (WP-6.5). Almost nothing about a metric changes
when it runs over a join rather than a single recording — rows with no reading
are excluded rather than read as zero, and the load's duration term is the
recordings' own summed — which is exactly why the **cumulative** channel is
worth a test file of its own: the odometer is the only column whose meaning
depends on where one recording ends and the next begins, and reading it end to
end blames the athlete's device for a column arc itself assembled.
"""

import datetime as dt

import pytest

from app.domain.metrics import Measured, distance_km
from app.domain.streams import StreamChannel
from app.ingest.analysis import _join, _Segment
from app.ingest.parquet import StoredStreams
from app.persistence.activity import RecordingRow

ORIGIN = dt.datetime(2026, 8, 10, 6, 0, tzinfo=dt.UTC)

#: Rows in each of the two recordings, and the garage-door gap between them.
LEG_ROWS = 600
GAP_ROWS = 100


def stored(*, start: dt.datetime, speed: float, metres_per_row: float) -> StoredStreams:
    """One recording's stored grid: steady speed and an odometer from zero.

    From **zero**, because that is what a device writes: every recording's
    cumulative distance field counts from the moment that recording started,
    and the second file of a merged session knows nothing about the first.
    """
    return StoredStreams(
        t0=start,
        device_t=tuple(start + dt.timedelta(seconds=row) for row in range(LEG_ROWS)),
        raw={},
        fixed={
            StreamChannel.SPEED: tuple(speed for _ in range(LEG_ROWS)),
            StreamChannel.DISTANCE: tuple(
                row * metres_per_row for row in range(LEG_ROWS)
            ),
        },
        sources={StreamChannel.DISTANCE: "record.distance"},
    )


def two_recordings() -> list[_Segment]:
    """The merged session under test, in the order the join receives it."""
    second_start = ORIGIN + dt.timedelta(seconds=LEG_ROWS + GAP_ROWS)
    return [
        _Segment(
            recording=RecordingRow(recording_stops=[]),
            stored=stored(start=ORIGIN, speed=10.0, metres_per_row=10.15),
            offset=0,
        ),
        _Segment(
            recording=RecordingRow(recording_stops=[]),
            stored=stored(start=second_start, speed=8.0, metres_per_row=8.12),
            offset=LEG_ROWS + GAP_ROWS,
        ),
    ]


def test_the_join_carries_where_each_recording_starts_and_ends() -> None:
    joined = _join(two_recordings())

    assert joined.length == 2 * LEG_ROWS + GAP_ROWS
    assert joined.segments == ((0, LEG_ROWS), (LEG_ROWS + GAP_ROWS, joined.length))
    # The gap is unrecorded rows and a reported stop, which is what it is.
    assert joined.recording_stops == ((LEG_ROWS, LEG_ROWS + GAP_ROWS),)


def test_the_joined_odometer_is_read_per_recording_and_summed() -> None:
    # Read end to end the joined column runs 0 -> 6 080, gap, 0 -> 4 864: not
    # monotone, and the reset guard would tell the athlete their device
    # corrupted a file this join assembled. Per segment it is two ordinary
    # spans that add up.
    joined = _join(two_recordings())
    speed = joined.channels[StreamChannel.SPEED]
    odometer = joined.channels[StreamChannel.DISTANCE]

    assessed = distance_km(speed, odometer, segments=joined.segments)
    end_to_end = distance_km(speed, odometer)

    assert isinstance(assessed, Measured)
    assert isinstance(end_to_end, Measured)
    assert assessed.value == pytest.approx((599 * 10.15 + 599 * 8.12) / 1000)
    assert not any(
        "reset" in note or "corrupted" in note
        for note in assessed.explanation.assumptions
    )
    # And the state this replaced, pinned so the fix cannot quietly regress:
    # end to end the column is refused and the ride is integrated from speed.
    assert "goes backwards" in end_to_end.explanation.assumptions[0]


def test_a_single_recording_joins_to_exactly_one_segment() -> None:
    # Every session the MVP ingests. The segment list must not turn the
    # ordinary path into a special case of the merged one.
    [only] = two_recordings()[:1]

    joined = _join([only])

    assert joined.segments == ((0, LEG_ROWS),)
    assert joined.recording_stops == ()
