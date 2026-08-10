"""Driving the MCP server the way a real client does, in-process.

Two test modules need to speak as an authenticated MCP key — the identity unit
tests and the end-to-end tool tests — so the plumbing lives here rather than in
either of them.

**Auth over the in-memory transport.** `fastmcp.Client(server)` connects
directly to a server object in this process: there is no HTTP request, so
`RequireAuthMiddleware` never runs and there is no request scope to read a
token from. FastMCP's `get_access_token()` falls back to the SDK's
`auth_context_var` in exactly that case, which is what
:func:`authenticated_as` sets — the same variable `AuthContextMiddleware` sets
on a real request. So `require_scope` in `app.mcp.identity` does its real work
here, against a token the real `StaticKeyVerifier` really issued.

The contextvar must be set **before the client connects**: the transport
starts the server with `task_group.start_soon`, and a task inherits a copy of
the context it was created in. :func:`connected_as` gets that order right, and
is the reason to use it rather than nesting the two by hand.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from fastmcp import Client, FastMCP
from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from app.mcp.auth import parse_api_keys
from app.mcp.main import StaticKeyVerifier, create_server


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


async def token_for(entry: str) -> AccessToken:
    """The access token `app.mcp.main` really issues for a configured key.

    Args:
        entry: One `label:scope:key` entry, as `MCP__API_KEYS` holds them.
    """
    keys = parse_api_keys(entry)
    token = await StaticKeyVerifier(keys).verify_token(keys[0].key)
    assert token is not None
    return token


def server_for(*entries: str) -> FastMCP:
    """Build the real server, with the real tool surface, for these keys."""
    return create_server(parse_api_keys(",".join(entries)))


@asynccontextmanager
async def connected_as(server: FastMCP, entry: str) -> AsyncIterator[Client]:
    """Yield a connected client whose calls carry ``entry``'s identity.

    Args:
        server: The server to talk to, from :func:`server_for`.
        entry: The `label:scope:key` entry to authenticate as. One client is
            one key, which is also how the real thing works — a connection
            presents one bearer token.
    """
    token = await token_for(entry)
    with authenticated_as(token):
        async with Client(server) as client:
            yield client
