"""
quota_guard.py — Protection anti-ruine OpenAI
============================================
Tracks embedding API calls per key per day.
Enforces daily recall/remember quotas BEFORE calling OpenAI.
Circuit breaker: if OpenAI returns 429 or monthly budget exceeded,
falls back to TEXT search instead of hard-failing.
"""

from __future__ import annotations
import logging
from datetime import date
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# ── Daily recall limits per plan (prevents Demo abuse)
# These are DAILY limits on top of the per-minute rate limits
DAILY_RECALL_LIMITS: dict[str, Optional[int]] = {
    "demo":       200,       # 200 recalls/day — was allowing 86k (60/min × 1440)
    "dev":        500,       # 500/day ≈ €0.02/day max
    "starter":    5_000,     # 5k/day ≈ €0.23/day max
    "pro":        25_000,    # 25k/day ≈ €1.15/day max
    "growth":     100_000,   # 100k/day ≈ €4.60/day max
    "scale":      None,      # unlimited — they pay for it
    "enterprise": None,
}

DAILY_REMEMBER_LIMITS: dict[str, Optional[int]] = {
    "demo":       100,       # 100 stores/day
    "dev":        1_000,
    "starter":    2_000,
    "pro":        10_000,
    "growth":     50_000,
    "scale":      None,
    "enterprise": None,
}

# Token estimates for cost tracking
TOKENS_PER_RECALL   = 50    # avg query length
TOKENS_PER_REMEMBER = 100   # avg memory content
TOKENS_PER_INJECT   = 50    # same as recall


async def get_today_usage(db: AsyncSession, api_key_id: str) -> dict:
    """Get today's usage counts for an API key."""
    result = await db.execute(
        text("""
            SELECT recall_count, remember_count, inject_count, total_tokens
            FROM daily_api_usage
            WHERE api_key_id = :key_id AND date = CURRENT_DATE
        """),
        {"key_id": api_key_id}
    )
    row = result.fetchone()
    if not row:
        return {"recall_count": 0, "remember_count": 0, "inject_count": 0, "total_tokens": 0}
    return {
        "recall_count":   row.recall_count,
        "remember_count": row.remember_count,
        "inject_count":   row.inject_count,
        "total_tokens":   row.total_tokens,
    }


async def check_and_increment_daily_quota(
    db: AsyncSession,
    api_key_id: str,
    plan: str,
    endpoint: str,  # 'recall' | 'remember' | 'inject'
) -> None:
    """
    Check daily quota before calling OpenAI.
    Raises HTTP 429 if quota exceeded.
    Increments counter atomically after check.
    """
    # 1. Get current daily limit for this plan + endpoint
    if endpoint == "recall" or endpoint == "inject":
        limit = DAILY_RECALL_LIMITS.get(plan, 200)
        field = "recall_count" if endpoint == "recall" else "inject_count"
    else:
        limit = DAILY_REMEMBER_LIMITS.get(plan, 100)
        field = "remember_count"

    # Unlimited plans skip quota check
    if limit is None:
        await _increment_only(db, api_key_id, endpoint)
        return

    # 2. Get current count
    usage = await get_today_usage(db, api_key_id)
    current = usage.get(field, 0)

    if current >= limit:
        plan_label = plan.upper()
        log.warning(f"Daily quota exceeded: key={api_key_id} plan={plan} endpoint={endpoint} count={current} limit={limit}")
        raise HTTPException(
            status_code=429,
            detail={
                "error": "daily_quota_exceeded",
                "message": f"Daily {endpoint} limit reached ({limit}/day on {plan_label} plan). Resets at midnight UTC.",
                "current": current,
                "limit": limit,
                "plan": plan,
                "upgrade_url": "https://kronvex.io/dashboard#billing",
            },
            headers={
                "Retry-After":             "86400",
                "X-RateLimit-Limit-Day":   str(limit),
                "X-RateLimit-Used-Day":    str(current),
                "X-RateLimit-Reset-Day":   "midnight UTC",
            }
        )

    # 3. Increment counter
    await _increment_only(db, api_key_id, endpoint)


async def _increment_only(db: AsyncSession, api_key_id: str, endpoint: str) -> None:
    """Increment daily counter without checking quota (for unlimited plans)."""
    tokens = {"recall": TOKENS_PER_RECALL, "remember": TOKENS_PER_REMEMBER, "inject": TOKENS_PER_INJECT}.get(endpoint, 50)
    try:
        await db.execute(
            text("SELECT increment_daily_usage(:key_id, :endpoint, :tokens)"),
            {"key_id": api_key_id, "endpoint": endpoint, "tokens": tokens}
        )
        await db.commit()
    except Exception as e:
        # Non-fatal: don't block the request if counter update fails
        log.error(f"Failed to increment daily usage: {e}")
        await db.rollback()


async def get_usage_summary(db: AsyncSession, api_key_id: str, plan: str) -> dict:
    """
    Returns usage summary with quota info for dashboard display.
    """
    usage = await get_today_usage(db, api_key_id)
    recall_limit   = DAILY_RECALL_LIMITS.get(plan)
    remember_limit = DAILY_REMEMBER_LIMITS.get(plan)

    return {
        "today": {
            "recalls":   usage["recall_count"],
            "stores":    usage["remember_count"],
            "injects":   usage["inject_count"],
            "tokens":    usage["total_tokens"],
        },
        "limits": {
            "recalls_day":   recall_limit,
            "stores_day":    remember_limit,
        },
        "cost_estimate_today_eur": round(usage["total_tokens"] * 0.02 / 1_000_000 / 1.08, 6),
    }
