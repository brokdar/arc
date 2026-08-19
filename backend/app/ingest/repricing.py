"""Appending an anchor, and repricing the history it governs (issue #18).

The versioning doctrine's cascade — anchors → per-session scores — needs both
halves of the stack: `app.services.anchors.AnchorService` owns the append, and
`app.ingest.analysis.SessionAnalyser` owns the recompute, because only the
ingest layer may read the stored streams. A service cannot call the analyser
(imports point inward: ``ingest → services``), so the orchestration lives
here, one layer out, where both are importable — not in a scheduler job,
because the athlete who just appended an FTP is looking at the screen the
stale numbers are on.

**The measurement is never hostage to the repricing.** :func:`append_anchor_and_reprice`
lets the service commit the anchor first; everything after that degrades —
a session whose recompute fails is counted and logged and the loop moves on,
and a scan that fails wholesale is reported as unknown rather than unwinding
an append that already happened. Every affected session stays individually
recomputable through ``POST /sessions/{id}/metrics/recompute``.

**Affected** means the repricing would change the price. A session is
recomputed when the version now governing its date
(`app.domain.anchors.anchor_effective_on`) and the version its current
artefact pinned disagree *as measurements* — present where the other is
absent, or a different value. Comparing measurements rather than version ids
is what makes the cascade converge: appending an identical correction changes
the governing id but no number, and recomputing the whole history to move a
pin between two equal values would report churn where nothing changed.
"""

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.actor import Actor
from app.domain.anchors import (
    AnchorSource,
    AnchorType,
    AnchorUnit,
    AnchorVersion,
    Provenance,
    anchor_effective_on,
)
from app.ingest.analysis import SessionAnalyser
from app.persistence.activity import SessionRepository
from app.persistence.anchors import AnchorRepository, AnchorVersionRow
from app.persistence.metrics import SessionMetricsRepository
from app.services.anchors import AnchorService
from app.services.metrics import SessionMetricsService

logger = get_logger(__name__)

#: What a scan failure reports where the counts would be. The append is
#: committed by then, so the honest answer is not an error but this sentence.
SCAN_FAILED_NOTE = (
    "repriced: unknown — recompute failed, sessions remain individually recomputable"
)


@dataclass(frozen=True, slots=True)
class RepriceReport:
    """What appending one anchor version did to the recorded history.

    Args:
        examined: Sessions whose current metric artefact was checked.
        repriced: Sessions that got a new metric version out of it.
        unchanged: Sessions whose price the new version could not change —
            their date is governed by the same measurement as before.
        failed: Sessions whose recompute raised; each is logged, none stops
            the loop, and every one stays individually recomputable.
        note: ``None`` normally; :data:`SCAN_FAILED_NOTE` when the scan
            itself failed after the anchor was committed, in which case the
            counts are all zero and mean "unknown", not "nothing".
    """

    examined: int
    repriced: int
    unchanged: int
    failed: int
    note: str | None = None


@dataclass(frozen=True, slots=True)
class RepricePrediction:
    """What a dry-run append **would** do to the recorded history.

    The same scan as the real thing, run read-only against the history plus
    the draft — so the prediction cannot use a different rule than the write.
    """

    examined: int
    would_reprice: int
    unchanged: int


async def append_anchor_and_reprice(
    session: AsyncSession,
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
) -> tuple[AnchorVersionRow, RepriceReport]:
    """Append one anchor version, then reprice the sessions it governs.

    The append is `AnchorService.append`, unchanged — it validates, commits
    and audits exactly as it would without a cascade, and any refusal it
    raises propagates with nothing written. Only after the version is
    committed does the scan run; from that point nothing raises (see the
    module docstring for the degradation ladder).

    Returns:
        The committed version and the :class:`RepriceReport` describing what
        repricing it triggered.

    Raises:
        ValidationError: For exactly the reasons `AnchorService.append`
            would raise it.
        RateLimitedError: When an agent actor's write cap is spent.
    """
    row = await AnchorService.from_session(session).append(
        actor=actor,
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
    try:
        affected, unchanged = await _scan(session, anchor_type=anchor_type)
    except Exception:
        logger.exception(
            "anchor_reprice_scan_failed",
            anchor_type=anchor_type.value,
            anchor_version_id=str(row.id),
        )
        await session.rollback()
        # The rollback expired the committed row; reload it so the adapter
        # can still render the version that *did* land.
        await session.refresh(row)
        return row, RepriceReport(
            examined=0, repriced=0, unchanged=0, failed=0, note=SCAN_FAILED_NOTE
        )

    analyser = SessionAnalyser.from_session(session)
    reason = f"repriced: {anchor_type.value} anchor appended"
    repriced = 0
    failed = 0
    for session_id in affected:
        try:
            await analyser.compute(session_id, actor=actor, reason=reason)
            repriced += 1
        except Exception:
            logger.exception(
                "anchor_reprice_session_failed",
                session_id=str(session_id),
                anchor_type=anchor_type.value,
                anchor_version_id=str(row.id),
            )
            # The failed compute may have left the transaction dirty; a clean
            # slate is what lets the next session's recompute proceed.
            await session.rollback()
            failed += 1
    if failed:
        # Rolling back expired every loaded instance, the committed anchor
        # row included; reload it so the adapter can still render it.
        await session.refresh(row)
    return row, RepriceReport(
        examined=len(affected) + unchanged,
        repriced=repriced,
        unchanged=unchanged,
        failed=failed,
    )


async def preview_anchor_append(
    session: AsyncSession,
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
) -> tuple[AnchorVersion, RepricePrediction]:
    """The dry run of :func:`append_anchor_and_reprice`, writing nothing.

    Validation is `AnchorService.preview` — the same rules the write applies,
    because the write builds its row from that same method. The prediction is
    the same affected-session scan the write runs, with the draft standing in
    for the version an append would create.

    Raises:
        ValidationError: For exactly the reasons the real append would
            raise it.
    """
    draft = await AnchorService.from_session(session).preview(
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
    affected, unchanged = await _scan(session, anchor_type=anchor_type, draft=draft)
    return draft, RepricePrediction(
        examined=len(affected) + unchanged,
        would_reprice=len(affected),
        unchanged=unchanged,
    )


async def _scan(
    session: AsyncSession,
    *,
    anchor_type: AnchorType,
    draft: AnchorVersion | None = None,
) -> tuple[list[uuid.UUID], int]:
    """Split every session with a current artefact into affected / unchanged.

    Reads the pins through `SessionMetricsService.pins` — the same machinery
    every reader of "what was this computed against" uses — and the governing
    version through the domain's `anchor_effective_on`, over the history as
    stored plus ``draft`` when predicting.

    Returns:
        The affected session ids (oldest local date first, so the repricing
        walks the history in the order the athlete rode it) and the count of
        sessions examined but unchanged.
    """
    history = [
        row.to_domain() for row in await AnchorRepository(session).history(anchor_type)
    ]
    if draft is not None:
        history.append(draft)
    metrics_repository = SessionMetricsRepository(session)
    current = list(await metrics_repository.all_current())
    pins = await SessionMetricsService.from_session(session).pins(current)
    sessions_by_id = await SessionRepository(session).by_ids(
        [row.session_id for row in current]
    )

    affected: list[tuple[dt.date, uuid.UUID]] = []
    unchanged = 0
    for row in current:
        session_row = sessions_by_id.get(row.session_id)
        if session_row is None:  # pragma: no cover — the FK forbids it
            continue
        pinned = next(
            (
                version
                for pinned_type, version in pins.get(row.id, [])
                if pinned_type is anchor_type
            ),
            None,
        )
        governing = anchor_effective_on(history, session_row.local_date)
        if _prices_identically(pinned, governing):
            unchanged += 1
        else:
            affected.append((session_row.local_date, row.session_id))
    return [session_id for _, session_id in sorted(affected)], unchanged


def _prices_identically(
    pinned: AnchorVersionRow | None, governing: AnchorVersion | None
) -> bool:
    """Whether the pinned and the governing version price a session the same.

    A measurement prices by its value in its unit; provenance, protocol and
    the version's identity do not reach a single derived number. Absent on
    one side and present on the other always differs — that is the issue-#18
    case, a null pin against a history that now has an answer.
    """
    if pinned is None or governing is None:
        return pinned is None and governing is None
    return pinned.value == governing.value and pinned.unit is governing.unit
