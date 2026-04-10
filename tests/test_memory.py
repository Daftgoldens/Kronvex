"""
Integration tests for Agent Memory Layer.
Run with: pytest tests/ -v

Requires:
- Docker running (docker compose up db)
- OPENAI_API_KEY set in .env
"""
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_create_agent(client):
    r = await client.post("/api/v1/agents", json={
        "name": "test-agent",
        "description": "A test agent",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "test-agent"
    assert "id" in data
    return data["id"]


@pytest.mark.asyncio
async def test_remember_and_recall(client):
    # Create agent
    r = await client.post("/api/v1/agents", json={"name": "memory-test-agent"})
    agent_id = r.json()["id"]

    # Store memories
    memories = [
        "The user prefers Python over JavaScript",
        "Last session: user was debugging a FastAPI authentication issue",
        "User's tech stack: FastAPI, PostgreSQL, React, Docker",
        "User is building a B2B SaaS startup in the AI infrastructure space",
    ]
    for content in memories:
        r = await client.post(f"/api/v1/agents/{agent_id}/remember", json={
            "content": content,
            "memory_type": "semantic",
        })
        assert r.status_code == 201

    # Recall relevant memories
    r = await client.post(f"/api/v1/agents/{agent_id}/recall", json={
        "query": "What programming language does the user prefer?",
        "top_k": 3,
        "threshold": 0.5,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["total_found"] >= 1
    # The Python preference should be the top result
    assert "Python" in data["results"][0]["memory"]["content"]


@pytest.mark.asyncio
async def test_inject_context(client):
    r = await client.post("/api/v1/agents", json={"name": "context-test-agent"})
    agent_id = r.json()["id"]

    await client.post(f"/api/v1/agents/{agent_id}/remember", json={
        "content": "Alice is a platinum subscriber who prefers email over phone",
        "memory_type": "semantic",
        "metadata": {"entity": "Alice", "importance": "high"}
    })

    r = await client.post(f"/api/v1/agents/{agent_id}/inject-context", json={
        "message": "I have a billing issue with my subscription",
        "threshold": 0.5,
    })
    assert r.status_code == 200
    data = r.json()
    assert "[AGENT MEMORY CONTEXT]" in data["context_block"]
    assert data["memories_used"] >= 1


@pytest.mark.asyncio
async def test_agent_not_found(client):
    fake_id = str(uuid.uuid4())
    r = await client.post(f"/api/v1/agents/{fake_id}/remember", json={
        "content": "test"
    })
    assert r.status_code == 404
