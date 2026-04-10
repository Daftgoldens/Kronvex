"""
AsyncKronvex — async/await version of the Kronvex client.
"""
from __future__ import annotations

import httpx
from typing import Any

from .agent import Agent
import asyncio
from .exceptions import (
    KronvexError, AuthenticationError, RateLimitError,
    MemoryLimitError, AgentNotFoundError, ServiceUnavailableError,
)

BASE_URL = "https://api.kronvex.io"


class AsyncAgent:
    """Async version of Agent."""

    def __init__(self, agent_id: str, client: "AsyncKronvex", _data: dict | None = None):
        self.id = agent_id
        self._client = client
        self._data = _data or {}

    @property
    def name(self) -> str | None:
        return self._data.get("name")

    async def remember(self, content: str, *, memory_type="episodic",
                       session_id=None, ttl_days=None, pinned=False, metadata=None) -> dict:
        return await self._client._request(
            "POST", f"/api/v1/agents/{self.id}/remember",
            json={"content": content, "memory_type": memory_type,
                  "session_id": session_id, "ttl_days": ttl_days,
                  "pinned": pinned, "metadata": metadata or {}},
        )

    async def recall(self, query: str, *, top_k=5, memory_type=None,
                     session_id=None, threshold=None) -> list[dict]:
        body: dict[str, Any] = {"query": query, "top_k": top_k}
        if memory_type: body["memory_type"] = memory_type
        if session_id:  body["session_id"] = session_id
        if threshold is not None: body["threshold"] = threshold
        result = await self._client._request("POST", f"/api/v1/agents/{self.id}/recall", json=body)
        return result.get("memories", result) if isinstance(result, dict) else result

    async def inject_context(self, message: str, *, top_k=5,
                             session_id=None, memory_type=None) -> str:
        body: dict[str, Any] = {"message": message, "top_k": top_k}
        if session_id: body["session_id"] = session_id
        if memory_type: body["memory_type"] = memory_type
        result = await self._client._request("POST", f"/api/v1/agents/{self.id}/inject-context", json=body)
        return result.get("context", "") if isinstance(result, dict) else str(result)

    async def sessions(self) -> list[dict]:
        result = await self._client._request("GET", f"/api/v1/agents/{self.id}/sessions")
        return result.get("sessions", result) if isinstance(result, dict) else result

    async def memories(self, *, session_id=None, memory_type=None, limit=50, offset=0) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if session_id: params["session_id"] = session_id
        if memory_type: params["memory_type"] = memory_type
        result = await self._client._request("GET", f"/api/v1/agents/{self.id}/memories", params=params)
        return result.get("memories", result) if isinstance(result, dict) else result

    async def clear(self) -> dict:
        return await self._client._request("DELETE", f"/api/v1/agents/{self.id}/memories")

    def __repr__(self) -> str:
        return f"<AsyncAgent id={self.id!r}>"


class AsyncKronvex:
    """
    Async Kronvex client for use with asyncio / LangChain / etc.

    Usage::

        async with AsyncKronvex("kv-your-api-key") as kx:
            agent = kx.agent("agent-id")
            await agent.remember("User is from Paris")
            memories = await agent.recall("where is the user from?")
    """

    def __init__(self, api_key: str, *, base_url: str = BASE_URL, timeout: float = 30.0):
        if not api_key:
            raise AuthenticationError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
                "User-Agent": "kronvex-python/0.1.0",
            },
            timeout=timeout,
        )

    def agent(self, agent_id: str) -> AsyncAgent:
        return AsyncAgent(agent_id=agent_id, client=self)

    async def list_agents(self) -> list[dict]:
        return await self._request("GET", "/api/v1/agents")

    async def create_agent(self, name: str, description: str = "") -> AsyncAgent:
        data = await self._request("POST", "/api/v1/agents",
                                   json={"name": name, "description": description})
        return AsyncAgent(agent_id=str(data["id"]), client=self, _data=data)

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        for attempt in range(3):
            try:
                resp = await self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as e:
                raise KronvexError(f"Request timed out: {e}")
            except httpx.RequestError as e:
                raise KronvexError(f"Network error: {e}")

            if resp.status_code in (200, 201, 204):
                return resp.json() if resp.content else {}

            if resp.status_code == 503 and attempt < 2:
                await asyncio.sleep(2 ** attempt)  # 1s, 2s
                continue

            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text

            if resp.status_code == 401: raise AuthenticationError(detail, 401)
            if resp.status_code == 404: raise AgentNotFoundError(detail, 404)
            if resp.status_code == 429: raise RateLimitError(detail, 429)
            if resp.status_code == 402 or "memory limit" in str(detail).lower():
                raise MemoryLimitError(detail, resp.status_code)
            if resp.status_code == 503:
                raise ServiceUnavailableError(detail, 503)
            raise KronvexError(detail, resp.status_code)

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
