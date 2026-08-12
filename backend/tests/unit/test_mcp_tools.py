"""The coaching agent's tool surface, driven end to end (WP-8.1).

Through a real `fastmcp.Client` against a real `create_server`, with a real
key on the auth context, against the SQLite database the `session_factory`
fixture binds `session_scope()` to. Nothing here is stubbed: a tool that would
fail over the wire fails here.

The seeding goes through the athlete's own HTTP API, so every fixture in these
tests is a state the application can actually be in — a plan the athlete
really made, a session they really logged.

Two things this file exists to pin that no other file can:

* **what is on the surface**, exactly (`test_the_surface_is_exactly_this`) —
  the invariant is about tools that must *not* exist, and only an exhaustive
  assertion catches one being added;
* **that the guardrails bind through MCP** — they are implemented in the
  service layer and tested there, and these prove the adapter reaches them
  rather than around them.
"""

import ast
import datetime as dt
import inspect
import uuid
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domain.athlete import Discipline
from app.mcp.tools import register_tools
from app.persistence.agent_notes import AgentNoteRow
from app.persistence.anchors import AnchorVersionRow
from app.persistence.audit import AuditLogEntry
from app.persistence.proposals import PlanProposalRow
from app.persistence.workouts import MAX_NAME_LENGTH, WorkoutRow
from tests.unit.mcp_harness import connected_as, server_for
from tests.unit.prescriptions import EASY_RIDE, HARD_RIDE

ANCHORS = "/api/v1/anchors"
ATHLETE = "/api/v1/athlete"
MANUAL = "/api/v1/manual-sessions"
PLANNED = "/api/v1/planned-sessions"
PROPOSALS = "/api/v1/proposals"

_KEY = "a1b2c3d4" * 4
#: Deliberately single-scope keys (a real coach would carry `read+write`):
#: scopes are named requirements and not a hierarchy, so a write-only key
#: cannot read, and these keys keep each refusal observable per scope.
COACH = f"coach:write:{_KEY}"
READER = f"reader:read:{_KEY[::-1]}"

MODEL = "claude-opus-4-6"

#: A Monday, and the week every test plans into.
MONDAY = dt.date(2026, 8, 10)

#: The tools the coaching agent has, and all of them. Adding one is a decision
#: about what an agent may do to an athlete's training record, so it is a
#: decision this list makes someone write down.
EXPECTED_TOOLS = {
    "ping",
    # reads
    "get_athlete",
    "get_anchors",
    "get_plan_week",
    "get_session_detail",
    "list_sessions",
    "get_workout_library",
    "search_history",
    # writes
    "append_anchor",
    "create_workout",
    "propose_plan_change",
    "write_session_evaluation",
    "annotate",
}

#: Every read tool, with arguments that reach an answer. `{session}` is
#: substituted with a real recorded session's id. Exhaustive on purpose: "the
#: flag is on every read" is only true if it is on every one of them, and a
#: sample of three would let the eighth ship without it.
READ_TOOLS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("get_athlete", {}),
    ("get_anchors", {}),
    ("get_plan_week", {}),
    ("get_session_detail", {"session_id": "{session}"}),
    ("list_sessions", {}),
    ("get_workout_library", {}),
    ("search_history", {"start": "2026-08-10", "end": "2026-08-16"}),
)


# --- helpers ---------------------------------------------------------------------


def expires() -> str:
    """An expiry two days out, with an offset, as the tool requires."""
    return (dt.datetime.now(dt.UTC) + dt.timedelta(days=2)).isoformat()


async def append_ftp(client: AsyncClient, value: float = 250) -> str:
    """Append an FTP anchor version through the athlete's API."""
    response = await client.post(
        ANCHORS,
        json={"anchor_type": "ftp", "value": value, "provenance": "estimated"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def plan(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Plan a session, asserting it was accepted."""
    payload: dict[str, Any] = {
        "date": MONDAY.isoformat(),
        "purpose": "endurance",
        "structure": EASY_RIDE,
    } | overrides
    response = await client.post(PLANNED, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def record(client: AsyncClient, **overrides: Any) -> str:
    """Log a manual session and return its id."""
    payload: dict[str, Any] = {
        "start_time": f"{MONDAY.isoformat()}T17:00:00Z",
        "timezone": "UTC",
        "duration_s": 3_600,
        "discipline": "cycling",
    } | overrides
    response = await client.post(MANUAL, json=payload)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def raise_red_flag(client: AsyncClient) -> None:
    """Set the athlete's illness/injury flag."""
    response = await client.patch(
        ATHLETE,
        json={
            "red_flag_active": True,
            "red_flag_severity": "moderate",
            "red_flag_note": "chest infection",
        },
    )
    assert response.status_code == 200, response.text


async def call(entry: str, tool: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call one tool as ``entry``, over the in-memory transport."""
    async with connected_as(server_for(COACH, READER), entry) as client:
        result = await client.call_tool(tool, arguments or {})
        return result.data


async def rows(session: AsyncSession, model: Any) -> list[Any]:
    """Every row of one table."""
    session.expire_all()
    return list((await session.execute(select(model))).scalars())


# --- the shape of the surface ------------------------------------------------------


async def test_the_surface_is_exactly_this(session_factory: Any) -> None:
    async with connected_as(server_for(COACH, READER), READER) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names == EXPECTED_TOOLS


async def test_nothing_here_mutates_a_recording_a_stream_or_a_verdict(
    session_factory: Any,
) -> None:
    # Build-plan invariant: the agent may never write `declared_verdict`,
    # reasons, recordings or streams. The check is over the *names* of the
    # tools, because a tool that could do it would have to be one of these.
    async with connected_as(server_for(COACH, READER), READER) as client:
        names = {tool.name for tool in await client.list_tools()}

    forbidden = ("recording", "stream", "verdict", "declare", "reason", "dispute")
    assert not [name for name in names for word in forbidden if word in name], (
        "the agent's surface must not name a thing only the athlete may write"
    )


async def test_ping_still_answers(session_factory: Any) -> None:
    assert (await call(READER, "ping"))["status"] == "ok"


# --- scopes ------------------------------------------------------------------------


async def test_a_read_key_may_not_write(session_factory: Any) -> None:
    with pytest.raises(ToolError, match="'write' is required") as excinfo:
        await call(
            READER,
            "append_anchor",
            {"anchor_type": "ftp", "value": 250, "provenance": "estimated"},
        )

    assert "reader" in str(excinfo.value)
    assert _KEY not in str(excinfo.value), "a refusal must never quote the key"


async def test_a_write_key_may_not_read(session_factory: Any) -> None:
    # Scopes are named requirements, not a hierarchy.
    with pytest.raises(ToolError, match="'read' is required"):
        await call(COACH, "get_athlete")


async def test_nothing_was_written_by_a_refused_write(
    session_factory: Any, db_session: AsyncSession
) -> None:
    with pytest.raises(ToolError):
        await call(
            READER,
            "append_anchor",
            {"anchor_type": "ftp", "value": 250, "provenance": "estimated"},
        )

    assert await rows(db_session, AnchorVersionRow) == []


# --- reads -------------------------------------------------------------------------


async def test_a_read_returns_the_seeded_athlete_and_the_red_flag(
    client: AsyncClient,
) -> None:
    await client.patch(ATHLETE, json={"name": "Jo", "height_cm": 180.0})
    await raise_red_flag(client)

    data = await call(READER, "get_athlete")

    assert data["athlete"]["name"] == "Jo"
    assert data["athlete"]["height_cm"] == 180.0
    assert data["athlete"]["plan_state"] == "active"
    assert data["red_flag"] == {
        "active": True,
        "severity": "moderate",
        "note": "chest infection",
    }


@pytest.mark.parametrize(("tool", "arguments"), READ_TOOLS)
async def test_every_read_carries_the_red_flag(
    client: AsyncClient, tool: str, arguments: dict[str, Any]
) -> None:
    # WP-8.4: an agent that has to remember to ask will one day not ask, and
    # the refusal it then walks into looks like a bug rather than a rule.
    session_id = await record(client)
    await raise_red_flag(client)

    data = await call(
        READER,
        tool,
        {
            key: value.format(session=session_id) if isinstance(value, str) else value
            for key, value in arguments.items()
        },
    )

    assert data["red_flag"]["active"] is True
    assert data["red_flag"]["severity"] == "moderate"
    assert data["red_flag"]["note"] == "chest infection"


async def test_the_read_tools_under_test_are_all_of_them(session_factory: Any) -> None:
    # Keeps the parametrization above honest: a new read tool fails here until
    # it is added to READ_TOOLS and proved to carry the flag.
    writes = {
        "append_anchor",
        "create_workout",
        "propose_plan_change",
        "write_session_evaluation",
        "annotate",
    }

    assert {tool for tool, _ in READ_TOOLS} == EXPECTED_TOOLS - writes - {"ping"}


async def test_the_plan_week_carries_the_concurrency_token(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    planned = await plan(client)

    data = await call(READER, "get_plan_week", {"start": MONDAY.isoformat()})

    [card] = data["week"]["sessions"]
    assert card["id"] == planned["id"]
    assert card["purpose"] == "endurance"
    assert card["intent_version"] == planned["intent"]["version"]
    assert card["predicted_load"] is not None


async def test_the_anchor_history_carries_provenance(client: AsyncClient) -> None:
    await append_ftp(client, 250)

    data = await call(READER, "get_anchors", {"anchor_type": "ftp"})

    assert data["total"] == 1
    [version] = data["items"]
    assert version["value"] == 250
    assert version["unit"] == "W"
    assert version["provenance"] == "estimated"
    assert version["source"] == "athlete"


async def test_a_session_detail_composes_the_four_reads(client: AsyncClient) -> None:
    session_id = await record(client)

    data = await call(READER, "get_session_detail", {"session_id": session_id})

    assert data["session"]["id"] == session_id
    assert data["session"]["local_date"] == MONDAY.isoformat()
    assert data["session"]["duration_s"] == 3_600
    # A manually logged session is measured — there is a metrics version — but
    # it has no streams, so there is nothing to price it from and nothing to
    # score it against. Null load and null score are answers, not gaps, and
    # the agent must be able to tell them from a zero.
    assert data["metrics"]["version"] == 1
    assert data["metrics"]["training_load"] is None
    assert data["score"] is None
    assert data["declaration"] is None
    assert data["match"] is None
    assert data["red_flag"]["active"] is False


async def test_a_session_that_does_not_exist_is_a_readable_refusal(
    session_factory: Any,
) -> None:
    with pytest.raises(ToolError, match="not_found:"):
        await call(READER, "get_session_detail", {"session_id": str(uuid.uuid4())})


async def test_a_malformed_id_names_the_argument(session_factory: Any) -> None:
    with pytest.raises(ToolError, match="session_id must be a uuid"):
        await call(READER, "get_session_detail", {"session_id": "last Tuesday"})


async def test_the_workout_library_lists_without_the_structure(
    client: AsyncClient,
) -> None:
    await call(
        COACH,
        "create_workout",
        {"name": "2x20 threshold", "structure": HARD_RIDE, "tags": ["threshold"]},
    )

    data = await call(READER, "get_workout_library", {"query": "threshold"})

    [workout] = data["items"]
    assert workout["name"] == "2x20 threshold"
    assert workout["discipline"] == "cycling"
    assert workout["tags"] == ["threshold"]
    assert "structure" not in workout


async def test_one_unparseable_workout_does_not_cost_the_whole_page(
    session_factory: Any, db_session: AsyncSession
) -> None:
    # A library the agent cannot list because one old document went stale is
    # worse than a list with a null in it — and the failure kinds a stored
    # document can produce are not only `ValueError`.
    await call(COACH, "create_workout", {"name": "Easy hour", "structure": EASY_RIDE})
    db_session.add(
        WorkoutRow(
            name="Stale",
            description=None,
            discipline=Discipline.CYCLING,
            structure={"discipline": "cycling", "steps": [{"kind": "nonsense"}]},
            folder=None,
        )
    )
    await db_session.commit()

    data = await call(READER, "get_workout_library")

    assert {item["name"]: item["step_count"] for item in data["items"]} == {
        "Easy hour": 1,
        "Stale": None,
    }


async def test_search_history_folds_sessions_into_weeks(client: AsyncClient) -> None:
    await record(client)
    await record(
        client, start_time=f"{(MONDAY + dt.timedelta(days=14)).isoformat()}T09:00:00Z"
    )

    data = await call(
        READER,
        "search_history",
        {
            "start": MONDAY.isoformat(),
            "end": (MONDAY + dt.timedelta(days=20)).isoformat(),
        },
    )

    history = data["history"]
    assert history["session_count"] == 2
    assert history["duration_s"] == 7_200
    # Two sessions a week apart with an empty week between them: the blank
    # week is reported, not skipped.
    assert [week["session_count"] for week in history["weeks"]] == [1, 0, 1]
    assert history["verdicts"] == {"undeclared": 2}


async def test_search_history_clips_a_partial_week_to_the_range(
    client: AsyncClient,
) -> None:
    # Asking from a Wednesday used to report the first bucket as the whole
    # Monday-to-Sunday week, so five days of training read as a full week's.
    wednesday = MONDAY + dt.timedelta(days=2)
    await record(client, start_time=f"{wednesday.isoformat()}T17:00:00Z")

    data = await call(
        READER,
        "search_history",
        {
            "start": wednesday.isoformat(),
            "end": (MONDAY + dt.timedelta(days=9)).isoformat(),
        },
    )

    first, second = data["history"]["weeks"]
    assert first["start"] == wednesday.isoformat()
    assert first["end"] == (MONDAY + dt.timedelta(days=6)).isoformat()
    # The tail is clipped the same way, and a whole week in between would not be.
    assert second["start"] == (MONDAY + dt.timedelta(days=7)).isoformat()
    assert second["end"] == (MONDAY + dt.timedelta(days=9)).isoformat()
    assert first["session_count"] == 1


async def test_a_read_can_page_past_the_first_page(client: AsyncClient) -> None:
    # `total` on its own tells an agent it is missing rows and gives it no way
    # to read them.
    for day in range(3):
        await record(
            client,
            start_time=f"{(MONDAY + dt.timedelta(days=day)).isoformat()}T17:00:00Z",
        )

    first = await call(READER, "list_sessions", {"limit": 2})
    second = await call(READER, "list_sessions", {"limit": 2, "offset": 2})

    assert first["total"] == second["total"] == 3
    assert first["offset"] == 0
    assert second["offset"] == 2
    assert [item["id"] for item in first["items"]] != [
        item["id"] for item in second["items"]
    ]
    assert len(second["items"]) == 1


async def test_a_negative_offset_is_refused_by_name(session_factory: Any) -> None:
    with pytest.raises(ToolError, match="invalid: offset"):
        await call(READER, "get_workout_library", {"offset": -1})


async def test_search_history_refuses_an_inverted_range(session_factory: Any) -> None:
    with pytest.raises(ToolError, match="invalid:"):
        await call(
            READER,
            "search_history",
            {"start": "2026-08-31", "end": "2026-08-01"},
        )


# --- append_anchor -----------------------------------------------------------------


async def test_append_anchor_appends_as_the_agent(
    session_factory: Any, db_session: AsyncSession
) -> None:
    data = await call(
        COACH,
        "append_anchor",
        {
            "anchor_type": "ftp",
            "value": 268,
            "provenance": "tested",
            "protocol": "20-minute test x0.95",
        },
    )

    assert data["dry_run"] is False
    assert data["anchor"]["value"] == 268
    assert data["anchor"]["source"] == "agent"
    [row] = await rows(db_session, AnchorVersionRow)
    assert row.protocol == "20-minute test x0.95"


async def test_tested_provenance_without_a_protocol_is_refused(
    session_factory: Any, db_session: AsyncSession
) -> None:
    # "Never guess an anchor": a test with no protocol is not a test.
    with pytest.raises(ToolError, match="invalid:") as excinfo:
        await call(
            COACH,
            "append_anchor",
            {"anchor_type": "ftp", "value": 268, "provenance": "tested"},
        )

    assert "protocol" in str(excinfo.value)
    assert await rows(db_session, AnchorVersionRow) == []


async def test_an_append_dry_run_writes_nothing(
    session_factory: Any, db_session: AsyncSession
) -> None:
    data = await call(
        COACH,
        "append_anchor",
        {
            "anchor_type": "ftp",
            "value": 268,
            "provenance": "estimated",
            "dry_run": True,
        },
    )

    assert data["dry_run"] is True
    assert data["anchor"]["value"] == 268
    assert "id" not in data["anchor"], "nothing was written, so there is no id"
    assert await rows(db_session, AnchorVersionRow) == []


async def test_a_dry_run_still_refuses_what_the_write_would_refuse(
    session_factory: Any,
) -> None:
    with pytest.raises(ToolError, match="protocol"):
        await call(
            COACH,
            "append_anchor",
            {
                "anchor_type": "ftp",
                "value": 268,
                "provenance": "tested",
                "dry_run": True,
            },
        )


async def test_a_reserved_anchor_type_is_refused(session_factory: Any) -> None:
    with pytest.raises(ToolError, match="reserved"):
        await call(
            COACH,
            "append_anchor",
            {"anchor_type": "cp", "value": 260, "provenance": "estimated"},
        )


# --- create_workout ----------------------------------------------------------------


async def test_create_workout_adds_to_the_library(
    session_factory: Any, db_session: AsyncSession
) -> None:
    data = await call(
        COACH, "create_workout", {"name": "Easy hour", "structure": EASY_RIDE}
    )

    assert data["dry_run"] is False
    assert data["workout"]["name"] == "Easy hour"
    assert len(await rows(db_session, WorkoutRow)) == 1


async def test_a_workout_dry_run_writes_nothing(
    session_factory: Any, db_session: AsyncSession
) -> None:
    data = await call(
        COACH,
        "create_workout",
        {"name": "Easy hour", "structure": EASY_RIDE, "dry_run": True},
    )

    assert data["dry_run"] is True
    assert data["workout"]["discipline"] == "cycling"
    assert data["workout"]["step_count"] == 1
    assert await rows(db_session, WorkoutRow) == []


async def test_an_unparseable_structure_is_refused(session_factory: Any) -> None:
    with pytest.raises(ToolError, match="invalid:"):
        await call(
            COACH,
            "create_workout",
            {"name": "Nonsense", "structure": {"discipline": "cycling", "steps": []}},
        )


async def test_a_workout_dry_run_refuses_what_the_write_refuses(
    session_factory: Any, db_session: AsyncSession
) -> None:
    # The dry run used to validate the structure and let the tags straight
    # through, so a preview could say yes to a call the write then refused —
    # which is the one thing a dry run must never do.
    request = {"name": "Easy hour", "structure": EASY_RIDE, "tags": ["   ", "a" * 400]}

    with pytest.raises(ToolError) as previewed:
        await call(COACH, "create_workout", request | {"dry_run": True})
    with pytest.raises(ToolError) as written:
        await call(COACH, "create_workout", request)

    assert str(previewed.value).startswith("invalid:")
    assert str(previewed.value) == str(written.value), (
        "the dry run must refuse for the same stated reason as the write"
    )
    assert await rows(db_session, WorkoutRow) == []


async def test_a_workout_dry_run_reports_the_tags_the_write_would_store(
    session_factory: Any,
) -> None:
    # Tags are normalized on write — stripped, lowercased, deduplicated — so a
    # preview that echoed the request back would describe a library entry that
    # is not the one about to be created.
    request = {"name": "Easy hour", "structure": EASY_RIDE, "tags": ["ZZ", "zz"]}

    previewed = await call(COACH, "create_workout", request | {"dry_run": True})
    written = await call(COACH, "create_workout", request)

    assert previewed["workout"]["tags"] == ["zz"]
    assert previewed["workout"]["tags"] == written["workout"]["tags"]
    assert previewed["workout"]["step_count"] == written["workout"]["step_count"]
    assert previewed["workout"]["discipline"] == written["workout"]["discipline"]


# --- propose_plan_change -----------------------------------------------------------


async def test_a_proposal_from_the_tool_is_accepted_over_http(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The whole loop: the agent suggests through MCP, the athlete answers
    # through the API, and the plan moves.
    await append_ftp(client)
    planned = await plan(client)

    data = await call(
        COACH,
        "propose_plan_change",
        {
            "changes": [
                {
                    "kind": "move",
                    "planned_session_id": planned["id"],
                    "expected_intent_version": planned["intent"]["version"],
                    "date": (MONDAY + dt.timedelta(days=2)).isoformat(),
                }
            ],
            "rationale": "Tuesday looks heavy after Sunday's ride.",
            "expires_at": expires(),
        },
    )

    assert data["dry_run"] is False
    assert data["proposal"]["status"] == "pending"
    assert data["proposal"]["created_by"] == "agent:coach"
    [entry] = data["diff"]
    assert entry["before"]["date"] == MONDAY.isoformat()

    accepted = await client.post(f"{PROPOSALS}/{data['proposal']['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"

    moved = await client.get(f"{PLANNED}/{planned['id']}")
    assert moved.json()["date"] == (MONDAY + dt.timedelta(days=2)).isoformat()


async def test_a_proposal_dry_run_writes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    planned = await plan(client)

    data = await call(
        COACH,
        "propose_plan_change",
        {
            "changes": [
                {
                    "kind": "delete",
                    "planned_session_id": planned["id"],
                    "expected_intent_version": planned["intent"]["version"],
                }
            ],
            "rationale": "You are away that week.",
            "expires_at": expires(),
            "dry_run": True,
        },
    )

    assert data["dry_run"] is True
    assert data["proposal"] is None
    assert data["diff"], "the diff is the whole answer on a dry run"
    assert data["superseded"] == [], "there was nothing open to displace"
    assert await rows(db_session, PlanProposalRow) == []


async def test_a_dry_run_reports_what_the_write_would_supersede(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Supersession used to be computed after the dry-run return, so a preview
    # said `superseded: []` while the real call was about to throw away a
    # standing suggestion about the same session. What a proposal displaces is
    # part of what it does.
    await append_ftp(client)
    planned = await plan(client)
    standing = await call(
        COACH,
        "propose_plan_change",
        {
            "changes": [
                {
                    "kind": "move",
                    "planned_session_id": planned["id"],
                    "expected_intent_version": planned["intent"]["version"],
                    "date": (MONDAY + dt.timedelta(days=2)).isoformat(),
                }
            ],
            "rationale": "Tuesday is quieter.",
            "expires_at": expires(),
        },
    )

    data = await call(
        COACH,
        "propose_plan_change",
        {
            "changes": [
                {
                    "kind": "delete",
                    "planned_session_id": planned["id"],
                    "expected_intent_version": planned["intent"]["version"],
                }
            ],
            "rationale": "You are away that week.",
            "expires_at": expires(),
            "dry_run": True,
        },
    )

    assert data["dry_run"] is True
    assert [row["id"] for row in data["superseded"]] == [standing["proposal"]["id"]]
    # Reported, not closed: the dry run still wrote nothing.
    assert [row.status.value for row in await rows(db_session, PlanProposalRow)] == [
        "pending"
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [("structure", 5), ("success_criteria", [1, 2])],
)
async def test_a_wrong_typed_change_field_is_a_named_refusal(
    session_factory: Any, field: str, value: Any
) -> None:
    # These used to raise TypeError out of the domain, which the generic
    # handler reported as "the server failed" — telling the agent to retry a
    # call that can never work. It has to be told which change and which field.
    with pytest.raises(ToolError) as excinfo:
        await call(
            COACH,
            "propose_plan_change",
            {
                "changes": [
                    {
                        "kind": "create",
                        "date": MONDAY.isoformat(),
                        "purpose": "endurance",
                        field: value,
                    }
                ],
                "rationale": "Adding an easy hour.",
                "expires_at": expires(),
            },
        )

    message = str(excinfo.value)
    assert message.startswith("invalid:")
    assert "change 0" in message
    assert field in message
    assert "server failed" not in message


async def test_a_stale_concurrency_token_is_a_readable_conflict(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    planned = await plan(client)
    revised = await client.patch(
        f"{PLANNED}/{planned['id']}", json={"intent_text": "keep it easy"}
    )
    assert revised.status_code == 200, revised.text

    with pytest.raises(ToolError, match="conflict:") as excinfo:
        await call(
            COACH,
            "propose_plan_change",
            {
                "changes": [
                    {
                        "kind": "move",
                        "planned_session_id": planned["id"],
                        "expected_intent_version": planned["intent"]["version"],
                        "date": (MONDAY + dt.timedelta(days=2)).isoformat(),
                    }
                ],
                "rationale": "Move it.",
                "expires_at": expires(),
            },
        )

    # The agent's next move is to re-read and re-propose, so the message has
    # to say which version is in force.
    assert "has moved on" in str(excinfo.value)


async def test_the_red_flag_blocks_an_intensifying_proposal(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await append_ftp(client)
    planned = await plan(client)
    await raise_red_flag(client)

    with pytest.raises(ToolError, match="red_flag:") as excinfo:
        await call(
            COACH,
            "propose_plan_change",
            {
                "changes": [
                    {
                        "kind": "update",
                        "planned_session_id": planned["id"],
                        "expected_intent_version": planned["intent"]["version"],
                        "updates": {"purpose": "vo2max", "structure": HARD_RIDE},
                    }
                ],
                "rationale": "Time to sharpen up.",
                "expires_at": expires(),
            },
        )

    assert "moderate" in str(excinfo.value)
    assert await rows(db_session, PlanProposalRow) == []


async def test_the_red_flag_still_allows_a_reduction(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The flag restrains the coach, not the athlete: the plan must stay
    # lightenable while they are ill.
    await append_ftp(client)
    planned = await plan(client)
    await raise_red_flag(client)

    data = await call(
        COACH,
        "propose_plan_change",
        {
            "changes": [
                {
                    "kind": "delete",
                    "planned_session_id": planned["id"],
                    "expected_intent_version": planned["intent"]["version"],
                }
            ],
            "rationale": "Rest until the chest clears.",
            "expires_at": expires(),
        },
    )

    assert data["proposal"]["status"] == "pending"


async def test_a_naive_expiry_is_refused_by_name(client: AsyncClient) -> None:
    await append_ftp(client)
    planned = await plan(client)

    with pytest.raises(ToolError, match="expires_at must carry a timezone"):
        await call(
            COACH,
            "propose_plan_change",
            {
                "changes": [
                    {
                        "kind": "delete",
                        "planned_session_id": planned["id"],
                        "expected_intent_version": planned["intent"]["version"],
                    }
                ],
                "rationale": "Away.",
                "expires_at": "2026-12-01T09:00:00",
            },
        )


async def test_two_changes_about_one_session_are_refused(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    planned = await plan(client)
    change = {
        "kind": "move",
        "planned_session_id": planned["id"],
        "expected_intent_version": planned["intent"]["version"],
        "date": (MONDAY + dt.timedelta(days=2)).isoformat(),
    }

    with pytest.raises(ToolError, match="at most one change per planned session"):
        await call(
            COACH,
            "propose_plan_change",
            {
                "changes": [change, {**change, "date": MONDAY.isoformat()}],
                "rationale": "Twice.",
                "expires_at": expires(),
            },
        )


# --- the rate cap ------------------------------------------------------------------


async def test_the_write_cap_trips_at_the_configured_cap(
    session_factory: Any, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A circuit breaker on the whole agent surface, counted over the audit
    # log — so it does not matter which write spends it.
    monkeypatch.setenv("MCP__WRITE_CAP_PER_HOUR", "2")
    get_settings.cache_clear()

    for value in (250, 255):
        await call(
            COACH,
            "append_anchor",
            {"anchor_type": "ftp", "value": value, "provenance": "estimated"},
        )

    with pytest.raises(ToolError, match="rate_limited:") as excinfo:
        await call(
            COACH,
            "append_anchor",
            {"anchor_type": "ftp", "value": 260, "provenance": "estimated"},
        )

    assert "cap of 2 per hour" in str(excinfo.value)
    assert len(await rows(db_session, AnchorVersionRow)) == 2


async def test_a_dry_run_costs_no_cap_budget(
    session_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP__WRITE_CAP_PER_HOUR", "1")
    get_settings.cache_clear()

    for _ in range(4):
        await call(
            COACH,
            "append_anchor",
            {
                "anchor_type": "ftp",
                "value": 250,
                "provenance": "estimated",
                "dry_run": True,
            },
        )

    data = await call(
        COACH,
        "append_anchor",
        {"anchor_type": "ftp", "value": 250, "provenance": "estimated"},
    )
    assert data["dry_run"] is False


# --- notes -------------------------------------------------------------------------


async def test_write_session_evaluation_lands_an_attributed_note(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    data = await call(
        COACH,
        "write_session_evaluation",
        {
            "session_id": session_id,
            "text": "Steady all the way through.",
            "model_id": MODEL,
            "cites": [session_id],
        },
    )

    assert data["dry_run"] is False
    assert data["note"]["kind"] == "evaluation"
    assert data["note"]["model_id"] == MODEL
    assert data["note"]["created_by"] == "agent:coach"
    assert data["note"]["cites"] == [session_id]
    [row] = await rows(db_session, AgentNoteRow)
    assert row.session_id == uuid.UUID(session_id)

    # And the athlete can read it back through their own API.
    response = await client.get(
        "/api/v1/agent-notes", params={"session_id": session_id}
    )
    assert [item["model_id"] for item in response.json()["items"]] == [MODEL]


async def test_an_evaluation_without_attribution_is_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    with pytest.raises(ToolError, match="model_id"):
        await call(
            COACH,
            "write_session_evaluation",
            {"session_id": session_id, "text": "Good.", "model_id": "  "},
        )

    assert await rows(db_session, AgentNoteRow) == []


async def test_an_evaluation_dry_run_writes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    data = await call(
        COACH,
        "write_session_evaluation",
        {
            "session_id": session_id,
            "text": "Steady.",
            "model_id": MODEL,
            "dry_run": True,
        },
    )

    assert data["dry_run"] is True
    assert data["note"]["id"] is None
    assert await rows(db_session, AgentNoteRow) == []


async def test_annotate_files_a_week_note_under_its_monday(
    session_factory: Any, db_session: AsyncSession
) -> None:
    data = await call(
        COACH,
        "annotate",
        {
            "text": "Three weeks of threshold with no easy week.",
            "model_id": MODEL,
            "plan_week": MONDAY.isoformat(),
        },
    )

    assert data["note"]["kind"] == "annotation"
    assert data["note"]["plan_week"] == MONDAY.isoformat()
    [row] = await rows(db_session, AgentNoteRow)
    assert row.plan_week == MONDAY


async def test_annotating_a_week_that_is_not_a_monday_is_refused(
    session_factory: Any,
) -> None:
    with pytest.raises(ToolError, match="Monday") as excinfo:
        await call(
            COACH,
            "annotate",
            {
                "text": "Busy week.",
                "model_id": MODEL,
                "plan_week": (MONDAY + dt.timedelta(days=3)).isoformat(),
            },
        )

    # The refusal names the Monday it was reaching for, so the retry is
    # obvious rather than a guess.
    assert MONDAY.isoformat() in str(excinfo.value)


async def test_a_note_about_both_a_session_and_a_week_is_refused(
    client: AsyncClient,
) -> None:
    session_id = await record(client)

    with pytest.raises(ToolError, match="not both"):
        await call(
            COACH,
            "annotate",
            {
                "text": "Both.",
                "model_id": MODEL,
                "session_id": session_id,
                "plan_week": MONDAY.isoformat(),
            },
        )


# --- what the trail says the agent did --------------------------------------------


async def test_every_tool_asks_for_its_scope(session_factory: Any) -> None:
    # `ping` is the only tool that answers without a key, and it answers
    # nothing about the athlete. Everything else names the scope it needs, in
    # its own body — an adapter-level assertion, because a tool that forgot to
    # ask would still pass every service-level guardrail test, and the
    # authorization it skipped is the only thing standing between a read key
    # and the plan.
    source = ast.parse(inspect.getsource(register_tools))
    registered = {
        node.name: node
        for node in ast.walk(source)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(decorator, ast.Attribute) and decorator.attr == "tool"
            for decorator in node.decorator_list
        )
    }

    assert set(registered) == EXPECTED_TOOLS, "the sweep must cover every tool"
    unguarded = [
        name
        for name, node in registered.items()
        if name != "ping" and "require_scope" not in ast.unparse(node)
    ]
    assert unguarded == []


async def test_two_agent_keys_are_distinguishable_in_the_audit_trail(
    session_factory: Any, db_session: AsyncSession
) -> None:
    # The label is the agent's identity: `agent:<label>` is what the athlete
    # reads when asking who suggested something, so two keys must not become
    # one actor.
    second = f"nightly:write:{('9f8e7d6c' * 4)}"
    async with connected_as(server_for(COACH, second), COACH) as client:
        await client.call_tool(
            "annotate",
            {
                "text": "From the coach.",
                "model_id": MODEL,
                "plan_week": MONDAY.isoformat(),
            },
        )
    async with connected_as(server_for(COACH, second), second) as client:
        await client.call_tool(
            "annotate",
            {
                "text": "From the nightly job.",
                "model_id": MODEL,
                "plan_week": MONDAY.isoformat(),
            },
        )

    actors = sorted(row.actor for row in await rows(db_session, AuditLogEntry))

    assert actors == ["agent:coach", "agent:nightly"]


async def test_an_annotation_dry_run_writes_nothing(
    session_factory: Any, db_session: AsyncSession
) -> None:
    data = await call(
        COACH,
        "annotate",
        {
            "text": "Three weeks of threshold with no easy week.",
            "model_id": MODEL,
            "plan_week": MONDAY.isoformat(),
            "dry_run": True,
        },
    )

    assert data["dry_run"] is True
    assert data["note"]["id"] is None
    assert data["note"]["plan_week"] == MONDAY.isoformat()
    assert await rows(db_session, AgentNoteRow) == []


async def test_the_write_cap_binds_create_workout_too(
    session_factory: Any, db_session: AsyncSession
) -> None:
    # The cap is a property of the agent surface, not of one tool: it is
    # enforced in the service layer, so every write an agent can reach pays
    # into the same budget.
    settings = get_settings()
    cap = settings.mcp.write_cap_per_hour
    for index in range(cap):
        await call(
            COACH,
            "create_workout",
            {"name": f"Easy hour {index}", "structure": EASY_RIDE},
        )

    with pytest.raises(ToolError, match="rate_limited"):
        await call(
            COACH,
            "create_workout",
            {"name": "One too many", "structure": EASY_RIDE},
        )

    assert len(await rows(db_session, WorkoutRow)) == cap


async def test_an_over_long_workout_name_is_a_refusal_not_a_crash(
    session_factory: Any, db_session: AsyncSession
) -> None:
    # No schema stands between an agent and this service, so the bound has to
    # be in the service: unbounded text reached Postgres as a truncation error
    # — an agent-triggerable 500 where a named refusal belongs.
    with pytest.raises(ToolError, match="invalid:") as excinfo:
        await call(
            COACH,
            "create_workout",
            {"name": "x" * (MAX_NAME_LENGTH + 1), "structure": EASY_RIDE},
        )

    assert "name" in str(excinfo.value)
    assert await rows(db_session, WorkoutRow) == []
