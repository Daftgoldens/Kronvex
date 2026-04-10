import asyncio
import uuid
import math
import time
import logging
from datetime import datetime, timezone, timedelta

import json

from fastapi import HTTPException
from openai import AsyncOpenAI

from sqlalchemy import select, func, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, Memory, ApiKey, ApiCall, Entity, EntityRelation
from app.schemas import (
    AgentCreate, AgentResponse,
    RememberRequest, MemoryResponse,
    RecallRequest, RecallResult, RecallResponse,
    InjectContextRequest, InjectContextResponse,
    IngestRequest, IngestResponse,
)
from app.embeddings import embed
from app.auth import check_memory_limit, check_agent_limit
from app.entity_extraction import extract_entities as _extract_entities
from app.consolidation import consolidate_agent_memories, CONSOLIDATION_TRIGGER
from app.plans import INGEST_LIMITS
from app.config import settings

logger = logging.getLogger(__name__)


async def _track(db: AsyncSession, api_key_id: uuid.UUID, agent_id: uuid.UUID | None,
                 endpoint: str, latency_ms: int, status_code: int = 200):
    """Fire-and-forget API call record. Never raises."""
    try:
        call = ApiCall(
            api_key_id=api_key_id,
            agent_id=agent_id,
            endpoint=endpoint,
            latency_ms=latency_ms,
            status_code=status_code,
        )
        db.add(call)
        await db.commit()
    except Exception:
        pass  # tracking must never break the request


async def create_agent(db: AsyncSession, data: AgentCreate, api_key: ApiKey) -> AgentResponse:
    """Crée un agent après avoir vérifié la limite du plan."""
    await check_agent_limit(db, api_key)
    agent = Agent(name=data.name, description=data.description, metadata_=data.metadata, api_key_id=api_key.id)
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return AgentResponse(id=agent.id, name=agent.name, description=agent.description,
                         metadata=agent.metadata_, created_at=agent.created_at, memory_count=0)


async def get_agent(db: AsyncSession, agent_id: uuid.UUID, api_key_id: uuid.UUID) -> Agent | None:
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.api_key_id == api_key_id))
    return result.scalar_one_or_none()


async def get_agent_with_count(db: AsyncSession, agent_id: uuid.UUID, api_key_id: uuid.UUID) -> AgentResponse | None:
    result = await db.execute(
        select(Agent, func.count(Memory.id).label("memory_count"))
        .outerjoin(Memory, (Memory.agent_id == Agent.id) & Memory.deleted_at.is_(None))
        .where(Agent.id == agent_id, Agent.api_key_id == api_key_id)
        .group_by(Agent.id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    agent, count = row
    return AgentResponse(id=agent.id, name=agent.name, description=agent.description,
                         metadata=agent.metadata_, created_at=agent.created_at, memory_count=count)


async def list_agents(db: AsyncSession, api_key_id: uuid.UUID) -> list[AgentResponse]:
    result = await db.execute(
        select(Agent, func.count(Memory.id).label("memory_count"))
        .outerjoin(Memory, (Memory.agent_id == Agent.id) & Memory.deleted_at.is_(None))
        .where(Agent.api_key_id == api_key_id)
        .group_by(Agent.id)
        .order_by(Agent.created_at.desc())
    )
    return [AgentResponse(id=a.id, name=a.name, description=a.description,
                          metadata=a.metadata_, created_at=a.created_at, memory_count=count)
            for a, count in result.all()]


async def remember(db: AsyncSession, agent_id: uuid.UUID, data: RememberRequest, api_key: ApiKey) -> MemoryResponse:
    """Stocke une mémoire avec TTL optionnel. Déduplique si similarité cosine >= 0.95."""
    t0 = time.monotonic()
    await check_memory_limit(db, api_key)
    try:
        vector = await embed(data.content)
    except Exception as e:
        raise HTTPException(status_code=503, detail={
            "error": "embedding_unavailable",
            "message": "Embedding service temporarily unavailable. Please retry.",
        }) from e
    now = datetime.now(timezone.utc)

    # ── Deduplication: skip if near-identical memory already exists ──
    dup_result = await db.execute(
        select(Memory)
        .where(
            Memory.agent_id == agent_id,
            Memory.deleted_at.is_(None),
            (1 - Memory.embedding.cosine_distance(vector)) >= 0.95,
        )
        .order_by(Memory.embedding.cosine_distance(vector))
        .limit(1)
    )
    existing = dup_result.scalar_one_or_none()
    if existing:
        await _track(db, api_key.id, agent_id, "remember_dedup", int((time.monotonic()-t0)*1000))
        result = _memory_to_schema(existing)
        result.deduplicated = True
        return result

    # ── Superseding: mark semantically overlapping memories as superseded ──
    # 0.72–0.94: high enough to be the same topic, below the dedup threshold (0.95).
    # Pinned memories are never superseded.
    overlap_result = await db.execute(
        select(Memory)
        .where(
            Memory.agent_id == agent_id,
            Memory.deleted_at.is_(None),
            Memory.superseded_at.is_(None),
            Memory.pinned.is_(False),
            (1 - Memory.embedding.cosine_distance(vector)) >= 0.72,
            (1 - Memory.embedding.cosine_distance(vector)) <  0.95,
        )
        .order_by(Memory.embedding.cosine_distance(vector))
        .limit(5)
    )
    overlapping = overlap_result.scalars().all()

    # Auto-classify memory type if caller left it at the default "episodic"
    memory_type = data.memory_type
    if memory_type == "episodic" and not data.pinned:
        memory_type = await _classify_memory_type(data.content)

    # Compute expires_at from ttl_days (pinned memories never expire)
    expires_at = None
    if data.ttl_days and not data.pinned:
        expires_at = now + timedelta(days=data.ttl_days)
    memory = Memory(
        agent_id=agent_id,
        content=data.content,
        embedding=vector,
        session_id=data.session_id,
        memory_type=memory_type,
        metadata_=data.metadata,
        expires_at=expires_at,
        pinned=data.pinned,
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    conflicts_count = 0
    if overlapping:
        for old in overlapping:
            old.superseded_at = now
            old.superseded_by = memory.id
        await db.commit()
        conflicts_count = len(overlapping)
    # Increment monthly cycle counter
    api_key.cycle_memories_used = (api_key.cycle_memories_used or 0) + 1
    await db.commit()
    await _track(db, api_key.id, agent_id, "remember", int((time.monotonic()-t0)*1000))
    asyncio.create_task(persist_entities(agent_id, memory.id, data.content))
    total_count = api_key.cycle_memories_used or 0
    if total_count > 0 and total_count % CONSOLIDATION_TRIGGER == 0:
        asyncio.create_task(consolidate_agent_memories(agent_id))
    result = _memory_to_schema(memory)
    result.conflict_detected = conflicts_count > 0
    result.conflicts_resolved = conflicts_count
    return result


# Decay inflection points per memory type (days). Larger = slower decay.
_DECAY_INFLECTION: dict[str, float] = {
    "fact":       180.0,
    "semantic":   180.0,   # legacy alias
    "preference":  60.0,
    "procedure":   90.0,
    "context":      3.0,
    "episodic":    14.0,
    "procedural":  90.0,   # legacy alias
}

def _confidence_score(similarity: float, memory: Memory, now: datetime) -> float:
    """Composite confidence: similarity×0.6 + recency×0.2 + frequency×0.2.

    recency: sigmoid with type-aware inflection point.
    frequency: log-scaled access count.
    """
    age_days = (now - memory.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 86400
    inflection = _DECAY_INFLECTION.get(memory.memory_type or "episodic", 14.0)
    recency = 1.0 / (1.0 + math.exp(0.05 * (age_days - inflection)))
    freq = min(math.log1p(memory.access_count) / math.log1p(10), 1.0)
    return round(similarity * 0.6 + recency * 0.2 + freq * 0.2, 4)


async def recall(db: AsyncSession, agent_id: uuid.UUID, data: RecallRequest, api_key_id: uuid.UUID | None = None) -> RecallResponse:
    t0 = time.monotonic()
    now = datetime.now(timezone.utc)
    try:
        query_vector = await embed(data.query)
    except Exception as e:
        raise HTTPException(status_code=503, detail={
            "error": "embedding_unavailable",
            "message": "Embedding service temporarily unavailable. Please retry.",
        }) from e
    similarity_expr = (1 - Memory.embedding.cosine_distance(query_vector)).label("similarity")
    stmt = (
        select(Memory, similarity_expr)
        .where(
            Memory.agent_id == agent_id,
            (1 - Memory.embedding.cosine_distance(query_vector)) >= data.threshold,
            # Exclude expired memories (unless pinned)
            (Memory.expires_at.is_(None) | (Memory.expires_at > now) | Memory.pinned),
            # Exclude soft-deleted memories
            Memory.deleted_at.is_(None),
            # Exclude superseded memories (replaced by a newer memory on the same topic)
            Memory.superseded_at.is_(None),
        )
        .order_by(similarity_expr.desc())
        .limit(data.top_k)
    )
    if data.session_id:
        stmt = stmt.where(Memory.session_id == data.session_id)
    if data.memory_type:
        stmt = stmt.where(Memory.memory_type == data.memory_type)
    result = await db.execute(stmt)
    rows = result.all()
    try:
        graph_extras = await _graph_recall(db, agent_id, data.query)
        rows = _merge_graph_memories(list(rows), graph_extras)
    except Exception as exc:
        logger.warning("graph recall failed (non-fatal): %s", exc)
    if data.context_messages:
        try:
            rows = await _rerank_with_context(list(rows), data.context_messages)
        except Exception as exc:
            logger.warning("Re-ranking skipped: %s", exc)
    if rows:
        memory_ids = [m.id for m, _ in rows]
        await db.execute(
            update(Memory)
            .where(Memory.id.in_(memory_ids))
            .values(last_accessed_at=now, access_count=Memory.access_count + 1)
        )
    await db.commit()
    if api_key_id:
        await _track(db, api_key_id, agent_id, "recall", int((time.monotonic()-t0)*1000))
    return RecallResponse(
        query=data.query,
        results=[
            RecallResult(
                memory=_memory_to_schema(m),
                similarity=round(float(sim), 4),
                confidence=_confidence_score(float(sim), m, now),
            )
            for m, sim in rows
        ],
        total_found=len(rows),
    )


async def inject_context(db: AsyncSession, agent_id: uuid.UUID, data: InjectContextRequest, api_key_id: uuid.UUID | None = None) -> InjectContextResponse:
    t0 = time.monotonic()
    recall_result = await recall(db, agent_id, RecallRequest(query=data.message, top_k=data.top_k, threshold=data.threshold))
    if not recall_result.results:
        if api_key_id:
            await _track(db, api_key_id, agent_id, "inject_context", int((time.monotonic()-t0)*1000))
        return InjectContextResponse(context_block="", memories_used=0, memories=[])
    lines = ["[KRONVEX CONTEXT]"]
    for r in recall_result.results:
        lines.append(f"- {r.memory.content} (confidence: {r.confidence})")
    if api_key_id:
        await _track(db, api_key_id, agent_id, "inject_context", int((time.monotonic()-t0)*1000))
    return InjectContextResponse(context_block="\n".join(lines), memories_used=len(recall_result.results), memories=recall_result.results)


async def delete_memory(db: AsyncSession, agent_id: uuid.UUID, memory_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(Memory).where(Memory.id == memory_id, Memory.agent_id == agent_id, Memory.deleted_at.is_(None))
    )
    memory = result.scalar_one_or_none()
    if memory is None:
        return False
    memory.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return True


async def delete_all_memories(db: AsyncSession, agent_id: uuid.UUID) -> int:
    result = await db.execute(
        select(Memory).where(Memory.agent_id == agent_id, Memory.deleted_at.is_(None))
    )
    memories = result.scalars().all()
    now = datetime.now(timezone.utc)
    for memory in memories:
        memory.deleted_at = now
    await db.commit()
    return len(memories)


async def bulk_delete_memories(db: AsyncSession, agent_id: uuid.UUID, memory_ids: list[uuid.UUID]) -> int:
    result = await db.execute(
        delete(Memory).where(Memory.agent_id == agent_id, Memory.id.in_(memory_ids)).returning(Memory.id)
    )
    await db.commit()
    return len(result.fetchall())


def _memory_to_schema(m: Memory) -> MemoryResponse:
    return MemoryResponse(
        id=m.id, agent_id=m.agent_id, content=m.content,
        session_id=m.session_id, memory_type=m.memory_type,
        metadata=m.metadata_, created_at=m.created_at,
        access_count=m.access_count,
        expires_at=m.expires_at,
        pinned=m.pinned,
        is_meta=m.is_meta,
        consolidation_count=m.consolidation_count,
        user_id=m.user_id,
        superseded=m.superseded_at is not None,
        superseded_by=m.superseded_by,
    )


async def persist_entities(agent_id: uuid.UUID, memory_id: uuid.UUID, content: str) -> None:
    """Extract entities from content and persist to entity tables. Never raises."""
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            result = await _extract_entities(content)
            if not result.entities:
                return
            label_to_id: dict[str, uuid.UUID] = {}
            for e in result.entities:
                entity = Entity(agent_id=agent_id, memory_id=memory_id, label=e.label, entity_type=e.entity_type)
                db.add(entity)
                await db.flush()
                label_to_id[e.label] = entity.id
            for r in result.relations:
                src_id = label_to_id.get(r.source)
                tgt_id = label_to_id.get(r.target)
                if src_id and tgt_id:
                    db.add(EntityRelation(agent_id=agent_id, source_entity_id=src_id, relation=r.relation, target_entity_id=tgt_id))
            await db.commit()
        except Exception as exc:
            logger.warning("persist_entities failed (non-fatal): %s", exc)
            await db.rollback()


def _merge_graph_memories(vector_results: list, graph_extras: list) -> list:
    """Merge graph results into vector results, deduplicating by memory id."""
    seen_ids = {m.id for m, _ in vector_results}
    merged = list(vector_results)
    for memory, sim in graph_extras:
        if memory.id not in seen_ids:
            seen_ids.add(memory.id)
            merged.append((memory, sim))
    return merged


async def _graph_recall(db: AsyncSession, agent_id: uuid.UUID, query: str) -> list:
    """Find memories via entity graph traversal on the query string."""
    from sqlalchemy import select as sa_select
    result = await _extract_entities(query)
    if not result.entities:
        return []
    labels = [e.label for e in result.entities]
    entity_rows = await db.execute(sa_select(Entity).where(Entity.agent_id == agent_id, Entity.label.in_(labels)))
    entity_ids = [e.id for e in entity_rows.scalars().all()]
    if not entity_ids:
        return []
    rel_rows = await db.execute(
        sa_select(EntityRelation).where(
            EntityRelation.agent_id == agent_id,
            (EntityRelation.source_entity_id.in_(entity_ids) | EntityRelation.target_entity_id.in_(entity_ids))
        )
    )
    related_ids: set[uuid.UUID] = set(entity_ids)
    for rel in rel_rows.scalars().all():
        related_ids.add(rel.source_entity_id)
        related_ids.add(rel.target_entity_id)
    mem_entity_rows = await db.execute(sa_select(Entity.memory_id).where(Entity.id.in_(related_ids), Entity.agent_id == agent_id).distinct())
    memory_ids = [row[0] for row in mem_entity_rows.all()]
    if not memory_ids:
        return []
    now = datetime.now(timezone.utc)
    mem_rows = await db.execute(
        sa_select(Memory).where(
            Memory.id.in_(memory_ids), Memory.agent_id == agent_id, Memory.deleted_at.is_(None),
            (Memory.expires_at.is_(None) | (Memory.expires_at > now) | Memory.pinned)
        )
    )
    return [(m, 0.5) for m in mem_rows.scalars().all()]


async def _rerank_with_context(results: list, context_messages: list[dict]) -> list:
    """Re-rank memory results using conversation context via GPT-4o-mini. Falls back on error."""
    if not context_messages or not results:
        return results
    client = AsyncOpenAI()
    memories_text = "\n".join(f"[{i}] {m.content}" for i, (m, _) in enumerate(results))
    context_text = "\n".join(f"{msg['role']}: {msg['content']}" for msg in context_messages[-5:])
    prompt = (f"Given this conversation:\n{context_text}\n\nRank these memories by relevance (most relevant first). "
              f"Return only a JSON array of indices, e.g. [2, 0, 1].\n\nMemories:\n{memories_text}")
    try:
        import json as _json
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=64,
        )
        order = _json.loads(response.choices[0].message.content.strip())
        if isinstance(order, list) and len(order) == len(results):
            return [results[i] for i in order if 0 <= i < len(results)]
    except Exception as exc:
        logger.warning("Contextual re-ranking failed (non-fatal): %s", exc)
    return results


def _compute_health(memories: list, now: datetime) -> dict:
    """Compute health scores for an agent's memory set."""
    if not memories:
        return {"coverage_score": 0.0, "freshness_score": 0.0, "coherence_score": 1.0,
                "utilization_score": 0.0, "recommendations": ["No memories stored yet. Use /remember to add context."]}
    total = len(memories)
    cutoff_30d = now - timedelta(days=30)
    recalled_recently = sum(1 for m in memories if m.last_accessed_at and m.last_accessed_at.replace(tzinfo=timezone.utc) >= cutoff_30d)
    utilization_score = round(recalled_recently / total, 4)
    total_weight = weighted_recency = 0.0
    for m in memories:
        age_days = (now - m.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 86400
        recency = 1.0 / (1.0 + math.exp(0.05 * (age_days - 30)))
        weight = 1.0 + math.log1p(m.access_count)
        weighted_recency += recency * weight
        total_weight += weight
    freshness_score = round(weighted_recency / total_weight if total_weight else 0.0, 4)
    distinct_types = len({m.memory_type for m in memories})
    coverage_score = round(min(distinct_types / 5, 1.0), 4)
    # Coherence: ratio of non-superseded active memories.
    superseded_total = sum(1 for m in memories if m.superseded_at is not None)
    coherence_score = round(1.0 - (superseded_total / total), 4)
    stale_60d = sum(1 for m in memories if m.access_count == 0 and (now - m.created_at.replace(tzinfo=timezone.utc)).days > 60)
    never_recalled = sum(1 for m in memories if m.access_count == 0)
    recs = []
    if never_recalled: recs.append(f"{never_recalled} memories have never been recalled.")
    if stale_60d: recs.append(f"{stale_60d} memories are 60+ days old and never recalled — consider archiving.")
    if superseded_total: recs.append(f"{superseded_total} memories have been superseded by newer ones.")
    if utilization_score < 0.3: recs.append("Low utilization: check your recall threshold.")
    if coverage_score < 0.4: recs.append("Low coverage: add diverse memory types for richer context.")
    return {"coverage_score": coverage_score, "freshness_score": freshness_score,
            "coherence_score": coherence_score, "utilization_score": utilization_score,
            "recommendations": recs if recs else ["Memory health looks good."]}


async def get_agent_health(db: AsyncSession, agent_id: uuid.UUID, api_key_id: uuid.UUID) -> dict | None:
    """Load active memories and compute health scores. Returns None if agent not found."""
    agent = await get_agent(db, agent_id, api_key_id)
    if agent is None:
        return None
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Memory).where(Memory.agent_id == agent_id, Memory.deleted_at.is_(None),
                             (Memory.expires_at.is_(None) | (Memory.expires_at > now) | Memory.pinned))
    )
    memories = list(result.scalars().all())

    # Auto-cleanup: soft-delete superseded memories never recalled, older than 30 days
    cutoff_supersede = now - timedelta(days=30)
    changed = False
    for m in memories:
        if (
            m.superseded_at is not None
            and m.access_count == 0
            and m.superseded_at.replace(tzinfo=timezone.utc) < cutoff_supersede
            and m.deleted_at is None
        ):
            m.deleted_at = now
            changed = True
    if changed:
        await db.commit()

    return _compute_health(memories, now)


async def get_agent_conflicts(
    db: AsyncSession,
    agent_id: uuid.UUID,
    api_key_id: uuid.UUID,
    limit: int = 50,
) -> dict | None:
    """Return superseded memories for an agent, most recent first.
    Returns None if agent not found."""
    agent = await get_agent(db, agent_id, api_key_id)
    if agent is None:
        return None
    result = await db.execute(
        select(Memory)
        .where(
            Memory.agent_id == agent_id,
            Memory.superseded_at.is_not(None),
        )
        .order_by(Memory.superseded_at.desc())
        .limit(limit)
    )
    memories = result.scalars().all()
    return {
        "agent_id": agent_id,
        "total": len(memories),
        "memories": memories,
    }


async def get_agent_graph(
    db: AsyncSession,
    agent_id: uuid.UUID,
    api_key_id: uuid.UUID,
    entity_type: str | None = None,
    search: str | None = None,
    limit: int = 200,
) -> dict | None:
    """Return entity graph for an agent.
    Returns None if agent not found.
    entity_type: filter by type (person|organization|preference|fact|procedure)
    search: filter entities by label (case-insensitive contains)
    limit: max entities returned (default 200, max 500)
    """
    agent = await get_agent(db, agent_id, api_key_id)
    if agent is None:
        return None

    entity_where = [Entity.agent_id == agent_id]
    if entity_type:
        entity_where.append(Entity.entity_type == entity_type)
    if search:
        entity_where.append(Entity.label.ilike(f"%{search}%"))

    entity_result = await db.execute(
        select(Entity)
        .where(*entity_where)
        .order_by(Entity.created_at.desc())
        .limit(min(limit, 500))
    )
    entities = entity_result.scalars().all()

    if not entities:
        return {
            "agent_id": agent_id,
            "entities": [],
            "relations": [],
            "total_entities": 0,
            "total_relations": 0,
        }

    entity_ids = [e.id for e in entities]
    relation_result = await db.execute(
        select(EntityRelation)
        .where(
            EntityRelation.agent_id == agent_id,
            EntityRelation.source_entity_id.in_(entity_ids),
        )
        .limit(1000)
    )
    relations = relation_result.scalars().all()

    return {
        "agent_id": agent_id,
        "entities": entities,
        "relations": relations,
        "total_entities": len(entities),
        "total_relations": len(relations),
    }


_TYPE_CLASSIFY_PROMPT = """Classify this memory into exactly one type.
Types: fact, preference, procedure, context, episodic
Return only the type name, nothing else.
Memory: """

async def _classify_memory_type(content: str) -> str:
    """Classify memory type via GPT-4o-mini. Falls back to 'episodic' on failure."""
    client = AsyncOpenAI()
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": _TYPE_CLASSIFY_PROMPT + content}],
            temperature=0.0, max_tokens=10,
        )
        result = response.choices[0].message.content.strip().lower()
        return result if result in {"fact", "preference", "procedure", "context", "episodic"} else "episodic"
    except Exception:
        return "episodic"


async def ingest(
    db: AsyncSession,
    agent_id: uuid.UUID,
    data: IngestRequest,
    api_key: ApiKey,
) -> IngestResponse:
    plan = api_key.plan or "demo"
    limits = INGEST_LIMITS.get(plan, INGEST_LIMITS["demo"])

    # Enforce content length
    content = data.content[: limits["max_chars"]]
    max_memories = min(data.max_memories, limits["max_memories"])

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    prompt = f"""Extract up to {max_memories} distinct, atomic facts from the following content.
Each fact must be a self-contained statement an AI agent should remember.
Return ONLY a JSON object: {{"facts": ["fact 1", "fact 2", ...]}}
Do not include duplicate or redundant facts.

Content:
{content}"""

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Memory extraction failed: {str(e)}")

    tokens_used = response.usage.total_tokens
    raw = response.choices[0].message.content

    try:
        facts = json.loads(raw).get("facts", [])
    except Exception:
        facts = []

    facts = facts[:max_memories]

    stored = []
    for fact in facts:
        if not fact or not fact.strip():
            continue
        remember_data = RememberRequest(
            content=fact.strip(),
            memory_type=data.memory_type,
            metadata={"source": data.source, "ingest": True} if data.source else {"ingest": True},
        )
        try:
            mem = await remember(db, agent_id, remember_data, api_key)
            stored.append(mem)
        except HTTPException:
            raise
        except Exception:
            continue

    return IngestResponse(
        extracted=len(stored),
        memories=stored,
        tokens_used=tokens_used,
        source=data.source,
    )

