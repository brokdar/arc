"""Who is calling an MCP tool, and may they.

`app/mcp/main.py` puts the matched key's label and scope set on the request's
`AccessToken` (`client_id`, `scopes`, `claims`). This module reads them back
and turns them into the two things a tool needs: a domain :class:`Actor` for
the audit trail, and a hard scope check.

Both are plain functions rather than FastMCP dependencies so they can be unit
tested by setting the auth context directly, and so a service never has to know
it was reached through MCP.
"""

from fastmcp.exceptions import ToolError
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.dependencies import get_access_token

from app.domain.actor import Actor
from app.mcp.auth import Scope


def _require_token() -> AccessToken:
    """Return the current request's access token.

    Raises:
        ToolError: When there is none. `RequireAuthMiddleware` rejects
            unauthenticated requests long before a tool runs, so this means the
            server was built without `StaticKeyVerifier` — refuse rather than
            attribute a write to nobody.
    """
    token = get_access_token()
    if token is None:
        raise ToolError("Unauthenticated MCP request: no access token in context")
    return token


def _actor_for(token: AccessToken) -> Actor:
    """Build the actor for a verified token."""
    label = token.claims.get("label") or token.client_id
    if not label:
        raise ToolError("Authenticated MCP request carries no key label")
    return Actor.agent(str(label))


def current_actor() -> Actor:
    """Return the actor for the key on the current MCP request.

    Returns:
        ``agent:<key-label>``, taken from the token's ``label`` claim (falling
        back to ``client_id``, which `StaticKeyVerifier` sets to the same
        value).

    Raises:
        ToolError: When the request is unauthenticated or the token carries no
            label.
    """
    return _actor_for(_require_token())


def require_scope(scope: Scope) -> Actor:
    """Assert the calling key carries ``scope``, and return its actor.

    Scopes are not nested: `write` does not imply `read`. A key may carry
    several scopes (`read+write`); each tool names the one it needs and the
    key's set must contain it (see `app.mcp.auth.Scope`).

    Args:
        scope: The scope the tool requires.

    Returns:
        The caller's actor, so a tool can open with
        ``actor = require_scope(Scope.WRITE)``.

    Raises:
        ToolError: When the key does not carry ``scope``. The message names the
            required scope and the key's label, never the key.
    """
    token = _require_token()
    actor = _actor_for(token)
    granted = sorted(set(token.scopes or ()))
    if scope.value not in granted:
        raise ToolError(
            f"Key {actor.label!r} is not permitted to do this: scope "
            f"{scope.value!r} is required, key has {granted or 'none'}"
        )
    return actor
