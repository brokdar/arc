"""One search over the free text people have written about training.

Three places hold that text, and a coach asking "when did I last say
something about fluid" or "why did we drop the Thursday intervals" should not
need to know which:

* the athlete's **session notes** (`sessions.notes`) — read with
  `SessionService.list(query=...)`, because the answer is a session and the
  caller wants the session list's own summary rows;
* the coach's **agent notes** (`agent_notes.text`);
* **proposal** rationales and resolution notes — the coach's reasons and the
  athlete's answer to them.

This service merges the last two into one newest-first page, because their
hits are the same kind of thing — *something said, at a time, about a
subject* — and a caller paging two lists by hand could not interleave them.
Session notes are not folded in: a hit there is a session, `list_sessions`
returns it better, and a third source would make `total` mean a sum of unlike
things.

It displaces nothing: `search_history` stays the date-range weekly aggregator
it is, and no existing read grew a hidden text mode.

Every match is a case-insensitive **literal** substring — `%` and `_` in the
query are characters, not wildcards (`app.persistence.search`).
"""

from dataclasses import dataclass
from typing import Literal, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.persistence.agent_notes import AgentNoteRepository, AgentNoteRow
from app.persistence.proposals import PlanProposalRepository, PlanProposalRow


@dataclass(frozen=True, slots=True)
class NoteHit:
    """One matching row, whichever table it came from.

    The row itself rather than a re-spelled copy: the adapter already knows how
    to render an agent note and a proposal (`app.mcp.views`), and a third
    shape here would be one more thing to keep in step with those two.
    """

    #: `agent_note` or `proposal`.
    source: Literal["agent_note", "proposal"]
    row: AgentNoteRow | PlanProposalRow


class NoteSearchService:
    """Search notes and proposals. Read-only."""

    def __init__(
        self, notes: AgentNoteRepository, proposals: PlanProposalRepository
    ) -> None:
        self._notes = notes
        self._proposals = proposals

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(AgentNoteRepository(session), PlanProposalRepository(session))

    async def search(
        self, query: str, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[NoteHit], int]:
        """A page of hits, newest first, plus how many matched in all.

        Each source is asked for its newest ``offset + limit`` rows, which is
        enough to know the merged page without reading either table whole.

        Newest means newest *written*: hits merge on their uuid7 ids, which
        this process mints in strictly increasing order, not on ``created_at``
        — a backwards wall-clock step can stamp a later row with an earlier
        time (`.claude/rules/clock-monotonicity.md`), and keying on the stamp
        made a proposal filed after a note sort behind it.

        Raises:
            ValidationError: When ``query`` is blank. An empty substring
                matches everything, and "search" quietly returning the whole
                archive would hide the caller's mistake.
        """
        term = query.strip()
        if not term:
            raise ValidationError("query must not be blank")
        depth = offset + limit
        notes, note_total = await self._notes.search(term, limit=depth)
        proposals, proposal_total = await self._proposals.list(
            query=term, offset=0, limit=depth
        )
        hits = [NoteHit("agent_note", row) for row in notes] + [
            NoteHit("proposal", row) for row in proposals
        ]
        hits.sort(key=lambda hit: hit.row.id, reverse=True)
        return hits[offset:depth], note_total + proposal_total
