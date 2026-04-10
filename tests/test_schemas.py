from app.schemas import RecallRequest, HealthResponse, RememberRequest

def test_recall_request_accepts_context_messages():
    r = RecallRequest(query="test", context_messages=[{"role": "user", "content": "hello"}])
    assert len(r.context_messages) == 1

def test_recall_request_context_messages_optional():
    r = RecallRequest(query="test")
    assert r.context_messages == []

def test_remember_request_accepts_all_memory_types():
    for t in ["episodic", "fact", "preference", "procedural", "context"]:
        r = RememberRequest(content="hello", memory_type=t)
        assert r.memory_type == t

def test_health_response_fields():
    h = HealthResponse(coverage_score=0.7, freshness_score=0.8, coherence_score=0.9, utilization_score=0.5, recommendations=[])
    assert h.coverage_score == 0.7
