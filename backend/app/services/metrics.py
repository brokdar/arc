"""Writing and reading the metric artefact. The versioning doctrine, in code.

This service is the **only** writer of `session_metrics`, and it does exactly
one thing to the chain: appends. A recomputation writes version *n+1* and sets
``superseded_by`` on version *n*; there is no update path and no delete path,
which is what makes "the numbers a verdict was confirmed against" a question
with an answer (invariant 1).

It takes **prepared domain values** — a `SessionAnalysis` and the anchor
version ids it was computed against — and never reads a stream. Parquet lives
a layer out, in `app.ingest.analysis`, because a service may not import the
ingest layer. That split is what lets the strength path live wholly here: a
manual session's metrics come from its logged sets, and there is no file to
read.

**Resolving the inputs lives here too.** `current_anchors` and the athlete's
sex are read the same way for a typed-in gym session as for a ride, and
`app.ingest.analysis` calls *this* method rather than keeping its own copy —
ingest may import services, so there is one answer to "which anchor versions
were in force". Two copies produced two different artefacts for one unchanged
session: the create path recorded "no anchor is in force" and no pins, and a
recompute of the very same session then wrote a divergent version 2 with real
ones. A version chain whose links differ for no reason is worse than no chain.
"""

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.domain.actor import Actor
from app.domain.anchors import AnchorType, AnchorVersion
from app.domain.athlete import Sex
from app.domain.metrics import LoadBasis, PerformedSet, zone_channel_for_aggregation
from app.domain.session_analysis import (
    SessionAnalysis,
    SessionInputs,
    analyse_session,
    analysis_to_json,
    zone_model_of,
)
from app.domain.versioning import FIRST_VERSION, next_version
from app.persistence.activity import SessionRepository, SessionRow
from app.persistence.anchors import AnchorVersionRow
from app.persistence.athlete import AthleteRepository
from app.persistence.audit import AuditRepository
from app.persistence.db import commit
from app.persistence.metrics import (
    MAX_REASON_LENGTH,
    SessionMetricsRepository,
    SessionMetricsRow,
)
from app.services.anchors import AnchorService

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "session_metrics"

#: The two actions the audit trail distinguishes: a first computation and
#: every one after it.
COMPUTED = "session.metrics_computed"
RECOMPUTED = "session.metrics_recomputed"

#: The column each anchor type is pinned in. Written out rather than derived
#: from the type's name so that adding an anchor is a deliberate edit here and
#: not a silent no-op — a pin that lands nowhere is a metric that cannot say
#: what it was computed against.
#: The anchors a metric artefact may pin, in the order they are resolved.
#: Every one goes through `AnchorService.current` — never a dictionary indexed
#: by type at a call site (addenda §7) — because "which version is in force"
#: is a domain rule about effective dates and creation times, not a lookup.
PINNED_ANCHORS: Sequence[AnchorType] = (
    AnchorType.FTP,
    AnchorType.LTHR,
    AnchorType.MAX_HR,
    AnchorType.RESTING_HR,
)

PIN_COLUMNS: Mapping[AnchorType, str] = {
    AnchorType.FTP: "ftp_anchor_version_id",
    AnchorType.LTHR: "lthr_anchor_version_id",
    AnchorType.MAX_HR: "max_hr_anchor_version_id",
    AnchorType.RESTING_HR: "resting_hr_anchor_version_id",
}


class SessionMetricsService:
    """Use-cases for the metric artefact. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: SessionMetricsRepository,
        sessions: SessionRepository,
        anchors: AnchorService,
        athletes: AthleteRepository,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._sessions = sessions
        self._anchors = anchors
        self._athletes = athletes
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            SessionMetricsRepository(session),
            SessionRepository(session),
            AnchorService.from_session(session),
            AthleteRepository(session),
            AuditRepository(session),
        )

    # --- reads ---------------------------------------------------------------

    async def get_current(self, session_id: uuid.UUID) -> SessionMetricsRow | None:
        """The metric version in force for one session, or ``None``."""
        return await self._repository.get_current(session_id)

    async def history(self, session_id: uuid.UUID) -> Sequence[SessionMetricsRow]:
        """Every version of one session's metrics, oldest first."""
        return await self._repository.history(session_id)

    async def current_for_sessions(
        self, session_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, SessionMetricsRow]:
        """The version in force for each of several sessions, in one query."""
        return await self._repository.current_for_sessions(session_ids)

    async def pins(
        self, rows: Iterable[SessionMetricsRow]
    ) -> dict[uuid.UUID, list[tuple[AnchorType, AnchorVersionRow]]]:
        """Resolve every artefact's pinned anchor ids to their versions.

        One query for all of them, keyed by metric-row id. A pin whose version
        cannot be found is left out rather than raising: the artefact is still
        readable, and a read path must not 500 over a dangling reference it
        can simply not render.
        """
        held = list(rows)
        versions = await self._anchors.by_ids(
            version_id
            for row in held
            for version_id in _pin_ids(row).values()
            if version_id is not None
        )
        return {
            row.id: [
                (anchor_type, versions[version_id])
                for anchor_type, version_id in _pin_ids(row).items()
                if version_id is not None and version_id in versions
            ]
            for row in held
        }

    async def current_anchors(
        self,
    ) -> dict[AnchorType, tuple[AnchorVersion, uuid.UUID]]:
        """Every pinnable anchor in force now, with the id it is pinned by.

        The single answer to that question. `app.ingest.analysis` calls this
        rather than resolving anchors itself, so a ride and a typed-in gym
        session cannot disagree about what was in force at the same instant.

        A type with no version in force is simply absent, and the metric that
        needed it reports the reason — an athlete who has never entered a
        resting heart rate is not an error condition.
        """
        resolved: dict[AnchorType, tuple[AnchorVersion, uuid.UUID]] = {}
        for anchor_type in PINNED_ANCHORS:
            try:
                row = await self._anchors.current(anchor_type)
            except NotFoundError:
                continue
            resolved[anchor_type] = (row.to_domain(), row.id)
        return resolved

    async def athlete_sex(self) -> Sex:
        """The athlete's sex, or `unspecified` before a profile exists.

        HRSS's coefficient depends on it (Appendix A.2), so it is an input to
        the artefact and is resolved on the same path for every session.
        """
        profile = await self._athletes.get()
        return profile.sex if profile is not None else Sex.UNSPECIFIED

    # --- writes --------------------------------------------------------------

    async def record(
        self,
        session_id: uuid.UUID,
        analysis: SessionAnalysis,
        *,
        actor: Actor,
        pins: Mapping[AnchorType, uuid.UUID] | None = None,
        reason: str | None = None,
    ) -> SessionMetricsRow:
        """Append a metric version for one session.

        Version 1 when there is nothing yet; otherwise *n+1*, with version *n*
        marked superseded in the same transaction. ``reason`` is required from
        version 2 onward — the versioning vocabulary says a recomputation
        states why — and ignored on version 1, which has no predecessor to
        explain itself against.

        Args:
            session_id: The session the metrics describe.
            analysis: The whole metric set, already computed.
            actor: Who is credited on the audit row.
            pins: Anchor type -> the version id the metrics were computed
                against. Types absent here are stored as NULL pins, which is
                the honest record of "no anchor was in force".
            reason: Why this recomputation happened.

        Raises:
            NotFoundError: When no session has that id.
        """
        session_row = await self._sessions.get(session_id)
        if session_row is None:
            raise NotFoundError(f"Session {session_id} not found")

        chain = await self._repository.history(session_id)
        # pyrefly: ignore[bad-specialization]
        # `SessionMetricsRow` satisfies `VersionRecord` at runtime; pyrefly
        # does not see through SQLAlchemy's `Mapped[X]` descriptors when
        # structurally matching a protocol. Same suppression, same reason, as
        # the intent chain's `current_intent`.
        version = next_version(chain)
        previous = await self._repository.get_current(session_id)

        row = SessionMetricsRow(
            session_id=session_id,
            version=version,
            as_of=dt.datetime.now(dt.UTC),
            recompute_reason=(
                None
                if version == FIRST_VERSION
                else (reason or "recomputed")[:MAX_REASON_LENGTH]
            ),
            power_zone_model=zone_model_of(analysis.power_time_in_zone),
            hr_zone_model=zone_model_of(analysis.hr_time_in_zone),
            payload=analysis_to_json(analysis),
            **{
                column: (pins or {}).get(anchor_type)
                for anchor_type, column in PIN_COLUMNS.items()
            },
        )
        row = await self._repository.add(row)
        if previous is not None:
            # The old version is closed off in the same transaction as the new
            # one: a reader holding the old id has to be able to walk forward,
            # and a chain with two unsuperseded tips is a broken chain.
            previous.superseded_by = row.id
            await self._repository.add(previous)

        await self._audit.record(
            actor=actor,
            action=COMPUTED if version == FIRST_VERSION else RECOMPUTED,
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "session_id": str(session_id),
                "version": version,
                "superseded": str(previous.id) if previous is not None else None,
                "recompute_reason": row.recompute_reason,
                "pins": {
                    anchor_type.value: str(version_id)
                    for anchor_type, version_id in (pins or {}).items()
                },
            },
        )
        await commit(self._session)
        await self._session.refresh(row)
        return row

    async def record_strength(
        self, session_row: SessionRow, *, actor: Actor, reason: str | None = None
    ) -> SessionMetricsRow:
        """Compute and store the metrics of a session with no stream.

        A manual strength session's whole metric set comes from its logged
        sets, so it needs no parquet file — which is why this path lives in
        the service rather than in `app.ingest.analysis`. Every stream-derived
        slot on the artefact carries its reason, exactly as it would for a
        ride whose power meter was flat.

        It still resolves and **pins the anchors in force**, even though no
        metric here consumes one. Two reasons, and the second is the
        load-bearing one. The artefact is a record of what the computation
        looked at, and "no resting-HR anchor is in force" and "there was no
        heart rate to apply one to" are different sentences to show an
        athlete. And a recompute of an unchanged session goes through
        `app.ingest.analysis`, which resolves them — so omitting them here
        made version 2 differ from version 1 for no reason anyone could point
        at.
        """
        anchors = await self.current_anchors()
        analysis = analyse_session(
            _strength_inputs(session_row, anchors=anchors, sex=await self.athlete_sex())
        )
        return await self.record(
            session_row.id,
            analysis,
            actor=actor,
            pins={
                anchor_type: version_id
                for anchor_type, (_, version_id) in anchors.items()
            },
            reason=reason,
        )


def _strength_inputs(
    session_row: SessionRow,
    *,
    anchors: Mapping[AnchorType, tuple[AnchorVersion, uuid.UUID]],
    sex: Sex,
) -> SessionInputs:
    """The domain inputs of a session that has logged sets and no stream.

    Deliberately the same shape `app.ingest.analysis` builds for a session
    whose recording is absent, field for field — that is what makes a
    recompute of an unchanged manual session reproduce its own payload.
    """
    return SessionInputs(
        discipline=session_row.discipline,
        recording_time_s=0.0,
        elapsed_time_s=session_row.duration_s,
        columns={},
        sex=sex,
        anchors={anchor_type: version for anchor_type, (version, _) in anchors.items()},
        sets=[
            PerformedSet(
                reps=logged.reps,
                load_kg=logged.load_kg,
                duration_s=logged.duration_s,
                per_side=logged.per_side,
            )
            for logged in session_row.logged_sets
        ],
    )


def _pin_ids(row: SessionMetricsRow) -> dict[AnchorType, uuid.UUID | None]:
    """The anchor version id pinned in each of the artefact's pin columns."""
    return {
        anchor_type: getattr(row, column) for anchor_type, column in PIN_COLUMNS.items()
    }


@dataclass(frozen=True, slots=True)
class MetricSummary:
    """The handful of numbers an aggregate reads off one metric artefact.

    The stored payload is JSON, and reading it by key at three call sites is
    three places to mis-spell ``training_load``. Everything that totals
    sessions — the week rail, the session list, WP-7's trends — goes through
    this instead.

    Args:
        session_id: Which session these came from.
        version: Which version of its metrics.
        recording_time_s: The duration the load was computed over (A5.1).
        training_load: The selected load, or ``None`` when neither model
            could be computed.
        load_basis: Which model produced it.
        easy_s: Seconds in the easy bands of the **one** channel A5.4's rule
            selects, or ``None`` when neither channel produced a distribution.
        moderate_s: The same, moderate.
        hard_s: The same, hard.
        zone_channel: Which channel those three came from.
        normalized_power: Recorded NP in watts, or ``None`` when no power was
            recorded. WP-6's intensity term compares it against the planned NP.
        average_hr: Recorded average heart rate, the fallback that term uses
            when there is no power on either side.
        distance_km: How far the ride went, off the artefact's speed block, or
            ``None`` for a session with no speed channel **and** for one whose
            artefact predates the block — the log row keeps its slot either
            way, and neither case is a zero.
        interval_count: How many work intervals the detector found
            (`app.domain.alignment.detect_work_intervals`, stored with the
            artefact). ``None`` when the artefact carries no interval block at
            all — an older payload — which is different from a ride in which
            none were detected, and WP-6's structure hint needs the
            distinction.
    """

    session_id: uuid.UUID
    version: int
    recording_time_s: float | None
    training_load: float | None
    load_basis: LoadBasis | None
    easy_s: float | None
    moderate_s: float | None
    hard_s: float | None
    zone_channel: LoadBasis | None
    normalized_power: float | None = None
    average_hr: float | None = None
    distance_km: float | None = None
    interval_count: int | None = None


def _number(document: Mapping[str, Any] | None, key: str) -> float | None:
    """One numeric field of a stored block, or ``None`` when it is absent."""
    value = (document or {}).get(key)
    return float(value) if isinstance(value, int | float) else None


def _block(payload: Mapping[str, Any], *keys: str) -> Mapping[str, Any] | None:
    """Walk into a nested block of the stored payload, tolerating absence."""
    current: Any = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current if isinstance(current, Mapping) else None


def summarise(row: SessionMetricsRow) -> MetricSummary:
    """Read one artefact's aggregate-facing numbers out of its payload.

    Tolerant by construction: a payload written by an earlier version of the
    metric set is missing keys this reads, and the honest answer to "what was
    the load" is then ``None`` — which every total already knows how to count
    as uncounted. Raising would make one old artefact fail a whole week.
    """
    payload: Mapping[str, Any] = row.payload if isinstance(row.payload, Mapping) else {}
    load = _block(payload, "load")
    basis_value = (load or {}).get("load_basis")
    basis = LoadBasis(basis_value) if basis_value in set(LoadBasis) else None

    power_zones = _block(payload, "time_in_zone", "power")
    hr_zones = _block(payload, "time_in_zone", "hr")
    channel = zone_channel_for_aggregation(
        basis,
        power_available=_number(power_zones, "total_s") is not None,
        hr_available=_number(hr_zones, "total_s") is not None,
    )
    chosen = power_zones if channel is LoadBasis.POWER else hr_zones
    intervals = payload.get("intervals")
    return MetricSummary(
        session_id=row.session_id,
        version=row.version,
        recording_time_s=_number(payload, "recording_time_s"),
        training_load=_number(load, "training_load"),
        load_basis=basis,
        easy_s=_number(chosen, "easy_s") if channel is not None else None,
        moderate_s=_number(chosen, "moderate_s") if channel is not None else None,
        hard_s=_number(chosen, "hard_s") if channel is not None else None,
        zone_channel=channel,
        normalized_power=_number(_block(payload, "power", "normalized_power"), "value"),
        average_hr=_number(_block(payload, "heart_rate", "average_hr"), "value"),
        distance_km=_number(_block(payload, "speed", "distance_km"), "value"),
        interval_count=len(intervals) if isinstance(intervals, list) else None,
    )


@dataclass(frozen=True, slots=True)
class MeasuredChannels:
    """The peaks and ranges one *detail* read shows off a metric artefact.

    A second reader beside `MetricSummary` rather than eight more fields on
    it, and the split is by audience: `MetricSummary` is what an **aggregate**
    reads — the week rail, the session list and the trends construct one per
    session, per row, and none of them totals a maximum heart rate. This one
    is the **detail's**, built only where a single session is being read in
    full, so a number nobody sums is not carried through every rollup that
    does not want it.

    Every field is what the athlete's device actually measured, straight off
    the stored assessment's ``value`` — the same pull `normalized_power` makes
    above, and the same tolerance :func:`summarise` documents: ``None`` where
    the channel was not recorded **and** where the payload predates the block,
    because an artefact written by an earlier metric set must stay readable.
    ``None`` is never a zero: a ride with no altimeter climbed an unknown
    amount, not nothing.

    Args:
        max_hr: Highest heart rate observed, in bpm. The number that says a
            max-HR anchor is due an append.
        max_power: Peak power, in watts.
        average_cadence: Mean cadence over the recording, in rpm.
        max_cadence: Peak cadence, in rpm.
        elevation_gain_m: Total ascent, in metres — whether 150 W was flat or
            climbing.
        average_temp_c: The **stream's** mean ambient temperature in °C, which
            is a measurement and not the athlete's recollection (that one is
            `SessionRow.temperature_c`, and the two are never merged).
        min_temp_c: The coldest sample, in °C.
        max_temp_c: The warmest sample, in °C.
    """

    max_hr: float | None
    max_power: float | None
    average_cadence: float | None
    max_cadence: float | None
    elevation_gain_m: float | None
    average_temp_c: float | None
    min_temp_c: float | None
    max_temp_c: float | None


def measured_channels(row: SessionMetricsRow) -> MeasuredChannels:
    """Read one artefact's measured channels out of its payload.

    Tolerant on exactly the terms :func:`summarise` is, and for the same
    reason — see :class:`MeasuredChannels`.
    """
    payload: Mapping[str, Any] = row.payload if isinstance(row.payload, Mapping) else {}
    return MeasuredChannels(
        max_hr=_number(_block(payload, "heart_rate", "max_hr"), "value"),
        max_power=_number(_block(payload, "power", "max_power"), "value"),
        average_cadence=_number(_block(payload, "cadence", "average_cadence"), "value"),
        max_cadence=_number(_block(payload, "cadence", "max_cadence"), "value"),
        elevation_gain_m=_number(_block(payload, "elevation_gain_m"), "value"),
        average_temp_c=_number(
            _block(payload, "temperature", "average_temp_c"), "value"
        ),
        min_temp_c=_number(_block(payload, "temperature", "min_temp_c"), "value"),
        max_temp_c=_number(_block(payload, "temperature", "max_temp_c"), "value"),
    )
