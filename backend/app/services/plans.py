"""Use-cases for plans: create, read, change, tag a week, and find a date's plan.

The rules are `app.domain.plans`'; this module applies them against what is
stored. Two of them are worth restating where they are enforced:

* **Two active plans may not overlap in dates** (409). Checked on every write
  that could make two overlap — a new active plan, a plan activated, a plan's
  dates moved — against every other active plan *except the default*, which is
  undated by design and which every dated plan carves out of.
* **The default plan stays undated and active** (422). It is the plan of every
  date no other plan covers; dating or deactivating it would leave dates with
  no plan, which is the state migration `0028` exists to end. Its name, phase
  and follow-exactly flag are the athlete's to change like any plan's.

The audit follows `AthleteSettingsService`: one ``plan.updated`` row carrying
``{field: {from, to}}`` for exactly the fields whose value moved, and none for
a PATCH that moves nothing.
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.actor import Actor
from app.domain.plans import (
    PlanPhase,
    PlanWeekTag,
    check_span,
    check_week_start,
    covers,
    normalise_tags,
    spans_overlap,
)
from app.persistence.audit import AuditRepository
from app.persistence.db import commit, refresh
from app.persistence.plans import PlanRepository, PlanRow
from app.services.guardrails import check_write_cap

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "plan"

#: Fields `update` accepts.
UPDATABLE_FIELDS = (
    "name",
    "phase",
    "follow_exactly",
    "active",
    "starts_on",
    "ends_before",
)

#: Fields an explicit ``null`` may not clear: a plan always has these.
UNCLEARABLE_FIELDS = ("name", "phase", "follow_exactly", "active")

#: Fields the default plan may not change (see the module docstring).
FIXED_ON_DEFAULT = ("active", "starts_on", "ends_before")


class PlansService:
    """Use-cases for plans. Raises AppError subclasses."""

    def __init__(
        self, session: AsyncSession, repository: PlanRepository, audit: AuditRepository
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(session, PlanRepository(session), AuditRepository(session))

    # --- reads ---------------------------------------------------------------

    async def list(self) -> Sequence[PlanRow]:
        """Every plan, oldest first."""
        return await self._repository.list()

    async def get(self, plan_id: uuid.UUID) -> PlanRow:
        """One plan.

        Raises:
            NotFoundError: When no plan has that id.
        """
        row = await self._repository.get(plan_id)
        if row is None:
            raise NotFoundError(f"Plan {plan_id} not found")
        return row

    async def plan_covering(self, day: dt.date) -> PlanRow | None:
        """:meth:`plan_for` without the write.

        Answers ``None`` where :meth:`plan_for` would create the default plan;
        for read paths, which must not insert.
        """
        for row in await self._repository.active_dated():
            if covers(row.starts_on, row.ends_before, day):
                return row
        return await self._repository.default()

    async def plan_for(self, day: dt.date) -> PlanRow:
        """The plan ``day`` belongs to.

        That is the active dated plan covering it, else the default plan. At
        most one active dated plan covers any day — the overlap rule — so
        the first match is the only one. Creates the default plan on a
        database that has none (see `PlanRepository.ensure_default`); that
        write is unaudited, like the migration that normally makes it, because
        it restores infrastructure rather than recording a choice.
        """
        found = await self.plan_covering(day)
        if found is not None:
            return found
        return await self._repository.ensure_default()

    async def week_tags(
        self, plan_id: uuid.UUID, week_start: dt.date
    ) -> tuple[PlanWeekTag, ...]:
        """The tags on one week of one plan.

        Raises:
            NotFoundError: When no plan has that id.
            ValidationError: When ``week_start`` is not a Monday.
        """
        await self.get(plan_id)
        _check_week(week_start)
        return normalise_tags(await self._repository.week_tags(plan_id, week_start))

    # --- writes --------------------------------------------------------------

    async def create(
        self,
        *,
        actor: Actor,
        name: str,
        phase: PlanPhase,
        follow_exactly: bool = False,
        active: bool = True,
        starts_on: dt.date | None = None,
        ends_before: dt.date | None = None,
    ) -> PlanRow:
        """Create a plan and commit.

        Raises:
            ValidationError: When its dates hold no day.
            ConflictError: When it is active and overlaps another active plan.
        """
        _check_span(starts_on, ends_before)
        if active:
            await self._check_overlap(None, starts_on, ends_before)
        await check_write_cap(self._session, actor)
        row = await self._repository.add(
            PlanRow(
                name=name,
                phase=phase,
                follow_exactly=follow_exactly,
                active=active,
                starts_on=starts_on,
                ends_before=ends_before,
            )
        )
        await self._audit.record(
            actor=actor,
            action="plan.created",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload=_snapshot(row),
        )
        await commit(self._session)
        await refresh(self._session, row)
        return row

    async def update(
        self, plan_id: uuid.UUID, updates: Mapping[str, Any], *, actor: Actor
    ) -> PlanRow:
        """Apply a partial change; ``updates`` holds only the fields sent.

        Raises:
            NotFoundError: When no plan has that id.
            ValidationError: When a field is unknown or cleared that cannot
                be, when the dates hold no day, or when the change would date
                or deactivate the default plan.
            ConflictError: When the plan would be active and overlap another
                active plan.
        """
        unknown = set(updates) - set(UPDATABLE_FIELDS)
        if unknown:
            raise ValidationError(f"Unknown plan fields: {', '.join(sorted(unknown))}")
        for name in UNCLEARABLE_FIELDS:
            if name in updates and updates[name] is None:
                raise ValidationError(f"{name} cannot be cleared")

        row = await self.get(plan_id)
        changed = {
            field: {"from": _jsonable(getattr(row, field)), "to": _jsonable(value)}
            for field, value in updates.items()
            if getattr(row, field) != value
        }
        if not changed:
            return row
        if row.default and set(changed) & set(FIXED_ON_DEFAULT):
            raise ValidationError(
                "The default plan stays undated and active: it is the plan of "
                "every date no other plan covers. Create a dated plan instead."
            )

        starts_on = updates.get("starts_on", row.starts_on)
        ends_before = updates.get("ends_before", row.ends_before)
        active = updates.get("active", row.active)
        _check_span(starts_on, ends_before)
        if active and not row.default:
            await self._check_overlap(row.id, starts_on, ends_before)
        await check_write_cap(self._session, actor)

        for field in changed:
            setattr(row, field, updates[field])
        row = await self._repository.add(row)
        await self._audit.record(
            actor=actor,
            action="plan.updated",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={"changed": changed},
        )
        await commit(self._session)
        await refresh(self._session, row)
        return row

    async def set_week_tags(
        self,
        plan_id: uuid.UUID,
        week_start: dt.date,
        tags: Sequence[PlanWeekTag],
        *,
        actor: Actor,
    ) -> tuple[PlanWeekTag, ...]:
        """Make one week's tags exactly ``tags``, and commit.

        A PUT, so ``[]`` clears the week. A call that changes nothing writes
        no audit row.

        Raises:
            NotFoundError: When no plan has that id.
            ValidationError: When ``week_start`` is not a Monday.
        """
        before = await self.week_tags(plan_id, week_start)
        after = normalise_tags(tags)
        if before == after:
            return after
        await check_write_cap(self._session, actor)
        await self._repository.replace_week_tags(plan_id, week_start, after)
        await self._audit.record(
            actor=actor,
            action="plan.week_tagged",
            entity_type=ENTITY_TYPE,
            entity_id=plan_id,
            payload={
                "week_start": week_start.isoformat(),
                "from": [tag.value for tag in before],
                "to": [tag.value for tag in after],
            },
        )
        await commit(self._session)
        return after

    # --- helpers -------------------------------------------------------------

    async def _check_overlap(
        self,
        plan_id: uuid.UUID | None,
        starts_on: dt.date | None,
        ends_before: dt.date | None,
    ) -> None:
        """Refuse a span that shares a day with another active dated plan."""
        for other in await self._repository.active_dated():
            if other.id == plan_id:
                continue
            if spans_overlap(
                (starts_on, ends_before), (other.starts_on, other.ends_before)
            ):
                raise ConflictError(
                    f"Active plan {other.id} ({other.name!r}, "
                    f"{_span(other.starts_on, other.ends_before)}) already covers "
                    f"some of {_span(starts_on, ends_before)}. Two active plans "
                    "may not overlap: change one's dates or deactivate it."
                )


def _check_span(starts_on: dt.date | None, ends_before: dt.date | None) -> None:
    """`check_span`, as a ValidationError."""
    try:
        check_span(starts_on, ends_before)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def _check_week(week_start: dt.date) -> None:
    """`check_week_start`, as a ValidationError."""
    try:
        check_week_start(week_start)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def _span(starts_on: dt.date | None, ends_before: dt.date | None) -> str:
    """A half-open span, written for a message."""
    start = starts_on.isoformat() if starts_on else "the beginning"
    end = ends_before.isoformat() if ends_before else "open-ended"
    return f"[{start}, {end})"


def _snapshot(row: PlanRow) -> dict[str, Any]:
    """A plan's fields as plain JSON, for the creation audit row."""
    return {field: _jsonable(getattr(row, field)) for field in UPDATABLE_FIELDS}


def _jsonable(value: Any) -> Any:
    """An enum as its value and a date as ISO, so the payload is plain JSON."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dt.date):
        return value.isoformat()
    return value
