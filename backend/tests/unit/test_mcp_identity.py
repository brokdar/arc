"""The MCP key's label and scope, as tools will consume them (WP-8).

The auth context is set the way `AuthContextMiddleware` sets it on a real
request, and the tokens come from the real `StaticKeyVerifier`, so these
exercise the actual lookup rather than a stubbed one.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from app.domain.actor import Actor
from app.mcp.auth import Scope, parse_api_keys
from app.mcp.identity import current_actor, require_scope
from app.mcp.main import StaticKeyVerifier

COACH_KEY = "a1b2c3d4" * 4


@contextmanager
def authenticated_as(token: AccessToken) -> Iterator[None]:
    """Put ``token`` on the auth context, the way `AuthContextMiddleware` does.

    A context manager rather than a fixture: a contextvar can only be reset
    from the context that set it, and pytest-asyncio runs the test body in its
    own task — a fixture teardown would raise instead of cleaning up.
    """
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


async def _token_for(entry: str) -> AccessToken:
    """The access token `app.mcp.main` really issues for a configured key."""
    keys = parse_api_keys(entry)
    token = await StaticKeyVerifier(keys).verify_token(keys[0].key)
    assert token is not None
    return token


async def test_the_actor_is_the_key_label() -> None:
    with authenticated_as(await _token_for(f"coach:write:{COACH_KEY}")):
        assert current_actor() == Actor.agent("coach")
        assert str(current_actor()) == "agent:coach"


async def test_a_matching_scope_returns_the_actor() -> None:
    with authenticated_as(await _token_for(f"coach:write:{COACH_KEY}")):
        assert require_scope(Scope.WRITE) == Actor.agent("coach")


async def test_a_missing_scope_is_refused() -> None:
    with (
        authenticated_as(await _token_for(f"readonly:read:{COACH_KEY}")),
        pytest.raises(ToolError, match="'write' is required") as excinfo,
    ):
        require_scope(Scope.WRITE)

    assert "readonly" in str(excinfo.value)
    assert COACH_KEY not in str(excinfo.value)


async def test_write_scope_does_not_imply_read() -> None:
    # Scopes are named requirements, not a hierarchy — a write-only key cannot
    # call a read tool.
    with (
        authenticated_as(await _token_for(f"coach:write:{COACH_KEY}")),
        pytest.raises(ToolError, match="'read' is required"),
    ):
        require_scope(Scope.READ)


def test_no_token_is_refused_rather_than_attributed_to_nobody() -> None:
    with pytest.raises(ToolError, match="no access token"):
        current_actor()

    with pytest.raises(ToolError, match="no access token"):
        require_scope(Scope.READ)


def test_a_token_without_a_label_is_refused() -> None:
    blank = AccessToken(token="t", client_id="", scopes=["read"], claims={})

    with authenticated_as(blank), pytest.raises(ToolError, match="no key label"):
        current_actor()


def test_the_client_id_is_used_when_claims_are_absent() -> None:
    # A verifier that sets only `client_id` (FastMCP's own token shape carries
    # no `label` claim) must still identify the caller.
    with authenticated_as(AccessToken(token="t", client_id="coach", scopes=["read"])):
        assert current_actor() == Actor.agent("coach")
