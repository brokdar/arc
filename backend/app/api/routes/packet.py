"""HTTP endpoint for the session packet. Thin layer over `app.services.packet`.

`/strength/packet` is a document, not a collection: one GET, nothing to write.
Mounted on the protected `/api/v1` router (`app.api.routers`).
"""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.schemas.exercise_preferences import ease_back_read, swap_read
from app.api.schemas.exercises import ExerciseRead
from app.api.schemas.packet import (
    EffortQuestionRead,
    GymRead,
    LastNumbersRead,
    PacketLineRead,
    PacketRead,
    PacketSessionRead,
    PacketSettingsRead,
    PlatePairRead,
    PrefillRead,
    WarmupRead,
)
from app.api.schemas.workouts import StrengthSetSchema
from app.domain.plans import format_time_of_day
from app.domain.strength import strength_set_to_json
from app.persistence.db import SessionDep
from app.services.packet import Packet, PacketLine, PacketService, PacketSession

router = APIRouter(prefix="/strength/packet", tags=["strength"])


def get_service(session: SessionDep) -> PacketService:
    """Bind the service to a request-scoped session."""
    return PacketService.from_session(session)


ServiceDep = Annotated[PacketService, Depends(get_service)]


@router.get("")
async def get_packet(service: ServiceDep) -> PacketRead:
    """The next seven days of strength sessions, with every number the gym needs."""
    return _packet_read(await service.build())


def _line_read(line: PacketLine) -> PacketLineRead:
    return PacketLineRead(
        line_ref=line.line_ref,
        group_index=line.group_index,
        item_index=line.item_index,
        group_label=line.group_label,
        exercise_id=line.exercise_id,
        exercise_name=line.exercise_name,
        prescription=StrengthSetSchema.model_validate(
            strength_set_to_json(line.prescription)
        ),
        main=line.main,
        equipment=line.equipment,
        prefill=PrefillRead.model_validate(line.prefill),
        warmups=[WarmupRead.model_validate(step) for step in line.warmups],
        plates_per_side=line.plates_per_side,
        swaps=[swap_read(row) for row in line.swaps],
        avoided=line.avoided,
        ease_back=ease_back_read(line.ease_back),
    )


def _session_read(session: PacketSession) -> PacketSessionRead:
    return PacketSessionRead(
        planned_session_id=session.planned_session_id,
        date=session.date,
        time_of_day=(
            None
            if session.time_of_day is None
            else format_time_of_day(session.time_of_day)
        ),
        purpose=session.purpose,
        intent_text=session.intent_text,
        key=session.key,
        plan_id=session.plan_id,
        follow_exactly=session.follow_exactly,
        lines=[_line_read(line) for line in session.lines],
    )


def _packet_read(packet: Packet) -> PacketRead:
    gym = packet.gym
    return PacketRead(
        schema_version=packet.schema_version,
        generated_at=packet.generated_at,
        starts_on=packet.starts_on,
        ends_before=packet.ends_before,
        settings=PacketSettingsRead.model_validate(packet.settings),
        effort=EffortQuestionRead.model_validate(packet.effort),
        gym=GymRead(
            bar_kg=gym.bar_kg,
            plate_pairs=[
                PlatePairRead(kg=kg, pairs=pairs) for kg, pairs in gym.plate_pairs
            ],
            dumbbell_step_kg=gym.dumbbell_step_kg,
            dumbbell_max_kg=gym.dumbbell_max_kg,
            stack_step_kg=gym.stack_step_kg,
            stack_max_kg=gym.stack_max_kg,
        ),
        sessions=[_session_read(session) for session in packet.sessions],
        last_numbers=[
            LastNumbersRead(**asdict(entry)) for entry in packet.last_numbers
        ],
        library=[ExerciseRead.model_validate(row) for row in packet.library],
    )
