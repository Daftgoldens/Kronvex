"""
Kronvex client — entry point for the SDK.
"""
from __future__ import annotations

import time
import httpx
from typing import Any

from .agent import Agent
from .exceptions import (
    KronvexError, AuthenticationError, RateLimitError,
    MemoryLimitError, AgentNotFoundError, ServiceUnavailableError,
)

BASE_URL = "https://api.kronvex.io"


class Kronvex:
    """
    Kronvex client.

    Usage::

        from kronvex import Kronvex

        kx = Kronvex("kx_your_api_key")
        agent = kx.agent("your-agent-id")

        # Store a memory
        agent.remember("User prefers concise answers", memory_type="preference")

        # Recall
        memories = agent.recall("user preferences")

        # Inject into prompt
        context = agent.inject_context("What does the user want?")
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
    ):
        if not api_key:
            raise AuthenticationError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
                "User-Agent": f"kronvex-python/0.5.1",
            },
            timeout=timeout,
        )

    # ── Agent factory ──────────────────────────────────────────────────────

    def agent(self, agent_id: str) -> Agent:
        """Return an Agent handle for the given agent_id."""
        return Agent(agent_id=agent_id, client=self)

    # ── Agent management ───────────────────────────────────────────────────

    def list_agents(self) -> list[dict]:
        """List all agents for this API key."""
        return self._request("GET", "/api/v1/agents")

    def create_agent(self, name: str, description: str = "") -> Agent:
        """Create a new agent and return an Agent handle."""
        data = self._request("POST", "/api/v1/agents", json={"name": name, "description": description})
        return Agent(agent_id=str(data["id"]), client=self, _data=data)

    def delete_agent(self, agent_id: str) -> None:
        """Delete an agent by ID."""
        self._request("DELETE", f"/api/v1/agents/{agent_id}")

    # ── Internal ───────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs) -> Any:
        for attempt in range(3):
            try:
                resp = self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as e:
                raise KronvexError(f"Request timed out: {e}")
            except httpx.RequestError as e:
                raise KronvexError(f"Network error: {e}")

            if resp.status_code in (200, 201, 204):
                return resp.json() if resp.content else {}

            # Retry on 503 (embedding service temporarily unavailable)
            if resp.status_code == 503 and attempt < 2:
                time.sleep(2 ** attempt)  # 1s, 2s
                continue

            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text

            if resp.status_code == 401:
                raise AuthenticationError(detail, status_code=401)
            if resp.status_code == 402 or "memory limit" in str(detail).lower():
                raise MemoryLimitError(detail, status_code=resp.status_code)
            if resp.status_code == 404:
                raise AgentNotFoundError(detail, status_code=404)
            if resp.status_code == 429:
                raise RateLimitError(detail, status_code=429)
            if resp.status_code == 503:
                raise ServiceUnavailableError(detail, status_code=503)
            raise KronvexError(detail, status_code=resp.status_code)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
