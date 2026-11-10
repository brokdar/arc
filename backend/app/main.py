"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.api.deps import require_session
from app.api.routers import GUARDED_ROUTERS
from app.api.routes.auth import router as auth_router
from app.api.routes.health import router as health_router
from app.api.session import ClockStepTolerantSessionMiddleware
from app.core.config import get_settings
from app.core.exceptions import ErrorDetail, register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.scheduler import create_scheduler
from app.ingest.feeds import register_feed_poll_job
from app.ingest.inbox import register_inbox_job
from app.ingest.scoring import install_stream_loader
from app.services.cards import register_card_expiry_job
from app.services.matching import register_missed_sessions_job
from app.services.proposals import register_proposal_expiry_job
from app.services.scoring import register_prompt_expiry_job
from app.services.templates import verify_bundled_resources
from app.services.wellness import register_wellness_prompt_job

#: Runtime data tree created on startup, relative to `settings.data.root`.
DATA_SUBDIRECTORIES = ("inbox", "originals", "streams", "quarantine")


def ensure_data_directories() -> None:
    """Create the runtime data tree so ingest never races on a missing dir."""
    root = get_settings().data.root
    for name in DATA_SUBDIRECTORIES:
        (root / name).mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup/shutdown."""
    configure_logging()
    ensure_data_directories()
    # Loud and early: a purpose template or catalogue entry the domain rejects
    # stops the boot, rather than surfacing months later as a session that
    # cannot be scored. Reading files only — nothing here touches the database,
    # so a successful boot still does not depend on one.
    verify_bundled_resources()
    app.state.scheduler = create_scheduler()
    # The watched folder (WP-4.3). Registered here rather than in
    # `create_scheduler`, which owns no jobs of its own: each work package
    # adds the job it needs.
    register_inbox_job(app.state.scheduler)
    # The Dropbox feeds. A second ingest job beside the watched folder rather
    # than an extension of it: the local sweep must keep working when the
    # connector cannot (`app.ingest.feeds`).
    register_feed_poll_job(app.state.scheduler)
    # The missed-session sweep (WP-6.7). Hourly, and idempotent, so it needs no
    # agreement with the athlete's midnight beyond `MATCHING__TIMEZONE`.
    register_missed_sessions_job(app.state.scheduler)
    # The evening-prompt expiry sweep (WP-7.3). Hourly and idempotent, like the
    # one above; each prompt carries its own 72-hour deadline.
    register_prompt_expiry_job(app.state.scheduler)
    # The plan-change proposal expiry sweep (WP-8.2). Hourly and idempotent
    # like the two above; nothing is applied on expiry — a lapsed proposal
    # means the committed plan stands.
    register_proposal_expiry_job(app.state.scheduler)
    # The daily wellness prompt (Increment 1). One job that both raises the
    # day's question at `WELLNESS__PROMPT_HOUR_LOCAL` and closes the ones whose
    # window has run out; hourly and idempotent like the three above, and the
    # unique constraint on `wellness_prompts.local_date` — not this schedule —
    # is what makes "one prompt a day" true.
    register_wellness_prompt_job(app.state.scheduler)
    # The suggestion-card expiry sweep. Hourly and idempotent; reads already
    # hide a card whose seven days since first display are up.
    register_card_expiry_job(app.state.scheduler)
    get_logger(__name__).info("application_started")
    yield
    app.state.scheduler.shutdown(wait=False)


def generate_operation_id(route: APIRoute) -> str:
    """Readable OpenAPI operationIds (anchors-get_zones, not the mangled default).

    These become the function/hook names in generated API clients.
    """
    tag = str(route.tags[0]) if route.tags else "api"
    return f"{tag}-{route.name}"


def create_app() -> FastAPI:
    """Build the FastAPI application with all routers and middleware."""
    settings = get_settings()
    # The scoring engine reads the cleaned 1 Hz columns, which only the ingest
    # layer may open (`app.services` cannot import `app.ingest`), so the loader
    # is installed here rather than imported there. Wiring rather than lifespan:
    # scoring is triggered from matching and from the rescore seam on every path
    # into the application, tests included, and those do not run a lifespan.
    install_stream_loader()
    app = FastAPI(
        title=settings.application_name,
        lifespan=lifespan,
        generate_unique_id_function=generate_operation_id,
    )

    # Middleware order matters: Starlette applies the LAST added first, so
    # adding the session before CORS leaves CORS outermost. Every response —
    # including the 401s the session guard produces — then carries the CORS
    # headers the browser needs to read it, and `allow_credentials=True` lets
    # the cross-origin dev setup (localhost:3000 -> localhost:8000) send the
    # cookie at all. Behind Caddy everything is same-origin and CORS is moot.
    #
    # Stock `SessionMiddleware` except for its signer, which is swapped for one
    # that survives a wall clock stepping backwards — see `app.api.session` for
    # why that is not a weakening of the cookie (issue #61).
    app.add_middleware(
        ClockStepTolerantSessionMiddleware,
        secret_key=settings.auth.session.secret_key.get_secret_value(),
        session_cookie=settings.auth.session.cookie_name,
        max_age=settings.auth.session.max_age_seconds,
        same_site="lax",
        https_only=settings.auth.session.https_only,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    # Unversioned health endpoint for Docker/load-balancer probes.
    app.include_router(health_router)

    # Open: logging in cannot itself require a session.
    open_api = APIRouter(prefix=settings.api_path)
    open_api.include_router(auth_router)
    app.include_router(open_api)

    # Everything else is behind the session cookie. New routers go in
    # `GUARDED_ROUTERS` (`app/api/routers.py`), which is mounted only here,
    # under the guard. That file is outside the owner lane on purpose: adding
    # a guarded router is routine work. Making one public still means editing
    # this file, which is owned, and `test_router_protection.py` demands a 401
    # from every endpoint not on its short open list wherever it was mounted.
    # Declared once here rather than on each route, the way 401 already is:
    # both are properties of the router, not of any one endpoint. Any write can
    # lose a race — a uniqueness pre-check overtaken, or a row deleted between
    # the read and the flush of a read-modify-write — and
    # `app.persistence.db` answers all of them 409, so every operation mounted
    # here can produce one. `test_api_contract.py` pins that the mutating ones
    # publish it.
    shared: dict[int | str, dict[str, Any]] = {
        401: {"model": ErrorDetail, "description": "No valid session"},
        409: {
            "model": ErrorDetail,
            "description": "The write lost a race against a concurrent one",
        },
    }
    api = APIRouter(
        prefix=settings.api_path,
        dependencies=[Depends(require_session)],
        responses=shared,
    )
    for router in GUARDED_ROUTERS:
        api.include_router(router)
    app.include_router(api)

    return app


app = create_app()
