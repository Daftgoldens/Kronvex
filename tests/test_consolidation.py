from unittest.mock import MagicMock
import uuid
from app.consolidation import _cluster_memories

def _make_mem():
    m = MagicMock()
    m.id = uuid.uuid4()
    m.pinned = False
    m.is_meta = False
    return m

def test_cluster_groups_similar():
    mems = [_make_mem() for _ in range(4)]
    similar = {mems[0].id, mems[1].id, mems[2].id}
    def sim_fn(a, b): return 0.85 if {a.id, b.id}.issubset(similar) else 0.3
    clusters = _cluster_memories(mems, sim_fn)
    assert len(clusters) == 1 and len(clusters[0]) == 3

def test_cluster_skips_pinned():
    mems = [_make_mem() for _ in range(3)]
    mems[0].pinned = True
    def sim_fn(a, b): return 0.90
    clusters = _cluster_memories(mems, sim_fn)
    for c in clusters:
        assert mems[0] not in c

def test_cluster_skips_meta():
    mems = [_make_mem() for _ in range(3)]
    mems[1].is_meta = True
    def sim_fn(a, b): return 0.90
    clusters = _cluster_memories(mems, sim_fn)
    for c in clusters:
        assert mems[1] not in c
