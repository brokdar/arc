"""The athlete's training settings: one row, and its repository.

**A table of its own, not columns on `athlete`.** Mode, units, locale and the
rest are how arc presents itself, not facts about the athlete's body; adding
them to `Athlete` would grow `AthleteService.UPDATABLE_FIELDS` and the athlete
PATCH contract with presentation settings, and every profile validation would
re-check them.

**A unique `scope`, the shape `app.persistence.ingest_settings` uses.** The
database, not the service, holds "at most one training-settings row": a second
insert under :data:`TRAINING_SCOPE` is a unique-constraint violation, which
is also what lets migration `0025`'s seed be replayed without doubling up.

**Seeded by the migration, tolerated absent.** `0025` writes the default row
on every database that existed then; a database built another way (the unit
suite's `create_all`, a restored dump from before 0025 was stamped) has none,
and the service answers the domain defaults for it and inserts on the first
real change — the read never writes.
"""

import datetime as dt
import uuid
from typing import Final

from sqlalchemy import Boolean, String, UniqueConstraint, func, select
from sqlalchemy import false as sa_false
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.training_mode import (
    AskFrequency,
    LiftedBefore,
    Locale,
    TrainingMode,
    TrainingSettings,
    Units,
)
from app.persistence.db import Base, flush
from app.persistence.types import UtcDateTime, enum_column

#: Longest `scope` accepted. Room for a name, not for a sentence.
MAX_SCOPE_LENGTH = 40

#: The one scope training settings are stored under.
TRAINING_SCOPE: Final = "training"


class AthleteSettingsRow(Base):
    """The stored training settings. At most one row, scope :data:`TRAINING_SCOPE`."""

    __tablename__ = "athlete_settings"
    __table_args__ = (UniqueConstraint("scope", name="uq_athlete_settings_scope"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    scope: Mapped[str] = mapped_column(String(MAX_SCOPE_LENGTH))
    mode: Mapped[TrainingMode] = mapped_column(enum_column(TrainingMode))
    plain_logbook: Mapped[bool] = mapped_column(Boolean)
    ask_frequency: Mapped[AskFrequency] = mapped_column(enum_column(AskFrequency))
    units: Mapped[Units] = mapped_column(enum_column(Units))
    locale: Mapped[Locale] = mapped_column(enum_column(Locale))
    high_contrast: Mapped[bool] = mapped_column(Boolean)
    before_you_start_seen: Mapped[bool] = mapped_column(Boolean)
    first_run_seen: Mapped[bool] = mapped_column(Boolean)
    #: Added by migration 0031 to a table that already held its row, hence the
    #: `server_default`: an athlete who never saw the question has no flag.
    no_max_efforts: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa_false()
    )
    #: Null until the first-run question is answered; skipping leaves it null.
    lifted_before: Mapped[LiftedBefore | None] = mapped_column(
        enum_column(LiftedBefore)
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

    def to_domain(self) -> TrainingSettings:
        """The row as the domain value."""
        return TrainingSettings(
            mode=self.mode,
            plain_logbook=self.plain_logbook,
            ask_frequency=self.ask_frequency,
            units=self.units,
            locale=self.locale,
            high_contrast=self.high_contrast,
            before_you_start_seen=self.before_you_start_seen,
            first_run_seen=self.first_run_seen,
            lifted_before=self.lifted_before,
            no_max_efforts=self.no_max_efforts,
        )


class AthleteSettingsRepository:
    """SQLAlchemy repository for the training settings."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> AthleteSettingsRow | None:
        """The stored row, or None when nothing has been stored."""
        result = await self._session.execute(
            select(AthleteSettingsRow).where(AthleteSettingsRow.scope == TRAINING_SCOPE)
        )
        return result.scalars().first()

    async def save(self, settings: TrainingSettings) -> AthleteSettingsRow:
        """Store ``settings``, updating the row in place or inserting it.

        Update-or-insert rather than delete-and-add, so the row's id — the
        `entity_id` on every audit row about it — and `created_at` survive.
        """
        row = await self.get()
        if row is None:
            row = AthleteSettingsRow(scope=TRAINING_SCOPE)
            self._session.add(row)
        row.mode = settings.mode
        row.plain_logbook = settings.plain_logbook
        row.ask_frequency = settings.ask_frequency
        row.units = settings.units
        row.locale = settings.locale
        row.high_contrast = settings.high_contrast
        row.before_you_start_seen = settings.before_you_start_seen
        row.first_run_seen = settings.first_run_seen
        row.lifted_before = settings.lifted_before
        row.no_max_efforts = settings.no_max_efforts
        await flush(self._session)
        return row
