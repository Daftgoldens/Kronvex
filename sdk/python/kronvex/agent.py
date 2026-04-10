"""
Agent — represents a single AI agent's memory space.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .client import Kronvex

MemoryType = Literal["episodic", "semantic", "procedural"]


class Agent:
    """
    Handle for a specific agent's memory operations.

    Obtain via ``kx.agent("agent-id")`` or ``kx.create_agent("name")``.
    """

    def __init__(self, agent_id: str, client: "Kronvex", _data: dict | None = None):
        self.id = agent_id
        self._client = client
        self._data = _data or {}

    @property
    def name(self) -> str | None:
        return self._data.get("name")

    def to_dict(self) -> dict:
        """Return the agent's data as a plain dictionary."""
        return self._data if self._data else {"id": self.id, "name": self.name}

    # ── Core endpoints ─────────────────────────────────────────────────────

    def remember(
        self,
        content: str,
        *,
        memory_type: MemoryType = "episodic",
        session_id: str | None = None,
        ttl_days: int | None = None,
        pinned: bool = False,
        metadata: dict | None = None,
    ) -> dict:
        """
        Store a memory for this agent.

        Args:
            content:     The text to remember (required).
            memory_type: "episodic" | "semantic" | "procedural".
            session_id:  Group memories by conversation/session.
            ttl_days:    Expire after N days (None = never).
            pinned:      Pinned memories never expire.
            metadata:    Arbitrary key-value data attached to the memory.

        Returns:
            The created memory object (dict).

        Example::

            agent.remember(
                "User prefers concise answers",
                memory_type="preference",
                session_id="conv-42",
            )
        """
        return self._client._request(
            "POST",
            f"/api/v1/agents/{self.id}/remember",
            json={
                "content": content,
                "memory_type": memory_type,
                "session_id": session_id,
                "ttl_days": ttl_days,
                "pinned": pinned,
                "metadata": metadata or {},
            },
        )

    def recall(
        self,
        query: str,
        *,
        top_k: int = 5,
        memory_type: MemoryType | None = None,
        session_id: str | None = None,
        threshold: float | None = None,
    ) -> list[dict]:
        """
        Retrieve memories semantically similar to *query*.

        Args:
            query:       Natural language search query.
            top_k:       Max number of memories to return (default 5).
            memory_type: Filter by memory type.
            session_id:  Filter to a specific session.
            threshold:   Minimum similarity score (0–1).

        Returns:
            List of memory objects, ordered by relevance.

        Example::

            memories = agent.recall("user preferences", top_k=3)
            for m in memories:
                print(m["content"], m["score"])
        """
        body: dict[str, Any] = {"query": query, "top_k": top_k}
        if memory_type:
            body["memory_type"] = memory_type
        if session_id:
            body["session_id"] = session_id
        if threshold is not None:
            body["threshold"] = threshold

        result = self._client._request(
            "POST",
            f"/api/v1/agents/{self.id}/recall",
            json=body,
        )
        return result.get("memories", result) if isinstance(result, dict) else result

    def inject_context(
        self,
        message: str,
        *,
        top_k: int = 5,
        session_id: str | None = None,
        memory_type: MemoryType | None = None,
    ) -> str:
        """
        Get a ready-to-inject context block for a system prompt.

        Returns a formatted string you can prepend to your LLM prompt.

        Example::

            context = agent.inject_context("What does the user prefer?")
            messages = [
                {"role": "system", "content": context + "\\n\\nYou are a helpful assistant."},
                {"role": "user",   "content": user_message},
            ]
        """
        body: dict[str, Any] = {"message": message, "top_k": top_k}
        if session_id:
            body["session_id"] = session_id
        if memory_type:
            body["memory_type"] = memory_type

        result = self._client._request(
            "POST",
            f"/api/v1/agents/{self.id}/inject-context",
            json=body,
        )
        return result.get("context", "") if isinstance(result, dict) else str(result)

    # ── Session helpers ────────────────────────────────────────────────────

    def sessions(self) -> list[dict]:
        """List all sessions (conversation IDs) for this agent."""
        result = self._client._request("GET", f"/api/v1/agents/{self.id}/sessions")
        return result.get("sessions", result) if isinstance(result, dict) else result

    def memories(
        self,
        *,
        session_id: str | None = None,
        memory_type: MemoryType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List stored memories with optional filters."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if session_id:
            params["session_id"] = session_id
        if memory_type:
            params["memory_type"] = memory_type

        result = self._client._request(
            "GET",
            f"/api/v1/agents/{self.id}/memories",
            params=params,
        )
        return result.get("memories", result) if isinstance(result, dict) else result

    def delete_memory(self, memory_id: str) -> None:
        """Delete a specific memory by ID."""
        self._client._request("DELETE", f"/api/v1/agents/{self.id}/memories/{memory_id}")

    def clear(self) -> dict:
        """Delete ALL memories for this agent. Irreversible."""
        return self._client._request("DELETE", f"/api/v1/agents/{self.id}/memories")

    def __repr__(self) -> str:
        name = self._data.get("name", "")
        return f"<Agent id={self.id!r}{' name=' + repr(name) if name else ''}>"
