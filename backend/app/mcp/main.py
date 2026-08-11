"""MCP server entrypoint.

Runs as its own process from the same image as the API
(`python -m app.mcp.main`), serving streamable HTTP on :8001 behind Caddy's
`/mcp*` route. Every MCP request must present one of the bearer keys from
`MCP__API_KEYS`; the unauthenticated `/health` route exists for the container
healthcheck.

The tool surface itself lives in `app.mcp.tools`, registered onto the server
by `create_server`: the entrypoint owns transport, auth and the healthcheck,
and the tools own the coaching contract.
"""

from fastmcp import FastMCP
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.mcp.auth import McpKey, parse_api_keys, verify_key
from app.mcp.tools import register_tools

logger = get_logger(__name__)

SERVER_NAME = "arc-mcp"
HOST = "0.0.0.0"  # noqa: S104 — containerised; published on loopback by compose
PORT = 8001


class StaticKeyVerifier(TokenVerifier):
    """Verifies bearer tokens against the keys configured in `MCP__API_KEYS`.

    FastMCP ships `StaticTokenVerifier`, but it looks tokens up in a dict —
    a non-constant-time comparison on secret material. This subclass keeps
    FastMCP's plumbing (the `RequireAuthMiddleware` wrapping the MCP endpoint,
    the 401 with `WWW-Authenticate`) and swaps in `app.mcp.auth.verify_key`.
    """

    def __init__(self, keys: list[McpKey]) -> None:
        super().__init__()
        self._keys = keys

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the access token for a valid key, or None to reject."""
        matched = verify_key(self._keys, token)
        if matched is None:
            logger.warning("mcp_auth_rejected")
            return None

        # client_id/scopes land on the request's authenticated identity, so
        # tools can read the caller's label and scope from the MCP context.
        return AccessToken(
            token=token,
            client_id=matched.label,
            scopes=[matched.scope.value],
            claims={"label": matched.label, "scope": matched.scope.value},
        )


def create_server(keys: list[McpKey]) -> FastMCP:
    """Build the MCP server with bearer auth, the tool surface and /health."""
    mcp: FastMCP = FastMCP(name=SERVER_NAME, auth=StaticKeyVerifier(keys))
    register_tools(mcp)

    # Custom routes sit outside the MCP endpoint that RequireAuthMiddleware
    # wraps, so this stays reachable without a bearer token — the container
    # healthcheck has no key.
    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return mcp


def load_keys() -> list[McpKey]:
    """Parse the configured keys, or exit non-zero if there are none.

    Raises:
        SystemExit: When `MCP__API_KEYS` is unset, empty or malformed. An MCP
            server with no keys would either reject everything or, worse, be
            mistaken for a working one — refuse to start instead.
    """
    raw = get_settings().mcp.api_keys.get_secret_value()
    try:
        keys = parse_api_keys(raw)
    except ValueError as exc:
        logger.error("mcp_api_keys_invalid", error=str(exc))
        raise SystemExit(1) from exc

    if not keys:
        logger.error(
            "mcp_api_keys_missing",
            hint=(
                "set MCP__API_KEYS='label:scope:key,...' (scope: read or write) "
                "before starting the MCP server"
            ),
        )
        raise SystemExit(1)

    return keys


def main() -> None:
    """Run the MCP server over streamable HTTP."""
    configure_logging()
    keys = load_keys()
    logger.info(
        "mcp_server_starting",
        host=HOST,
        port=PORT,
        keys=[{"label": key.label, "scope": key.scope.value} for key in keys],
    )
    # show_banner=False also skips FastMCP's PyPI update check, which the
    # banner triggers — a service should not phone home on every boot.
    create_server(keys).run(transport="http", host=HOST, port=PORT, show_banner=False)


if __name__ == "__main__":
    main()
