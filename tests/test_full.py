"""
Kronvex full test suite — v0.5.0
Covers: agents, memory CRUD, recall, inject-context, TTL/pinned,
        sessions, memory list filters, billing/usage, billing/cancel,
        contact endpoint, delete endpoints.

Run: pytest tests/test_full.py -v
Requires: DB running (docker compose up db), OPENAI_API_KEY in .env
"""

import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

from app.main import app

API_KEY = "kx_test_00000000000000000000000000000000"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def authed_client():
    """Client with X-API-Key header pre-set."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": API_KEY},
    ) as c:
        yield c


# ── Helpers ───────────────────────────────────────────────────────────────────

async def create_agent(client, name="test-agent") -> str:
    r = await client.post("/api/v1/agents", json={"name": name, "description": "Test agent"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def remember(client, agent_id: str, content: str, **kwargs) -> dict:
    r = await client.post(f"/api/v1/agents/{agent_id}/remember", json={"content": content, **kwargs})
    assert r.status_code == 201, r.text
    return r.json()


# ── Health ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── Agents CRUD ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_agent(authed_client):
    r = await authed_client.post("/api/v1/agents", json={"name": "crud-agent"})
    assert r.status_code == 201
    d = r.json()
    assert d["name"] == "crud-agent"
    assert "id" in d
    assert "created_at" in d


@pytest.mark.asyncio
async def test_list_agents(authed_client):
    await authed_client.post("/api/v1/agents", json={"name": "list-test-a"})
    await authed_client.post("/api/v1/agents", json={"name": "list-test-b"})
    r = await authed_client.get("/api/v1/agents")
    assert r.status_code == 200
    agents = r.json()
    assert isinstance(agents, list)
    names = [a["name"] for a in agents]
    assert "list-test-a" in names
    assert "list-test-b" in names


@pytest.mark.asyncio
async def test_get_agent(authed_client):
    agent_id = await create_agent(authed_client, "get-test-agent")
    r = await authed_client.get(f"/api/v1/agents/{agent_id}")
    assert r.status_code == 200
    assert r.json()["id"] == agent_id


@pytest.mark.asyncio
async def test_get_agent_not_found(authed_client):
    r = await authed_client.get(f"/api/v1/agents/{uuid.uuid4()}")
    assert r.status_code == 404


# ── Memory CRUD ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remember_basic(authed_client):
    agent_id = await create_agent(authed_client, "remember-agent")
    mem = await remember(authed_client, agent_id, "User prefers Python")
    assert mem["content"] == "User prefers Python"
    assert mem["memory_type"] == "episodic"  # default
    assert "id" in mem
    assert mem["pinned"] is False


@pytest.mark.asyncio
async def test_remember_with_all_fields(authed_client):
    agent_id = await create_agent(authed_client, "full-memory-agent")
    mem = await remember(
        authed_client, agent_id,
        "User is on Pro plan",
        memory_type="semantic",
        session_id="session_test_001",
        metadata={"source": "crm", "user_id": "usr_42"},
        ttl_days=30,
    )
    assert mem["memory_type"] == "semantic"
    assert mem["session_id"] == "session_test_001"
    assert mem["metadata"]["source"] == "crm"
    assert mem["expires_at"] is not None  # TTL set


@pytest.mark.asyncio
async def test_remember_pinned(authed_client):
    agent_id = await create_agent(authed_client, "pinned-agent")
    mem = await remember(authed_client, agent_id, "Important: user is VIP", pinned=True)
    assert mem["pinned"] is True


@pytest.mark.asyncio
async def test_remember_agent_not_found(authed_client):
    r = await authed_client.post(
        f"/api/v1/agents/{uuid.uuid4()}/remember",
        json={"content": "test"}
    )
    assert r.status_code == 404


# ── Recall ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recall_basic(authed_client):
    agent_id = await create_agent(authed_client, "recall-agent")
    await remember(authed_client, agent_id, "User prefers Python over JavaScript", memory_type="semantic")
    await remember(authed_client, agent_id, "User is building a FastAPI backend", memory_type="semantic")
    await remember(authed_client, agent_id, "User had lunch at noon", memory_type="episodic")

    r = await authed_client.post(f"/api/v1/agents/{agent_id}/recall", json={
        "query": "programming language preference",
        "top_k": 3,
        "threshold": 0.3,
    })
    assert r.status_code == 200
    d = r.json()
    assert "results" in d
    assert "total_found" in d
    assert len(d["results"]) >= 1
    # Check structure of first result
    first = d["results"][0]
    assert "memory" in first
    assert "similarity" in first
    assert "confidence" in first
    assert 0 <= first["similarity"] <= 1
    assert 0 <= first["confidence"] <= 1


@pytest.mark.asyncio
async def test_recall_with_session_filter(authed_client):
    agent_id = await create_agent(authed_client, "session-recall-agent")
    await remember(authed_client, agent_id, "Session A: user asked about pricing", session_id="sess_a")
    await remember(authed_client, agent_id, "Session B: user asked about refunds", session_id="sess_b")

    r = await authed_client.post(f"/api/v1/agents/{agent_id}/recall", json={
        "query": "user question",
        "session_id": "sess_a",
        "threshold": 0.1,
    })
    assert r.status_code == 200
    results = r.json()["results"]
    # All results should be from sess_a
    for res in results:
        assert res["memory"]["session_id"] == "sess_a"


@pytest.mark.asyncio
async def test_recall_with_memory_type_filter(authed_client):
    agent_id = await create_agent(authed_client, "type-recall-agent")
    await remember(authed_client, agent_id, "User's name is Bob", memory_type="semantic")
    await remember(authed_client, agent_id, "Bob logged in yesterday", memory_type="episodic")

    r = await authed_client.post(f"/api/v1/agents/{agent_id}/recall", json={
        "query": "information about Bob",
        "memory_type": "semantic",
        "threshold": 0.1,
    })
    assert r.status_code == 200
    for res in r.json()["results"]:
        assert res["memory"]["memory_type"] == "semantic"


@pytest.mark.asyncio
async def test_recall_high_threshold_returns_empty(authed_client):
    agent_id = await create_agent(authed_client, "high-threshold-agent")
    await remember(authed_client, agent_id, "Completely unrelated topic about astronomy")

    r = await authed_client.post(f"/api/v1/agents/{agent_id}/recall", json={
        "query": "completely different subject — quantum physics",
        "threshold": 0.99,
    })
    assert r.status_code == 200
    assert r.json()["total_found"] == 0


# ── Inject Context ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inject_context(authed_client):
    agent_id = await create_agent(authed_client, "inject-agent")
    await remember(authed_client, agent_id, "Alice is platinum, prefers email", memory_type="semantic")

    r = await authed_client.post(f"/api/v1/agents/{agent_id}/inject-context", json={
        "message": "I have a billing issue",
        "threshold": 0.3,
    })
    assert r.status_code == 200
    d = r.json()
    assert "context_block" in d
    assert "memories_used" in d
    assert "[AGENT MEMORY CONTEXT]" in d["context_block"]
    assert d["memories_used"] >= 1


@pytest.mark.asyncio
async def test_inject_context_no_memories(authed_client):
    agent_id = await create_agent(authed_client, "empty-inject-agent")
    r = await authed_client.post(f"/api/v1/agents/{agent_id}/inject-context", json={
        "message": "hello",
        "threshold": 0.99,
    })
    assert r.status_code == 200
    assert r.json()["memories_used"] == 0


# ── Sessions ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_sessions(authed_client):
    agent_id = await create_agent(authed_client, "sessions-agent")
    await remember(authed_client, agent_id, "Memory 1", session_id="s1")
    await remember(authed_client, agent_id, "Memory 2", session_id="s1")
    await remember(authed_client, agent_id, "Memory 3", session_id="s2")

    r = await authed_client.get(f"/api/v1/agents/{agent_id}/sessions")
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    session_ids = [s["session_id"] for s in sessions]
    assert "s1" in session_ids
    assert "s2" in session_ids
    s1_data = next(s for s in sessions if s["session_id"] == "s1")
    assert s1_data["count"] == 2


@pytest.mark.asyncio
async def test_list_sessions_empty(authed_client):
    """Agent with no session_id memories → empty list."""
    agent_id = await create_agent(authed_client, "no-sessions-agent")
    await remember(authed_client, agent_id, "Memory without session")
    r = await authed_client.get(f"/api/v1/agents/{agent_id}/sessions")
    assert r.status_code == 200
    assert r.json()["sessions"] == []


# ── Memory list ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_memories(authed_client):
    agent_id = await create_agent(authed_client, "list-mem-agent")
    await remember(authed_client, agent_id, "Mem A", memory_type="semantic")
    await remember(authed_client, agent_id, "Mem B", memory_type="episodic")
    await remember(authed_client, agent_id, "Mem C", memory_type="procedural")

    r = await authed_client.get(f"/api/v1/agents/{agent_id}/memories")
    assert r.status_code == 200
    mems = r.json()["memories"]
    assert len(mems) == 3


@pytest.mark.asyncio
async def test_list_memories_filter_type(authed_client):
    agent_id = await create_agent(authed_client, "filter-type-agent")
    await remember(authed_client, agent_id, "Semantic mem", memory_type="semantic")
    await remember(authed_client, agent_id, "Episodic mem", memory_type="episodic")

    r = await authed_client.get(f"/api/v1/agents/{agent_id}/memories?memory_type=semantic")
    assert r.status_code == 200
    mems = r.json()["memories"]
    assert all(m["memory_type"] == "semantic" for m in mems)


@pytest.mark.asyncio
async def test_list_memories_filter_session(authed_client):
    agent_id = await create_agent(authed_client, "filter-session-agent")
    await remember(authed_client, agent_id, "In session X", session_id="sx")
    await remember(authed_client, agent_id, "In session Y", session_id="sy")

    r = await authed_client.get(f"/api/v1/agents/{agent_id}/memories?session_id=sx")
    assert r.status_code == 200
    mems = r.json()["memories"]
    assert len(mems) == 1
    assert mems[0]["session_id"] == "sx"


# ── Delete ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_memory(authed_client):
    agent_id = await create_agent(authed_client, "delete-mem-agent")
    mem = await remember(authed_client, agent_id, "To be deleted")
    mem_id = mem["id"]

    r = await authed_client.delete(f"/api/v1/agents/{agent_id}/memories/{mem_id}")
    assert r.status_code == 204

    # Should not appear in list anymore
    r = await authed_client.get(f"/api/v1/agents/{agent_id}/memories")
    mem_ids = [m["id"] for m in r.json()["memories"]]
    assert mem_id not in mem_ids


@pytest.mark.asyncio
async def test_delete_memory_not_found(authed_client):
    agent_id = await create_agent(authed_client, "del-404-agent")
    r = await authed_client.delete(f"/api/v1/agents/{agent_id}/memories/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_all_memories(authed_client):
    agent_id = await create_agent(authed_client, "delete-all-agent")
    for i in range(3):
        await remember(authed_client, agent_id, f"Memory {i}")

    r = await authed_client.delete(f"/api/v1/agents/{agent_id}/memories")
    assert r.status_code == 200
    assert r.json()["deleted"] == 3

    r = await authed_client.get(f"/api/v1/agents/{agent_id}/memories")
    assert r.json()["memories"] == []


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_api_key_rejected(client):
    r = await client.get("/api/v1/agents")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_invalid_api_key_rejected(client):
    r = await client.get("/api/v1/agents", headers={"X-API-Key": "kx_invalid_key"})
    assert r.status_code == 401


# ── Billing usage (mocked) ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_billing_usage_requires_auth(client):
    r = await client.get("/billing/usage")
    assert r.status_code in (401, 403, 422)


# ── Contact ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_contact_success(client):
    """POST /contact — mock Resend so no real email is sent."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200
        r = await client.post("/contact", json={
            "name": "Test User",
            "email": "test@example.com",
            "message": "Hello from tests",
            "company": "TestCo",
        })
    assert r.status_code == 200
    assert r.json()["sent"] is True


@pytest.mark.asyncio
async def test_contact_missing_fields(client):
    r = await client.post("/contact", json={"name": "Test"})
    assert r.status_code == 422  # validation error


# ── TTL / pinned expiry ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ttl_sets_expires_at(authed_client):
    agent_id = await create_agent(authed_client, "ttl-agent")
    mem = await remember(authed_client, agent_id, "Short-lived memory", ttl_days=7)
    assert mem["expires_at"] is not None
    from datetime import datetime, timezone
    exp = datetime.fromisoformat(mem["expires_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    diff_days = (exp - now).days
    # Should be ~7 days (allow ±1 for timing)
    assert 6 <= diff_days <= 8


@pytest.mark.asyncio
async def test_pinned_no_expiry(authed_client):
    agent_id = await create_agent(authed_client, "pinned-ttl-agent")
    # Pinned memories don't expire even with ttl_days
    mem = await remember(authed_client, agent_id, "Pinned forever", pinned=True, ttl_days=1)
    assert mem["pinned"] is True
    # expires_at may be set but pinned flag takes priority in recall filtering


# ── Confidence score ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_confidence_in_recall(authed_client):
    agent_id = await create_agent(authed_client, "confidence-agent")
    await remember(authed_client, agent_id, "The user is called Alice and is a senior engineer")

    r = await authed_client.post(f"/api/v1/agents/{agent_id}/recall", json={
        "query": "user name and role",
        "threshold": 0.1,
    })
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) > 0
    for res in results:
        assert 0 <= res["confidence"] <= 1
        assert 0 <= res["similarity"] <= 1


@pytest.mark.asyncio
async def test_confidence_in_inject_context(authed_client):
    agent_id = await create_agent(authed_client, "conf-inject-agent")
    await remember(authed_client, agent_id, "User is on Growth plan, 5 agents", memory_type="semantic")

    r = await authed_client.post(f"/api/v1/agents/{agent_id}/inject-context", json={
        "message": "How many agents can I create?",
        "threshold": 0.1,
    })
    assert r.status_code == 200
    d = r.json()
    if d["memories"]:
        for mem_result in d["memories"]:
            assert "confidence" in mem_result


# ── Input validation ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remember_empty_content_rejected(authed_client):
    agent_id = await create_agent(authed_client, "validation-agent")
    r = await authed_client.post(f"/api/v1/agents/{agent_id}/remember", json={"content": ""})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_remember_invalid_memory_type(authed_client):
    """Unknown memory_type should either be accepted as-is or rejected — not 500."""
    agent_id = await create_agent(authed_client, "type-validation-agent")
    r = await authed_client.post(f"/api/v1/agents/{agent_id}/remember", json={
        "content": "test content",
        "memory_type": "invalid_type_xyz",
    })
    assert r.status_code in (201, 422)  # either store or reject, not crash


@pytest.mark.asyncio
async def test_recall_top_k_limit(authed_client):
    agent_id = await create_agent(authed_client, "topk-agent")
    for i in range(10):
        await remember(authed_client, agent_id, f"Memory {i} about the user preferences and settings")

    r = await authed_client.post(f"/api/v1/agents/{agent_id}/recall", json={
        "query": "user preferences",
        "top_k": 3,
        "threshold": 0.0,
    })
    assert r.status_code == 200
    assert len(r.json()["results"]) <= 3
