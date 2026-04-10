"""
Simple in-memory IP rate limiter — no Redis required.
Used on public endpoints that have no API key auth.
"""
from collections import defaultdict
from time import time

from fastapi import Request, HTTPException

_store: dict[str, list[float]] = defaultdict(list)


def ip_rate_limit(max_requests: int, window_seconds: int):
    """FastAPI dependency: raises 429 if IP exceeds max_requests in window_seconds."""
    def _check(request: Request):
        ip = request.client.host if request.client else "unknown"
        key = f"{ip}:{request.url.path}"
        now = time()
        _store[key] = [t for t in _store[key] if now - t < window_seconds]
        if len(_store[key]) >= max_requests:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(window_seconds)},
            )
        _store[key].append(now)
    return _check
