"""New routers are protected by default — proven, not documented.

`app.main` mounts protected routers on an `APIRouter` carrying
`Depends(require_session)`. Nothing stops a future router from being included
on `app` directly, or on the open `/auth` router, and the mistake is invisible:
the endpoint works, it is simply reachable without a session. This walks the
real route table and demands a 401 from everything that is not deliberately
open.
"""

import re
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import AsyncClient

#: The only endpoints allowed to answer without a session.
#:
#: `/health` is polled by every container healthcheck and by Caddy; the auth
#: routes cannot require the session they exist to create, drop, or report on.
#: Adding a path here is a security decision — that is the point of the list.
#: That these four really are reachable anonymously is covered in test_auth.py.
OPEN_PATHS = frozenset(
    {
        "/health",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/session",
    }
)

#: Methods every route answers implicitly; not endpoints of their own.
_IMPLICIT_METHODS = frozenset({"HEAD", "OPTIONS"})

_PATH_PARAMETER = re.compile(r"\{[^{}]+\}")


def _endpoints(app: FastAPI) -> set[tuple[str, str]]:
    """Every ``(method, path)`` the app serves, from the route table itself.

    Not from `app.openapi()`: a router included with `include_in_schema=False`
    would be absent there and is exactly the kind of quiet mount this test
    exists to catch. Recursion is needed because FastAPI wraps each
    `include_router` in a lazy container rather than flattening the routes;
    `test_the_walk_sees_every_documented_endpoint` fails loudly if that
    structure changes under us, instead of this returning an empty set.
    """
    found: set[tuple[str, str]] = set()

    def walk(router: Any, prefix: str) -> None:
        for route in getattr(router, "routes", ()):
            if isinstance(route, APIRoute):
                found.update(
                    (method, prefix + route.path)
                    for method in (route.methods or set()) - _IMPLICIT_METHODS
                )
                continue
            included = getattr(route, "original_router", None)
            if included is not None:
                context = getattr(route, "include_context", None)
                walk(included, prefix + getattr(context, "prefix", ""))

    walk(app.router, "")
    return found


def _callable(path: str) -> str:
    """Fill path parameters with something that parses, so any 401 is the guard's."""
    return _PATH_PARAMETER.sub(lambda _: str(uuid.uuid4()), path)


def test_the_walk_sees_every_documented_endpoint(app: FastAPI) -> None:
    walked = {path for _, path in _endpoints(app)}
    documented = set(app.openapi()["paths"])

    assert documented <= walked, (
        f"The route walk missed {sorted(documented - walked)}. FastAPI's "
        "route-table structure has changed — fix `_endpoints`, otherwise the "
        "protection test below passes without checking anything."
    )


def test_the_open_path_allowlist_is_not_stale(app: FastAPI) -> None:
    declared = {path for _, path in _endpoints(app)}

    assert declared >= OPEN_PATHS, (
        "OPEN_PATHS names endpoints that no longer exist: "
        f"{sorted(OPEN_PATHS - declared)}. Remove them, so the list stays a "
        "record of real decisions."
    )


async def test_every_non_open_endpoint_rejects_an_anonymous_caller(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    guarded = sorted(
        (method, path) for method, path in _endpoints(app) if path not in OPEN_PATHS
    )
    assert guarded, "No guarded endpoints found — the walk is not finding routes."

    unprotected = [
        (method, path)
        for method, path in guarded
        if (await anon_client.request(method, _callable(path))).status_code != 401
    ]

    assert not unprotected, (
        f"These endpoints answer an anonymous caller: {unprotected}. Mount the "
        "router on the guarded `/api/v1` router in `app.main`, or add the path "
        "to OPEN_PATHS in this test if it is deliberately public."
    )
