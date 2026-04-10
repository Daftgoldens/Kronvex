"""
LangGraph integration — memory nodes.

Install: pip install "kronvex[langgraph]"

Usage::

    from langgraph.graph import StateGraph, END
    from typing import TypedDict, Annotated, Optional
    import operator

    from kronvex.integrations.langgraph import make_recall_node, make_store_node

    class AgentState(TypedDict):
        messages: Annotated[list, operator.add]
        memory_context: Optional[str]

    recall_node = make_recall_node("kv-your-key", "your-agent-id")
    store_node  = make_store_node("kv-your-key", "your-agent-id")

    builder = StateGraph(AgentState)
    builder.add_node("recall",  recall_node)
    builder.add_node("agent",   call_model)   # your LLM node
    builder.add_node("store",   store_node)
    builder.set_entry_point("recall")
    builder.add_edge("recall", "agent")
    builder.add_edge("agent",  "store")
    builder.add_edge("store",  END)
    graph = builder.compile()
"""
from __future__ import annotations

from typing import Any, Callable

try:
    import langgraph  # noqa: F401
except ImportError:
    raise ImportError(
        "langgraph is required for the LangGraph integration. "
        'Install it with: pip install "kronvex[langgraph]"'
    )

from kronvex import Kronvex


def make_recall_node(
    api_key: str,
    agent_id: str,
    *,
    top_k: int = 5,
    query_key: str = "messages",
    output_key: str = "memory_context",
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """
    Return a LangGraph node that recalls memories before the LLM call.

    The node reads the last message from state[query_key], calls Kronvex recall,
    and returns {output_key: "<formatted context string>"}.

    Args:
        api_key:    Your Kronvex API key.
        agent_id:   The agent whose memories to query.
        top_k:      Max memories to retrieve (default 5).
        query_key:  State key containing the message list (default "messages").
        output_key: State key to write the context into (default "memory_context").
    """
    agent = Kronvex(api_key).agent(agent_id)

    def recall_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get(query_key, [])
        if not messages:
            return {output_key: None}
        last = messages[-1]
        query = last.get("content", str(last)) if isinstance(last, dict) else str(last)
        memories = agent.recall(query, top_k=top_k)
        if not memories:
            return {output_key: None}
        lines = [f"[{m.get('memory_type', 'memory')}] {m.get('content', '')}" for m in memories]
        return {output_key: "\n".join(lines)}

    recall_node.__name__ = "kronvex_recall_node"
    return recall_node


def make_store_node(
    api_key: str,
    agent_id: str,
    *,
    content_key: str = "messages",
    memory_type: str = "episodic",
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """
    Return a LangGraph node that stores the latest AI message after the LLM call.

    The node reads state[content_key], picks the last message, and calls remember().

    Args:
        api_key:     Your Kronvex API key.
        agent_id:    The agent to store memories for.
        content_key: State key containing the message list (default "messages").
        memory_type: Kronvex memory type to use (default "episodic").
    """
    agent = Kronvex(api_key).agent(agent_id)

    def store_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get(content_key, [])
        if not messages:
            return {}
        last = messages[-1]
        content = last.get("content", str(last)) if isinstance(last, dict) else str(last)
        if content:
            agent.remember(content, memory_type=memory_type)  # type: ignore[arg-type]
        return {}

    store_node.__name__ = "kronvex_store_node"
    return store_node
