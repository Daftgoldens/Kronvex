"""
Memory consolidation — A1.
Finds semantic clusters of similar memories and merges them into meta-memories.
Pinned and is_meta memories are never merged.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Callable

from openai import AsyncOpenAI
from sqlalchemy import select

from app.embeddings import embed
from app.models import Memory, Agent as AgentModel

logger = logging.getLogger(__name__)

CONSOLIDATION_THRESHOLD = 0.82
MIN_CLUSTER_SIZE = 2
CONSOLIDATION_TRIGGER = 50


def _cluster_memories(memories: list, sim_fn: Callable, threshold: float = CONSOLIDATION_THRESHOLD) -> list[list]:
    """Greedy clustering by pairwise cosine similarity. Skips pinned and meta memories."""
    eligible = [m for m in memories if not m.pinned and not m.is_meta]
    visited: set[uuid.UUID] = set()
    clusters: list[list] = []
    for i, mem in enumerate(eligible):
        if mem.id in visited:
            continue
        cluster = [mem]
        visited.add(mem.id)
        for other in eligible[i + 1:]:
            if other.id in visited:
                continue
            if sim_fn(mem, other) >= threshold:
                cluster.append(other)
                visited.add(other.id)
        if len(cluster) >= MIN_CLUSTER_SIZE:
            clusters.append(cluster)
    return clusters


async def _summarize_cluster(memories: list) -> str:
    """GPT-4o-mini: merge a cluster of similar memories into one concise statement."""
    client = AsyncOpenAI()
    bullet_points = "\n".join(f"- {m.content}" for m in memories)
    prompt = ("Merge these semantically similar memories into one concise factual statement. "
              "Preserve all distinct information. Return only the merged statement.\n\n" + bullet_points)
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=256,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("Consolidation summarization failed: %s", exc)
        return " | ".join(m.content for m in memories)


async def consolidate_agent_memories(agent_id: uuid.UUID) -> int:
    """Run consolidation for one agent. Returns number of clusters merged."""
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        # Look up api_key_id for webhook dispatch
        agent_result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
        agent_obj = agent_result.scalar_one_or_none()
        api_key_id = agent_obj.api_key_id if agent_obj else None

        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(Memory).where(
                Memory.agent_id == agent_id, Memory.deleted_at.is_(None),
                (Memory.expires_at.is_(None) | (Memory.expires_at > now) | Memory.pinned),
            )
        )
        memories = result.scalars().all()
        if len(memories) < MIN_CLUSTER_SIZE:
            return 0

        def sim_fn(a: Memory, b: Memory) -> float:
            va, vb = a.embedding, b.embedding
            if va is None or vb is None:
                return 0.0
            dot = sum(x * y for x, y in zip(va, vb))
            na = sum(x * x for x in va) ** 0.5
            nb = sum(x * x for x in vb) ** 0.5
            return dot / (na * nb) if na and nb else 0.0

        clusters = _cluster_memories(list(memories), sim_fn)
        merged_count = 0
        for cluster in clusters:
            try:
                merged_content = await _summarize_cluster(cluster)
                merged_embedding = await embed(merged_content)
                meta = Memory(
                    agent_id=agent_id, content=merged_content, embedding=merged_embedding,
                    memory_type=cluster[0].memory_type, is_meta=True,
                    consolidation_count=len(cluster),
                    consolidated_from=[str(m.id) for m in cluster],
                )
                db.add(meta)
                for source in cluster:
                    source.deleted_at = now
                await db.commit()
                merged_count += 1
            except Exception as exc:
                logger.error("Failed to merge cluster: %s", exc)
                await db.rollback()

        # Fire memory.consolidated webhook after all clusters are processed
        if merged_count > 0 and api_key_id is not None:
            from app.webhooks import fire_webhook_event
            await fire_webhook_event(
                "memory.consolidated",
                api_key_id,
                db,
                {"agent_id": str(agent_id), "merged_count": merged_count},
            )

        return merged_count
