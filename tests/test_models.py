from app.models import Memory, Entity, EntityRelation

def test_memory_has_consolidation_fields():
    cols = {c.key for c in Memory.__table__.columns}
    assert "is_meta" in cols
    assert "consolidation_count" in cols
    assert "consolidated_from" in cols
    assert "user_id" in cols

def test_entity_model_exists():
    cols = {c.key for c in Entity.__table__.columns}
    assert "label" in cols
    assert "entity_type" in cols
    assert "memory_id" in cols
    assert "updated_at" in cols

def test_entity_relation_model_exists():
    cols = {c.key for c in EntityRelation.__table__.columns}
    assert "relation" in cols
    assert "source_entity_id" in cols
    assert "target_entity_id" in cols
