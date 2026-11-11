"""The routers mounted behind the session guard, in mount order.

`app.main` includes every router listed here on the `APIRouter` that carries
`Depends(require_session)`, and nowhere else, so a router added to this tuple
is protected by default. This file is deliberately NOT in `.github/CODEOWNERS`:
adding a guarded router is routine work and should not wait for the operator's
review. What IS owned is the open/guarded split itself, in `app/main.py` (the
open `/auth` router and `/health`), so making an endpoint public still needs
that file. `tests/unit/test_router_protection.py` walks the live route table
and demands a 401 from every endpoint outside its open list, whatever file
mounted it.

Order matters only where two routers could match one path; append new routers
at the end.
"""

from fastapi import APIRouter

from app.api.routes.activity import manual_router as manual_sessions_router
from app.api.routes.activity import router as sessions_router
from app.api.routes.agent_notes import router as agent_notes_router
from app.api.routes.anchors import router as anchors_router
from app.api.routes.athlete import router as athlete_router
from app.api.routes.cards import router as cards_router
from app.api.routes.clock import router as clock_router
from app.api.routes.connections import router as connections_router
from app.api.routes.exercises import router as exercises_router
from app.api.routes.exports import router as exports_router
from app.api.routes.exports import session_router as session_export_router
from app.api.routes.fitness import router as fitness_router
from app.api.routes.ingest import router as ingest_router
from app.api.routes.integrations import catalogue_router as integration_catalogue_router
from app.api.routes.integrations import router as integrations_router
from app.api.routes.matching import router as matches_router
from app.api.routes.matching import session_router as session_matches_router
from app.api.routes.plan import router as plan_router
from app.api.routes.planned_sessions import router as planned_sessions_router
from app.api.routes.power_curve import router as power_curve_router
from app.api.routes.proposals import router as proposals_router
from app.api.routes.purposes import router as purposes_router
from app.api.routes.scoring import planned_router as planned_reasons_router
from app.api.routes.scoring import router as scores_router
from app.api.routes.settings_training import router as training_settings_router
from app.api.routes.wellness import router as wellness_router
from app.api.routes.workouts import labels_router as workout_labels_router
from app.api.routes.workouts import router as workouts_router
from app.api.routes.zones import router as zones_router

GUARDED_ROUTERS: tuple[APIRouter, ...] = (
    athlete_router,
    clock_router,
    anchors_router,
    wellness_router,
    zones_router,
    exercises_router,
    purposes_router,
    workouts_router,
    workout_labels_router,
    planned_sessions_router,
    proposals_router,
    agent_notes_router,
    plan_router,
    sessions_router,
    manual_sessions_router,
    ingest_router,
    connections_router,
    integrations_router,
    integration_catalogue_router,
    matches_router,
    session_matches_router,
    scores_router,
    planned_reasons_router,
    fitness_router,
    power_curve_router,
    cards_router,
    training_settings_router,
    exports_router,
    session_export_router,
)
