"""A completed session — what the athlete actually did — and its vocabulary.

**This repo has two things called a session, and they are not the same.**

* `app.domain.sessions` is the **planned** session: a prescription on the
  calendar with versioned intent and pinned anchors. Its row is
  `PlannedSessionRow` (table ``planned_sessions``).
* This module is the **completed** session: a real-world event reconstructed
  from a device file (WP-4) or typed in by hand. Its row is
  `app.persistence.activity.SessionRow` (table ``sessions``).

WP-6 links the two: `app.domain.matching` scores a recording against a
prescription and :class:`SessionMatchStatus` is where the answer lands on this
side of the link.

What lives here is vocabulary — the enums the session, recording, quarantine
and ingest-log rows are written in — plus the two calculations that are about
the session rather than its samples: which discipline it was
(:func:`classify_discipline`) and which athlete-local day it belongs to
(:func:`session_date`). The stream mathematics are next door in
`app.domain.streams`, which imports :class:`QuarantineReason` from here and is
otherwise independent of this module.
"""

import datetime as dt
import re
from enum import StrEnum
from functools import cache
from zoneinfo import ZoneInfo, available_timezones

from app.domain.athlete import Discipline


class SessionDiscipline(StrEnum):
    """What a *recorded* session was.

    A superset of `app.domain.athlete.Discipline`, and deliberately a separate
    enum: `Discipline` is the vocabulary of things we **prescribe**, and every
    purpose, workout and planned session is one of its two members. A device
    file, by contrast, can hold a walk, a swim or a sport the head unit does
    not name — things we never plan and never score, but must still be able to
    ingest without lying about. ``OTHER`` is that bucket.

    The two shared members carry the **same string values** as `Discipline`,
    so the WP-6 candidate query is a value comparison and the athlete-facing
    vocabulary is one vocabulary (see :func:`as_planned_discipline`).
    """

    CYCLING = "cycling"
    STRENGTH = "strength"
    OTHER = "other"


def as_planned_discipline(discipline: SessionDiscipline) -> Discipline | None:
    """Return the planning discipline this recorded one corresponds to.

    ``None`` for ``OTHER``: there is no planned session it could ever match,
    which is exactly what WP-6 needs to know before it looks for one.
    """
    if discipline is SessionDiscipline.OTHER:
        return None
    return Discipline(discipline.value)


class ClassificationSource(StrEnum):
    """How a session's discipline was arrived at.

    Stored beside the discipline so the athlete's override (which sets
    ``discipline_overridden``) can be told from a guess, and so a guess can be
    told from the file saying so outright.

    ``MANUAL`` is the third answer and belongs to the sessions nobody
    classified: a hand-entered gym session (B-6) has a discipline because the
    athlete typed one, which is neither a file's sport field nor an inference
    over channels it does not have. Spelling it ``sport_field`` would put the
    weakest claim in this system — "the recording said so" — on the one
    session that has no recording.
    """

    SPORT_FIELD = "sport_field"
    HEURISTIC = "heuristic"
    MANUAL = "manual"


class RecordingKind(StrEnum):
    """Whether a session came from a device file or was entered by hand."""

    DEVICE = "device"
    MANUAL = "manual"


class SessionMatchStatus(StrEnum):
    """Where a completed session stands relative to the plan.

    WP-6 fills the lifecycle the column was sized for — every member here
    is at most as long as ``unmatched``, so no migration widened it.

    ``UNMATCHED`` is the state every session is written in and the state a
    **pending** proposal leaves it in: a proposal is a question, and a session
    with an unanswered question about it has not been matched. ``UNPLANNED``
    is an answer — nothing was planned that this could be, or the athlete said
    so — and is what distinguishes "we have not decided" from "there is
    nothing to decide". ``DISPLACED`` is the executed-instead-of case
    (`app.domain.matching`): the athlete trained, deliberately not the thing on
    the calendar, and the session is scored standalone.
    """

    UNMATCHED = "unmatched"
    MATCHED = "matched"
    UNPLANNED = "unplanned"
    DISPLACED = "displaced"


class SessionContext(StrEnum):
    """What kind of outing the session was (addenda R5).

    **Reserved, no behavior.** The MVP writes ``TRAINING`` for every session.
    WP-6 does not read it either: matching is a comparison of a recording with
    a prescription, and nothing in that comparison changes with the context —
    it is WP-7's *rubric* that switches on it (a group ride is not judged for
    adherence), and WP-7 is where the values are first consumed.
    """

    TRAINING = "training"
    COMMUTE = "commute"
    GROUP_RIDE = "group_ride"
    RACE = "race"
    EVENT = "event"


class QuarantineReason(StrEnum):
    """Why a file (or one activity within it) was not ingested.

    Machine-readable because both the quarantine row and the inbox UI show it,
    and because the confirm/reject actions (B-4) differ by reason: only a
    ``SUSPECTED_DUPLICATE`` has something safe to ingest on reject.

    The first four are `app.domain.streams.validate`'s verdicts. The last two
    are the pipeline's own (WP-4 Phase B) — a file it could not read at all,
    and a file whose time range overlaps a session already ingested — and are
    named here so the column's vocabulary is complete from the first migration.
    """

    NO_SAMPLES = "no_samples"
    NON_MONOTONIC_TIMESTAMPS = "non_monotonic_timestamps"
    TOO_SHORT = "too_short"
    IMPLAUSIBLE_CHANNEL = "implausible_channel"
    UNREADABLE_FILE = "unreadable_file"
    SUSPECTED_DUPLICATE = "suspected_duplicate"


class QuarantineStatus(StrEnum):
    """What the athlete decided about a quarantined file.

    ``PENDING`` until they act. ``CONFIRMED_DISCARDED`` means the quarantined
    copy was thrown away — never the original of an already-ingested twin.
    ``REJECTED_INGESTED`` means "this is not a duplicate", and the file went
    back through the pipeline as its own session.
    """

    PENDING = "pending"
    CONFIRMED_DISCARDED = "confirmed_discarded"
    REJECTED_INGESTED = "rejected_ingested"


class IngestOutcome(StrEnum):
    """What happened to one file the pipeline looked at.

    ``DUPLICATE_FILE`` is a success, not a failure: re-seeing a hash the
    pipeline has already ingested is the idempotency guarantee working, and it
    is logged rather than raised.
    """

    INGESTED = "ingested"
    DUPLICATE_FILE = "duplicate_file"
    QUARANTINED = "quarantined"
    ERROR = "error"


# --- discipline classification (work order A-5) -------------------------------

#: Raw sport strings that name a discipline outright. **Keys are matched
#: against the file's string lowercased and stripped** (see
#: :func:`classify_discipline`), so every key here is lowercase and one key
#: covers every casing a source uses — but not every *spelling*: Strava writes
#: ``VirtualRide``, which lowercases to ``virtualride``, while a FIT sub-sport
#: and most exporters write ``virtual_ride``, and neither is reachable from the
#: other. Both are listed. FIT's `sport` enum is the source of most of them;
#: GPX and TCX carry free text, which is why the map is on strings rather than
#: on a parsed enum.
SPORT_FIELD_DISCIPLINE: dict[str, SessionDiscipline] = {
    "cycling": SessionDiscipline.CYCLING,
    "biking": SessionDiscipline.CYCLING,
    "ride": SessionDiscipline.CYCLING,
    "virtualride": SessionDiscipline.CYCLING,
    "virtual_ride": SessionDiscipline.CYCLING,
    "e_biking": SessionDiscipline.CYCLING,
    "training": SessionDiscipline.STRENGTH,
    "strength_training": SessionDiscipline.STRENGTH,
    "weight_training": SessionDiscipline.STRENGTH,
    "fitness_equipment": SessionDiscipline.STRENGTH,
}

#: Longest a session may be and still be guessed to be a gym session. A ride
#: without power or GPS (a trainer with only a speed sensor, a head unit that
#: lost its satellites) is common; a three-hour gym session is not.
MAX_STRENGTH_HEURISTIC_S = 90 * 60


def classify_discipline(
    *,
    sport: str | None,
    has_power: bool,
    has_speed: bool,
    has_gps: bool,
    duration_s: float,
) -> tuple[SessionDiscipline, ClassificationSource]:
    """Guess what a recorded session was, and say how the guess was made.

    The file's own sport field wins whenever it says something we recognise
    (:data:`SPORT_FIELD_DISCIPLINE`) — a head unit set to "strength training"
    is better evidence than any inference over the channels it recorded.

    Failing that, the channels decide, in this order:

    1. power or speed present -> ``CYCLING``. Both are things a bike computer
       records and a gym watch does not.
    2. shorter than :data:`MAX_STRENGTH_HEURISTIC_S`, no GPS and no power ->
       ``STRENGTH``. That is the shape of a watch recording in a gym: heart
       rate, indoors, an hour.
    3. anything else -> ``OTHER``, which is honest rather than a coin flip.
       Nothing scores an ``OTHER`` session; the athlete overrides it if it
       was really a ride (B-6), and the override is recorded as such.

    Args:
        sport: The raw sport string from the file, if it carried one.
        has_power: Whether a power channel was recorded.
        has_speed: Whether a speed channel was recorded.
        has_gps: Whether latitude/longitude were recorded.
        duration_s: Elapsed duration in seconds.

    Returns:
        The discipline and how it was reached — never a bare discipline, so
        the session row can record what it is trusting.
    """
    if sport is not None:
        known = SPORT_FIELD_DISCIPLINE.get(sport.strip().lower())
        if known is not None:
            return known, ClassificationSource.SPORT_FIELD
    if has_power or has_speed:
        return SessionDiscipline.CYCLING, ClassificationSource.HEURISTIC
    if duration_s < MAX_STRENGTH_HEURISTIC_S and not has_gps and not has_power:
        return SessionDiscipline.STRENGTH, ClassificationSource.HEURISTIC
    return SessionDiscipline.OTHER, ClassificationSource.HEURISTIC


# --- the conditions a measurement was taken under (#23) -----------------------

#: Plausibility bounds on an athlete-reported ambient temperature, in °C.
#: Wide enough for a Nordic winter commute and a heatwave climb; anything
#: outside them is a typo or a unit mix-up (a Fahrenheit 85 must be refused,
#: not stored as 85 °C — every later heat-comparison would trust it).
MIN_TEMPERATURE_C, MAX_TEMPERATURE_C = -30.0, 50.0


def check_temperature(temperature_c: float) -> None:
    """Refuse an implausible ambient temperature.

    Raises:
        ValueError: When it is outside the plausibility bounds, naming them.
    """
    if not MIN_TEMPERATURE_C <= temperature_c <= MAX_TEMPERATURE_C:
        raise ValueError(
            f"temperature_c must be between {MIN_TEMPERATURE_C:g} and "
            f"{MAX_TEMPERATURE_C:g} °C (ambient temperature, in Celsius); "
            f"got {temperature_c:g}"
        )


# --- the athlete-local day (work order A-6, build plan WP-4.4) ----------------

#: The timezone stored when the file offers nothing better. Not a guess about
#: where the athlete was — a statement that the file did not say.
UTC_TIMEZONE = "UTC"

#: `UTC+02:00` / `UTC-05:00`: the fixed-offset spelling written when a file
#: carries a local offset but no region name (which is the usual FIT case —
#: `local_timestamp` is a shifted clock reading, not a zone).
_FIXED_OFFSET = re.compile(r"^UTC(?P<sign>[+-])(?P<hours>\d{2}):(?P<minutes>\d{2})$")


def fixed_offset_label(offset: dt.timedelta) -> str:
    """Render a UTC offset as the fixed-offset timezone string we store.

    ``timedelta(hours=2)`` -> ``"UTC+02:00"``; ``timedelta(hours=-5, minutes=-30)``
    -> ``"UTC-05:30"``; a zero offset -> :data:`UTC_TIMEZONE`, because "UTC" is
    what the file is actually saying and round-tripping it as ``UTC+00:00``
    would invent a distinction.

    Raises:
        ValueError: When the offset is not a whole number of minutes, which no
            real zone is and every device that writes one gets right.
    """
    total_seconds = int(offset.total_seconds())
    if total_seconds != offset.total_seconds() or total_seconds % 60:
        raise ValueError(
            f"a UTC offset must be a whole number of minutes, got {offset!r}"
        )
    if total_seconds == 0:
        return UTC_TIMEZONE
    sign = "-" if total_seconds < 0 else "+"
    hours, minutes = divmod(abs(total_seconds) // 60, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def timezone_label(local_offset: dt.timedelta | None) -> str:
    """The timezone string to store for an activity, best-effort (§0 decision 5).

    A parser that recovered a region name stores it directly; this is the
    fallback for everything else, and it is the common case — FIT's
    ``local_timestamp`` gives an offset, never a zone.
    """
    if local_offset is None:
        return UTC_TIMEZONE
    return fixed_offset_label(local_offset)


#: Keys the zone database ships that are not IANA zone *names*: ``localtime``
#: is a copy of whatever the host's clock is set to and ``Factory`` is the
#: database's "nobody configured a timezone" placeholder. Both are TZif files
#: on ``zoneinfo``'s search path, so `ZoneInfo` resolves them happily — and the
#: browser's `Intl` resolves neither, so storing one gives the UI a timezone it
#: cannot render and re-derives the athlete's local date from whatever
#: ``/etc/localtime`` the container happens to have.
_NON_IANA_ZONE_KEYS = frozenset({"localtime", "Factory", "posixrules"})


@cache
def _iana_timezones() -> frozenset[str]:
    """Every IANA zone name this installation knows, scanned once.

    Cached because :func:`zoneinfo.available_timezones` walks the whole zone
    directory, and :func:`parse_timezone` runs per session read.
    """
    return frozenset(available_timezones()) - _NON_IANA_ZONE_KEYS


def parse_timezone(tz: str) -> dt.tzinfo:
    """Resolve a stored timezone string to a ``tzinfo``.

    Accepts the three forms the session column can hold: :data:`UTC_TIMEZONE`,
    a fixed offset as written by :func:`fixed_offset_label`, and an IANA region
    name for the rare source that provides one.

    The IANA branch accepts only names in :func:`zoneinfo.available_timezones`
    less :data:`_NON_IANA_ZONE_KEYS` — a stricter test than "``ZoneInfo`` took
    it", and deliberately so: this value is stored and then handed to the
    frontend, which resolves it with `Intl`. A name Python accepts and `Intl`
    does not is a session whose date the UI cannot re-derive, and the athlete
    is the one who finds out.

    Raises:
        ValueError: When the string is none of those. Callers store this value;
            a stored timezone that cannot be resolved would make the session's
            date unrecoverable, so it fails loudly at the boundary instead.
    """
    if tz == UTC_TIMEZONE:
        return dt.UTC
    match = _FIXED_OFFSET.match(tz)
    if match is not None:
        offset = dt.timedelta(hours=int(match["hours"]), minutes=int(match["minutes"]))
        if match["sign"] == "-":
            offset = -offset
        try:
            return dt.timezone(offset)
        except ValueError as exc:
            raise ValueError(f"{tz!r} is not a usable UTC offset") from exc
    unresolvable = ValueError(
        f"{tz!r} is neither {UTC_TIMEZONE!r}, a UTC±HH:MM offset, nor a "
        "known IANA timezone name"
    )
    if tz not in _iana_timezones():
        raise unresolvable
    try:
        return ZoneInfo(tz)
    except (KeyError, ValueError) as exc:
        raise unresolvable from exc


def session_date(start_utc: dt.datetime, tz: str) -> dt.date:
    """The athlete-local day a completed session belongs to.

    The date of its **start** in the athlete's local timezone, so a session
    that runs past midnight belongs to the day it began — the day the athlete
    would name if asked, and the day the planned session it answers to sits on
    (build plan WP-4.4).

    Args:
        start_utc: When the session started, aware UTC.
        tz: The session's stored timezone; see :func:`parse_timezone`.

    Raises:
        ValueError: When ``start_utc`` is naive, or ``tz`` is unresolvable.
    """
    if start_utc.tzinfo is None:
        raise ValueError(
            "session_date needs an aware start time; a naive one would silently "
            "be read as local time on whichever machine happened to run this"
        )
    return start_utc.astimezone(parse_timezone(tz)).date()
