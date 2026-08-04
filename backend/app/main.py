"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.scheduler import create_scheduler
from app.domains.health.endpoints import router as health_router
from app.domains.items.endpoints import router as items_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup/shutdown."""
    configure_logging()
    app.state.scheduler = create_scheduler()
    get_logger(__name__).info("application_started")
    yield
    app.state.scheduler.shutdown(wait=False)


def generate_operation_id(route: APIRoute) -> str:
    """Readable OpenAPI operationIds (items-list_items, not the path-mangled default).

    These become the function/hook names in generated API clients.
    """
    tag = str(route.tags[0]) if route.tags else "api"
    return f"{tag}-{route.name}"


def create_app() -> FastAPI:
    """Build the FastAPI application with all routers and middleware."""
    settings = get_settings()
    app = FastAPI(
        title=settings.application_name,
        lifespan=lifespan,
        generate_unique_id_function=generate_operation_id,
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

    api = APIRouter(prefix=settings.api_path)
    api.include_router(items_router)
    app.include_router(api)

    return app


app = create_app()
