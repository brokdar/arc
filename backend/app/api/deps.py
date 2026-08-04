"""Shared API dependencies.

Currently just the session guard applied to every protected router in
`app.main`.
"""

from typing import Annotated

from fastapi import Request, Security
from fastapi.security import APIKeyCookie

from app.core.config import SessionSettings
from app.core.exceptions import UnauthorizedError

#: The cookie name published in the OpenAPI security scheme.
#
# `AUTH__SESSION__COOKIE_NAME` is configurable at runtime, but a security
# scheme is baked into the static schema, so the documented name is always the
# default. Deployments that rename the cookie serve a schema whose
# `cookieAuth` name is cosmetically stale — the guard below reads the session
# from the request, not from this scheme, so authentication still works.
DOCUMENTED_COOKIE_NAME = SessionSettings().cookie_name

#: `auto_error=False`: the scheme exists to document the cookie in OpenAPI;
#: rejection is done by `require_session` so the body is our standard
#: `ErrorDetail` shape rather than FastAPI's.
session_cookie_scheme = APIKeyCookie(name=DOCUMENTED_COOKIE_NAME, auto_error=False)


async def require_session(
    request: Request,
    _cookie: Annotated[str | None, Security(session_cookie_scheme)] = None,
) -> None:
    """Reject requests that do not carry an authenticated session cookie.

    Raises:
        UnauthorizedError: When the signed session has no `auth` marker —
            missing, expired, or tampered cookies all land here, because
            `SessionMiddleware` silently drops a cookie whose signature does
            not verify.
    """
    if not request.session.get("auth"):
        raise UnauthorizedError("Not authenticated")
