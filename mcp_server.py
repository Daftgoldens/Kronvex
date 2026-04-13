#!/usr/bin/env python3
"""
Kronvex MCP Server
==================
Persistent memory for Claude Code, Cursor, and Windsurf via MCP.

Setup:
  pip install mcp httpx

Claude Code (~/.claude/settings.json or .claude/settings.json):
  {
    "mcpServers": {
      "memory": {
        "command": "python",
        "args": ["/path/to/mcp_server.py"],
        "env": {
          "KRONVEX_API_KEY": "kv-your-api-key",
          "KRONVEX_AGENT_ID": "my-project"
        }
      }
    }
  }

Cursor (.cursor/mcp.json):
  {
    "mcpServers": {
      "memory": {
        "command": "python",
        "args": ["/path/to/mcp_server.py"],
        "env": {
          "KRONVEX_API_KEY": "kv-your-api-key",
          "KRONVEX_AGENT_ID": "my-project"
        }
      }
    }
  }

Get your free API key at: https://kronvex.io
"""

import os
import sys
import asyncio
import httpx
from typing import Any

# ── MCP import with helpful error message ──────────────────────────────────
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp import types
except ImportError:
    print(
        "Error: 'mcp' package not found.\n"
        "Install it with: pip install mcp httpx\n",
        file=sys.stderr
    )
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────────
KRONVEX_BASE   = os.environ.get("KRONVEX_BASE", "https://api.kronvex.io")
API_KEY        = os.environ.get("KRONVEX_API_KEY", "")
AGENT_ID       = os.environ.get("KRONVEX_AGENT_ID", "") or os.path.basename(os.getcwd())
TOP_K_DEFAULT  = int(os.environ.get("KRONVEX_TOP_K", "5"))
THRESHOLD      = float(os.environ.get("KRONVEX_THRESHOLD", "0.5"))

if not API_KEY:
    print(
        "Error: KRONVEX_API_KEY environment variable not set.\n"
        "Get your free API key at https://kronvex.io\n",
        file=sys.stderr
    )
    sys.exit(1)

HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json",
    "User-Agent": "kronvex-mcp/1.0",
}

# ── MCP Server ──────────────────────────────────────────────────────────────
app = Server("kronvex-memory")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """Expose the 3 Kronvex tools to the LLM."""
    return [
        types.Tool(
            name="remember",
            description=(
                "Store a memory persistently. Use this to save project decisions, "
                "user preferences, architectural choices, recurring context, or any "
                "information that should persist across sessions.\n"
                "Examples: 'User prefers TypeScript strict mode', "
                "'Database: PostgreSQL 16 with pgvector on Railway', "
                "'Never use any() in this codebase'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The information to remember. Be specific and complete."
                    },
                    "memory_type": {
                        "type": "string",
                        "enum": ["episodic", "semantic", "procedural"],
                        "description": (
                            "episodic = specific events/decisions, "
                            "semantic = general facts/preferences, "
                            "procedural = how-to knowledge/patterns"
                        ),
                        "default": "semantic"
                    }
                },
                "required": ["content"]
            }
        ),
        types.Tool(
            name="recall",
            description=(
                "Search memories semantically. Returns the most relevant stored memories "
                "ranked by confidence (similarity × recency × frequency). "
                "Use this before starting a task to retrieve relevant project context."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for. Use natural language."
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (1-10)",
                        "default": TOP_K_DEFAULT,
                        "minimum": 1,
                        "maximum": 10
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="inject_context",
            description=(
                "Get a formatted context block with the most relevant memories for the "
                "current task. Returns a ready-to-use context string. "
                "Use this at the start of complex tasks to automatically load relevant context."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The current task or topic to retrieve context for."
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="forget",
            description=(
                "Search for and delete memories matching a query. "
                "Use when outdated information should be removed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to forget. Searches semantically, then deletes matches."
                    }
                },
                "required": ["query"]
            }
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Handle tool calls from the LLM."""
    async with httpx.AsyncClient(timeout=30) as client:

        # ── remember ───────────────────────────────────────────────────────
        if name == "remember":
            content     = arguments["content"]
            memory_type = arguments.get("memory_type", "semantic")
            try:
                r = await client.post(
                    f"{KRONVEX_BASE}/api/v1/agents/{AGENT_ID}/remember",
                    headers=HEADERS,
                    json={"content": content, "memory_type": memory_type},
                )
                if r.status_code in (200, 201):
                    data = r.json()
                    return [types.TextContent(
                        type="text",
                        text=f"✓ Memory stored [{memory_type}]: {content[:80]}{'...' if len(content) > 80 else ''}"
                    )]
                else:
                    return [types.TextContent(
                        type="text",
                        text=f"Error storing memory: {r.status_code} — {r.text[:200]}"
                    )]
            except httpx.RequestError as e:
                return [types.TextContent(type="text", text=f"Network error: {e}")]

        # ── recall ─────────────────────────────────────────────────────────
        elif name == "recall":
            query = arguments["query"]
            top_k = arguments.get("top_k", TOP_K_DEFAULT)
            try:
                r = await client.post(
                    f"{KRONVEX_BASE}/api/v1/agents/{AGENT_ID}/recall",
                    headers=HEADERS,
                    json={"query": query, "top_k": top_k, "threshold": THRESHOLD},
                )
                if r.status_code == 200:
                    data     = r.json()
                    results  = data.get("results", [])
                    if not results:
                        return [types.TextContent(type="text", text="No relevant memories found.")]
                    lines = [f"Found {len(results)} relevant memories for: \"{query}\"\n"]
                    for i, item in enumerate(results, 1):
                        mem  = item.get("memory", {})
                        conf = item.get("confidence", 0)
                        lines.append(
                            f"{i}. [{conf:.0%} confidence | {mem.get('memory_type','?')}] "
                            f"{mem.get('content', '')}"
                        )
                    return [types.TextContent(type="text", text="\n".join(lines))]
                else:
                    return [types.TextContent(type="text", text=f"Recall error: {r.status_code}")]
            except httpx.RequestError as e:
                return [types.TextContent(type="text", text=f"Network error: {e}")]

        # ── inject_context ─────────────────────────────────────────────────
        elif name == "inject_context":
            query = arguments["query"]
            try:
                r = await client.post(
                    f"{KRONVEX_BASE}/api/v1/agents/{AGENT_ID}/inject-context",
                    headers=HEADERS,
                    json={"message": query, "top_k": TOP_K_DEFAULT, "threshold": THRESHOLD},
                )
                if r.status_code == 200:
                    data    = r.json()
                    context = data.get("context_block", "")
                    n_mems  = data.get("memories_used", 0)
                    if not context or n_mems == 0:
                        return [types.TextContent(type="text", text="No relevant context found in memory.")]
                    return [types.TextContent(
                        type="text",
                        text=f"Context loaded ({n_mems} memories):\n\n{context}"
                    )]
                else:
                    return [types.TextContent(type="text", text=f"Context error: {r.status_code}")]
            except httpx.RequestError as e:
                return [types.TextContent(type="text", text=f"Network error: {e}")]

        # ── forget ─────────────────────────────────────────────────────────
        elif name == "forget":
            query = arguments["query"]
            try:
                # First recall to find what to delete
                r = await client.post(
                    f"{KRONVEX_BASE}/api/v1/agents/{AGENT_ID}/recall",
                    headers=HEADERS,
                    json={"query": query, "top_k": 3, "threshold": 0.7},
                )
                if r.status_code != 200:
                    return [types.TextContent(type="text", text=f"Search error: {r.status_code}")]
                results = r.json().get("results", [])
                if not results:
                    return [types.TextContent(type="text", text=f"No memories found matching: \"{query}\"")]
                deleted = []
                for item in results:
                    mem_id = item.get("memory", {}).get("id")
                    if mem_id:
                        rd = await client.delete(
                            f"{KRONVEX_BASE}/api/v1/agents/{AGENT_ID}/memories/{mem_id}",
                            headers=HEADERS,
                        )
                        if rd.status_code in (200, 204):
                            deleted.append(item["memory"]["content"][:60])
                if deleted:
                    return [types.TextContent(
                        type="text",
                        text=f"✓ Deleted {len(deleted)} memories:\n" + "\n".join(f"- {d}" for d in deleted)
                    )]
                return [types.TextContent(type="text", text="No memories were deleted.")]
            except httpx.RequestError as e:
                return [types.TextContent(type="text", text=f"Network error: {e}")]

        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


# ── Entry point ──────────────────────────────────────────────────────────────
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )

if __name__ == "__main__":
    asyncio.run(main())
