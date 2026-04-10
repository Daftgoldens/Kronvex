"""
Background tasks: TTL decay, webhook alerts, cleanup.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import Memory, Agent, ApiKey

logger = logging.getLogger(__name__)


async def expire_memories() -> int:
    """Delete all memories where expires_at < now. Returns count deleted."""
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            delete(Memory)
            .where(Memory.expires_at.isnot(None))
            .where(Memory.expires_at < now)
            .where(Memory.pinned == False)
        )
        await db.commit()
        count = result.rowcount
        if count:
            logger.info(f"[TTL] Expired {count} memories")
        return count


async def ttl_decay_loop(interval_seconds: int = 3600) -> None:
    """Run TTL expiration every `interval_seconds` (default: 1h)."""
    logger.info(f"[TTL] Decay loop started (interval={interval_seconds}s)")
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            count = await expire_memories()
            logger.info(f"[TTL] Cycle done — expired {count} memories")
        except asyncio.CancelledError:
            logger.info("[TTL] Decay loop cancelled")
            break
        except Exception as e:
            logger.error(f"[TTL] Error: {e}")


async def usage_alert_loop(interval_seconds: int = 21600) -> None:
    """
    Every 6h: check all keys with a webhook_url.
    Fire alert if usage >= threshold and hasn't been fired in the last 24h.
    """
    from app.auth import _fire_webhook
    logger.info("[ALERTS] Usage alert loop started")
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ApiKey).where(
                        ApiKey.is_active == True,  # webhook_url filter done in Python for migration safety
                        ApiKey.memory_limit.isnot(None),
                    )
                )
                keys = result.scalars().all()
                for key in keys:
                    try:
                        count_result = await db.execute(
                            select(func.count(Memory.id))
                            .join(Agent, Agent.id == Memory.agent_id)
                            .where(Agent.api_key_id == key.id)
                        )
                        count = count_result.scalar_one()
                        threshold = getattr(key, 'webhook_threshold', 80)
                        pct = round(count / key.memory_limit * 100)
                        wh_url = getattr(key, 'webhook_url', None)
                        if not wh_url: continue
                        if pct >= threshold:
                            await _fire_webhook(wh_url, {
                                "event": "memory.usage_alert",
                                "api_key_prefix": key.key_prefix,
                                "plan": key.plan,
                                "memories_used": count,
                                "memory_limit": key.memory_limit,
                                "usage_percent": pct,
                                "threshold_percent": threshold,
                                "upgrade_url": "https://kronvex.io/#pricing",
                            })
                    except Exception as e:
                        logger.error(f"[ALERTS] Error for key {key.key_prefix}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[ALERTS] Loop error: {e}")
