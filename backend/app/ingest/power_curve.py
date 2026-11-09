"""A session's power curve, computed from its stored stream.

Kept out of `app.ingest.analysis` so that module grows by its wiring only:
`SessionAnalyser.compute` calls :func:`session_power_curve` once and hands the
answer to `app.services.power_curve.PowerCurveService.store`.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.core.exceptions import NotFoundError
from app.domain.power_curve import PowerCurve, power_curve
from app.domain.streams import AnomalyKind, StreamChannel
from app.persistence.activity import SessionRow

if TYPE_CHECKING:
    from app.ingest.analysis import SessionStreams


async def session_power_curve(
    session_row: SessionRow,
    load: Callable[[SessionRow], Awaitable[SessionStreams]],
) -> PowerCurve | None:
    """The session's power curve over its joined stream, or ``None``.

    Read through `app.ingest.analysis.load_streams` rather than from the
    columns :meth:`SessionAnalyser.compute` already holds, because the curve
    also needs the power channel's repaired regions on the joined grid, which
    only the chart path assembles. That is a second parquet read per
    computation — milliseconds, against keeping one implementation of the
    join. The loader is passed in rather than imported because
    `app.ingest.analysis` imports this module.

    ``None`` — no recording, or a stream file missing or unreadable — makes
    the caller delete the stored curve rather than keep a stale one. The curve
    is stored, and committed by `PowerCurveService.store`, *before* the metric
    artefact: a failure on this path prevents the artefact, and a failure in
    `SessionMetricsService.record` afterwards leaves a curve with no matching
    artefact. Both are harmless, because the curve is a pure function of the
    stored stream and the next compute writes the same one.
    """
    if not session_row.recordings:
        return None
    try:
        streams = await load(session_row)
    except NotFoundError:
        return None
    return power_curve(
        streams.channels.get(StreamChannel.POWER),
        anomalies=[
            (anomaly.start_index, anomaly.end_index)
            for anomaly in streams.anomalies
            # `RESAMPLED_ONLY` spans the whole frame and certifies that
            # nothing was repaired; counting it would mark every best unclean.
            if anomaly.channel is StreamChannel.POWER
            and anomaly.kind is not AnomalyKind.RESAMPLED_ONLY
        ],
    )
