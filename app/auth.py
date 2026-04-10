import hashlib
import secrets
from datetime import datetime, timezone

import httpx

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ApiKey, Memory, Agent
from app.plans import get_plan

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
KEY_PREFIX = "kv-"


def _generate_key() -> tuple[str, str, str]:
    raw = secrets.token_urlsafe(32)
    full_key = f"{KEY_PREFIX}{raw}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    key_prefix = full_key[:12] + "..."
    return full_key, key_hash, key_prefix


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def create_api_key(db: AsyncSession, name: str, plan: str = "starter") -> tuple[ApiKey, str]:
    """Crée une clé API commerciale avec les quotas du plan choisi."""
    p = get_plan(plan)
    full_key, key_hash, key_prefix = _generate_key()
    api_key = ApiKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=name,
        is_demo=False,
        plan=plan,
        memory_limit=p["memories"],
        agent_limit=p["agents"],
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return api_key, full_key


async def create_demo_key(db: AsyncSession, name: str, email: str, usecase: str) -> tuple[ApiKey, str]:
    """Clé de démo — limitée à 100 mémoires, 1 agent."""
    p = get_plan("demo")
    full_key, key_hash, key_prefix = _generate_key()
    api_key = ApiKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=f"[DEMO] {name}",
        is_demo=True,
        plan="demo",
        memory_limit=p["memories"],
        agent_limit=p["agents"],
        contact_email=email,
        contact_usecase=usecase,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return api_key, full_key


async def get_api_key(
    header_key: str | None = Security(API_KEY_HEADER),
    db: AsyncSession = Depends(get_db),
) -> ApiKey:
    if not header_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Add header: X-API-Key: kv-..."
        )
    key_hash = _hash_key(header_key)
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked API key.")
    api_key.last_used_at = datetime.now(timezone.utc)
    await db.commit()
    return api_key


async def check_memory_limit(db: AsyncSession, api_key: ApiKey) -> None:
    """Lève une 402 si la clé a atteint sa limite mensuelle de mémoires."""
    if api_key.memory_limit is None:
        return  # illimité (Scale / Enterprise)
    # Use billing-cycle counter (resets monthly on invoice.payment_succeeded)
    count = api_key.cycle_memories_used or 0
    # Fire webhook alert if threshold crossed (async, non-blocking)
    try:
        from asyncio import create_task
        create_task(check_memory_and_alert(db, api_key, count))
    except Exception:
        pass

    if count >= api_key.memory_limit:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "memory_limit_reached",
                "message": f"Monthly memory limit of {api_key.memory_limit:,} reached for plan '{api_key.plan}'. Resets at next billing cycle or upgrade to continue.",
                "memories_used_this_cycle": count,
                "limit": api_key.memory_limit,
                "plan": api_key.plan,
                "upgrade_url": "https://kronvex.io/#pricing",
            }
        )


async def check_agent_limit(db: AsyncSession, api_key: ApiKey) -> None:
    """Lève une 402 si la clé a atteint sa limite d'agents."""
    if api_key.agent_limit is None:
        return  # illimité
    result = await db.execute(
        select(func.count(Agent.id)).where(Agent.api_key_id == api_key.id)
    )
    count = result.scalar_one()
    if count >= api_key.agent_limit:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "agent_limit_reached",
                "message": f"Agent limit of {api_key.agent_limit} reached for plan '{api_key.plan}'. Upgrade to add more agents.",
                "agents_used": count,
                "limit": api_key.agent_limit,
                "plan": api_key.plan,
                "upgrade_url": "https://kronvex.io/#pricing",
            }
        )


# ── WEBHOOK ALERTS ────────────────────────────────────────────────────────────

async def _fire_webhook(url: str, payload: dict, max_retries: int = 3) -> None:
    """Fire a webhook alert with retry + exponential backoff (1s, 2s, 4s)."""
    import asyncio
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(max_retries):
            try:
                resp = await client.post(
                    url, json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=8.0,
                )
                if resp.status_code < 500:
                    return  # success or client-side error — no point retrying
            except Exception:
                pass
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # 0s→1s, 1s→2s before 3rd attempt


async def check_memory_and_alert(db: AsyncSession, api_key: "ApiKey", count: int) -> None:
    """Send webhook + email alerts when memory quota crosses 80% or 100%.

    `count` is the memory count BEFORE the current request's memory is saved,
    so after saving the count will be `count + 1`. We use this to fire each
    alert exactly once — only when the threshold is crossed, not on every
    subsequent request above it.
    """
    if not api_key.memory_limit:
        return

    limit = api_key.memory_limit
    new_count = count + 1  # count after the memory being stored right now

    # ── Webhook (legacy per-key webhook_url) ──────────────────────────────────
    if getattr(api_key, 'webhook_url', None):
        threshold = getattr(api_key, 'webhook_threshold', 80)
        pct = round(new_count / limit * 100)
        if pct >= threshold:
            await _fire_webhook(api_key.webhook_url, {
                "event": "memory.threshold_reached",
                "api_key_prefix": api_key.key_prefix,
                "plan": api_key.plan,
                "memories_used": new_count,
                "memory_limit": limit,
                "usage_percent": pct,
                "threshold_percent": threshold,
                "upgrade_url": "https://kronvex.io/#pricing",
            })

    # ── Email alerts ──────────────────────────────────────────────────────────
    email = getattr(api_key, 'contact_email', None)
    if not email:
        return

    plan  = api_key.plan or "demo"
    warn_threshold = round(limit * 0.8)  # 80% mark (rounded down to nearest int)

    from app.quota_emails import send_quota_warning_email, send_quota_reached_email

    # Send "quota reached" only when new_count exactly hits the limit
    if new_count >= limit and count < limit:
        await send_quota_reached_email(email, plan, new_count, limit)

    # Send "quota warning" only when new_count crosses the 80% mark for the first time
    elif new_count >= warn_threshold and count < warn_threshold:
        await send_quota_warning_email(email, plan, new_count, limit)
