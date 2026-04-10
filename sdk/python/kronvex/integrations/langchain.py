"""
LangChain integration — KronvexMemory.

Install: pip install "kronvex[langchain]"

Usage::

    from kronvex import Kronvex
    from kronvex.integrations.langchain import KronvexMemory
    from langchain_openai import ChatOpenAI
    from langchain.chains import ConversationChain

    kx = Kronvex("kv-your-api-key")
    memory = KronvexMemory(api_key="kv-your-api-key", agent_id="your-agent-id", top_k=5)

    chain = ConversationChain(llm=ChatOpenAI(), memory=memory)
    chain.predict(input="I prefer concise answers.")
"""
from __future__ import annotations

from typing import Any

try:
    from langchain_core.memory import BaseMemory
except ImportError:
    try:
        from langchain.memory import BaseMemory  # type: ignore[no-redef]
    except ImportError:
        raise ImportError(
            "langchain-core is required for the LangChain integration. "
            'Install it with: pip install "kronvex[langchain]"'
        )

from kronvex import Kronvex


class KronvexMemory(BaseMemory):
    """Persistent cross-session memory for LangChain powered by Kronvex."""

    api_key: str = ""
    agent_id: str = ""
    top_k: int = 5
    memory_key: str = "history"

    # Internal — not a Pydantic field so we manage it manually
    _agent: Any = None

    def __init__(self, *, api_key: str, agent_id: str, top_k: int = 5, memory_key: str = "history", **kwargs: Any):
        super().__init__(api_key=api_key, agent_id=agent_id, top_k=top_k, memory_key=memory_key, **kwargs)
        kx = Kronvex(api_key)
        object.__setattr__(self, "_agent", kx.agent(agent_id))

    @property
    def memory_variables(self) -> list[str]:
        return [self.memory_key]

    def load_memory_variables(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Called before the LLM — recall relevant memories."""
        query = next(iter(inputs.values()), "") if inputs else ""
        if not query:
            return {self.memory_key: ""}
        memories = self._agent.recall(str(query), top_k=self.top_k)
        if not memories:
            return {self.memory_key: ""}
        lines = [f"[{m.get('memory_type', 'memory')}] {m.get('content', '')}" for m in memories]
        return {self.memory_key: "\n".join(lines)}

    def save_context(self, inputs: dict[str, Any], outputs: dict[str, Any]) -> None:
        """Called after the LLM — store the exchange."""
        user_msg = next(iter(inputs.values()), "")
        ai_msg = next(iter(outputs.values()), "")
        if user_msg:
            self._agent.remember(str(user_msg), memory_type="episodic")
        if ai_msg:
            self._agent.remember(str(ai_msg), memory_type="episodic")

    def clear(self) -> None:
        """Delete all memories for this agent."""
        self._agent.clear()
