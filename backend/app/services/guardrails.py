"""The guardrails on the agent surface, in the service layer where they bind.

Build plan WP-8.3 is explicit that these are **not** enforced in the MCP shell.
An adapter can be bypassed — by a second adapter, by a test, by whatever WP-9
adds — and a guardrail that only exists in one of them is a guardrail on that
adapter, not on the agent. So they live here, in front of the services every
adapter shares, and this module is what phase 2's MCP tools call before they
write anything.

Three of the four guardrails are here:

* **the rate cap** (:func:`check_write_cap`) — a circuit breaker on how much an
  agent can change in an hour, counted over the audit log;
* **the red flag** (:func:`current_profile`) — the illness/injury state every
  agent read carries and every intensifying write is refused against;
* **who is asking** (:func:`is_agent`) — both of the above apply to agent
  actors and to nobody else. The athlete is never rate-limited on their own
  data, and an athlete who is ill may still plan whatever they like: the flag
  restrains the *coach*, not the person.

The fourth, optimistic concurrency, is not here because it has no shared
implementation to share: the token is the intent version of a specific planned
session, so the check lives where that version is read
(`app.services.proposals`).

Append-only anchors are the guardrail this module deliberately says nothing
about: `app.services.anchors` offers no update and no delete at all, so there
is no path to close. A new path that could is a change to that service, and
its absence of writes is the check.
"""

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import RateLimitedError
from app.domain.actor import Actor, ActorKind
from app.domain.athlete import AthleteProfile
from app.persistence.athlete import AthleteRepository
from app.persistence.audit import AuditRepository

#: Prefix of the stored actor string for every coaching-agent key
#: (`app.domain.actor.Actor.agent`). The rate cap counts over it, so all keys
#: share one budget: two keys are two labels on one agent, not two agents.
AGENT_ACTOR_PREFIX = f"{ActorKind.AGENT.value}:"

#: The window the cap is measured over.
WRITE_CAP_WINDOW = dt.timedelta(hours=1)


def is_agent(actor: Actor) -> bool:
    """Whether this actor is the coaching agent."""
    return actor.kind is ActorKind.AGENT


async def current_profile(session: AsyncSession) -> AthleteProfile:
    """Read the athlete profile without bootstrapping it.

    Deliberately **not** `AthleteService.get`, which creates the singleton row
    on first access and commits: this is called from read paths and from dry
    runs, and a dry run that writes a row is not a dry run. An empty profile is
    the honest answer before anyone has filled one in — and its red flag is
    down, which is the correct default for a rule that restrains the agent.
    """
    row = await AthleteRepository(session).get()
    return AthleteProfile() if row is None else row.to_domain()


async def check_write_cap(
    session: AsyncSession, actor: Actor, *, now: dt.datetime | None = None
) -> None:
    """Refuse an agent write once the trailing-hour cap is spent.

    Counted over `audit_log` rows whose actor starts ``agent:`` — the record
    of every agent write there is, so the counter cannot disagree with the
    trail (see `AuditRepository.count_since`). A **dry run must not call
    this**: it writes nothing, so it costs nothing, and charging for it would
    make "check before you act" the expensive option.

    Called *before* the work rather than after, so a refusal leaves no
    half-finished write to undo; the cap is therefore on writes admitted, and
    a use-case that appends several audit rows spends several.

    Args:
        session: The session to count over.
        actor: Who is asking. Anything but an agent returns immediately —
            athlete and system writes are never capped.
        now: The moment the window ends, for tests. Defaults to now.

    Raises:
        RateLimitedError: When the cap is already reached. The message says
            how many writes the window holds and when the window started, so
            the answer to "when may I try again" is derivable.
    """
    if not is_agent(actor):
        return
    cap = get_settings().mcp.write_cap_per_hour
    moment = now or dt.datetime.now(dt.UTC)
    since = moment - WRITE_CAP_WINDOW
    spent = await AuditRepository(session).count_since(
        actor_prefix=AGENT_ACTOR_PREFIX, since=since
    )
    if spent >= cap:
        raise RateLimitedError(
            f"The coaching agent has made {spent} writes since "
            f"{since.isoformat()}, which is at or over the cap of {cap} per "
            "hour (MCP__WRITE_CAP_PER_HOUR). Try again once the oldest of "
            "them falls out of the window."
        )
