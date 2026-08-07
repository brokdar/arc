"""Use-cases for completed sessions: read them, correct them, type one in.

Everything here is about a session **after** it exists. Creating one from a
device file is the pipeline's job (`app.ingest.pipeline`), which sits a layer
above this one; what is left is the three things the athlete does afterwards.

* **Read.** The list is a log and reads newest first, unlike the planned-session
  list, which is a calendar and reads forwards.
* **Correct.** Two overrides, and both are corrections of a guess this system
  made from a file: the discipline (which sets ``discipline_overridden``, so a
  later re-classification cannot quietly undo it) and the timezone (which
  **re-derives** ``local_date``, because the stored date is the local date of
  the start and a wrong zone puts a late-evening ride on the wrong day).
* **Type one in.** A gym session has no file. It gets the same session row with
  ``recording_kind=manual``, no recording, and its sets as `logged_sets` rows
  (B-6). WP-6 matches manual and device sessions with the same query.

Streams are not here and not in the API this exposes: `data/streams/` is read
by WP-5's endpoints, and a session's *metadata* is what WP-4 answers with.
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError, domain_rules
from app.domain.activity import (
    ClassificationSource,
    RecordingKind,
    SessionDiscipline,
    parse_timezone,
    session_date,
)
from app.domain.actor import Actor
from app.persistence.activity import (
    MAX_NOTES_LENGTH,
    LoggedSetRow,
    RecordingRepository,
    SessionRepository,
    SessionRow,
)
from app.persistence.audit import AuditRepository
from app.persistence.db import commit
from app.services.exercises import ExerciseService
from app.services.metrics import SessionMetricsService

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "session"

#: Fields `update` accepts. Deliberately short: this endpoint exists to
#: correct what the pipeline guessed, not to edit history.
UPDATABLE_FIELDS = ("discipline", "timezone")

#: Bounds on a manually entered session. RPE is the standard 0-10 scale; the
#: duration bound is the same "too short to be a session" line
#: `app.domain.streams.validate` draws for a recorded one, and the upper one
#: is a typo guard (a day).
MIN_RPE, MAX_RPE = 0.0, 10.0
MIN_MANUAL_DURATION_S = 60
MAX_MANUAL_DURATION_S = 24 * 60 * 60

#: Bounds on when a session can have happened. A timestamp outside them is a
#: typo or a fuzzer, and `start_time + duration` on year 9999 raises
#: `OverflowError` — a 500 where a 422 is the honest answer.
EARLIEST_SESSION = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
LATEST_SESSION = dt.datetime(2100, 1, 1, tzinfo=dt.UTC)

#: Bounds on one logged set.
MAX_SETS = 200
MAX_REPS = 1_000
MAX_LOAD_KG = 1_000.0
MIN_RIR, MAX_RIR = 0, 10

#: Longest free-text exercise name accepted for a set that names no catalogue
#: movement. Matches `LoggedSetRow.exercise_name`.
MAX_EXERCISE_NAME = 160


@dataclass(frozen=True, slots=True)
class LoggedSetInput:
    """One set as the athlete entered it.

    Args:
        exercise_id: Catalogue slug, when the movement is one of ours. The
            name stored on the row is then the catalogue's name at the time of
            logging, so the row stays readable if the catalogue moves on.
        exercise_name: Free text, for a movement the catalogue does not have.
            Exactly one of the two is given.
        reps: Repetitions performed.
        load_kg: External load, when there was one (bodyweight sets have none).
        rir: Reps in reserve, as reported after the set.
        notes: Anything the athlete wrote about this set.
    """

    reps: int
    exercise_id: str | None = None
    exercise_name: str | None = None
    load_kg: float | None = None
    rir: int | None = None
    notes: str | None = None


class SessionService:
    """Use-cases for completed sessions. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: SessionRepository,
        recordings: RecordingRepository,
        audit: AuditRepository,
        exercises: ExerciseService,
        metrics: SessionMetricsService,
    ) -> None:
        self._session = session
        self._repository = repository
        self._recordings = recordings
        self._audit = audit
        self._exercises = exercises
        self._metrics = metrics

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            SessionRepository(session),
            RecordingRepository(session),
            AuditRepository(session),
            ExerciseService.from_session(session),
            SessionMetricsService.from_session(session),
        )

    # --- reads ---------------------------------------------------------------

    async def list(
        self,
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        discipline: SessionDiscipline | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[SessionRow], int]:
        """A page of completed sessions, newest first, plus the total."""
        return await self._repository.list(
            start=start, end=end, discipline=discipline, offset=offset, limit=limit
        )

    async def get(self, session_id: uuid.UUID) -> SessionRow:
        """One completed session with its recordings and logged sets.

        Raises:
            NotFoundError: When no session has that id.
        """
        row = await self._repository.get(session_id)
        if row is None:
            raise NotFoundError(f"Session {session_id} not found")
        return row

    async def repair_counts(
        self, sessions: Sequence[SessionRow]
    ) -> dict[uuid.UUID, int]:
        """Repairs recorded per recording, for a page of sessions, in one query."""
        return await self._recordings.repair_counts(
            [recording.id for row in sessions for recording in row.recordings]
        )

    # --- writes --------------------------------------------------------------

    async def update(
        self, session_id: uuid.UUID, updates: Mapping[str, Any], *, actor: Actor
    ) -> SessionRow:
        """Apply the athlete's corrections to one session.

        Raises:
            NotFoundError: When no session has that id.
            ValidationError: When a field is unknown, cleared, or the timezone
                cannot be resolved.
        """
        unknown = set(updates) - set(UPDATABLE_FIELDS)
        if unknown:
            raise ValidationError(
                f"Unknown session fields: {', '.join(sorted(unknown))}"
            )
        for name in UPDATABLE_FIELDS:
            if name in updates and updates[name] is None:
                raise ValidationError(f"{name} cannot be cleared")

        row = await self.get(session_id)
        if not updates:
            raise ValidationError("Supply at least one field to update")

        changed: dict[str, Any] = {}
        if "discipline" in updates:
            row.discipline = SessionDiscipline(updates["discipline"])
            # The athlete's answer is not a classification, so the source stops
            # claiming one guessed it; the flag is what stops a later
            # re-classification from overwriting the correction.
            row.discipline_overridden = True
            row.classification_source = ClassificationSource.MANUAL
            changed["discipline"] = row.discipline.value
        if "timezone" in updates:
            timezone = str(updates["timezone"])
            with domain_rules():
                parse_timezone(timezone)
                row.timezone = timezone
                row.local_date = session_date(row.start_time, timezone)
            changed["timezone"] = timezone
            changed["local_date"] = row.local_date.isoformat()

        row = await self._repository.add(row)
        await self._audit.record(
            actor=actor,
            action="session.updated",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload=changed | {"changed": sorted(changed)},
        )
        await commit(self._session)
        await self._session.refresh(row)
        # A discipline correction changes which load model is preferred
        # (A5.2), so the artefact is stale the moment it is applied. Only the
        # stream-free path can be re-run from here: reading a parquet file is
        # `app.ingest`'s job and a service may not reach it, so a device
        # session's correction is recovered through the recompute endpoint,
        # which can.
        if not row.recordings:
            await self._recompute_strength(row, actor=actor, reason="session corrected")
        return row

    async def _recompute_strength(
        self, row: SessionRow, *, actor: Actor, reason: str | None
    ) -> None:
        """Append a metric version for a session that has no stream.

        Isolated from the write that preceded it: the session is already
        committed, and a metric failure must leave the athlete with a stored
        session and no numbers rather than losing what they typed in.
        """
        reloaded = await self._repository.get(row.id)
        if reloaded is None:  # pragma: no cover — it was committed a line ago
            return
        await self._metrics.record_strength(reloaded, actor=actor, reason=reason)

    async def create_manual(
        self,
        *,
        actor: Actor,
        start_time: dt.datetime,
        timezone: str,
        duration_s: int,
        discipline: SessionDiscipline = SessionDiscipline.STRENGTH,
        rpe: float | None = None,
        notes: str | None = None,
        sets: Sequence[LoggedSetInput] = (),
    ) -> SessionRow:
        """Record a session the athlete performed and typed in (B-6).

        Raises:
            ValidationError: When the timezone is unresolvable, the duration
                or RPE is out of range, or a set is malformed.
            NotFoundError: When a set names a catalogue exercise that does not
                exist.
        """
        with domain_rules():
            parse_timezone(timezone)
            local_date = session_date(start_time, timezone)
        _check_manual(start_time=start_time, duration_s=duration_s, rpe=rpe, sets=sets)
        rows = [
            await self._logged_set(entry, index) for index, entry in enumerate(sets)
        ]

        row = await self._repository.add(
            SessionRow(
                start_time=start_time,
                end_time=start_time + dt.timedelta(seconds=duration_s),
                timezone=timezone,
                local_date=local_date,
                discipline=discipline,
                # Nobody classified this: the athlete said what it was.
                classification_source=ClassificationSource.MANUAL,
                recording_kind=RecordingKind.MANUAL,
                rpe=rpe,
                notes=notes,
            )
        )
        for logged in rows:
            logged.session_id = row.id
        self._session.add_all(rows)
        await self._audit.record(
            actor=actor,
            action="session.manually_created",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "local_date": local_date.isoformat(),
                "discipline": discipline.value,
                "duration_s": duration_s,
                "rpe": rpe,
                "sets": len(rows),
            },
        )
        await commit(self._session)
        await self._session.refresh(row)
        await self._recompute_strength(row, actor=actor, reason=None)
        return row

    async def _logged_set(self, entry: LoggedSetInput, index: int) -> LoggedSetRow:
        """Build one set row, resolving a catalogue reference to its name.

        Raises:
            ValidationError: When the set names neither a catalogue movement
                nor a free-text one, or both.
            NotFoundError: When the catalogue has no such movement.
        """
        if (entry.exercise_id is None) == (entry.exercise_name is None):
            raise ValidationError(
                f"Set {index + 1} needs exactly one of exercise_id or "
                "exercise_name: a set names a catalogue movement or names its "
                "own, not both and not neither."
            )
        if entry.exercise_id is not None:
            catalogue = await self._exercises.get(entry.exercise_id)
            name = catalogue.name
        else:
            name = (entry.exercise_name or "").strip()
            if not name:
                raise ValidationError(f"Set {index + 1} has an empty exercise name")
        return LoggedSetRow(
            exercise_id=entry.exercise_id,
            exercise_name=name[:MAX_EXERCISE_NAME],
            set_index=index,
            reps=entry.reps,
            load_kg=entry.load_kg,
            rir=entry.rir,
            notes=(entry.notes or None),
        )


def _check_manual(
    *,
    start_time: dt.datetime,
    duration_s: int,
    rpe: float | None,
    sets: Sequence[LoggedSetInput],
) -> None:
    """Bounds a JSON schema cannot express, refused as 422s rather than 500s.

    Raises:
        ValidationError: When any of them is violated.
    """
    if start_time.tzinfo is None:
        raise ValidationError("start_time must carry a timezone offset")
    if not EARLIEST_SESSION <= start_time <= LATEST_SESSION:
        raise ValidationError(
            f"start_time must be between {EARLIEST_SESSION:%Y} and {LATEST_SESSION:%Y}"
        )
    if not MIN_MANUAL_DURATION_S <= duration_s <= MAX_MANUAL_DURATION_S:
        raise ValidationError(
            f"A session lasts between {MIN_MANUAL_DURATION_S} s and "
            f"{MAX_MANUAL_DURATION_S} s; {duration_s} s is not a session"
        )
    if rpe is not None and not MIN_RPE <= rpe <= MAX_RPE:
        raise ValidationError(f"RPE is on a {MIN_RPE:g}-{MAX_RPE:g} scale")
    if len(sets) > MAX_SETS:
        raise ValidationError(f"A session may log at most {MAX_SETS} sets")
    for index, entry in enumerate(sets, start=1):
        if not 1 <= entry.reps <= MAX_REPS:
            raise ValidationError(f"Set {index}: reps must be between 1 and {MAX_REPS}")
        if entry.load_kg is not None and not 0 <= entry.load_kg <= MAX_LOAD_KG:
            raise ValidationError(
                f"Set {index}: load must be between 0 and {MAX_LOAD_KG:g} kg"
            )
        if entry.rir is not None and not MIN_RIR <= entry.rir <= MAX_RIR:
            raise ValidationError(
                f"Set {index}: reps in reserve must be between {MIN_RIR} and {MAX_RIR}"
            )
        if entry.notes is not None and len(entry.notes) > MAX_NOTES_LENGTH:
            raise ValidationError(f"Set {index}: the note is too long")
