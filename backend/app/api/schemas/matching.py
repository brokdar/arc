"""Request/response schemas for match links (WP-6).

Three shapes, and the split is about who is reading.

`MatchSummary` is what a **session** or a **planned session** carries about its
own link: enough for a badge and a link to the other side, and nothing that
costs a second query. It hangs off both read shapes, so neither resource is
silent about a state the athlete can see on the calendar.

`MatchRead` is the link **as its own resource** — the score, its whole
breakdown, and enough of both sides to render a proposal inbox row without
fetching either. A proposal is answered from a list, and a list that made the
client fetch two resources per row to say "your 2 h ride on Tuesday, against
the 90 min endurance session planned for Monday" is a list nobody can answer
from.

`SessionMatchState` is what the operations that *remove* a link answer with:
the session's status and whatever link (if any) now stands. Rejecting a
proposal is not a session edit, and answering with the whole session — its
recordings, its metric artefact — would be a payload the client did not ask
for on every button press.
"""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict

from app.api.pagination import Page
from app.domain.activity import SessionDiscipline, SessionMatchStatus
from app.domain.athlete import Discipline
from app.domain.matching import MatchComponent, MatchLinkStatus
from app.domain.purpose import Purpose
from app.domain.sessions import SessionStatus


class MatchComponentRead(BaseModel):
    """One component of a similarity score, and what it compared.

    The two raw numbers travel with the ratio because the ratio alone is not
    explicable: 0.62 on duration means nothing until it reads "90 min
    prescribed against 56 min ridden".
    """

    component: MatchComponent
    #: Agreement, in ``[0, 1]``.
    score: float
    #: The weight actually applied — the nominal weight renormalised over the
    #: components that could be assessed, so the applied weights sum to 1.
    weight: float
    #: The weight the build plan gives this component, before renormalisation.
    nominal_weight: float
    #: The prescribed quantity: seconds, watts or bpm, or a count of units.
    planned: float
    #: Its recorded counterpart, in the same unit.
    actual: float
    #: Which channel or unit the pair is in (``power``, ``hr``, ``intervals``,
    #: ``sets``); null for duration, which has only seconds.
    basis: str | None


class MatchUnassessedRead(BaseModel):
    """One component that had nothing to compare, and why.

    Not an error and not a zero: the weights renormalise over what remains, so
    this is the record of *what the score was made of*. A similarity of 0.9
    over two components is a different claim from the same number over three.
    """

    component: MatchComponent
    nominal_weight: float
    reason: str


class MatchBreakdownRead(BaseModel):
    """How a similarity score was arrived at."""

    #: The score itself. **Null is not zero**: it means no component could be
    #: assessed at all, which is why the link is a proposal rather than a
    #: refusal.
    score: float | None
    #: The nominal weights, by component — the constants the build plan fixes.
    weights: dict[MatchComponent, float]
    components: list[MatchComponentRead]
    not_assessed: list[MatchUnassessedRead]


class MatchSummary(BaseModel):
    """One link, as the two resources it joins carry it."""

    id: uuid.UUID
    session_id: uuid.UUID
    planned_session_id: uuid.UUID
    status: MatchLinkStatus
    #: Null when nothing could be compared; see `MatchBreakdownRead.score`.
    similarity: float | None
    #: When the athlete made this link their own. Null while it is still a
    #: machine verdict the athlete has not ruled on.
    confirmed_at: dt.datetime | None


class MatchSessionContext(BaseModel):
    """Enough of the completed session to render a proposal row."""

    id: uuid.UUID
    local_date: dt.date
    discipline: SessionDiscipline
    status: SessionMatchStatus
    #: Recording time for a device session, wall clock for a typed-in one —
    #: the same number the session list shows.
    duration_s: float


class MatchPlannedContext(BaseModel):
    """Enough of the planned session to render a proposal row."""

    id: uuid.UUID
    date: dt.date
    discipline: Discipline
    purpose: Purpose
    status: SessionStatus
    #: The athlete's own one-line intent, when they wrote one.
    intent_text: str | None


class MatchRead(MatchSummary):
    """One link as its own resource, with both sides and the whole breakdown."""

    breakdown: MatchBreakdownRead
    #: `app.domain.actor.Actor` in string form — ``system`` for a link matching
    #: made on ingest, ``athlete`` for one made by hand, ``agent:<label>`` for
    #: one an agent proposed.
    created_by: str
    #: What this link displaced, and therefore what unlinking restores.
    previous_session_status: SessionMatchStatus
    previous_planned_status: SessionStatus
    session: MatchSessionContext
    planned_session: MatchPlannedContext
    created_at: dt.datetime
    updated_at: dt.datetime


MatchesPage = Page[MatchRead]


class SessionMatchState(BaseModel):
    """Where a completed session stands relative to the plan, after an edit."""

    session_id: uuid.UUID
    status: SessionMatchStatus
    #: The link that now stands, or null when none does.
    match: MatchSummary | None


class MatchOutcomeRead(SessionMatchState):
    """What one run of matching decided (`POST /sessions/{id}/rematch`)."""

    #: How many planned sessions were in the window and still unlinked.
    candidates: int
    #: True when an existing confirmed or displaced link was left alone. The
    #: run decided nothing, deliberately: those are the athlete's own words
    #: and no re-run overwrites them.
    sticky: bool


class MatchCreate(BaseModel):
    """Payload for linking a session to a planned session by hand."""

    model_config = ConfigDict(extra="forbid")

    session_id: uuid.UUID
    planned_session_id: uuid.UUID
    #: Set when the athlete trained and it was **not** this: the planned
    #: session becomes ``displaced`` rather than completed, and the activity is
    #: scored standalone with no adherence axes (build plan WP-6.4).
    displaced: bool = False


class MatchRetarget(BaseModel):
    """Payload for pointing an existing link at a different planned session."""

    model_config = ConfigDict(extra="forbid")

    planned_session_id: uuid.UUID


class SessionMerge(BaseModel):
    """Payload for folding a second recording of one ride into this session."""

    model_config = ConfigDict(extra="forbid")

    #: The session whose recordings move onto this one. That session row is
    #: removed; its recordings and their stream files are kept.
    absorbed_session_id: uuid.UUID
