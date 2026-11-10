"""Reading and changing the athlete's training settings.

The audit follows `AthleteService.update`: one ``athlete_settings.updated``
row per change, carrying ``{field: {from, to}}`` for exactly the fields whose
value moved — including the ones the domain derived (the mode an answer set,
the question marked seen). A change that moves nothing — an empty PATCH, or
one repeating the stored values — writes no row and no audit entry: the trail
records changes, and a save button pressed twice is not one.
"""

from collections.abc import Mapping
from dataclasses import asdict
from enum import Enum
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.domain.actor import Actor
from app.domain.training_mode import TrainingSettings, with_changes
from app.persistence.athlete_settings import AthleteSettingsRepository
from app.persistence.audit import AuditRepository
from app.persistence.db import commit
from app.services.guardrails import check_write_cap

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "athlete_settings"

#: The action written when a setting changes.
UPDATED_ACTION = "athlete_settings.updated"


class AthleteSettingsService:
    """Use-cases for the training settings. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: AthleteSettingsRepository,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session, AthleteSettingsRepository(session), AuditRepository(session)
        )

    async def read(self) -> TrainingSettings:
        """The stored settings, or the defaults when none are stored. Never writes."""
        row = await self._repository.get()
        return TrainingSettings() if row is None else row.to_domain()

    async def update(
        self, changes: Mapping[str, Any], *, actor: Actor
    ) -> TrainingSettings:
        """Apply a partial change; ``changes`` holds only the fields sent.

        Raises:
            ValidationError: When a field is unknown or sent as ``null`` —
                no training setting can be cleared.
        """
        current = await self.read()
        try:
            candidate = with_changes(current, changes)
        except ValueError as error:
            raise ValidationError(str(error)) from error

        before, after = asdict(current), asdict(candidate)
        changed = {
            field: {"from": _jsonable(before[field]), "to": _jsonable(after[field])}
            for field in after
            if before[field] != after[field]
        }
        if not changed:
            return current

        await check_write_cap(self._session, actor)
        row = await self._repository.save(candidate)
        await self._audit.record(
            actor=actor,
            action=UPDATED_ACTION,
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={"changed": changed},
        )
        await commit(self._session)
        return candidate


def _jsonable(value: Any) -> Any:
    """An enum member as its value, so the audit payload is plain JSON."""
    return value.value if isinstance(value, Enum) else value
