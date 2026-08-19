"""Use-cases for anchors: read the history, append to it. Never edit it.

There is no `update` and no `delete` here, and there is none in
`app.persistence.anchors` either (build-plan invariant 3). The API's 405
handlers state the rule; these two absences are what enforce it.
"""

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_today
from app.core.exceptions import NotFoundError, ValidationError, domain_rules
from app.core.logging import get_logger
from app.domain.actor import Actor
from app.domain.anchors import (
    ANCHOR_UNITS,
    MVP_STALENESS_STATE,
    RESERVED_ANCHOR_TYPES,
    AnchorSource,
    AnchorType,
    AnchorUnit,
    AnchorVersion,
    Provenance,
    anchor_effective_on,
)
from app.domain.prediction import PinnedAnchor
from app.persistence.anchors import AnchorRepository, AnchorVersionRow
from app.persistence.audit import AuditRepository
from app.persistence.db import commit
from app.services.guardrails import check_write_cap

logger = get_logger(__name__)

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "anchor_version"


class AnchorService:
    """Use-cases for anchor versions. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: AnchorRepository,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(session, AnchorRepository(session), AuditRepository(session))

    async def list(
        self,
        *,
        anchor_type: AnchorType | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[AnchorVersionRow], int]:
        """Return a page of anchor history, newest first, plus the total."""
        return await self._repository.list(
            anchor_type=anchor_type, offset=offset, limit=limit
        )

    async def get(self, anchor_version_id: uuid.UUID) -> AnchorVersionRow:
        """Return one anchor version by id.

        Raises:
            NotFoundError: When no version has that id.
        """
        row = await self._repository.get(anchor_version_id)
        if row is None:
            raise NotFoundError(f"Anchor version {anchor_version_id} not found")
        return row

    async def by_ids(
        self, anchor_version_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, AnchorVersionRow]:
        """Return the named versions keyed by id, in one query.

        For resolving *pins*, which are ids a planned session froze — never
        "what is in force now". A caller reading a week of sessions collects
        every pinned id first and asks once, so nothing scales with the number
        of sessions on screen. Missing ids are absent rather than an error:
        see :meth:`AnchorRepository.get_many`.
        """
        rows = await self._repository.get_many(anchor_version_ids)
        return {row.id: row for row in rows}

    async def current(self, anchor_type: AnchorType) -> AnchorVersionRow:
        """Return the version of ``anchor_type`` in force today.

        The choice is made by `app.domain.anchors.anchor_effective_on` over the
        whole history rather than by an ``ORDER BY ... LIMIT 1``: future-dated
        and back-dated versions both exist, and the rule for which one counts
        is a domain rule that scoring and metrics reuse. The day is the
        athlete's own (`app.core.clock.athlete_today`, from
        `MATCHING__TIMEZONE`), never the UTC one — an `effective_date` is a
        local calendar date (issue #62).

        **`anchor_effective_on` and not `anchor_as_of`, deliberately.** The
        second one additionally requires ``created_at <= moment``, which is
        what makes *reproducing a past read* honest — a value entered today
        cannot become what last week's score was looking at. Asked about
        **now**, that clause can only ever exclude a row that does not exist
        yet, so it looks free. It is not: `created_at` is stamped from
        `dt.datetime.now`, which reads `CLOCK_REALTIME` and is **not
        monotonic**. An NTP correction, a resumed VM or a virtualised host
        clock steps it backwards by milliseconds — this WSL2 container by
        ~180 ms every ~30 s — and a version written moments ago then carries a
        stamp later than the "now" of the very next request, so the athlete is
        told to "append an FTP first" about the FTP they just appended.
        `current` asks "which measurement does the history assign to today",
        and that question has no instant in it. Future *effective* dates are
        still excluded, by the half both functions share.

        **And therefore no ``moment`` parameter.** Naming an instant would put
        the reproducibility question back in the one place that must not ask
        it; no caller in `app/` ever passed one. Reproducing a past read is
        `anchor_as_of`, called by whoever is replaying it (#66, issue #62).

        Raises:
            NotFoundError: When no version of that type is in force.
        """
        day = athlete_today()
        rows = await self._repository.history(anchor_type)
        # Paired rather than keyed by the domain value: two versions can be
        # equal in every field the domain models and still be distinct rows.
        pairs = [(row.to_domain(), row) for row in rows]
        with domain_rules():
            in_force = anchor_effective_on((version for version, _ in pairs), day)
        if in_force is None:
            raise NotFoundError(
                f"No {anchor_type.value} anchor is in force on "
                f"{day.isoformat()}; append one first"
            )
        return next(row for version, row in pairs if version is in_force)

    async def _next_created_at(
        self, anchor_type: AnchorType, *, now: dt.datetime
    ) -> dt.datetime:
        """Choose the stamp a new version of ``anchor_type`` carries.

        The wall clock, clamped **strictly above the newest stamp already in
        that type's history** — rather than the wall clock trusted alone,
        which is what this displaced. The tie-break in
        `app.domain.anchors._ordering_key` is *"a correction appended later
        with the same effective date wins over the value it corrects"*, and
        ``created_at`` is its only witness to "later" — but `dt.datetime.now`
        reads ``CLOCK_REALTIME``, which is not monotonic. A backwards step
        (an NTP correction anywhere; this WSL2 host by ~95 ms twice a minute)
        landing between two appends would stamp the correction *earlier* than
        the row it corrects, and every read from then on would return the
        corrected value as current. One microsecond is the resolution of the
        column and of `dt.datetime`; the clamp only engages while the clock
        is behind the history, so stamps return to honest wall time as soon
        as it catches up. It also keeps `anchor_as_of`'s
        ``created_at <= moment`` replays ordered the way the appends actually
        happened. (Companion to #66, which made *reads* survive a stamp the
        clock has not reached; this makes *writes* stop producing
        out-of-order stamps at all.)

        Chosen here, once, for both callers of :meth:`preview`: the dry run's
        draft joins the stored history in the repricing `_scan`, so an
        unclamped draft would lose the tie-break the real append wins and the
        prediction would use a different rule than the write.

        Known and accepted: nothing serializes the ``max(created_at)`` read
        against the insert, so two *concurrent* appends of one type inside
        the clamp window could both compute ``latest + 1µs`` and recreate the
        tie. Accepted because this is a single-athlete application with no
        concurrent writer in practice, the window is the clock-error
        interval, and the failure mode is the pre-fix status quo (an ordering
        tie), not a new corruption. If multi-writer appends ever matter, a
        unique constraint on ``(anchor_type, created_at)`` plus a retry — or
        a per-type advisory lock — closes it.

        The clamp engaging at all means the host clock is broken *right now*,
        and since #66 made reads tolerate future stamps and this makes writes
        tolerate them, the log line below is the only remaining signal.
        """
        latest = await self._repository.latest_created_at(anchor_type)
        if latest is None or now > latest:
            return now
        logger.warning(
            "anchor_created_at_clamped",
            anchor_type=anchor_type.value,
            behind_seconds=(latest - now).total_seconds(),
        )
        return latest + dt.timedelta(microseconds=1)

    async def preview(
        self,
        *,
        anchor_type: AnchorType,
        value: float,
        provenance: Provenance,
        source: AnchorSource,
        effective_date: dt.date | None = None,
        unit: AnchorUnit | None = None,
        protocol: str | None = None,
        ci_low: float | None = None,
        ci_high: float | None = None,
    ) -> AnchorVersion:
        """Build and validate the version :meth:`append` would write.

        The dry run of an append (WP-8.3), and a **separate read-only method
        rather than a flag on the writer**. An append-only history is enforced
        by this service having exactly one way to write and no way to unwrite;
        giving that one writer a mode in which it does not write puts the
        question "did this actually persist" inside the append path, where the
        answer has to stay "always". A caller that wants the check without the
        consequence asks for the check.

        Every rule `append` applies is applied here — the reserved types, the
        unit, the domain invariants including *`tested` requires a protocol*,
        and the monotonic ``created_at`` (:meth:`_next_created_at`, the one
        read this otherwise write-free method does) — because `append` calls
        this to build its row. There is no second code path to disagree with,
        and the repricing dry run folds this draft into the stored history,
        where a stamp chosen by a different rule than the write's would make
        the prediction disagree with the append it predicts.

        Returns:
            The domain version, unsaved and unrecorded.

        Raises:
            ValidationError: For exactly the reasons `append` would raise it.
        """
        # Enforced here as well as in the API schema: this service is the
        # one path every adapter shares, and WP-8's MCP tools do not go
        # through `AnchorVersionCreate`.
        if anchor_type in RESERVED_ANCHOR_TYPES:
            raise ValidationError(
                f"{anchor_type.value} anchors are reserved for the "
                "critical-power model (WP-5) and cannot be appended yet"
            )
        now = dt.datetime.now(dt.UTC)
        created_at = await self._next_created_at(anchor_type, now=now)
        # The athlete's calendar day, not the UTC one: an FTP appended at 08:00
        # on the 20th in Auckland is effective from the 20th, and dating it the
        # 19th would put it in force a day early for every score that reads it
        # (issue #62).
        #
        # Read *outside* `domain_rules()` on purpose. An unresolvable
        # `MATCHING__TIMEZONE` is a broken deployment, not a broken request:
        # translated to a 422 it would tell a caller whose payload was perfectly
        # good that their input was invalid, and hide the operator's typo behind
        # a client error. Every other read of this clock — `PlanService.week`,
        # `AnchorService.current`, the MCP tools — lets it surface as a 500, and
        # this was the one place that did not.
        #
        # From the wall clock, not the clamped stamp: the default effective
        # date is the day the athlete is appending on, and a stamp pushed past
        # a future-dated one must not push their append into tomorrow.
        day = athlete_today(now)
        with domain_rules():
            return AnchorVersion(
                anchor_type=anchor_type,
                value=value,
                unit=unit or ANCHOR_UNITS[anchor_type],
                provenance=provenance,
                protocol=protocol,
                effective_date=effective_date or day,
                ci_low=ci_low,
                ci_high=ci_high,
                created_at=created_at,
                source=source,
                staleness_state=MVP_STALENESS_STATE,
            )

    async def append(
        self,
        *,
        actor: Actor,
        anchor_type: AnchorType,
        value: float,
        provenance: Provenance,
        source: AnchorSource,
        effective_date: dt.date | None = None,
        unit: AnchorUnit | None = None,
        protocol: str | None = None,
        ci_low: float | None = None,
        ci_high: float | None = None,
    ) -> AnchorVersionRow:
        """Append a new version to an anchor's history.

        Args:
            actor: Who is writing; recorded on the audit trail.
            anchor_type: Which anchor this is a version of.
            value: The measurement.
            provenance: How the value was arrived at. `tested` additionally
                requires ``protocol``.
            source: Whether the athlete or the agent is appending.
            effective_date: The date the value applies from; omitted, the
                athlete's own today (`MATCHING__TIMEZONE`).
            unit: The anchor type's own unit when omitted — supplying a
                different one is an error, not a conversion request.
            protocol: How the value was measured.
            ci_low: Lower bound of the confidence interval.
            ci_high: Upper bound of the confidence interval.

        Raises:
            ValidationError: When the version breaks a domain rule, or when
                ``anchor_type`` is reserved.
            RateLimitedError: When an agent actor's trailing-hour write cap is
                spent. Checked here rather than in the MCP tool, so it binds
                every path an agent can reach this write through.
        """
        await check_write_cap(self._session, actor)
        version = await self.preview(
            anchor_type=anchor_type,
            value=value,
            provenance=provenance,
            source=source,
            effective_date=effective_date,
            unit=unit,
            protocol=protocol,
            ci_low=ci_low,
            ci_high=ci_high,
        )

        row = await self._repository.add(
            AnchorVersionRow(
                anchor_type=version.anchor_type,
                value=version.value,
                unit=version.unit,
                provenance=version.provenance,
                protocol=version.protocol,
                effective_date=version.effective_date,
                ci_low=version.ci_low,
                ci_high=version.ci_high,
                source=version.source,
                staleness_state=version.staleness_state,
                created_at=version.created_at,
            )
        )
        await self._audit.record(
            actor=actor,
            action="anchor.appended",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload=_payload(row),
        )
        await commit(self._session)
        return row


def parse_pins(stored: Mapping[str, Any] | None) -> dict[AnchorType, uuid.UUID]:
    """Read a stored intent's ``pinned_anchor_versions`` back into domain types.

    The column holds JSON, so both halves arrive as strings; every caller that
    wants the pins as types wants exactly this, which is why it is a function
    rather than three copies of a dict comprehension.
    """
    return {
        AnchorType(anchor): uuid.UUID(version_id)
        for anchor, version_id in (stored or {}).items()
    }


def resolve_pins(
    pins: Mapping[AnchorType, uuid.UUID],
    versions: Mapping[uuid.UUID, AnchorVersionRow],
) -> dict[AnchorType, PinnedAnchor]:
    """Pair each pin with the version it names, dropping pins nothing answers.

    ``versions`` is normally the result of :meth:`AnchorService.by_ids` over
    every pin on the screen. A pin with no row is left out rather than raising:
    the derived value it feeds then reports itself as unresolvable, which is
    the honest answer and not a 500 on a read path.
    """
    resolved: dict[AnchorType, PinnedAnchor] = {}
    for anchor_type, version_id in pins.items():
        row = versions.get(version_id)
        if row is not None:
            resolved[anchor_type] = PinnedAnchor(
                version_id=row.id, version=row.to_domain()
            )
    return resolved


def _payload(row: AnchorVersionRow) -> dict[str, Any]:
    """The appended value, as JSON, for the audit trail."""
    return {
        "anchor_type": row.anchor_type.value,
        "value": row.value,
        "unit": row.unit.value,
        "provenance": row.provenance.value,
        "protocol": row.protocol,
        "effective_date": row.effective_date.isoformat(),
        "ci_low": row.ci_low,
        "ci_high": row.ci_high,
        "source": row.source.value,
    }
