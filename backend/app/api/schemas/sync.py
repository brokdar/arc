"""Request/response schemas for `POST /sync`, the phone's outbox upload.

A batch is a ``schema_version`` and an ordered list of mutations, one shape
per ``kind`` (a discriminated union, so the generated client narrows on it).
Every mutation carries the phone's ``mutation_id`` — its idempotency key — and
the ``client_edit_time`` the conflict rule orders by (`app.domain.sync`).

``schema_version`` has no upper bound here on purpose: a version above the
server's is not malformed, it is a phone on a newer build, and it gets the
defined 409 that tells it to update (`app.api.routes.sync`) rather than a
schema 422 it could not tell from a broken request.
"""

import uuid
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from app.api.schemas.activity import LoggedSetFields, TimezoneName
from app.api.validation import PostgresText
from app.domain.safety import RedFlagSymptom
from app.domain.sync import (
    MAX_BATCH_MUTATIONS,
    MIN_SCHEMA_VERSION,
    MutationStatus,
)
from app.persistence.activity import MAX_DONT_LEARN_REASON_LENGTH
from app.persistence.exercises import MAX_NOTE_LENGTH, MAX_SLUG_LENGTH
from app.services.activity import (
    EARLIEST_SESSION,
    LATEST_SESSION,
    MAX_SESSION_RATING,
    MAX_SETS,
    MIN_SESSION_RATING,
)

#: An instant a session or an edit can have happened at.
Instant = Annotated[AwareDatetime, Field(ge=EARLIEST_SESSION, le=LATEST_SESSION)]


class _Mutation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The phone-minted uuid7 of this outbox item. Seen twice, applied once.
    mutation_id: uuid.UUID
    #: When the edit was made on the phone. The conflict rule's first key.
    client_edit_time: Instant


class _SessionMutation(_Mutation):
    #: The phone-minted id of the session this mutation is about.
    session_client_id: uuid.UUID


class SessionStart(_SessionMutation):
    """Start a session (or, sent again, correct when it started)."""

    kind: Literal["session_start"]
    start_time: Instant
    #: Omitted, the athlete's configured zone.
    timezone: TimezoneName | SkipJsonSchema[None] = None


class SessionFinish(_SessionMutation):
    """Finish a session: it is then scored, matched and counted."""

    kind: Literal["session_finish"]
    end_time: Instant


class SyncSet(LoggedSetFields):
    """A logged set as the outbox sends it: only the fields it set.

    The fields are `LoggedSetFields`, the same a typed-in set has, so a set is
    the same set by either door; an omitted field is left as it is stored.
    The set's identity is the mutation's ``set_client_id``.
    """

    #: 0-based position in the session. Omitted on a new set, it goes last.
    set_index: Annotated[int, Field(ge=0, lt=MAX_SETS)] | SkipJsonSchema[None] = None


class SetUpsert(_SessionMutation):
    """Create or edit one logged set (an edit may also revive a deleted one)."""

    kind: Literal["set_upsert"]
    set_client_id: uuid.UUID
    set: SyncSet


class SetDelete(_SessionMutation):
    """Delete one logged set. It stays deleted unless a newer edit revives it."""

    kind: Literal["set_delete"]
    set_client_id: uuid.UUID


class SessionRating(_SessionMutation):
    """The Finish screen's whole-session rating; null clears it."""

    kind: Literal["session_rating"]
    session_rating: int | None = Field(ge=MIN_SESSION_RATING, le=MAX_SESSION_RATING)


class DontLearn(_SessionMutation):
    """Don't learn from today, toggled; the latest toggle wins."""

    kind: Literal["dont_learn"]
    dont_learn: bool
    #: The private reason, only with ``dont_learn: true``.
    reason: (
        Annotated[PostgresText, Field(max_length=MAX_DONT_LEARN_REASON_LENGTH)]
        | SkipJsonSchema[None]
    ) = None


class NoteUpsert(_Mutation):
    """The athlete's note on one exercise; empty text removes it."""

    kind: Literal["note_upsert"]
    exercise_id: str = Field(min_length=1, max_length=MAX_SLUG_LENGTH)
    text: PostgresText = Field(max_length=MAX_NOTE_LENGTH)


class RedFlag(_SessionMutation):
    """*Stop — something's wrong*: one symptom, which raises the red flag.

    The phone shows the symptom group's text at once, offline
    (`app.domain.safety.RED_FLAG_TEXT`), and sends ``session_finish`` after
    this; the server raises the athlete's flag and queues its follow-up card.
    """

    kind: Literal["red_flag"]
    symptom: RedFlagSymptom


type Mutation = Annotated[
    SessionStart
    | SessionFinish
    | SetUpsert
    | SetDelete
    | SessionRating
    | DontLearn
    | NoteUpsert
    | RedFlag,
    Field(discriminator="kind"),
]


class SyncBatch(BaseModel):
    """One upload from the outbox, applied in order, all or nothing."""

    model_config = ConfigDict(extra="forbid")

    #: The batch shape the phone wrote. Above the server's is a 409.
    schema_version: int = Field(ge=MIN_SCHEMA_VERSION)
    mutations: list[Mutation] = Field(max_length=MAX_BATCH_MUTATIONS)


class MutationResultRead(BaseModel):
    """What the server did with one mutation."""

    mutation_id: uuid.UUID
    status: MutationStatus


class SyncResult(BaseModel):
    """One result per mutation, in the order sent."""

    #: The batch shape this server writes.
    schema_version: int
    results: list[MutationResultRead]


class SchemaVersionUnsupported(BaseModel):
    """The 409 body: the phone keeps its queue and asks to be updated."""

    detail: str
    code: Literal["schema_version_unsupported"]
    server_schema_version: int


class SyncRejected(BaseModel):
    """The 422 body: a rejected mutation, or a malformed batch.

    A rejected mutation names itself; a malformed body is the framework's
    per-field list, whose ``loc`` carries the mutation's index.
    """

    detail: str | list[object]
    code: Literal["mutation_rejected"] | SkipJsonSchema[None] = None
    mutation_id: uuid.UUID | SkipJsonSchema[None] = None
