"""Request/response schemas for agent notes (WP-8.5).

There is **no create schema here**, and the omission is the point: notes are
the coaching agent's words, written through `app.services.agent_notes` from
the MCP tool surface. The athlete's half of this feature is reading them and
answering them with one tap.

The read carries `model_id` and `created_by` unabridged, because the UI
renders these in a visually distinct "coach" style and the attribution is what
makes that style honest — a purple card with no name on it is the application
speaking in a costume.
"""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict

from app.domain.agent_notes import DisputeRating, NoteKind


class AgentNoteRead(BaseModel):
    """One note, as the athlete's client reads it."""

    # `protected_namespaces=()`: pydantic reserves the `model_` prefix for its
    # own attributes and warns on `model_id`. The field is named for the thing
    # it holds — the identifier of the model that wrote the note — and renaming
    # a wire field to avoid a framework's namespace would be the framework
    # choosing the vocabulary.
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    #: The session this note is about; null when it is about a week.
    session_id: uuid.UUID | None
    #: The Monday of the plan week this note is about; null when it is about a
    #: session. Exactly one of these two is ever set.
    plan_week: dt.date | None
    kind: NoteKind
    text: str
    #: Which model wrote it. Always present — see the module docstring.
    model_id: str
    #: The actor that wrote it, `agent:<key-label>`.
    created_by: str
    created_at: dt.datetime
    #: Artefact ids the note rests on, in the order it gave them. May be empty.
    cites: list[uuid.UUID]
    #: The athlete's rating, or null if they have not given one.
    dispute: DisputeRating | None
    disputed_at: dt.datetime | None


class AgentNotesRead(BaseModel):
    """Every note about one subject, oldest first.

    Unpaged: a note is written about one session or one week, and the number
    of things worth saying about either is small.
    """

    items: list[AgentNoteRead]


class AgentNoteDispute(BaseModel):
    """The athlete's one-tap answer to a note.

    Genuinely nullable, unlike an optional query parameter: `null` is the
    third state of the toggle — "I take that back" — and the athlete must be
    able to say it, or they will stop giving ratings at all.
    """

    rating: DisputeRating | None = None
