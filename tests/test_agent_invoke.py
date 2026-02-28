"""Integration tests for the agent endpoints.

These tests require:
  - A running candidate-mcp server at MCP_SERVER_URL (default: http://localhost:8081/mcp)
  - A valid ANTHROPIC_API_KEY in the environment or .env file

Run with:
    uv run pytest tests/ -v
"""

import pytest
from httpx import ASGITransport, AsyncClient

from candidate_agent.main import app


@pytest.fixture()
async def client():
    """Async HTTP test client backed by the FastAPI app.

    Wraps the request in the app's lifespan context so that startup
    (MCP registry init, LangGraph compile) runs before the first test
    and shutdown runs after the last.
    """
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    """Health endpoint should return 200 with status=healthy."""
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert "mcp_connected" in body
    assert "llm_model" in body


@pytest.mark.asyncio
async def test_invoke_candidate_profile(client: AsyncClient):
    """Primary agent should answer a candidate profile query directly."""
    response = await client.post(
        "/api/v1/agent/invoke",
        json={
            "message": "What are the skills and experience of candidate C001?",
            "candidate_id": "C001",
            "thread_id": "test-profile-001",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["response"]  # non-empty response
    assert body["thread_id"] == "test-profile-001"
    assert "getCandidateProfile" in body["tool_calls"]


@pytest.mark.asyncio
async def test_invoke_application_status_routes_to_subagent(client: AsyncClient):
    """Application status query should route to the job_application_agent sub-agent."""
    response = await client.post(
        "/api/v1/agent/invoke",
        json={
            "message": "What is the current application status for application A001?",
            "candidate_id": "C001",
            "thread_id": "test-appstatus-001",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["response"]
    # Sub-agent should have been involved
    assert body["agent_used"] in ("job_application_agent", "candidate_primary")


@pytest.mark.asyncio
async def test_multi_turn_thread_context(client: AsyncClient):
    """Conversation context should persist across turns in the same thread."""
    thread_id = "test-multithread-001"

    # First turn: ask about a candidate
    r1 = await client.post(
        "/api/v1/agent/invoke",
        json={
            "message": "Tell me about candidate C002.",
            "candidate_id": "C002",
            "thread_id": thread_id,
        },
    )
    assert r1.status_code == 200

    # Second turn: follow-up in the same thread (context should be preserved)
    r2 = await client.post(
        "/api/v1/agent/invoke",
        json={
            "message": "What jobs match their skills?",
            "candidate_id": "C002",
            "thread_id": thread_id,
        },
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["response"]


@pytest.mark.asyncio
async def test_stream_returns_sse(client: AsyncClient):
    """Stream endpoint should return text/event-stream content type."""
    async with client.stream(
        "POST",
        "/api/v1/agent/stream",
        json={
            "message": "List all open jobs.",
            "thread_id": "test-stream-001",
        },
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        events = []
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                import json
                events.append(json.loads(line[6:]))
                if events and events[-1].get("event") == "done":
                    break

        event_types = {e.get("event") for e in events}
        assert "done" in event_types
