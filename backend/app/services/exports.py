"""Exporting what was logged: full JSON, set-level CSV, Strong CSV and text.

Lock-in is one of the athletes' top complaints (direction §11.2), and the
other thing asked for was handing a single workout to an AI assistant. So
everything logged leaves arc in four shapes, all read-only and all built here
from the same rows, so the four cannot disagree about what a session held:

- **workouts.json** — openweight `WorkoutLog` documents (the only published
  JSON interchange schema for lifting) inside an ``arc-export`` envelope.
  openweight allows extra keys, so what it cannot say goes under ``arc*``
  keys a plain openweight reader ignores: the set's phone id, its side, its
  exact arc kind, and an assistance weight (openweight forbids a negative
  ``weight``, so a legacy row's ``-5.0`` becomes ``arcAssistanceKg: 5.0``
  with ``weight`` omitted rather than an invalid document).
- **sets.csv** — one row per set, the columns of
  `docs/research/strength-2026-11/import-formats.md` §6, plus two trailing
  ``arc_*`` columns (the exact set kind and the phone id) that the shared
  ``set_type`` vocabulary cannot carry; trailing so a reader indexing the
  named columns is unaffected.
- **strong.csv** — Strong's own column layout, the de facto interchange that
  Hevy and most other apps import. Lossy by design (no side, no kind beyond
  W/D/F).
- **one session** as text (for pasting into an assistant) or as the
  set-level CSV restricted to it.

Weights are always kilograms. ``weight_kg`` in the CSVs is the stored load
**verbatim**, a negative legacy assistance included, so a re-import reads back
exactly what is stored; ``added_weight_kg`` / ``assistance_kg`` are derived
beside it for readers that want the bodyweight meaning spelled out. Every
text cell goes through `app.domain.csv_safety`, which is where the formula
escaping lives.

Exercise blocks are consecutive runs of the same movement in ``set_index``
order: a logged set has no block of its own, and position is the order arc
already scores by.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.activity import LoggedSetKind, SetSide, parse_timezone
from app.domain.csv_safety import Cell, CsvVariant, write_csv
from app.domain.strength import LoadType
from app.persistence.activity import (
    LoggedSetRow,
    SessionRepository,
    SessionRow,
    session_duration_s,
)
from app.persistence.exercises import ExerciseRepository, ExerciseRow
from app.services.activity import SessionService

#: The envelope's format tag and version (import-formats.md §6).
EXPORT_FORMAT = "arc-export"
EXPORT_VERSION = 1

SETS_HEADER: tuple[str, ...] = (
    "workout_id",
    "workout_start",
    "workout_end",
    "workout_title",
    "workout_notes",
    "exercise_order",
    "exercise_id",
    "exercise_name",
    "equipment",
    "superset_id",
    "exercise_notes",
    "set_index",
    "set_type",
    "weight_kg",
    "added_weight_kg",
    "assistance_kg",
    "bodyweight_kg",
    "reps",
    "duration_s",
    "distance_m",
    "rpe",
    "rir",
    "side",
    "is_warmup",
    "is_failure",
    "is_drop",
    "set_notes",
    "completed_at",
    "arc_set_kind",
    "arc_client_id",
)

STRONG_HEADER: tuple[str, ...] = (
    "Date",
    "Workout Name",
    "Duration",
    "Exercise Name",
    "Set Order",
    "Weight",
    "Reps",
    "Distance",
    "Seconds",
    "Notes",
    "Workout Notes",
    "RPE",
)

#: arc's kind → openweight ``type``. ``top`` and ``lengthened_partial`` have
#: no openweight word and are working sets to every other reader; the exact
#: kind rides along as ``arcSetKind``.
_OPENWEIGHT_TYPE: dict[LoggedSetKind, str] = {
    LoggedSetKind.WARMUP: "warmup",
    LoggedSetKind.WORKING: "working",
    LoggedSetKind.TOP: "working",
    LoggedSetKind.BACKOFF: "backoff",
    LoggedSetKind.AMRAP: "amrap",
    LoggedSetKind.DROP: "dropset",
    LoggedSetKind.MYO: "myo",
    LoggedSetKind.FAILURE: "failure",
    LoggedSetKind.LENGTHENED_PARTIAL: "working",
}

#: arc's kind → the set-level CSV's ``set_type`` (import-formats.md §6:
#: ``working|warmup|drop|failure|amrap|backoff``).
_CSV_TYPE: dict[LoggedSetKind, str] = {
    LoggedSetKind.WARMUP: "warmup",
    LoggedSetKind.WORKING: "working",
    LoggedSetKind.TOP: "working",
    LoggedSetKind.BACKOFF: "backoff",
    LoggedSetKind.AMRAP: "amrap",
    LoggedSetKind.DROP: "drop",
    LoggedSetKind.MYO: "working",
    LoggedSetKind.FAILURE: "failure",
    LoggedSetKind.LENGTHENED_PARTIAL: "working",
}

#: Strong's letters in ``Set Order``; every other kind is numbered.
_STRONG_LETTER: dict[LoggedSetKind, str] = {
    LoggedSetKind.WARMUP: "W",
    LoggedSetKind.DROP: "D",
    LoggedSetKind.FAILURE: "F",
}

#: The text export letters only the warm-up; other kinds are named in words.
_TEXT_LETTER: dict[LoggedSetKind, str] = {LoggedSetKind.WARMUP: "W"}

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclass(frozen=True, slots=True)
class SessionExport:
    """One session's export and the date its download is named after."""

    local_date: dt.date
    body: str


@dataclass(frozen=True, slots=True)
class Block:
    """A consecutive run of sets of one movement, 1-based in its session."""

    order: int
    exercise: ExerciseRow | None
    sets: tuple[LoggedSetRow, ...]


def blocks_of(
    sets: Sequence[LoggedSetRow], catalogue: dict[str, ExerciseRow]
) -> list[Block]:
    """Group a session's sets (already in ``set_index`` order) into blocks."""
    runs: list[list[LoggedSetRow]] = []
    for row in sets:
        key = row.exercise_id or row.exercise_name
        if runs and (runs[-1][0].exercise_id or runs[-1][0].exercise_name) == key:
            runs[-1].append(row)
        else:
            runs.append([row])
    return [
        Block(
            order=index,
            exercise=catalogue.get(run[0].exercise_id or ""),
            sets=tuple(run),
        )
        for index, run in enumerate(runs, start=1)
    ]


def equipment_of(exercise: ExerciseRow | None) -> str | None:
    """One openweight equipment word for a catalogue movement, when there is one.

    From the load type rather than the equipment list, which names everything
    in the room (a back squat is ``barbell`` *and* ``rack``). A stack is a
    cable when the movement needs one, else a machine.
    """
    if exercise is None or exercise.load_type is None:
        return None
    match exercise.load_type:
        case LoadType.STACK:
            return "cable" if "cable" in exercise.equipment else "machine"
        case LoadType.PLATE_LOADED:
            return "machine"
        case LoadType.BAND:
            return "resistance-band"
        case LoadType.UNLOADED:
            return None
        case other:
            return other.value


def is_bodyweight(exercise: ExerciseRow | None) -> bool:
    """Whether the movement's load is added to (or taken off) the body."""
    return exercise is not None and exercise.load_type is LoadType.BODYWEIGHT


def added_weight(row: LoggedSetRow, exercise: ExerciseRow | None) -> float | None:
    """Weight added to a bodyweight movement, as a positive number."""
    if is_bodyweight(exercise) and row.load_kg is not None and row.load_kg > 0:
        return row.load_kg
    return None


def assistance(row: LoggedSetRow) -> float | None:
    """The magnitude of an assistance weight a legacy row stored as negative."""
    if row.load_kg is not None and row.load_kg < 0:
        return -row.load_kg
    return None


def session_zone(session: SessionRow) -> dt.tzinfo:
    """The session's own zone, falling back to UTC for an unresolvable one."""
    try:
        return parse_timezone(session.timezone)
    except ValueError:
        return dt.UTC


def local(moment: dt.datetime, zone: dt.tzinfo) -> str:
    """ISO 8601 with the offset in force at that moment, never naive."""
    return moment.astimezone(zone).isoformat()


def set_completed_at(row: LoggedSetRow, zone: dt.tzinfo) -> str | None:
    """When the set was logged, in the device's offset when it gave one."""
    if row.completed_at is None:
        return None
    if row.utc_offset_min is not None:
        zone = dt.timezone(dt.timedelta(minutes=row.utc_offset_min))
    return local(row.completed_at, zone)


def workout_title(session: SessionRow) -> str:
    """What the workout is called in the exports: its discipline, capitalised.

    A logged session has no title of its own; the planned session it matched
    may, but an export of what was *done* should not depend on a link that
    can be changed later.
    """
    return session.discipline.value.capitalize()


def strong_duration(seconds: float) -> str:
    """Strong's duration spelling: ``1h 5m``, or ``45m`` under an hour."""
    minutes = round(seconds / 60)
    hours, rest = divmod(minutes, 60)
    return f"{hours}h {rest}m" if hours else f"{rest}m"


def set_orders(
    block: Block, letters: dict[LoggedSetKind, str], *, pair_sides: bool
) -> list[str]:
    """Each set's label within its block: a letter for its kind, or a number.

    Strong's ``Set Order`` (`_STRONG_LETTER`) letters warm-ups, drop and
    failure sets and numbers every other row — Strong has no sides, so a
    left and a right are two rows numbered apart. The text export
    (`_TEXT_LETTER`) letters only the warm-up, names any other kind after the
    set, and with ``pair_sides`` gives the right side of a left-right pair its
    left's number: the pair is one set (`app.domain.strength.logged_set_units`).
    """
    labels: list[str] = []
    number = 0
    previous: LoggedSetRow | None = None
    for row in block.sets:
        letter = letters.get(row.set_kind)
        if letter is not None:
            labels.append(letter)
        else:
            pairs_with_previous = (
                pair_sides
                and row.side is SetSide.RIGHT
                and previous is not None
                and previous.side is SetSide.LEFT
                and previous.set_kind not in letters
            )
            if not pairs_with_previous:
                number += 1
            labels.append(str(number))
        previous = row
    return labels


def _kg(value: float) -> str:
    """A weight for reading: ``100``, ``102.5`` — no trailing ``.0``."""
    return f"{value:g}"


def _one_line(text: str) -> str:
    """Notes on one line, so a note cannot break the text export's layout."""
    return " ".join(text.split())


def text_line(label: str, row: LoggedSetRow, exercise: ExerciseRow | None) -> str:
    """One set as a person would write it down: ``1  100 kg × 5, 2 reps left``."""
    load: str | None = None
    if is_bodyweight(exercise):
        load = "bodyweight"
        if row.load_kg:
            sign = "+" if row.load_kg > 0 else "−"
            load = f"bodyweight {sign} {_kg(abs(row.load_kg))} kg"
    elif row.load_kg is not None:
        load = f"{_kg(row.load_kg)} kg"

    if row.reps is not None:
        amount = f"× {row.reps}" if load else f"{row.reps} reps"
        body = f"{load} {amount}" if load else amount
    else:
        held = f"{row.duration_s} s"
        body = f"{load}, {held}" if load else held

    extras: list[str] = []
    if row.set_kind not in (LoggedSetKind.WARMUP, LoggedSetKind.WORKING):
        extras.append(row.set_kind.value.replace("_", " "))
    if row.side is not SetSide.BOTH:
        extras.append(row.side.value)
    if row.per_side:
        extras.append("each side")
    if row.rir is not None:
        count = f"{row.rir}+" if row.rir_capped else str(row.rir)
        extras.append(f"{count} rep left" if count == "1" else f"{count} reps left")
    if row.rpe is not None:
        extras.append(f"RPE {row.rpe:g}")
    line = ", ".join([body, *extras])
    if row.notes and row.notes.strip():
        line = f"{line} — {_one_line(row.notes)}"
    return f"  {label}  {line}"


def session_text(session: SessionRow, catalogue: dict[str, ExerciseRow]) -> str:
    """One workout as plain text, for reading or pasting into an assistant."""
    zone = session_zone(session)
    start = session.start_time.astimezone(zone)
    day = (
        f"{_WEEKDAYS[start.weekday()]} {start.day} "
        f"{_MONTHS[start.month - 1]} {start.year}"
    )
    when = (
        f"{day}, {start:%H:%M} {session.timezone}" if session.start_time_known else day
    )
    minutes = round(session_duration_s(session) / 60)
    lines = [f"{workout_title(session)} — {when}, {minutes} min"]
    if session.rpe is not None:
        lines.append(f"Session RPE: {session.rpe:g}")
    if session.session_rating is not None:
        lines.append(f"Session rating: {session.session_rating}/10")
    if session.notes and session.notes.strip():
        lines.append(f"Notes: {_one_line(session.notes)}")
    blocks = blocks_of(session.logged_sets, catalogue)
    if blocks:
        lines.append("")
    for block in blocks:
        lines.append(block.sets[0].exercise_name)
        for label, row in zip(
            set_orders(block, _TEXT_LETTER, pair_sides=True), block.sets, strict=True
        ):
            lines.append(text_line(label, row, block.exercise))
    return "\n".join(lines) + "\n"


def set_rows(
    session: SessionRow, catalogue: dict[str, ExerciseRow]
) -> list[list[Cell]]:
    """One session's rows of the set-level CSV, in `SETS_HEADER` order."""
    zone = session_zone(session)
    rows: list[list[Cell]] = []
    for block in blocks_of(session.logged_sets, catalogue):
        for index, row in enumerate(block.sets, start=1):
            rows.append(
                [
                    str(session.id),
                    local(session.start_time, zone),
                    local(session.end_time, zone),
                    workout_title(session),
                    session.notes,
                    block.order,
                    row.exercise_id,
                    row.exercise_name,
                    equipment_of(block.exercise),
                    None,
                    None,
                    index,
                    _CSV_TYPE[row.set_kind],
                    row.load_kg,
                    added_weight(row, block.exercise),
                    assistance(row),
                    session.weight_kg,
                    row.reps,
                    row.duration_s,
                    None,
                    row.rpe,
                    row.rir,
                    row.side.value,
                    row.set_kind is LoggedSetKind.WARMUP,
                    row.set_kind is LoggedSetKind.FAILURE,
                    row.set_kind is LoggedSetKind.DROP,
                    row.notes,
                    set_completed_at(row, zone),
                    row.set_kind.value,
                    str(row.client_id),
                ]
            )
    return rows


def strong_rows(
    session: SessionRow, catalogue: dict[str, ExerciseRow]
) -> list[list[Cell]]:
    """One session's rows of the Strong-format CSV, in `STRONG_HEADER` order."""
    start = session.start_time.astimezone(session_zone(session))
    rows: list[list[Cell]] = []
    for block in blocks_of(session.logged_sets, catalogue):
        for label, row in zip(
            set_orders(block, _STRONG_LETTER, pair_sides=False), block.sets, strict=True
        ):
            rows.append(
                [
                    f"{start:%Y-%m-%d %H:%M:%S}",
                    workout_title(session),
                    strong_duration(session_duration_s(session)),
                    row.exercise_name,
                    label,
                    row.load_kg,
                    row.reps,
                    None,
                    row.duration_s,
                    row.notes,
                    session.notes,
                    row.rpe,
                ]
            )
    return rows


def _compact(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop absent fields: openweight types them, and ``null`` is not a type."""
    return {key: value for key, value in fields.items() if value is not None}


def openweight_set(row: LoggedSetRow, zone: dt.tzinfo) -> dict[str, Any]:
    """One logged set as an openweight ``SetLog`` with arc's extensions."""
    weight = row.load_kg if row.load_kg is not None and row.load_kg >= 0 else None
    return _compact(
        {
            "reps": row.reps,
            "weight": weight,
            "unit": "kg" if weight is not None else None,
            "durationSeconds": row.duration_s,
            "rpe": row.rpe,
            "rir": row.rir,
            "toFailure": True if row.set_kind is LoggedSetKind.FAILURE else None,
            "type": _OPENWEIGHT_TYPE[row.set_kind],
            "restSeconds": row.rest_s,
            "notes": row.notes,
            "arcClientId": str(row.client_id),
            "arcSide": row.side.value,
            "arcSetKind": row.set_kind.value,
            "arcPerSide": row.per_side,
            "arcAssistanceKg": assistance(row),
            "arcRirCapped": row.rir_capped if row.rir is not None else None,
            "arcHoldQuality": row.hold_quality.value if row.hold_quality else None,
            "arcPlannedLineRef": row.planned_line_ref,
            "arcReplacedExerciseId": row.replaced_exercise_id,
            "arcSource": row.source.value,
            "arcConfidence": row.confidence.value,
            "arcPainKind": row.pain_kind.value if row.pain_kind else None,
            "arcPainBand": row.pain_band.value if row.pain_band else None,
            "arcCompletedAt": set_completed_at(row, zone),
        }
    )


def openweight_workout(
    session: SessionRow, catalogue: dict[str, ExerciseRow]
) -> dict[str, Any]:
    """One session as an openweight ``WorkoutLog`` with arc's extensions."""
    zone = session_zone(session)
    return _compact(
        {
            "date": local(session.start_time, zone),
            "name": workout_title(session),
            "notes": session.notes,
            "durationSeconds": round(session_duration_s(session)),
            "exercises": [
                _compact(
                    {
                        "exercise": _compact(
                            {
                                "name": block.sets[0].exercise_name,
                                "equipment": equipment_of(block.exercise),
                            }
                        ),
                        "order": block.order,
                        "sets": [openweight_set(row, zone) for row in block.sets],
                        "arcExerciseId": block.sets[0].exercise_id,
                    }
                )
                for block in blocks_of(session.logged_sets, catalogue)
            ],
            "arcId": str(session.id),
            "arcDiscipline": session.discipline.value,
            "arcTimezone": session.timezone,
            "arcStartTimeKnown": session.start_time_known,
            "arcSessionRpe": session.rpe,
            "arcSessionRating": session.session_rating,
            "arcBodyweightKg": session.weight_kg,
        }
    )


class ExportService:
    """Read-only use-cases that turn logged sessions into files."""

    def __init__(
        self,
        repository: SessionRepository,
        exercises: ExerciseRepository,
        sessions: SessionService,
    ) -> None:
        self._repository = repository
        self._exercises = exercises
        self._sessions = sessions

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            SessionRepository(session),
            ExerciseRepository(session),
            SessionService.from_session(session),
        )

    async def _catalogue(self) -> dict[str, ExerciseRow]:
        return {row.id: row for row in await self._exercises.all()}

    async def workouts(self, *, exported_at: dt.datetime) -> dict[str, Any]:
        """Every session with logged sets, as openweight in arc's envelope.

        Sessions without sets are left out: openweight requires at least one
        exercise, and a ride has none.
        """
        catalogue = await self._catalogue()
        sessions = await self._repository.with_logged_sets_oldest_first()
        return {
            "format": EXPORT_FORMAT,
            "version": EXPORT_VERSION,
            "exportedAt": exported_at.astimezone(dt.UTC).isoformat(),
            "workouts": [openweight_workout(row, catalogue) for row in sessions],
        }

    async def sets_csv(self, variant: CsvVariant) -> str:
        """Every logged set, one row each, oldest session first."""
        catalogue = await self._catalogue()
        sessions = await self._repository.with_logged_sets_oldest_first()
        rows = [row for session in sessions for row in set_rows(session, catalogue)]
        return write_csv(SETS_HEADER, rows, variant)

    async def strong_csv(self, variant: CsvVariant) -> str:
        """Every logged set in Strong's column layout."""
        catalogue = await self._catalogue()
        sessions = await self._repository.with_logged_sets_oldest_first()
        rows = [row for session in sessions for row in strong_rows(session, catalogue)]
        return write_csv(STRONG_HEADER, rows, variant)

    async def session_text(self, session_id: uuid.UUID) -> SessionExport:
        """One session as plain text.

        Raises:
            NotFoundError: When no session has that id.
        """
        session = await self._sessions.get(session_id)
        return SessionExport(
            local_date=session.local_date,
            body=session_text(session, await self._catalogue()),
        )

    async def session_csv(
        self, session_id: uuid.UUID, variant: CsvVariant
    ) -> SessionExport:
        """One session as the set-level CSV; a header alone when it has no sets.

        Raises:
            NotFoundError: When no session has that id.
        """
        session = await self._sessions.get(session_id)
        rows = set_rows(session, await self._catalogue())
        return SessionExport(
            local_date=session.local_date, body=write_csv(SETS_HEADER, rows, variant)
        )
