"""Rebuild stored streams from the original files, and recompute the metrics.

The operator's path for "the parser learned something new". Recomputing metrics
reads the stored parquet, so a stream written before a channel existed can
never gain that channel by recomputation — D197's odometer is the case this was
written for. This re-parses each original, rewrites its parquet and its
recording row (`app.ingest.rebuild`), and then appends a metric version for
every session whose stream changed, with a reason that says why.

Run it from ``backend/``::

    just rebuild-streams                     # everything, then recompute
    just rebuild-streams --no-recompute      # streams only
    just rebuild-streams --recording <uuid>  # one recording

Originals are opened read-only; nothing under ``data/originals/`` is moved or
deleted. Safe to re-run: a rebuild is idempotent for an unchanged parser, and
each session gains one metric version per run that reaches it.
"""

import argparse
import asyncio
import sys
import uuid

from app.core.logging import configure_logging
from app.domain.actor import Actor
from app.ingest.analysis import SessionAnalyser
from app.ingest.rebuild import RebuildOutcome, StreamRebuilder, session_ids
from app.persistence.db import session_scope

#: What the recomputed metric versions record as their reason. Read on the
#: session's version chain long after this run, so it names the cause rather
#: than the command.
RECOMPUTE_REASON = "stream rebuilt from the original file"


async def run(*, recording_id: uuid.UUID | None, recompute: bool) -> int:
    """Rebuild, recompute, and return the process exit code."""
    actor = Actor.system()
    async with session_scope() as session:
        rebuilder = StreamRebuilder.from_session(session)
        outcomes: list[RebuildOutcome] = (
            [await rebuilder.rebuild(recording_id, actor=actor)]
            if recording_id is not None
            else await rebuilder.rebuild_all(actor=actor)
        )

    for outcome in outcomes:
        print(  # noqa: T201 — a maintenance script's output is its interface
            f"{outcome.status.value:<17} {outcome.recording_id}  {outcome.detail}"
        )

    sessions = session_ids(outcomes)
    if recompute and sessions:
        # A session at a time, each in its own transaction: the metric chain is
        # append-only, so a run that fails on session nine must leave the eight
        # before it with their new versions rather than rolling them back.
        for session_id in sessions:
            async with session_scope() as session:
                row = await SessionAnalyser.from_session(session).compute(
                    session_id, actor=Actor.system(), reason=RECOMPUTE_REASON
                )
            print(f"recomputed       {session_id}  version {row.version}")  # noqa: T201

    rebuilt = sum(1 for outcome in outcomes if outcome.rebuilt)
    failed = len(outcomes) - rebuilt
    print(  # noqa: T201
        f"\n{rebuilt} stream(s) rebuilt, {len(sessions) if recompute else 0} "
        f"session(s) recomputed, {failed} skipped"
    )
    return 1 if failed else 0


def main() -> int:
    """Parse the arguments and run."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--recording",
        type=uuid.UUID,
        default=None,
        help="rebuild one recording instead of every one",
    )
    parser.add_argument(
        "--no-recompute",
        action="store_false",
        dest="recompute",
        help="rewrite the streams but leave the metric artefacts alone",
    )
    arguments = parser.parse_args()
    configure_logging()
    return asyncio.run(
        run(recording_id=arguments.recording, recompute=arguments.recompute)
    )


if __name__ == "__main__":
    sys.exit(main())
