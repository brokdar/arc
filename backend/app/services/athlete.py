"""Use-cases for the athlete profile.

Bootstrap policy: the singleton row is created **lazily, on first access** —
read or write — rather than seeded by a migration (see `docs/decisions.md`).
`get` is therefore a write path on its very first call, which is why it takes
an ``actor`` like every mutating method and appends an audit row when it
creates the profile.
"""

import datetime as dt
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ValidationError, domain_rules
from app.domain.actor import Actor
from app.domain.athlete import AthleteProfile, Sex
from app.domain.plan import PlanState
from app.persistence.athlete import SINGLETON_ATHLETE_ID, Athlete, AthleteRepository
from app.persistence.audit import AuditRepository
from app.persistence.db import commit

#: Fields `update` accepts. Anything else in the payload is a programming
#: error in the adapter, not user input — the API schema rejects unknown
#: fields long before a client gets here.
UPDATABLE_FIELDS = (
    "name",
    "date_of_birth",
    "sex",
    "height_cm",
    "capabilities",
    "plan_state",
    "red_flag_active",
    "red_flag_note",
    "red_flag_severity",
)

#: Fields whose "cleared" value is an empty state rather than an absence, so
#: an explicit ``null`` resets them instead of nulling a column.
EMPTY_VALUES: Mapping[str, Any] = {
    "sex": Sex.UNSPECIFIED,
    "capabilities": {},
    "plan_state": PlanState.ACTIVE,
    "red_flag_active": False,
}

#: The two fields that only mean anything while the red flag is up.
RED_FLAG_DETAILS = ("red_flag_note", "red_flag_severity")

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "athlete"


class AthleteService:
    """Use-cases for the single athlete. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: AthleteRepository,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(session, AthleteRepository(session), AuditRepository(session))

    async def get(self, *, actor: Actor) -> Athlete:
        """Return the athlete profile, bootstrapping it on first access."""
        athlete = await self._repository.get()
        if athlete is not None:
            return athlete

        try:
            athlete = await self._repository.add(Athlete(id=SINGLETON_ATHLETE_ID))
        except ConflictError:
            # Two first-ever calls raced and this one lost: the winner has
            # committed the row this one was about to create, so the right
            # answer is that row, not a 409. The failed flush already rolled
            # the session back, so nothing of the losing insert survives.
            athlete = await self._repository.get()
            if athlete is None:
                raise
            return athlete
        await self._audit.record(
            actor=actor,
            action="athlete.created",
            entity_type=ENTITY_TYPE,
            entity_id=athlete.id,
            payload={"bootstrap": True},
        )
        await commit(self._session)
        return athlete

    async def update(self, updates: Mapping[str, Any], *, actor: Actor) -> Athlete:
        """Partially update the profile, creating it first if it does not exist.

        ``updates`` holds only the fields the caller explicitly supplied
        (pydantic's ``model_dump(exclude_unset=True)``); an explicit ``None``
        clears a field.

        Raises:
            ValidationError: When a field is unknown, or when the resulting
                profile breaks a domain rule.
        """
        unknown = set(updates) - set(UPDATABLE_FIELDS)
        if unknown:
            raise ValidationError(
                f"Unknown athlete fields: {', '.join(sorted(unknown))}"
            )

        # Validate *before* bootstrapping, and bootstrap in the same
        # transaction as the update: a rejected PATCH on an empty database
        # must be a pure no-op, not a 422 that leaves a profile behind.
        athlete = await self._repository.get()
        before = _values(athlete) if athlete is not None else _empty_profile()
        candidate = {**before, **dict(updates)}
        for field, empty in EMPTY_VALUES.items():
            if candidate[field] is None:
                candidate[field] = empty

        # Lowering the red flag retracts the whole statement. The domain
        # refuses a note or a grade with no active flag (last month's `severe`
        # must not read as current), and an athlete who is finally well again
        # should not have to send three fields to say so — so the two details
        # are cleared here unless the same patch supplies them, in which case
        # it is asking for a contradiction and the domain says so.
        touched = set(updates)
        if "red_flag_active" in updates and not candidate["red_flag_active"]:
            for field in RED_FLAG_DETAILS:
                if field not in updates and candidate[field] is not None:
                    candidate[field] = None
                    touched.add(field)

        # Validate through the domain value object rather than field by field:
        # the rules live there, so the API and a future MCP tool cannot drift
        # apart on what a legal profile is.
        with domain_rules():
            AthleteProfile(**candidate)

        bootstrapping = athlete is None
        if bootstrapping:
            athlete = Athlete(id=SINGLETON_ATHLETE_ID)
        for field in UPDATABLE_FIELDS:
            if field in touched:
                setattr(athlete, field, candidate[field])
        athlete = await self._repository.add(athlete)
        if bootstrapping:
            await self._audit.record(
                actor=actor,
                action="athlete.created",
                entity_type=ENTITY_TYPE,
                entity_id=athlete.id,
                payload={"bootstrap": True},
            )

        after = _values(athlete)
        await self._audit.record(
            actor=actor,
            action="athlete.updated",
            entity_type=ENTITY_TYPE,
            entity_id=athlete.id,
            payload={
                "changed": {
                    field: {
                        "from": _jsonable(before[field]),
                        "to": _jsonable(after[field]),
                    }
                    for field in touched
                    if before[field] != after[field]
                }
            },
        )
        await commit(self._session)
        return athlete


def _empty_profile() -> dict[str, Any]:
    """The field values of a not-yet-bootstrapped profile."""
    return {
        "name": None,
        "date_of_birth": None,
        "sex": Sex.UNSPECIFIED,
        "height_cm": None,
        "capabilities": {},
        "plan_state": PlanState.ACTIVE,
        "red_flag_active": False,
        "red_flag_note": None,
        "red_flag_severity": None,
    }


def _values(athlete: Athlete) -> dict[str, Any]:
    """The profile's fields as native Python values, keyed by field name."""
    return {
        "name": athlete.name,
        "date_of_birth": athlete.date_of_birth,
        "sex": athlete.sex,
        "height_cm": athlete.height_cm,
        "capabilities": dict(athlete.capabilities or {}),
        "plan_state": athlete.plan_state,
        "red_flag_active": athlete.red_flag_active,
        "red_flag_note": athlete.red_flag_note,
        "red_flag_severity": athlete.red_flag_severity,
    }


def _jsonable(value: Any) -> Any:
    """Make a profile value storable in the audit payload column."""
    if isinstance(value, dt.date):
        return value.isoformat()
    # Every enum on the profile is a `StrEnum`, and the trail records the
    # member's value for the same reason the columns do: one spelling of the
    # vocabulary, whichever side of the ORM the reader is on.
    if isinstance(value, StrEnum):
        return value.value
    return value
