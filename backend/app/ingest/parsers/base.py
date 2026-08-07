"""What every parser shares: the failure, the units, and the source rules.

A parser's whole job is to turn one file into a sequence of
`app.domain.streams.ParsedActivity` — the interchange shape — and to say
honestly what it could not do. It never repairs, never resamples and never
decides whether a file is fit to ingest: those are the domain's
(:func:`app.domain.streams.resample`, :func:`~app.domain.streams.clean`,
:func:`~app.domain.streams.validate`) and the pipeline's.

The one error type is :class:`UnreadableFileError`. Anything a parser raises
that is not that is a bug; the pipeline still catches it, quarantines the file
and says so, because losing a file is worse than logging a stack trace.
"""

import datetime as dt
from collections.abc import Mapping, Sequence

from app.domain.streams import StreamChannel

#: Degrees per FIT semicircle. FIT stores latitude and longitude as signed
#: 32-bit semicircles; ``2**31`` of them make 180 degrees.
SEMICIRCLE_DEGREES = 180.0 / 2**31

#: File extensions `parse` knows how to open, lowercase and without the dot.
SUPPORTED_EXTENSIONS = frozenset({"fit", "gpx", "tcx"})

#: The rule recorded when a channel had exactly one plausible source, i.e.
#: there was no choice to make (A4.3).
ONLY_CANDIDATE = "only candidate"

#: The rule recorded when several devices could have produced a channel and
#: the file does not say which one did. FIT writes one `record.power` field
#: and a `device_info` message per paired sensor, with nothing linking them,
#: so the tie-break is deterministic rather than evidential — and it is spelled
#: out here so nobody mistakes it for evidence (A4.3).
LOWEST_DEVICE_INDEX = "lowest device_index among {count} candidates"

#: The rule recorded when the file carried the channel but named no device
#: capable of producing it.
NO_DEVICE_INFO = "no matching device_info entry; the record field was used"

#: The source label used when no device could be named.
RECORD_FIELD = "record.{channel}"


class UnreadableFileError(Exception):
    """The file could not be parsed at all.

    Raised for a corrupt container, a truncated file, an extension nothing
    here can open, or a document whose structure the parser cannot make sense
    of. The pipeline turns it into a
    `app.domain.activity.QuarantineReason.UNREADABLE_FILE` record, so the
    message is athlete-facing: say what was wrong with the file, not which
    library raised what.
    """


def semicircles_to_degrees(semicircles: float) -> float:
    """Convert a FIT position value to degrees."""
    return semicircles * SEMICIRCLE_DEGREES


def as_utc(value: dt.datetime) -> dt.datetime:
    """Return an aware UTC copy of a timestamp.

    Naive values are read as UTC. Every format here is specified in UTC —
    FIT counts seconds from 1989-12-31 UTC, GPX and TCX write ISO instants
    with a zone — so a naive value means the library dropped the zone, not
    that the instant is local.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def sample_values(
    candidates: Mapping[StreamChannel, float | int | None],
) -> dict[StreamChannel, float]:
    """Keep the channels this sample actually carried, as floats.

    A record with no power field is a sample **without** power, not a sample
    with zero watts — the distinction the whole null-versus-zero rule rests
    on, applied at the point the file is read.
    """
    return {
        channel: float(value)
        for channel, value in candidates.items()
        if value is not None
    }


def choose_source(
    candidates: Sequence[str], *, channel: StreamChannel, present: bool
) -> tuple[tuple[str, ...], str | None, str | None]:
    """Pick the source label for a channel and say why (A4.3).

    Args:
        candidates: Device labels that could have produced the channel, in the
            order the file listed them — which for FIT is device_index order.
        channel: The channel being sourced.
        present: Whether the samples actually carry the channel. A file that
            names a power meter but records no power gets no source at all.

    Returns:
        ``(candidates, source, rule)``. All three are ``None``/empty when the
        channel is absent, so a recording row never claims a source for a
        column it does not have.
    """
    if not present:
        return (), None, None
    if not candidates:
        return (), RECORD_FIELD.format(channel=channel.value), NO_DEVICE_INFO
    if len(candidates) == 1:
        return tuple(candidates), candidates[0], ONLY_CANDIDATE
    return (
        tuple(candidates),
        candidates[0],
        LOWEST_DEVICE_INDEX.format(count=len(candidates)),
    )
