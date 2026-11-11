"""The offline outbox's conflict rule: who wins when two edits meet.

Everything the athlete logs in the gym is written on the phone first and
uploaded later, maybe hours later, maybe after the coach changed the same set
over MCP. The server applies those uploads (`app.services.sync`) under one
rule, stated here once and pure so every case of it is a unit test:

**Per field, the edit with the latest client edit time wins; the server's
receive order breaks a tie.** An edit is a `Stamp` — the time the edit was
made on the device that made it, then the position the server received it
in — and stamps compare as that pair. A field is overwritten only by a
strictly greater stamp, so an edit that arrives late but was *made* early
loses to a newer one already stored, and an edit made offline after the
original uploaded still applies, because it is newer.

Displaced: **server receive time only** ("last upload wins"). It is the rule
that needs no clock on the phone, and it is wrong for exactly the case the
outbox exists for: a phone that was offline for an hour uploads an hour-old
edit *after* the coach's newer one and silently reverts it. The tie-breaker is
the receive **sequence** (an autoincrement the server assigns each mutation),
not a receive timestamp, because the server's wall clock steps backwards
(`.claude/rules/clock-monotonicity.md`) and two receipts a step apart would
otherwise order backwards.

**The coach has no device clock.** An agent's edit is stamped with the server
time it arrived (`stamp_for`), so a coach edit at 10:05 beats a phone edit
made at 10:00 and uploaded at 10:10.

**Deletes are tombstones.** Whether the entity exists is one more field,
`EXISTS`, under the same rule: a delete writes ``False`` at its stamp, an
upsert writes ``True`` at its stamp. A delete newer than an edit therefore
keeps the row gone, and an edit newer than the delete revives it — with every
field the entity ever had, because the other fields keep merging while it is
deleted.

**`schema_version`** is the shape of a mutation batch. A server answers only
the versions in ``MIN_SCHEMA_VERSION..MAX_SCHEMA_VERSION``; anything else is
refused whole rather than half-understood (`app.api.routes.sync`).

Pinned by `tests/unit/test_domain_sync.py` (one test per conflict-table row)
and, against Postgres through the service, by
`tests/integration/test_sync_conflicts.py`.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: The batch shape this server writes and understands. Bump it when a
#: mutation's meaning changes; a phone on a newer build is then told to update
#: rather than having its edits read as something they were not.
MAX_SCHEMA_VERSION = 1
#: The oldest batch shape still understood.
MIN_SCHEMA_VERSION = 1

#: Most mutations one upload may carry. A day of gym logging is a few hundred
#: at most; the phone sends its queue in slices of this size.
MAX_BATCH_MUTATIONS = 500

#: The field holding whether the entity exists (see the module docstring).
EXISTS = "_exists"

#: An active session with no logged set for this long is closed at its last
#: set (see :func:`auto_close_end`).
AUTO_CLOSE_AFTER = dt.timedelta(hours=3)

#: How long after its end a finished session may be reopened.
REOPEN_WINDOW = dt.timedelta(hours=24)


class MutationKind(StrEnum):
    """What one outbox item does."""

    SESSION_START = "session_start"
    SESSION_FINISH = "session_finish"
    SET_UPSERT = "set_upsert"
    SET_DELETE = "set_delete"
    SESSION_RATING = "session_rating"
    DONT_LEARN = "dont_learn"
    NOTE_UPSERT = "note_upsert"
    #: "Continue here?" accepted: this device now holds the session.
    SESSION_CLAIM = "session_claim"
    #: A finished session opened again to add or fix sets (within 24 h).
    SESSION_REOPEN = "session_reopen"


class MutationStatus(StrEnum):
    """What the server did with one mutation."""

    #: Received for the first time and merged (fields may still have lost).
    APPLIED = "applied"
    #: Its id was received before; nothing was done.
    ALREADY_APPLIED = "already_applied"


@dataclass(frozen=True, order=True, slots=True)
class Stamp:
    """When an edit was made, then when the server received it.

    Ordered as the pair, so ``max`` of two stamps is the winner.
    ``edit_time`` is aware: instants compare across offsets.
    """

    edit_time: dt.datetime
    received: int


@dataclass(frozen=True, slots=True)
class Merge:
    """The entity after one patch, and which fields the patch won."""

    values: dict[str, Any]
    clocks: dict[str, Stamp]
    changed: tuple[str, ...]


def stamp_for(
    *,
    agent: bool,
    client_edit_time: dt.datetime,
    server_now: dt.datetime,
    received: int,
) -> Stamp:
    """The stamp one mutation carries: the coach's edit time is server time."""
    return Stamp(edit_time=server_now if agent else client_edit_time, received=received)


def merge(
    values: Mapping[str, Any],
    clocks: Mapping[str, Stamp],
    patch: Mapping[str, Any],
    stamp: Stamp,
) -> Merge:
    """Apply ``patch`` at ``stamp``: each field takes it only if ``stamp`` is newer.

    Returns new mappings; the inputs are left untouched. ``changed`` lists the
    fields whose stored stamp moved, in patch order, whether or not the value
    differs — a field re-asserted by a newer edit is still that edit's.
    """
    merged = dict(values)
    stamped = dict(clocks)
    changed: list[str] = []
    for field, value in patch.items():
        current = stamped.get(field)
        if current is not None and stamp <= current:
            continue
        merged[field] = value
        stamped[field] = stamp
        changed.append(field)
    return Merge(values=merged, clocks=stamped, changed=tuple(changed))


def is_alive(values: Mapping[str, Any]) -> bool:
    """Whether the entity exists: its latest existence write was not a delete."""
    return bool(values.get(EXISTS, True))


def encode_clocks(clocks: Mapping[str, Stamp]) -> dict[str, list[Any]]:
    """Clocks as JSON: ``{field: [iso edit time, receive sequence]}``."""
    return {
        field: [stamp.edit_time.isoformat(), stamp.received]
        for field, stamp in clocks.items()
    }


def decode_clocks(raw: Mapping[str, Any]) -> dict[str, Stamp]:
    """The inverse of :func:`encode_clocks`."""
    return {
        field: Stamp(edit_time=dt.datetime.fromisoformat(edit), received=int(seq))
        for field, (edit, seq) in raw.items()
    }


def auto_close_end(
    *,
    start: dt.datetime,
    set_times: Sequence[dt.datetime],
    last_edit: dt.datetime | None,
    now: dt.datetime,
) -> dt.datetime | None:
    """When an abandoned active session ends, or None while it is still live.

    A session is abandoned once `AUTO_CLOSE_AFTER` has passed since its last
    sign of life — its latest logged set, or a later edit of the session
    itself (a reopen, a claim by another device) — and it then ends **at its
    last logged set**, or at its start when nothing was logged: the time the
    athlete stopped lifting, not the time somebody noticed. Displaced: ending
    it "now", which would credit three idle hours to the session's duration
    and every load number derived from it.

    The boundary is inclusive: at exactly three hours the session closes.
    """
    last_set = max(set_times, default=None)
    alive = max(moment for moment in (start, last_set, last_edit) if moment is not None)
    if now - alive < AUTO_CLOSE_AFTER:
        return None
    return last_set if last_set is not None else start


def may_reopen(*, ended: dt.datetime, at: dt.datetime) -> bool:
    """Whether a session that ended at ``ended`` may be reopened at ``at``.

    Judged on the reopening device's edit time, not on upload time, so a
    reopen made offline in time still counts when it arrives late.
    """
    return at - ended <= REOPEN_WINDOW


def schema_version_supported(version: int) -> bool:
    """Whether this server understands a batch of this shape."""
    return MIN_SCHEMA_VERSION <= version <= MAX_SCHEMA_VERSION
