"""The session packet: every number the phone needs to train offline.

The phone renders, it does not decide. A basement gym has no signal, so the
server prepares each strength session of the next seven days — every line's
pre-fill, its warm-up ramp and plates, which effort question to ask, whether
the plan must be followed exactly — together with the last numbers logged for
every exercise, the library entries those name, and the gym the plates were
computed for. The phone fetches it on sync and keeps it in IndexedDB
(`frontend/lib/offline/packet-store.ts`). This module is the only producer of
offline numbers: a later rule (progression, swaps) changes a number the gym
sees by changing it here, not by teaching the phone.

**Pre-fill precedence: prescribed load > last logged > blank.** A line whose
load is a weight in kilograms pre-fills that weight with the prescribed reps
(or hold). A line without one — an effort or %-of-max load with no estimate
yet — pre-fills the numbers last logged for the exercise, weight and reps
together, because "what you did last time" is the number the athlete would
type anyway. An exercise never logged pre-fills nothing and is flagged
``starting_flow``, which sends the phone into the guided starting-weight
flow. A bodyweight line has no weight to find and pre-fills its prescribed
reps. **Plain logbook uses the same order**: it displaces progression, and
until progression exists that is a statement about what a later rule may not
do here, pinned by AC-46 now and AC-72 when that rule lands.

**Follow-exactly** (`app.domain.plans`) pins every number to the
prescription: reps and holds come from the prescription even where history
says otherwise, and a kilogram load is used as written. Only a line whose
prescription has no weight at all falls back to history for the weight.

**Swaps travel with the line.** Each line carries its three ranked swaps
(`app.domain.swaps`, through `app.services.exercise_preferences`), whether the
athlete avoids the planned exercise (the phone then opens on the swap
prompt), and where they stand on *Ease back in*; every movement those name is
in ``library``, and its last numbers in ``last_numbers`` when it was ever
logged. A swapped line's first weight therefore comes from the substitute's
own history or the starting flow, never from the planned exercise's
pre-fill — the phone has everything it needs to decide that with no signal.

A rep range pre-fills its low end: the bottom of the range is the target
every set must reach, and the athlete adds reps from there.

**Pre-fills are the prescription as written, not rounded.** Plates and
warm-ups are computed for the nearest loadable weight (`app.domain.loading`),
so a 61 kg prescription still shows 61 kg — what the coach wrote — beside the
plates for 60.

The window is the athlete-local ``[today, today + 7)``, half-open like every
range; only sessions still ``planned`` are in it.
"""

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_today
from app.domain.athlete import Discipline
from app.domain.loading import DEFAULT_GYM, Equipment, Gym, plates_per_side
from app.domain.purpose import Purpose
from app.domain.safety import blocks_increase
from app.domain.sessions import SessionStatus
from app.domain.strength import (
    Exercise,
    LoadKind,
    LoadType,
    StrengthSet,
    StrengthWorkout,
    TargetRange,
    resolve_main_lines,
)
from app.domain.training_mode import AskFrequency, TrainingMode, TrainingSettings
from app.domain.warmups import warmup_lines, warmup_sets
from app.domain.workout import workout_body_from_json
from app.persistence.activity import LoggedSetRow, SessionRepository
from app.persistence.exercise_preferences import ExercisePreferenceRow
from app.persistence.exercises import ExerciseRow
from app.persistence.planned_sessions import PlannedSessionRow
from app.services.athlete_settings import AthleteSettingsService
from app.services.exercise_preferences import (
    EaseBack,
    ExercisePreferencesService,
    Library,
)
from app.services.planned_sessions import PlannedSessionService
from app.services.plans import PlansService

#: The packet's wire version. The phone refuses a packet newer than it
#: understands rather than misreading it; bump on any breaking change.
SCHEMA_VERSION = 1

#: Days ahead the packet covers, today included.
WINDOW_DAYS = 7

#: More planned sessions than any week can hold; the list is read unpaged.
_MAX_SESSIONS = 1_000

#: How each catalogue load type is made up, for the ones `app.domain.loading`
#: rounds. Anything else (plate-loaded machines, kettlebells, bands,
#: bodyweight) gets no plates and no warm-up ramp.
_EQUIPMENT: Mapping[LoadType, Equipment] = {
    LoadType.BARBELL: Equipment.BARBELL,
    LoadType.DUMBBELL: Equipment.DUMBBELL,
    LoadType.STACK: Equipment.STACK,
}


class PrefillSource(StrEnum):
    """Where a line's pre-filled numbers came from."""

    PRESCRIPTION = "prescription"
    LAST_LOGGED = "last_logged"
    BLANK = "blank"


class EffortScale(StrEnum):
    """Which effort question the phone asks after a set, by training mode."""

    EASY_GOOD_HARD = "easy_good_hard"
    REPS_LEFT = "reps_left"
    REPS_LEFT_OR_RPE = "reps_left_or_rpe"


_SCALE: Mapping[TrainingMode, EffortScale] = {
    TrainingMode.SIMPLE: EffortScale.EASY_GOOD_HARD,
    TrainingMode.STANDARD: EffortScale.REPS_LEFT,
    TrainingMode.FULL: EffortScale.REPS_LEFT_OR_RPE,
}


@dataclass(frozen=True, slots=True)
class Prefill:
    """The numbers a line's first set opens with."""

    source: PrefillSource
    load_kg: float | None
    reps: int | None
    duration_s: int | None
    starting_flow: bool


@dataclass(frozen=True, slots=True)
class PacketWarmup:
    """One generated warm-up set, with its plates on a barbell."""

    kg: float
    reps: int
    plates_per_side: list[float] | None


@dataclass(frozen=True, slots=True)
class PacketLine:
    """One prescription line as the gym screen shows it.

    ``line_ref`` is ``"<group>.<item>"`` (0-based): the reference a logged set
    carries in ``planned_line_ref`` so scoring can pair by line.
    """

    line_ref: str
    group_index: int
    item_index: int
    group_label: str | None
    exercise_id: str
    exercise_name: str
    prescription: StrengthSet
    main: bool
    equipment: Equipment | None
    prefill: Prefill
    warmups: list[PacketWarmup]
    plates_per_side: list[float] | None
    swaps: list[ExerciseRow]
    avoided: bool
    ease_back: EaseBack | None


@dataclass(frozen=True, slots=True)
class PacketSession:
    """One upcoming strength session."""

    planned_session_id: uuid.UUID
    date: dt.date
    time_of_day: dt.time | None
    purpose: Purpose
    intent_text: str | None
    key: bool
    plan_id: uuid.UUID
    follow_exactly: bool
    lines: list[PacketLine]


@dataclass(frozen=True, slots=True)
class LastNumbers:
    """The last non-warm-up set logged for one exercise."""

    exercise_id: str
    exercise_name: str
    load_kg: float | None
    reps: int | None
    duration_s: int | None
    per_side: bool
    logged_on: dt.date
    increase_blocked: bool = False


@dataclass(frozen=True, slots=True)
class EffortQuestion:
    """What the phone asks after a set, and how often."""

    scale: EffortScale
    ask: AskFrequency


@dataclass(frozen=True, slots=True)
class Packet:
    """Everything the phone stores to train the next seven days offline."""

    schema_version: int
    generated_at: dt.datetime
    starts_on: dt.date
    ends_before: dt.date
    settings: TrainingSettings
    effort: EffortQuestion
    gym: Gym
    sessions: list[PacketSession]
    last_numbers: list[LastNumbers]
    library: list[ExerciseRow]


class PacketService:
    """Builds the session packet. Read-only."""

    def __init__(
        self,
        planned: PlannedSessionService,
        plans: PlansService,
        settings: AthleteSettingsService,
        history: SessionRepository,
        preferences: ExercisePreferencesService,
    ) -> None:
        self._preferences = preferences
        self._planned = planned
        self._plans = plans
        self._settings = settings
        self._history = history

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and the services it composes to one session."""
        return cls(
            PlannedSessionService.from_session(session),
            PlansService.from_session(session),
            AthleteSettingsService.from_session(session),
            SessionRepository(session),
            ExercisePreferencesService.from_session(session),
        )

    async def build(self, *, now: dt.datetime | None = None) -> Packet:
        """The packet as of ``now`` (default: this instant)."""
        generated_at = now or dt.datetime.now(dt.UTC)
        # First: seeding the catalogue may commit or roll back mid-transaction,
        # which would expire rows loaded before it (`ExerciseService.ensure_seeded`).
        swaps = await self._preferences.library()
        catalogue = dict(swaps.rows)
        preference_rows = await self._preferences.rows()
        settings = await self._settings.read()
        starts_on = athlete_today(generated_at)
        ends_before = starts_on + dt.timedelta(days=WINDOW_DAYS)
        rows, _ = await self._planned.list(
            start=starts_on,
            end=ends_before - dt.timedelta(days=1),
            status=SessionStatus.PLANNED,
            limit=_MAX_SESSIONS,
        )
        strength = [row for row in rows if row.discipline is Discipline.STRENGTH]
        follow_exactly = {
            plan.id: plan.follow_exactly for plan in await self._plans.list()
        }
        last = await self._history.last_logged_sets()
        pain = await self._history.latest_session_pain()

        domain = swaps.domain
        sessions = [
            session
            for row in strength
            if (
                session := _session(
                    row,
                    catalogue,
                    domain,
                    last,
                    follow_exactly.get(row.plan_id, False),
                    swaps,
                    preference_rows,
                )
            )
            is not None
        ]
        last_numbers = [
            LastNumbers(
                exercise_id=slug,
                exercise_name=(
                    catalogue[slug].name if slug in catalogue else logged.exercise_name
                ),
                load_kg=logged.load_kg,
                reps=logged.reps,
                duration_s=logged.duration_s,
                per_side=logged.per_side,
                logged_on=logged_on,
                increase_blocked=blocks_increase(pain.get(slug, ())),
            )
            for slug, (logged, logged_on) in sorted(last.items())
        ]
        named = {
            slug for s in sessions for line in s.lines for slug in _named(line)
        } | set(last)
        library = [catalogue[slug] for slug in sorted(named) if slug in catalogue]
        return Packet(
            schema_version=SCHEMA_VERSION,
            generated_at=generated_at,
            starts_on=starts_on,
            ends_before=ends_before,
            settings=settings,
            effort=EffortQuestion(
                scale=_SCALE[settings.mode], ask=settings.ask_frequency
            ),
            gym=DEFAULT_GYM,
            sessions=sessions,
            last_numbers=last_numbers,
            library=library,
        )


def _session(
    row: PlannedSessionRow,
    catalogue: Mapping[str, ExerciseRow],
    domain: Mapping[str, Exercise],
    last: Mapping[str, tuple[LoggedSetRow, dt.date]],
    follow_exactly: bool,
    swaps: Library,
    preferences: Mapping[str, ExercisePreferenceRow],
) -> PacketSession | None:
    """One session's lines; ``None`` for a body that is not a lift."""
    intent = row.current_intent
    body = workout_body_from_json(intent.structure)
    if not isinstance(body, StrengthWorkout):
        return None
    mains = resolve_main_lines(body, domain)
    ramps = warmup_lines(body, domain)
    lines: list[PacketLine] = []
    position = 0
    for group_index, group in enumerate(body.groups):
        for item_index, item in enumerate(group.items):
            entry = catalogue.get(item.exercise_id)
            logged = last.get(item.exercise_id)
            equipment = (
                None
                if entry is None or entry.load_type is None
                else _EQUIPMENT.get(entry.load_type)
            )
            prefill = _prefill(
                item, None if logged is None else logged[0], follow_exactly
            )
            lines.append(
                PacketLine(
                    line_ref=f"{group_index}.{item_index}",
                    group_index=group_index,
                    item_index=item_index,
                    group_label=group.label,
                    exercise_id=item.exercise_id,
                    exercise_name=(
                        entry.name if entry is not None else item.exercise_id
                    ),
                    prescription=item,
                    main=mains[position],
                    equipment=equipment,
                    prefill=prefill,
                    warmups=_warmups(prefill.load_kg, equipment)
                    if ramps[position]
                    else [],
                    plates_per_side=_plates(prefill.load_kg, equipment),
                    swaps=swaps.substitutes(item.exercise_id),
                    avoided=item.exercise_id in swaps.context.avoided,
                    ease_back=swaps.ease_back(preferences.get(item.exercise_id)),
                )
            )
            position += 1
    return PacketSession(
        planned_session_id=row.id,
        date=row.date,
        time_of_day=row.time_of_day,
        purpose=intent.purpose,
        intent_text=intent.intent_text,
        key=row.key,
        plan_id=row.plan_id,
        follow_exactly=follow_exactly,
        lines=lines,
    )


def _named(line: PacketLine) -> set[str]:
    """Every slug a line names: its exercise, its swaps, its ease-back steps."""
    named = {line.exercise_id, *(row.id for row in line.swaps)}
    if line.ease_back is not None:
        named |= {row.id for row in line.ease_back.steps}
    return named


def _low(count: int | TargetRange | None) -> int | None:
    """A prescribed count's pre-fill: the number, or a range's low end."""
    return count.low if isinstance(count, TargetRange) else count


def _prefill(
    item: StrengthSet, logged: LoggedSetRow | None, follow_exactly: bool
) -> Prefill:
    """The precedence the module docstring states."""
    reps, duration_s = _low(item.reps), item.duration_s
    if item.load.kind is LoadKind.KG:
        return Prefill(
            PrefillSource.PRESCRIPTION, item.load.value, reps, duration_s, False
        )
    if item.load.kind is LoadKind.BODYWEIGHT:
        return Prefill(PrefillSource.PRESCRIPTION, None, reps, duration_s, False)
    if logged is None:
        return Prefill(PrefillSource.BLANK, None, None, None, True)
    if follow_exactly:
        # The prescription has no weight to follow; its reps it does have.
        return Prefill(
            PrefillSource.LAST_LOGGED, logged.load_kg, reps, duration_s, False
        )
    return Prefill(
        PrefillSource.LAST_LOGGED, logged.load_kg, logged.reps, logged.duration_s, False
    )


def _plates(kg: float | None, equipment: Equipment | None) -> list[float] | None:
    if kg is None or equipment is not Equipment.BARBELL:
        return None
    return plates_per_side(kg)


def _warmups(kg: float | None, equipment: Equipment | None) -> list[PacketWarmup]:
    if kg is None or equipment is None:
        return []
    return [
        PacketWarmup(
            kg=step.kg, reps=step.reps, plates_per_side=_plates(step.kg, equipment)
        )
        for step in warmup_sets(kg, equipment)
    ]
