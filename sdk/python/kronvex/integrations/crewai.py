"""
CrewAI integration — memory tools.

Install: pip install "kronvex[crewai]"

Usage::

    import os
    os.environ["KRONVEX_API_KEY"] = "kv-your-api-key"
    os.environ["KRONVEX_AGENT_ID"] = "your-agent-id"

    from kronvex.integrations.crewai import recall_memory, store_memory, get_context
    from crewai import Agent

    researcher = Agent(
        role="Researcher",
        goal="Research and remember key facts",
        tools=[recall_memory, store_memory, get_context],
    )
"""
from __future__ import annotations

import os

try:
    from crewai.tools import tool
except ImportError:
    raise ImportError(
        "crewai is required for the CrewAI integration. "
        'Install it with: pip install "kronvex[crewai]"'
    )

from kronvex import Kronvex

_api_key = os.environ.get("KRONVEX_API_KEY", "")
_agent_id = os.environ.get("KRONVEX_AGENT_ID", "")

if not _api_key or not _agent_id:
    raise EnvironmentError(
        "Set KRONVEX_API_KEY and KRONVEX_AGENT_ID environment variables "
        "before importing kronvex.integrations.crewai."
    )

_agent = Kronvex(_api_key).agent(_agent_id)


@tool("Recall from long-term memory")
def recall_memory(query: str) -> str:
    """Search long-term memory for information relevant to the query.
    Use this before starting any task to check what is already known."""
    memories = _agent.recall(query, top_k=6)
    if not memories:
        return "No relevant memories found."
    return "\n---\n".join(
        f"[{m.get('memory_type', 'memory')}] {m.get('content', '')}" for m in memories
    )


@tool("Store to long-term memory")
def store_memory(content: str, memory_type: str = "episodic") -> str:
    """Store important information to long-term memory.
    memory_type: 'episodic' (events), 'semantic' (facts), 'procedural' (how-to)."""
    _agent.remember(content, memory_type=memory_type)  # type: ignore[arg-type]
    return f"Stored to {memory_type} memory: {content[:80]}..."


@tool("Inject context from memory")
def get_context(topic: str) -> str:
    """Get a formatted context block from memory for a given topic.
    Returns a ready-to-use context string for the current task."""
    ctx = _agent.inject_context(topic, top_k=5)
    return ctx or "No context available for this topic."
