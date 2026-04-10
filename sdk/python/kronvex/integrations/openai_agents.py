"""
OpenAI Agents SDK integration — KronvexHooks.

Install: pip install "kronvex[openai-agents]"

Usage::

    from agents import Agent, Runner
    from kronvex.integrations.openai_agents import KronvexHooks

    hooks = KronvexHooks(
        api_key="kv-your-key",
        agent_id="your-agent-id",
        session_id="user-42",   # optional, for per-user isolation
    )

    result = await Runner.run(
        agent,
        messages=[{"role": "user", "content": "Hello"}],
        hooks=hooks,
    )
"""
from __future__ import annotations

from typing import Any

try:
    from agents import RunHooks, RunContextWrapper, Agent as OAAgent, RunResult
except ImportError:
    raise ImportError(
        "openai-agents is required for the OpenAI Agents SDK integration. "
        'Install it with: pip install "kronvex[openai-agents]"'
    )

from kronvex import Kronvex


class KronvexHooks(RunHooks):
    """
    RunHooks implementation that injects Kronvex memory before each agent run
    and stores the exchange after completion.
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
        self._last_input: str = ""

    async def on_agent_start(
        self,
        context: RunContextWrapper,
        agent: OAAgent,
    ) -> None:
        """Called before the agent runs — inject relevant memories into instructions."""
        messages = getattr(context, "messages", None) or []
        query = ""
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "user":
                query = m.get("content", "")
                break
            if hasattr(m, "role") and m.role == "user":
                query = getattr(m, "content", "")
                break

        if not query:
            return

        self._last_input = query
        memories = self._agent.recall(query, top_k=self._top_k)
        if not memories:
            return

        lines = [f"- [{m.get('memory_type', 'memory')}] {m.get('content', '')}" for m in memories]
        memory_block = "Relevant memories from past sessions:\n" + "\n".join(lines)

        current = agent.instructions or ""
        if callable(current):
            return  # dynamic instructions — skip injection
        agent.instructions = memory_block + "\n\n" + current

    async def on_agent_end(
        self,
        context: RunContextWrapper,
        agent: OAAgent,
        result: RunResult,
    ) -> None:
        """Called after the agent runs — store the exchange."""
        if self._last_input:
            self._agent.remember(
                self._last_input,
                memory_type="episodic",
                session_id=self._session_id,
            )
        output = getattr(result, "final_output", None) or ""
        if output:
            self._agent.remember(
                str(output),
                memory_type="episodic",
                session_id=self._session_id,
            )
