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

**Deploy first, rebuild second, and never roll back after a rebuild.** A
rebuilt parquet carries whatever channels the *new* parser produces, and an
older image has no `app.domain.streams.StreamChannel` member for a channel it
predates: `app.ingest.parquet.read_streams` raises on the unknown column and
`app.ingest.analysis` degrades the whole file to "the stream is missing", so
every rebuilt session loses its chart and every stream-derived metric at once.
Nothing is destroyed — the originals are untouched and re-deploying the newer
image restores every one of them — but the store is unreadable in the
meantime. So: deploy the image, then run this; and if the deploy has to be
rolled back, roll the image back *before* rebuilding, never after.
"""

import argparse
import asyncio
import sys
import uuid

from app.core.exceptions import NotFoundError
from app.core.logging import configure_logging
from app.domain.actor import Actor
from app.ingest.analysis import SessionAnalyser
from app.ingest.rebuild import RebuildOutcome, StreamRebuilder, session_ids
from app.persistence.db import session_scope

#: What the recomputed metric versions record as their reason. Read on the
#: session's version chain long after this run, so it names the cause rather
#: than the command.
RECOMPUTE_REASON = "stream rebuilt from the original file"

#: The ordering hazard, on ``--help`` as well as in this module's docstring.
#: An operator reading the flags is the one about to run it, and the sentence
#: is no use to them further up a file they did not open.
ORDERING_NOTE = (
    "Deploy the new image first, then rebuild — and never roll the image back "
    "after a rebuild: an older parser cannot read a channel it predates, and "
    "every rebuilt stream would read as missing until the newer image is back."
)

#: Exit code for a caller's mistake — an id that names no recording — as
#: opposed to 1, which means the run worked and some files could not be done.
USAGE_EXIT = 2


async def run(*, recording_id: uuid.UUID | None, recompute: bool) -> int:
    """Rebuild, recompute, and return the process exit code."""
    actor = Actor.system()
    async with session_scope() as session:
        rebuilder = StreamRebuilder.from_session(session)
        try:
            outcomes: list[RebuildOutcome] = (
                [await rebuilder.rebuild(recording_id, actor=actor)]
                if recording_id is not None
                else await rebuilder.rebuild_all(actor=actor)
            )
        except NotFoundError as error:
            # A mistyped id is the operator's typo, not a defect: one line and
            # a non-zero exit, never a traceback they have to read past.
            print(f"error: {error}", file=sys.stderr)  # noqa: T201
            return USAGE_EXIT

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


def build_parser() -> argparse.ArgumentParser:
    """The command line, with the ordering hazard on ``--help``."""
    parser = argparse.ArgumentParser(
        description=f"{__doc__.splitlines()[0]} {ORDERING_NOTE}"
    )
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
    return parser


def main() -> int:
    """Parse the arguments and run."""
    arguments = build_parser().parse_args()
    configure_logging()
    return asyncio.run(
        run(recording_id=arguments.recording, recompute=arguments.recompute)
    )


if __name__ == "__main__":
    sys.exit(main())
