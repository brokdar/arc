"""Feeding the scoring engine the samples it may not read for itself.

`app.services.scoring` computes the adherence, discipline and pacing axes over
the cleaned 1 Hz columns, and those columns live in parquet — which only this
layer may read (`app.services` may not import `app.ingest`; the layer contract
in `backend/pyproject.toml` enforces it). The metric artefact solves the same
problem by taking a *prepared* analysis, but scoring cannot: it is triggered
from inside `app.services.matching` when a link settles, and from the rescore
seam when an intent is edited post-hoc, and neither of those callers can reach
this layer either.

So the columns arrive through a seam instead. `app.services.scoring` declares a
loader callable with a null default; :func:`install_stream_loader` fills it with
the real one, and `app.main.create_app` calls that at wiring time so every path
into the application — HTTP, the scheduler, the inbox sweep — has it installed.
Nothing here decides anything: it reads the same joined grid the chart endpoint
serves (`load_streams`, merged-session aware per D143) and renames the channels
into the prescription's vocabulary.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.domain.streams import StreamChannel
from app.domain.workout import Channel
from app.ingest.analysis import load_streams
from app.persistence.activity import RecordingRepository, SessionRepository
from app.services.scoring import SessionColumns, set_stream_loader

logger = get_logger(__name__)

#: The prescribable channels, in the two vocabularies that name them. A stream
#: channel with no prescribable counterpart (speed, elevation, temperature) is
#: not dropped by accident: no criterion can band around it, so handing it to
#: the scorer would be handing it something it has no way to judge.
SCORED_CHANNELS: dict[StreamChannel, Channel] = {
    StreamChannel.POWER: Channel.POWER,
    StreamChannel.HR: Channel.HR,
    StreamChannel.CADENCE: Channel.CADENCE,
}


async def scoring_columns(
    session: AsyncSession, session_id: uuid.UUID
) -> SessionColumns | None:
    """The cleaned columns behind one session, keyed by prescribable channel.

    ``None`` when there is nothing to read — a session typed in by hand, or one
    whose stream files have gone missing. Absence is not an error here for the
    same reason it is not one in `app.ingest.analysis`: the score still
    computes, and every axis that needed a channel says which one it did not
    get.
    """
    row = await SessionRepository(session).get(session_id)
    if row is None or not row.recordings:
        return None
    try:
        streams = await load_streams(row, RecordingRepository(session))
    except NotFoundError:
        logger.warning("scoring_stream_missing", session_id=str(session_id))
        return None
    return {
        channel: values
        for stream_channel, channel in SCORED_CHANNELS.items()
        if (values := streams.channels.get(stream_channel)) is not None
    }


def install_stream_loader() -> None:
    """Point the scoring service's stream seam at the real parquet reader.

    Idempotent, and called from application wiring rather than from a lifespan
    hook: the unit suite builds the app with `create_app` and never runs a
    lifespan, and a seam that were only installed in production would make
    every stream-derived axis untested.
    """
    set_stream_loader(scoring_columns)
