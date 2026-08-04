"""Liveness/readiness endpoints (also used by Docker healthchecks)."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    """Health probe response."""

    status: str


@router.get("/health")
async def health() -> HealthStatus:
    """Liveness probe — returns 200 when the process is serving requests."""
    return HealthStatus(status="ok")
