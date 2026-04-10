import json
from app.entity_extraction import parse_extraction_response

def test_parse_valid_response():
    raw = json.dumps({"entities": [{"label": "Jean Dupont", "type": "person"}, {"label": "CEO", "type": "fact"}], "relations": [{"source": "Jean Dupont", "relation": "has_role", "target": "CEO"}]})
    result = parse_extraction_response(raw)
    assert len(result.entities) == 2
    assert result.entities[0].label == "Jean Dupont"
    assert len(result.relations) == 1
    assert result.relations[0].relation == "has_role"

def test_parse_malformed_returns_empty():
    assert parse_extraction_response("not valid json {{").entities == []

def test_parse_missing_keys_returns_empty():
    assert parse_extraction_response(json.dumps({"foo": "bar"})).entities == []

def test_parse_unknown_type_defaults_to_fact():
    raw = json.dumps({"entities": [{"label": "something", "type": "unknown_type"}], "relations": []})
    assert parse_extraction_response(raw).entities[0].entity_type == "fact"


from app.service import _merge_graph_memories
import uuid

def test_merge_graph_memories_deduplicates():
    from unittest.mock import MagicMock
    id1, id2, id3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    m1, m2, m3 = MagicMock(), MagicMock(), MagicMock()
    m1.id, m2.id, m3.id = id1, id2, id3
    vector = [(m1, 0.9), (m2, 0.7)]
    extras = [(m1, 0.5), (m3, 0.5)]  # m1 is duplicate
    merged = _merge_graph_memories(vector, extras)
    ids = [m.id for m, _ in merged]
    assert len(ids) == len(set(ids))
    assert merged[0][0].id == id1  # vector results first
    assert len(merged) == 3
