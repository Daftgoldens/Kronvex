"""
AutoGen integration — KronvexMemory helper.

Install: pip install "kronvex[autogen]"

Usage::

    from kronvex.integrations.autogen import KronvexMemory

    mem = KronvexMemory(api_key="kv-your-key", agent_id="your-agent-id")

    # Before agent runs — inject context into system message
    context = mem.inject_context(user_message)
    system_msg = f"You are a helpful assistant.\\n\\n{context}"

    # After agent runs — store the exchange
    mem.remember(f"User: {user_message}")
    mem.remember(f"Assistant: {ai_response}")
"""
from __future__ import annotations

try:
    import autogen  # noqa: F401
except ImportError:
    raise ImportError(
        "pyautogen is required for the AutoGen integration. "
        'Install it with: pip install "kronvex[autogen]"'
    )

from kronvex import Kronvex


class KronvexMemory:
    """
    Drop-in memory helper for AutoGen agents.

    Wraps the Kronvex Agent API with a simple interface:
    inject_context() before the agent call, remember() after.
    """

    def __init__(
        self,
        api_key: str,
        agent_id: str,
        *,
        session_id: str | None = None,
        top_k: int = 5,
    ):
        self._agent = Kronvex(api_key).agent(agent_id)
        self._session_id = session_id
        self._top_k = top_k

    def remember(
        self,
        content: str,
        memory_type: str = "episodic",
    ) -> None:
        """Store a memory. Call after each agent exchange."""
        self._agent.remember(
            content,
            memory_type=memory_type,  # type: ignore[arg-type]
            session_id=self._session_id,
        )

    def recall(self, query: str, top_k: int | None = None) -> list[dict]:
        """Semantic search over memories. Returns list of memory dicts."""
        return self._agent.recall(
            query,
            top_k=top_k or self._top_k,
            session_id=self._session_id,
        )

    def inject_context(self, message: str, top_k: int | None = None) -> str:
        """Get a formatted context block ready to prepend to a system prompt."""
        return self._agent.inject_context(
            message,
            top_k=top_k or self._top_k,
            session_id=self._session_id,
        )
