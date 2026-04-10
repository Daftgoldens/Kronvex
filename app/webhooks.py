"""
Webhook dispatch for Kronvex events.

Supported events:
  memory.stored | memory.recalled | memory.consolidated | memory.deleted | memory.expired
  quota.warning | quota.reached

Each WebhookConfig row stores a URL, a list of subscribed events, and an HMAC
secret used to sign the payload (X-Kronvex-Signature header).

Retry policy: up to 3 attempts per URL. After attempt 1, sleep 2s. After attempt 2, sleep 8s.
Only retry on 5xx or network errors. 4xx = no retry (client config error).
"""
import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Public alias used in tests and imports outside this module
AsyncWebhookClient = httpx.AsyncClient

_RETRY_DELAYS = [2, 8]  # seconds to sleep after attempt 0 and attempt 1


async def fire_webhook_event(
    event_name: str,
    api_key_id: uuid.UUID,
    db: AsyncSession,  # kept for backward compat, not used directly (fresh session opened below)
    payload: dict,
) -> None:
    """
    Find all WebhookConfig rows for api_key_id that subscribe to event_name,
    then POST the event payload to each URL (fire-and-forget, never raises).

    Retry policy: 3 attempts, backoff 2s then 8s, only on 5xx or network error.
    """
    from app.models import WebhookConfig  # local import to avoid circular deps

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WebhookConfig).where(WebhookConfig.api_key_id == api_key_id)
            )
            configs = result.scalars().all()
    except Exception as exc:
        logger.warning("Webhook dispatch: failed to query configs: %s", exc)
        return

    matching = [c for c in configs if event_name in (c.events or [])]
    if not matching:
        return

    timestamp = datetime.now(timezone.utc).isoformat()
    body = {
        "event": event_name,
        "data": payload,
        "timestamp": timestamp,
    }
    body_bytes = json.dumps(body, separators=(",", ":")).encode()

    async with AsyncWebhookClient(timeout=8.0) as client:
        for config in matching:
            sig = hmac.new(config.secret.encode(), body_bytes, hashlib.sha256).hexdigest()
            headers = {
                "Content-Type": "application/json",
                "X-Kronvex-Signature": f"sha256={sig}",
            }
            _MAX_ATTEMPTS = len(_RETRY_DELAYS) + 1
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    resp = await client.post(config.url, content=body_bytes, headers=headers)
                    if resp.status_code < 500:
                        break  # success (2xx/3xx) or client error (4xx) — no retry
                except Exception:
                    pass  # network error — retry if not last attempt
                if attempt < len(_RETRY_DELAYS):
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
