import uuid
import logging
import time as _time
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ApiKey
from app.auth import get_api_key

logger = logging.getLogger(__name__)
from app.schemas import (
    AgentCreate, AgentResponse,
    RememberRequest, MemoryResponse,
    RecallRequest, RecallResponse,
    InjectContextRequest, InjectContextResponse,
    BulkDeleteRequest, AgentUpdateRequest,
    BulkImportRequest, BulkImportResponse,
    MemoryRestoreResponse,
    IngestRequest, IngestResponse,
    ConflictsResponse,
    GraphResponse,
)
import app.service as svc
from app.quota_guard import check_and_increment_daily_quota, get_usage_summary

router = APIRouter(dependencies=[Depends(get_api_key)], tags=["Agents & Memories"])

# ── RATE LIMITING ──────────────────────────────────────────────────────────────
# Rate limit via DB — adds ~1 query per request but survives restarts

PLAN_RATE_LIMITS = {
    "demo":       60,
    "dev":        120,
    "starter":    300,
    "pro":        600,
    "growth":     1200,
    "scale":      None,      # unlimited
    "enterprise": None,
}

async def check_rate_limit(api_key: ApiKey, db: AsyncSession) -> None:
    from sqlalchemy import select, func
    from app.models import ApiCall
    from datetime import datetime, timezone, timedelta

    limit = PLAN_RATE_LIMITS.get(api_key.plan or "demo", 60)
    if limit is None:
        return  # unlimited

    window_start = datetime.now(timezone.utc) - timedelta(minutes=1)
    result = await db.execute(
        select(func.count(ApiCall.id))
        .where(
            ApiCall.api_key_id == api_key.id,
            ApiCall.called_at >= window_start,
        )
    )
    count = result.scalar_one()
    if count >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({limit} req/min for {api_key.plan} plan). Upgrade for higher limits.",
            headers={"Retry-After": "60", "X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"},
        )


async def _get_agent_or_404(agent_id: uuid.UUID, api_key: ApiKey, db: AsyncSession):
    agent = await svc.get_agent(db, agent_id, api_key_id=api_key.id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.post(
    "/agents",
    response_model=AgentResponse,
    status_code=201,
    summary="Create agent",
    description=(
        "Creates a new agent scoped to your API key. Each agent maintains its own isolated "
        "memory store. Your plan determines how many agents you can create (e.g. 1 on Starter, "
        "3 on Pro, 5 on Growth). Agents can represent different AI personas, users, or workflows."
    ),
)
async def create_agent(data: AgentCreate, api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    await check_rate_limit(api_key, db)
    return await svc.create_agent(db, data, api_key=api_key)


@router.get(
    "/agents",
    response_model=list[AgentResponse],
    summary="List agents",
    description="Returns all agents belonging to the authenticated API key.",
)
async def list_agents(api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    return await svc.list_agents(db, api_key_id=api_key.id)


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: uuid.UUID, api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    result = await svc.get_agent_with_count(db, agent_id, api_key.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return result


@router.post(
    "/agents/{agent_id}/remember",
    response_model=MemoryResponse,
    status_code=201,
    summary="Store a memory",
    description=(
        "Embeds and persists a memory for the agent using OpenAI text-embedding-3-small (1536 dims). "
        "Supports memory types: `episodic` (default), `semantic`, `procedural`. "
        "Optional fields: `session_id` to group memories by conversation, `ttl_days` for automatic expiry, "
        "`pinned` to prevent expiry, and a free-form `metadata` object for custom tags."
    ),
)
async def remember(agent_id: uuid.UUID, data: RememberRequest,
                   background_tasks: BackgroundTasks,
                   api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db),
                   response: Response = None):
    from sqlalchemy import select, func
    from app.models import Memory
    await check_rate_limit(api_key, db)
    await check_and_increment_daily_quota(db, str(api_key.id), api_key.plan or "demo", "remember")
    agent = await _get_agent_or_404(agent_id, api_key, db)
    result = await svc.remember(db, agent_id, data, api_key)
    # Count total memories for this agent so clients can self-throttle against quota
    total = await db.scalar(
        select(func.count(Memory.id)).where(Memory.agent_id == agent.id, Memory.deleted_at.is_(None))
    )
    result.agent_memory_count = total
    if result.deduplicated:
        if response is not None:
            response.headers["X-Dedup-Hit"] = "true"
        logger.info("memory.dedup", extra={"agent_id": str(agent_id), "memory_id": str(result.id)})
        return result
    logger.info("memory.stored", extra={"agent_id": str(agent_id), "memory_type": data.memory_type, "plan": api_key.plan})
    limit = PLAN_RATE_LIMITS.get(api_key.plan or "demo", 60)
    if response is not None and limit is not None:
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - 1))
        response.headers["X-RateLimit-Reset"] = str(int(_time.time()) + 60)
    # Fire webhook in background — non-blocking, never raises
    from app.webhooks import fire_webhook_event
    background_tasks.add_task(
        fire_webhook_event,
        "memory.stored",
        api_key.id,
        db,
        {
            "agent_id": str(agent_id),
            "memory_id": str(result.id),
            "memory_type": data.memory_type,
        },
    )
    return result


@router.post(
    "/agents/{agent_id}/ingest",
    response_model=IngestResponse,
    status_code=201,
    summary="Ingest content",
    description=(
        "Sends raw text or markdown to GPT-4o-mini, which extracts structured memories automatically. "
        "Each extracted memory is stored via the same pipeline as /remember. "
        "Quota is counted per extracted memory. "
        "Plan limits apply: Demo 5k chars/10 memories, Dev 50k/50, Starter 100k/100, Pro+ 200k/500."
    ),
)
async def ingest_content(
    agent_id: uuid.UUID,
    data: IngestRequest,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    await check_rate_limit(api_key, db)
    await _get_agent_or_404(agent_id, api_key, db)
    result = await svc.ingest(db, agent_id, data, api_key)
    logger.info(
        "memory.ingested",
        extra={"agent_id": str(agent_id), "extracted": result.extracted, "plan": api_key.plan},
    )
    return result


@router.post(
    "/agents/{agent_id}/recall",
    response_model=RecallResponse,
    summary="Semantic recall",
    description=(
        "Performs a pgvector cosine-similarity search over the agent's memories. "
        "Results are ranked by a composite confidence score: "
        "`similarity × 0.6 + recency × 0.2 + frequency × 0.2`. "
        "Recency uses a sigmoid with a 30-day inflection; frequency is log-scaled access count. "
        "Optionally filter by `session_id` or `memory_type`, and set `threshold` (0–1) to control minimum confidence."
    ),
)
async def recall(agent_id: uuid.UUID, data: RecallRequest,
                 background_tasks: BackgroundTasks,
                 api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db),
                 response: Response = None):
    await check_rate_limit(api_key, db)
    await check_and_increment_daily_quota(db, str(api_key.id), api_key.plan or "demo", "recall")
    await _get_agent_or_404(agent_id, api_key, db)
    recall_result = await svc.recall(db, agent_id, data, api_key_id=api_key.id)
    logger.info("memory.recalled", extra={"agent_id": str(agent_id), "results": recall_result.total_found})
    from app.webhooks import fire_webhook_event
    background_tasks.add_task(
        fire_webhook_event,
        "memory.recalled",
        api_key.id,
        db,
        {
            "agent_id": str(agent_id),
            "results_count": recall_result.total_found,
            "query": data.query[:200],
        },
    )
    limit = PLAN_RATE_LIMITS.get(api_key.plan or "demo", 60)
    if response is not None and limit is not None:
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - 1))
        response.headers["X-RateLimit-Reset"] = str(int(_time.time()) + 60)
    return recall_result


@router.post(
    "/agents/{agent_id}/inject-context",
    response_model=InjectContextResponse,
    summary="Inject context",
    description=(
        "Recalls the most relevant memories and formats them as a ready-to-use system prompt block. "
        "The output (`context_block`) can be prepended directly to an LLM system prompt. "
        "Memories are ranked by the same confidence scoring as `/recall`. "
        "Use `max_tokens` to cap the injected context length for your model's context window."
    ),
)
async def inject_context(agent_id: uuid.UUID, data: InjectContextRequest,
                         api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db),
                         response: Response = None):
    await check_rate_limit(api_key, db)
    await check_and_increment_daily_quota(db, str(api_key.id), api_key.plan or "demo", "inject")
    await _get_agent_or_404(agent_id, api_key, db)
    result = await svc.inject_context(db, agent_id, data, api_key_id=api_key.id)
    limit = PLAN_RATE_LIMITS.get(api_key.plan or "demo", 60)
    if response is not None and limit is not None:
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - 1))
        response.headers["X-RateLimit-Reset"] = str(int(_time.time()) + 60)
    return result


@router.delete("/agents/{agent_id}/memories/{memory_id}", status_code=204)
async def delete_memory(agent_id: uuid.UUID, memory_id: uuid.UUID,
                        background_tasks: BackgroundTasks,
                        api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    await _get_agent_or_404(agent_id, api_key, db)
    if not await svc.delete_memory(db, agent_id, memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    api_key.deleted_memories_count = (api_key.deleted_memories_count or 0) + 1
    await db.commit()
    from app.webhooks import fire_webhook_event
    background_tasks.add_task(
        fire_webhook_event,
        "memory.deleted",
        api_key.id,
        db,
        {"agent_id": str(agent_id), "memory_id": str(memory_id)},
    )


@router.post("/agents/{agent_id}/memories/{memory_id}/restore", response_model=MemoryRestoreResponse)
async def restore_memory(agent_id: uuid.UUID, memory_id: uuid.UUID,
                         api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    """Restore a soft-deleted memory. Sets deleted_at back to None."""
    from sqlalchemy import select
    from app.models import Memory
    await _get_agent_or_404(agent_id, api_key, db)
    result = await db.execute(
        select(Memory).where(Memory.id == memory_id, Memory.agent_id == agent_id)
    )
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    if memory.deleted_at is None:
        raise HTTPException(status_code=409, detail="Memory is not deleted")
    memory.deleted_at = None
    api_key.cycle_memories_used = (api_key.cycle_memories_used or 0) + 1
    await db.commit()
    return MemoryRestoreResponse(id=str(memory_id), restored=True, content=memory.content)


@router.patch("/agents/{agent_id}/memories/{memory_id}", response_model=MemoryResponse)
async def update_memory(agent_id: uuid.UUID, memory_id: uuid.UUID, data: dict,
                        api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    """Toggle pinned status or update metadata for a memory."""
    from sqlalchemy import select
    from app.models import Memory
    await _get_agent_or_404(agent_id, api_key, db)
    result = await db.execute(
        select(Memory).where(Memory.id == memory_id, Memory.agent_id == agent_id)
    )
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    if "pinned" in data:
        memory.pinned = bool(data["pinned"])
        # Pinned memories don't expire — clear expires_at when pinning
        if memory.pinned:
            memory.expires_at = None
    if "memory_type" in data:
        memory.memory_type = data["memory_type"]
    await db.commit()
    await db.refresh(memory)
    return svc._memory_to_schema(memory)


@router.delete("/agents/{agent_id}/memories", status_code=200)
async def delete_all_memories(agent_id: uuid.UUID,
                               api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    await _get_agent_or_404(agent_id, api_key, db)
    count = await svc.delete_all_memories(db, agent_id)
    api_key.deleted_memories_count = (api_key.deleted_memories_count or 0) + count
    await db.commit()
    return {"deleted": count}


@router.delete("/agents/{agent_id}/memories/bulk-delete", summary="Bulk delete memories", status_code=200)
@router.delete("/agents/{agent_id}/memories/bulk", summary="Bulk delete memories (legacy)", status_code=200, include_in_schema=False)
async def bulk_delete_memories(
    agent_id: uuid.UUID,
    body: BulkDeleteRequest,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from app.models import Memory
    from datetime import datetime, timezone
    await _get_agent_or_404(agent_id, api_key, db)

    base_where = [Memory.agent_id == agent_id, Memory.deleted_at.is_(None)]

    if body.memory_ids is not None:
        ids = [uuid.UUID(mid) for mid in body.memory_ids]
        base_where.append(Memory.id.in_(ids))
    elif body.memory_type is not None:
        base_where.append(Memory.memory_type == body.memory_type)
    elif body.before_date is not None:
        base_where.append(Memory.created_at < body.before_date)
    else:
        raise HTTPException(status_code=422, detail="Provide at least one filter: memory_ids, memory_type, or before_date.")

    result = await db.execute(select(Memory).where(*base_where))
    memories = result.scalars().all()
    now = datetime.now(timezone.utc)
    for memory in memories:
        memory.deleted_at = now
    api_key.deleted_memories_count = (api_key.deleted_memories_count or 0) + len(memories)
    await db.commit()
    return {"deleted": len(memories)}


@router.get("/agents/{agent_id}/sessions", summary="List distinct session_ids for an agent")
async def list_sessions(agent_id: uuid.UUID,
                        api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select, func
    from app.models import Memory
    await _get_agent_or_404(agent_id, api_key, db)
    result = await db.execute(
        select(Memory.session_id, func.count(Memory.id).label("count"))
        .where(Memory.agent_id == agent_id, Memory.session_id.isnot(None), Memory.deleted_at.is_(None))
        .group_by(Memory.session_id)
        .order_by(func.max(Memory.created_at).desc())
    )
    return {"sessions": [{"session_id": r.session_id, "count": r.count} for r in result.all()]}


@router.get("/agents/{agent_id}/memories", summary="List memories", tags=["Agents & Memories"])
async def list_memories(
    agent_id: uuid.UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    memory_type: str | None = Query(None),
    search: str | None = Query(None),
    sort: str = Query("recent", pattern="^(recent|oldest|access_count)$"),
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, func
    from app.models import Memory
    from datetime import datetime, timezone
    await _get_agent_or_404(agent_id, api_key, db)
    now = datetime.now(timezone.utc)

    base_where = [
        Memory.agent_id == agent_id,
        (Memory.expires_at.is_(None) | (Memory.expires_at > now) | Memory.pinned),
        Memory.deleted_at.is_(None),
    ]
    if memory_type:
        base_where.append(Memory.memory_type == memory_type)
    if search and search.strip():
        base_where.append(Memory.content.ilike(f"%{search.strip()}%"))

    if sort == "oldest":
        order = Memory.created_at.asc()
    elif sort == "access_count":
        order = Memory.access_count.desc()
    else:  # recent (default)
        order = Memory.created_at.desc()

    count_stmt = select(func.count()).select_from(Memory).where(*base_where)
    total = (await db.execute(count_stmt)).scalar() or 0

    offset = (page - 1) * per_page
    stmt = (
        select(Memory)
        .where(*base_where)
        .order_by(order)
        .limit(per_page)
        .offset(offset)
    )
    result = await db.execute(stmt)
    memories = result.scalars().all()
    pages = (total + per_page - 1) // per_page if total > 0 else 1
    return {
        "memories": [svc._memory_to_schema(m) for m in memories],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.get("/agents/{agent_id}/memories/export", summary="Export memories as JSON")
async def export_memories(
    agent_id: uuid.UUID,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from app.models import Memory
    from starlette.responses import JSONResponse
    await _get_agent_or_404(agent_id, api_key, db)
    result = await db.execute(
        select(Memory)
        .where(Memory.agent_id == agent_id, Memory.deleted_at.is_(None))
        .order_by(Memory.created_at.desc())
    )
    memories = result.scalars().all()
    payload = [svc._memory_to_schema(m).model_dump(mode="json") for m in memories]
    return JSONResponse(
        content={"agent_id": str(agent_id), "total": len(payload), "memories": payload},
        headers={"Content-Disposition": f'attachment; filename="memories-{agent_id}.json"'},
    )


@router.patch("/agents/{agent_id}", response_model=AgentResponse, summary="Update agent")
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdateRequest,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Update agent name and/or metadata."""
    agent = await _get_agent_or_404(agent_id, api_key, db)
    if body.name is not None:
        agent.name = body.name
    if body.metadata is not None:
        agent.metadata_ = body.metadata
    await db.commit()
    result = await svc.get_agent_with_count(db, agent_id, api_key.id)
    return result or AgentResponse(id=agent.id, name=agent.name, description=agent.description,
                                   metadata=agent.metadata_, created_at=agent.created_at)


@router.delete(
    "/agents/{agent_id}",
    status_code=204,
    summary="Delete agent and all its memories",
    description=(
        "Permanently deletes the agent and cascades deletion to all associated memories. "
        "This action is irreversible. The agent must belong to the authenticated API key."
    ),
)
async def delete_agent(agent_id: uuid.UUID,
                       api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from app.models import Memory
    from datetime import datetime, timezone
    agent = await _get_agent_or_404(agent_id, api_key, db)
    # Soft-delete all non-deleted memories for this agent
    mem_result = await db.execute(
        select(Memory).where(Memory.agent_id == agent.id, Memory.deleted_at.is_(None))
    )
    memories = mem_result.scalars().all()
    mem_count = len(memories)
    now = datetime.now(timezone.utc)
    for memory in memories:
        memory.deleted_at = now
    await db.delete(agent)
    api_key.deleted_memories_count = (api_key.deleted_memories_count or 0) + mem_count
    await db.commit()


# ── PHASE A INTELLIGENCE ───────────────────────────────────────────────────────

@router.post("/agents/{agent_id}/consolidate", summary="Trigger memory consolidation manually")
async def trigger_consolidation(
    agent_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Manually trigger memory consolidation for an agent (runs in background)."""
    from app.consolidation import consolidate_agent_memories
    agent = await svc.get_agent(db, agent_id, api_key.id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    background_tasks.add_task(consolidate_agent_memories, agent_id)
    return {"status": "consolidation_queued", "agent_id": str(agent_id)}


@router.get("/agents/{agent_id}/health", summary="Memory health score")
async def get_health(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Return health metrics for an agent's memory set (A5)."""
    from app.schemas import HealthResponse
    health = await svc.get_agent_health(db, agent_id, api_key.id)
    if health is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return HealthResponse(**health)


@router.get(
    "/agents/{agent_id}/conflicts",
    response_model=ConflictsResponse,
    summary="List superseded memories (conflict history)",
    description=(
        "Returns memories that were automatically superseded by newer memories on the same topic. "
        "When a new memory has cosine similarity 0.72–0.94 with an existing memory, the older one "
        "is marked superseded (last-write-wins). This endpoint exposes that history for audit and debugging. "
        "Results are ordered by superseded_at descending. Max 50 results."
    ),
)
async def get_conflicts(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Return conflict history for an agent (A3 — Conflict Detection)."""
    from app.schemas import SupersededMemory
    data = await svc.get_agent_conflicts(db, agent_id, api_key.id)
    if data is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return ConflictsResponse(
        agent_id=data["agent_id"],
        total=data["total"],
        memories=[
            SupersededMemory(
                id=m.id,
                content=m.content,
                memory_type=m.memory_type,
                superseded_at=m.superseded_at,
                superseded_by=m.superseded_by,
                created_at=m.created_at,
            )
            for m in data["memories"]
        ],
    )


@router.get(
    "/agents/{agent_id}/graph",
    response_model=GraphResponse,
    summary="Entity knowledge graph",
    description=(
        "Returns the entity knowledge graph extracted from this agent's memories. "
        "Entities are extracted automatically at write time (GPT-4o-mini, fire-and-forget). "
        "Types: person, organization, preference, fact, procedure. "
        "Filter by `entity_type` or `search` (label contains). "
        "Max 200 entities per call (use `limit` param up to 500). "
        "Relations link source → target entity via a verb phrase."
    ),
)
async def get_graph(
    agent_id: uuid.UUID,
    entity_type: str | None = Query(None, pattern="^(person|organization|preference|fact|procedure)$"),
    search: str | None = Query(None, max_length=100),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(get_api_key),
):
    """Return entity graph for an agent (A2 — Knowledge Graph)."""
    from app.schemas import EntityOut, EntityRelationOut
    data = await svc.get_agent_graph(db, agent_id, api_key.id, entity_type=entity_type, search=search, limit=limit)
    if data is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return GraphResponse(
        agent_id=data["agent_id"],
        total_entities=data["total_entities"],
        total_relations=data["total_relations"],
        entities=[
            EntityOut(
                id=e.id,
                label=e.label,
                entity_type=e.entity_type,
                memory_id=e.memory_id,
                created_at=e.created_at,
            )
            for e in data["entities"]
        ],
        relations=[
            EntityRelationOut(
                id=r.id,
                source_entity_id=r.source_entity_id,
                relation=r.relation,
                target_entity_id=r.target_entity_id,
                created_at=r.created_at,
            )
            for r in data["relations"]
        ],
    )


# ── ANALYTICS AVANCÉS ──────────────────────────────────────────────────────────

@router.get("/agents/{agent_id}/analytics", summary="Advanced analytics for an agent")
async def agent_analytics(
    agent_id: uuid.UUID,
    days: int = 30,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Top memories, session heatmap, recall frequency, memory type breakdown."""
    from sqlalchemy import select, func, cast, Date
    from app.models import Memory
    from datetime import datetime, timezone, timedelta

    agent = await _get_agent_or_404(agent_id, api_key, db)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Top memories by access_count, exclude soft-deleted
    top_result = await db.execute(
        select(Memory.id, Memory.content, Memory.memory_type,
               Memory.access_count, Memory.created_at, Memory.session_id)
        .where(Memory.agent_id == agent.id, Memory.deleted_at.is_(None))
        .order_by(Memory.access_count.desc())
        .limit(10)
    )
    top_memories = [
        {
            "id": str(r.id),
            "content": r.content[:120] + ("…" if len(r.content) > 120 else ""),
            "memory_type": r.memory_type,
            "access_count": r.access_count,
            "session_id": r.session_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in top_result.all()
    ]

    # Memory type breakdown, exclude soft-deleted
    type_result = await db.execute(
        select(Memory.memory_type, func.count(Memory.id).label("cnt"))
        .where(Memory.agent_id == agent.id, Memory.deleted_at.is_(None))
        .group_by(Memory.memory_type)
    )
    by_type = {r.memory_type: r.cnt for r in type_result.all()}

    # Sessions breakdown — top 10 by memory count, exclude soft-deleted
    session_result = await db.execute(
        select(Memory.session_id, func.count(Memory.id).label("cnt"),
               func.max(Memory.created_at).label("last_at"))
        .where(Memory.agent_id == agent.id, Memory.session_id.isnot(None), Memory.deleted_at.is_(None))
        .group_by(Memory.session_id)
        .order_by(func.count(Memory.id).desc())
        .limit(10)
    )
    sessions = [
        {"session_id": r.session_id, "memory_count": r.cnt, "last_at": r.last_at.isoformat() if r.last_at else None}
        for r in session_result.all()
    ]

    # Memories created per day (last 14 days)
    day_result = await db.execute(
        select(cast(Memory.created_at, Date).label("day"), func.count(Memory.id).label("cnt"))
        .where(Memory.agent_id == agent.id, Memory.deleted_at.is_(None),
               Memory.created_at >= datetime.now(timezone.utc) - timedelta(days=14))
        .group_by("day").order_by("day")
    )
    daily_memories = [{"date": str(r.day), "count": r.cnt} for r in day_result.all()]

    # Total memories + pinned count, exclude soft-deleted
    total_result = await db.execute(
        select(func.count(Memory.id)).where(Memory.agent_id == agent.id, Memory.deleted_at.is_(None))
    )
    total = total_result.scalar_one()

    pinned_result = await db.execute(
        select(func.count(Memory.id)).where(Memory.agent_id == agent.id, Memory.deleted_at.is_(None), Memory.pinned == True)
    )
    pinned = pinned_result.scalar_one()

    return {
        "agent_id": str(agent.id),
        "total_memories": total,
        "pinned_memories": pinned,
        "top_memories": top_memories,
        "by_type": by_type,
        "sessions": sessions,
        "daily_memories": daily_memories,
    }


@router.get("/analytics/global", summary="Global analytics across all agents")
async def global_analytics(
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Aggregated stats across all agents for this API key."""
    from sqlalchemy import select, func, cast, Date
    from app.models import Memory, Agent
    from datetime import datetime, timezone, timedelta

    agents_result = await db.execute(
        select(Agent).where(Agent.api_key_id == api_key.id)
    )
    agents_list = agents_result.scalars().all()
    agent_ids = [a.id for a in agents_list]

    if not agent_ids:
        return {"total_memories": 0, "by_type": {}, "top_memories": [], "daily_memories": [], "agents": []}

    # Total per agent — single GROUP BY query (no N+1), exclude soft-deleted
    counts_result = await db.execute(
        select(Memory.agent_id, func.count(Memory.id).label("cnt"))
        .where(Memory.agent_id.in_(agent_ids), Memory.deleted_at.is_(None))
        .group_by(Memory.agent_id)
    )
    counts_map = {str(r.agent_id): r.cnt for r in counts_result.all()}
    per_agent = [{"id": str(a.id), "name": a.name, "memory_count": counts_map.get(str(a.id), 0)} for a in agents_list]

    # Global type breakdown, exclude soft-deleted
    type_result = await db.execute(
        select(Memory.memory_type, func.count(Memory.id).label("cnt"))
        .where(Memory.agent_id.in_(agent_ids), Memory.deleted_at.is_(None))
        .group_by(Memory.memory_type)
    )
    by_type = {r.memory_type: r.cnt for r in type_result.all()}

    # Top memories globally, exclude soft-deleted
    top_result = await db.execute(
        select(Memory.id, Memory.content, Memory.memory_type, Memory.access_count, Memory.agent_id)
        .where(Memory.agent_id.in_(agent_ids), Memory.deleted_at.is_(None))
        .order_by(Memory.access_count.desc())
        .limit(10)
    )
    top_memories = [
        {"id": str(r.id), "content": r.content[:120] + ("…" if len(r.content) > 120 else ""),
         "memory_type": r.memory_type, "access_count": r.access_count, "agent_id": str(r.agent_id)}
        for r in top_result.all()
    ]

    # Daily memories (14 days)
    day_result = await db.execute(
        select(cast(Memory.created_at, Date).label("day"), func.count(Memory.id).label("cnt"))
        .where(Memory.agent_id.in_(agent_ids), Memory.deleted_at.is_(None),
               Memory.created_at >= datetime.now(timezone.utc) - timedelta(days=14))
        .group_by("day").order_by("day")
    )
    daily = [{"date": str(r.day), "count": r.cnt} for r in day_result.all()]

    # Pinned count globally
    pinned_result = await db.execute(
        select(func.count(Memory.id))
        .where(Memory.agent_id.in_(agent_ids), Memory.deleted_at.is_(None), Memory.pinned == True)
    )
    pinned_count = pinned_result.scalar_one()

    # Sessions breakdown — top 10 globally by memory count
    session_result = await db.execute(
        select(Memory.session_id, func.count(Memory.id).label("cnt"),
               func.max(Memory.created_at).label("last_at"))
        .where(Memory.agent_id.in_(agent_ids), Memory.session_id.isnot(None), Memory.deleted_at.is_(None))
        .group_by(Memory.session_id)
        .order_by(func.count(Memory.id).desc())
        .limit(10)
    )
    sessions = [
        {"session_id": r.session_id, "memory_count": r.cnt, "last_at": r.last_at.isoformat() if r.last_at else None}
        for r in session_result.all()
    ]

    return {
        "total_memories": sum(a["memory_count"] for a in per_agent),
        "pinned_memories": pinned_count,
        "sessions": sessions,
        "by_type": by_type,
        "top_memories": top_memories,
        "daily_memories": daily,
        "agents": per_agent,
    }


@router.get("/usage/today")
async def get_today_usage(api_key: ApiKey = Depends(get_api_key), db: AsyncSession = Depends(get_db)):
    """Returns today's API usage with quota info. Used by dashboard."""
    return await get_usage_summary(db, str(api_key.id), api_key.plan or "demo")


@router.get("/stats/weekly", summary="Weekly activity stats for the last 14 days")
async def get_weekly_stats(
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Returns per-day memory storage counts for the last 14 days, plus totals."""
    from sqlalchemy import select, func, cast, Date
    from app.models import Memory, Agent
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=14)

    # Build date series for last 14 days (today included)
    date_series = [(now - timedelta(days=i)).date() for i in range(13, -1, -1)]

    # Get all agent IDs for this API key
    agents_result = await db.execute(
        select(Agent.id).where(Agent.api_key_id == api_key.id)
    )
    agent_ids = [row[0] for row in agents_result.all()]

    # Memories stored per day (last 14 days)
    if agent_ids:
        day_result = await db.execute(
            select(cast(Memory.created_at, Date).label("day"), func.count(Memory.id).label("cnt"))
            .where(
                Memory.agent_id.in_(agent_ids),
                Memory.created_at >= cutoff,
            )
            .group_by("day")
            .order_by("day")
        )
        counts_by_day = {str(r.day): r.cnt for r in day_result.all()}
    else:
        counts_by_day = {}

    # Recalls per day from ApiCall table
    from app.models import ApiCall
    recalls_result = await db.execute(
        select(cast(ApiCall.called_at, Date).label("day"), func.count(ApiCall.id).label("cnt"))
        .where(
            ApiCall.api_key_id == api_key.id,
            ApiCall.endpoint == "recall",
            ApiCall.called_at >= cutoff,
        )
        .group_by("day")
        .order_by("day")
    )
    recalls_by_day = {str(r.day): r.cnt for r in recalls_result.all()}

    last_14_days = [
        {
            "date": str(d),
            "memories_stored": counts_by_day.get(str(d), 0),
            "recalls": recalls_by_day.get(str(d), 0),
        }
        for d in date_series
    ]

    # Total memories across all agents, exclude soft-deleted
    total_memories = 0
    if agent_ids:
        total_result = await db.execute(
            select(func.count(Memory.id)).where(Memory.agent_id.in_(agent_ids), Memory.deleted_at.is_(None))
        )
        total_memories = total_result.scalar_one() or 0

    return {
        "last_14_days": last_14_days,
        "total_memories": total_memories,
        "total_agents": len(agent_ids),
    }


# ── BULK IMPORT ────────────────────────────────────────────────────────────────

@router.post(
    "/agents/{agent_id}/memories/bulk-import",
    response_model=BulkImportResponse,
    status_code=200,
    summary="Bulk import memories",
    description=(
        "Import up to 100 memories in a single request. Each memory is embedded and persisted. "
        "Returns counts of successful imports and failures with per-item error messages."
    ),
)
async def bulk_import_memories(
    agent_id: uuid.UUID,
    body: BulkImportRequest,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    from app.models import Memory
    from app.embeddings import embed
    from datetime import datetime, timezone, timedelta

    if len(body.memories) > 100:
        raise HTTPException(status_code=422, detail="Maximum 100 memories per bulk import.")

    await check_rate_limit(api_key, db)
    agent = await _get_agent_or_404(agent_id, api_key, db)

    imported = 0
    failed = 0
    errors: list[str] = []

    for idx, item in enumerate(body.memories):
        try:
            vector = await embed(item.content)
            now = datetime.now(timezone.utc)
            expires_at = None
            if item.ttl_days:
                expires_at = now + timedelta(days=item.ttl_days)
            memory = Memory(
                agent_id=agent.id,
                content=item.content,
                embedding=vector,
                memory_type=item.memory_type,
                metadata_=item.metadata,
                expires_at=expires_at,
            )
            db.add(memory)
            await db.flush()
            imported += 1
        except Exception as exc:
            failed += 1
            errors.append(f"Item {idx}: {exc}")

    if imported > 0:
        api_key.cycle_memories_used = (api_key.cycle_memories_used or 0) + imported
        await db.commit()
        # Fire webhook for each stored memory would be too noisy — fire one summary event
        try:
            from app.webhooks import fire_webhook_event
            await fire_webhook_event("memory.stored", api_key.id, db, {
                "agent_id": str(agent.id),
                "bulk": True,
                "count": imported,
            })
        except Exception:
            pass

    return BulkImportResponse(imported=imported, failed=failed, errors=errors)


# ── CLEANUP EXPIRED (admin) ────────────────────────────────────────────────────

# This router has Depends(get_api_key) at the router level, so we need a separate
# admin-only router or just check the admin key manually inside the endpoint.
# We use a plain APIRouter without the global api_key dependency for this endpoint.
_admin_router = APIRouter(tags=["Admin"])


@_admin_router.delete(
    "/cleanup-expired",
    summary="Delete all expired memories (admin)",
    description=(
        "Deletes all memories whose `expires_at` is in the past (and are not pinned). "
        "Requires `X-Admin-Key` header matching the `ADMIN_KEY` environment variable."
    ),
)
async def cleanup_expired_memories(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    import os
    from sqlalchemy import delete as sql_delete, select as sql_select
    from app.models import Memory, Agent as AgentModel
    from datetime import datetime, timezone

    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key:
        raise HTTPException(status_code=503, detail="Admin key not configured on server.")
    provided = request.headers.get("X-Admin-Key", "")
    if provided != admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key.")

    now = datetime.now(timezone.utc)

    # Fetch expired memories with their agent's api_key_id for webhook dispatch
    rows_result = await db.execute(
        sql_select(Memory.id, Memory.agent_id, AgentModel.api_key_id)
        .join(AgentModel, Memory.agent_id == AgentModel.id)
        .where(
            Memory.expires_at.isnot(None),
            Memory.expires_at <= now,
            Memory.pinned == False,
        )
    )
    rows = rows_result.all()

    if not rows:
        return {"deleted": 0}

    memory_ids = [r[0] for r in rows]

    # Bulk delete
    result = await db.execute(
        sql_delete(Memory).where(Memory.id.in_(memory_ids))
    )
    await db.commit()
    deleted = result.rowcount

    # Fire memory.expired once per api_key_id with a count
    from app.webhooks import fire_webhook_event
    from collections import defaultdict
    by_key: dict = defaultdict(int)
    for _, _agent_id_row, api_key_id in rows:
        by_key[api_key_id] += 1
    for api_key_id, count in by_key.items():
        background_tasks.add_task(
            fire_webhook_event,
            "memory.expired",
            api_key_id,
            db,
            {"expired_count": count},
        )

    return {"deleted": deleted}


@_admin_router.delete(
    "/purge-cancelled-accounts",
    summary="Purge data for accounts cancelled 30+ days ago (RGPD)",
    description=(
        "Hard-deletes all agents and memories for API keys where data_purge_at <= now. "
        "Requires X-Admin-Key header. Run daily via external cron."
    ),
)
async def purge_cancelled_accounts(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    import os
    from sqlalchemy import select, delete as sql_delete
    from app.models import Agent, Memory
    from datetime import datetime, timezone

    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key:
        raise HTTPException(status_code=503, detail="Admin key not configured on server.")
    provided = request.headers.get("X-Admin-Key", "")
    if provided != admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key.")

    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(ApiKey).where(
            ApiKey.data_purge_at.isnot(None),
            ApiKey.data_purge_at <= now,
        )
    )
    keys = result.scalars().all()

    purged_keys = 0
    purged_agents = 0
    purged_memories = 0

    for key in keys:
        agents_result = await db.execute(
            select(Agent).where(Agent.api_key_id == key.id)
        )
        agents = agents_result.scalars().all()
        agent_ids = [a.id for a in agents]

        if agent_ids:
            mem_result = await db.execute(
                sql_delete(Memory).where(Memory.agent_id.in_(agent_ids)).returning(Memory.id)
            )
            purged_memories += len(mem_result.fetchall())

        await db.execute(sql_delete(Agent).where(Agent.api_key_id == key.id))
        purged_agents += len(agents)

        key.data_purge_at = None
        key.is_active = False
        purged_keys += 1

    await db.commit()
    logger.info(f"[PURGE] Purged {purged_keys} accounts, {purged_agents} agents, {purged_memories} memories")
    return {"purged_keys": purged_keys, "purged_agents": purged_agents, "purged_memories": purged_memories}


# ── GDPR — RIGHT TO ERASURE (Art. 17) ─────────────────────────────────────────

@router.delete(
    "/agents/{agent_id}/memories/user/{user_id}",
    status_code=200,
    summary="GDPR right to erasure — delete all memories for a user",
    description=(
        "Soft-deletes all memories associated with `user_id` (mapped to `session_id`) for the given agent. "
        "Implements GDPR Article 17 — right to erasure. Returns the count of deleted memories and a timestamp. "
        "Use this to honour a user's deletion request within your AI product."
    ),
)
async def gdpr_erase_user_memories(
    agent_id: uuid.UUID,
    user_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, or_
    from app.models import Memory
    from datetime import datetime, timezone

    await _get_agent_or_404(agent_id, api_key, db)
    result = await db.execute(
        select(Memory).where(
            Memory.agent_id == agent_id,
            Memory.deleted_at.is_(None),
            or_(
                Memory.user_id == user_id,
                Memory.session_id == user_id,
            ),
        )
    )
    memories = result.scalars().all()
    now = datetime.now(timezone.utc)
    for memory in memories:
        memory.deleted_at = now
    count = len(memories)
    api_key.deleted_memories_count = (api_key.deleted_memories_count or 0) + count
    await db.commit()
    logger.info("gdpr.erasure", extra={"agent_id": str(agent_id), "user_id": user_id, "deleted": count})
    return {
        "erased": count,
        "user_id": user_id,
        "agent_id": str(agent_id),
        "erased_at": now.isoformat(),
        "gdpr_article": "Art. 17 — Right to erasure",
    }


# ── GDPR — RIGHT TO DATA PORTABILITY (Art. 20) ────────────────────────────────

@router.get(
    "/agents/{agent_id}/memories/user/{user_id}/export",
    summary="GDPR right to data portability — export all memories for a user",
    description=(
        "Returns all non-deleted memories associated with `user_id` as a portable JSON array. "
        "Implements GDPR Article 20 — right to data portability. "
        "Each memory includes content, metadata, creation date, and session context. "
        "Use this to honour a user's data export request within your AI product."
    ),
)
async def gdpr_export_user_memories(
    agent_id: uuid.UUID,
    user_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, or_
    from app.models import Memory
    from datetime import datetime, timezone

    await _get_agent_or_404(agent_id, api_key, db)
    result = await db.execute(
        select(Memory).where(
            Memory.agent_id == agent_id,
            Memory.deleted_at.is_(None),
            or_(
                Memory.user_id == user_id,
                Memory.session_id == user_id,
            ),
        ).order_by(Memory.created_at)
    )
    memories = result.scalars().all()
    now = datetime.now(timezone.utc)
    return {
        "user_id": user_id,
        "agent_id": str(agent_id),
        "exported_at": now.isoformat(),
        "gdpr_article": "Art. 20 — Right to data portability",
        "memory_count": len(memories),
        "memories": [
            {
                "id": str(m.id),
                "content": m.content,
                "memory_type": m.memory_type,
                "session_id": m.session_id,
                "user_id": m.user_id,
                "metadata": m.metadata_,
                "created_at": m.created_at.isoformat(),
                "last_accessed_at": m.last_accessed_at.isoformat() if m.last_accessed_at else None,
                "access_count": m.access_count,
                "pinned": m.pinned,
            }
            for m in memories
        ],
    }


# ── GDPR — AUDIT LOG ───────────────────────────────────────────────────────────

@router.get(
    "/agents/{agent_id}/audit",
    summary="GDPR audit log — memory access and activity report",
    description=(
        "Returns a structured audit report for the agent: memory stats, API activity, "
        "session breakdown, and GDPR compliance metadata. "
        "Exportable as JSON for DPO review. Available on Pro plan and above."
    ),
)
async def audit_log(
    agent_id: uuid.UUID,
    days: int = Query(30, ge=1, le=365),
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, func, Integer
    from app.models import Memory, ApiCall
    from datetime import datetime, timezone, timedelta

    AUDIT_PLANS = {"pro", "growth", "scale", "enterprise"}
    if (api_key.plan or "demo") not in AUDIT_PLANS:
        raise HTTPException(
            status_code=403,
            detail="Audit log requires Pro plan or above. Upgrade at https://kronvex.io/pricing",
        )

    agent = await _get_agent_or_404(agent_id, api_key, db)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    # Memory stats
    mem_result = await db.execute(
        select(
            func.count(Memory.id).label("total"),
            func.sum(Memory.pinned.cast(Integer)).label("pinned"),
            func.count(Memory.deleted_at).label("deleted"),
        ).where(Memory.agent_id == agent_id)
    )
    mem_row = mem_result.one()

    type_result = await db.execute(
        select(Memory.memory_type, func.count(Memory.id).label("cnt"))
        .where(Memory.agent_id == agent_id, Memory.deleted_at.is_(None))
        .group_by(Memory.memory_type)
    )
    by_type = {r.memory_type: r.cnt for r in type_result.all()}

    # Session breakdown (users seen)
    session_result = await db.execute(
        select(Memory.session_id, func.count(Memory.id).label("cnt"))
        .where(Memory.agent_id == agent_id, Memory.session_id.isnot(None), Memory.deleted_at.is_(None))
        .group_by(Memory.session_id)
        .order_by(func.count(Memory.id).desc())
        .limit(20)
    )
    sessions = [{"user_id": r.session_id, "memory_count": r.cnt} for r in session_result.all()]

    # API activity
    call_result = await db.execute(
        select(
            ApiCall.endpoint,
            func.count(ApiCall.id).label("cnt"),
            func.avg(ApiCall.latency_ms).label("avg_latency"),
        )
        .where(ApiCall.agent_id == agent_id, ApiCall.called_at >= cutoff)
        .group_by(ApiCall.endpoint)
    )
    activity = {
        r.endpoint: {"calls": r.cnt, "avg_latency_ms": round(float(r.avg_latency or 0), 1)}
        for r in call_result.all()
    }

    last_call_result = await db.execute(
        select(func.max(ApiCall.called_at)).where(ApiCall.agent_id == agent_id)
    )
    last_activity_at = last_call_result.scalar_one_or_none()

    return {
        "agent_id": str(agent_id),
        "agent_name": agent.name,
        "report_generated_at": now.isoformat(),
        "period_days": days,
        "memory_stats": {
            "total_active": (mem_row.total or 0) - (mem_row.deleted or 0),
            "total_deleted": mem_row.deleted or 0,
            "pinned": mem_row.pinned or 0,
            "by_type": by_type,
        },
        "user_sessions": {
            "distinct_users": len(sessions),
            "breakdown": sessions,
        },
        "api_activity": activity,
        "last_activity_at": last_activity_at.isoformat() if last_activity_at else None,
        "compliance": {
            "data_residency": "EU — Paris, France",
            "gdpr_compliant": True,
            "right_to_erasure": f"DELETE /api/v1/agents/{agent_id}/memories/user/{{user_id}}",
            "data_export": f"GET /api/v1/agents/{agent_id}/memories/export",
            "encryption": "AES-256 at rest · TLS 1.3 in transit",
        },
    }
