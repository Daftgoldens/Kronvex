from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from app.service import _confidence_score

NOW = datetime.now(timezone.utc)

def _make_memory(memory_type="episodic", access_count=0, age_days=0):
    m = MagicMock()
    m.memory_type = memory_type
    m.access_count = access_count
    m.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    return m

def test_fact_decays_slower_than_episodic():
    assert _confidence_score(0.8, _make_memory("fact", age_days=60), NOW) > \
           _confidence_score(0.8, _make_memory("episodic", age_days=60), NOW)

def test_context_decays_faster_than_preference():
    assert _confidence_score(0.8, _make_memory("preference", age_days=3), NOW) > \
           _confidence_score(0.8, _make_memory("context", age_days=3), NOW)

def test_fresh_memory_scores_high():
    now = datetime.now(timezone.utc)
    assert _confidence_score(0.9, _make_memory("episodic", age_days=0), now) > 0.65

def test_old_episodic_scores_low():
    assert _confidence_score(0.7, _make_memory("episodic", age_days=90), NOW) < 0.6


import pytest
from app.service import _rerank_with_context
from unittest.mock import MagicMock, AsyncMock, patch
import uuid

@pytest.mark.asyncio
async def test_rerank_returns_same_count():
    results = []
    for i in range(3):
        m = MagicMock(); m.id = uuid.uuid4(); m.content = f"memory {i}"
        results.append((m, 0.8 - i * 0.1))
    context = [{"role": "user", "content": "tell me about project X"}]
    with patch("app.service.AsyncOpenAI") as mock_client:
        instance = mock_client.return_value
        instance.chat.completions.create = AsyncMock(return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content='[0, 2, 1]'))]
        ))
        reranked = await _rerank_with_context(results, context)
    assert len(reranked) == len(results)

@pytest.mark.asyncio
async def test_rerank_empty_context_returns_unchanged():
    results = [(MagicMock(), 0.9)]
    assert await _rerank_with_context(results, []) == results


from app.service import _compute_health

def test_compute_health_empty():
    h = _compute_health([], datetime.now(timezone.utc))
    assert h["coverage_score"] == 0.0
    assert "No memories" in h["recommendations"][0]

def test_compute_health_scores_in_range():
    now = datetime.now(timezone.utc)
    mems = []
    for i in range(5):
        m = MagicMock(); m.id = uuid.uuid4(); m.access_count = i
        m.last_accessed_at = now - timedelta(days=i * 5)
        m.created_at = now - timedelta(days=30); m.memory_type = "fact"
        mems.append(m)
    h = _compute_health(mems, now)
    assert 0.0 <= h["utilization_score"] <= 1.0
    assert 0.0 <= h["freshness_score"] <= 1.0
    assert isinstance(h["recommendations"], list)
