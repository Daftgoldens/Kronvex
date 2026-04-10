"""
Kronvex CLI — interact with the Kronvex memory API from the terminal.

Usage:
    kronvex --api-key kv-xxx agents list
    kronvex --api-key kv-xxx agent create my-bot
    kronvex --api-key kv-xxx remember <agent_id> "User likes dark mode"
    kronvex --api-key kv-xxx recall <agent_id> "user preferences"
    kronvex --api-key kv-xxx memories list <agent_id>

Set KRONVEX_API_KEY env var to avoid passing --api-key every time.
"""
from __future__ import annotations

import json
import sys

import click

from .client import Kronvex


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_client(api_key: str | None, base_url: str) -> Kronvex:
    if not api_key:
        click.echo(
            "Error: API key required. Use --api-key or set KRONVEX_API_KEY.",
            err=True,
        )
        sys.exit(1)
    return Kronvex(api_key=api_key, base_url=base_url)


def _output(data: object, pretty: bool) -> None:
    """Print *data* as JSON (compact or pretty-printed)."""
    if pretty:
        if isinstance(data, list):
            for item in data:
                click.echo(json.dumps(item, indent=2, ensure_ascii=False))
        else:
            click.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        click.echo(json.dumps(data, ensure_ascii=False))


# ── Root group ─────────────────────────────────────────────────────────────


@click.group()
@click.option(
    "--api-key",
    envvar="KRONVEX_API_KEY",
    default=None,
    help="Kronvex API key (or set KRONVEX_API_KEY).",
)
@click.option(
    "--base-url",
    envvar="KRONVEX_BASE_URL",
    default="https://api.kronvex.io",
    show_default=True,
    help="Base URL of the Kronvex API.",
)
@click.option(
    "--pretty",
    is_flag=True,
    default=False,
    help="Pretty-print JSON output.",
)
@click.version_option(package_name="kronvex", prog_name="kronvex")
@click.pass_context
def cli(ctx: click.Context, api_key: str | None, base_url: str, pretty: bool) -> None:
    """Kronvex — persistent memory for AI agents."""
    ctx.ensure_object(dict)
    ctx.obj["api_key"] = api_key
    ctx.obj["base_url"] = base_url
    ctx.obj["pretty"] = pretty


# ── agents group ───────────────────────────────────────────────────────────


@cli.group()
def agents() -> None:
    """Manage agents."""


@agents.command("list")
@click.pass_context
def agents_list(ctx: click.Context) -> None:
    """List all agents for this API key."""
    client = _make_client(ctx.obj["api_key"], ctx.obj["base_url"])
    data = client.list_agents()
    _output(data, ctx.obj["pretty"])


# ── agent group (singular — create / delete) ───────────────────────────────


@cli.group()
def agent() -> None:
    """Create or delete agents."""


@agent.command("create")
@click.argument("name")
@click.option("--description", default="", help="Optional description.")
@click.pass_context
def agent_create(ctx: click.Context, name: str, description: str) -> None:
    """Create a new agent with NAME."""
    client = _make_client(ctx.obj["api_key"], ctx.obj["base_url"])
    ag = client.create_agent(name=name, description=description)
    # ag is an Agent object; expose its underlying data dict
    _output(ag.to_dict(), ctx.obj["pretty"])


@agent.command("delete")
@click.argument("agent_id")
@click.pass_context
def agent_delete(ctx: click.Context, agent_id: str) -> None:
    """Delete agent AGENT_ID."""
    client = _make_client(ctx.obj["api_key"], ctx.obj["base_url"])
    client.delete_agent(agent_id)
    click.echo(f"Agent {agent_id} deleted.")


# ── memories group ─────────────────────────────────────────────────────────


@cli.group()
def memories() -> None:
    """Browse stored memories."""


@memories.command("list")
@click.argument("agent_id")
@click.option("--limit", default=50, show_default=True, help="Max memories to return.")
@click.option("--offset", default=0, show_default=True, help="Pagination offset.")
@click.option("--session-id", default=None, help="Filter by session ID.")
@click.option(
    "--memory-type",
    default=None,
    type=click.Choice(["episodic", "semantic", "procedural"]),
    help="Filter by memory type.",
)
@click.pass_context
def memories_list(
    ctx: click.Context,
    agent_id: str,
    limit: int,
    offset: int,
    session_id: str | None,
    memory_type: str | None,
) -> None:
    """List memories for AGENT_ID."""
    client = _make_client(ctx.obj["api_key"], ctx.obj["base_url"])
    ag = client.agent(agent_id)
    data = ag.memories(limit=limit, offset=offset, session_id=session_id, memory_type=memory_type)
    _output(data, ctx.obj["pretty"])


# ── remember ───────────────────────────────────────────────────────────────


@cli.command()
@click.argument("agent_id")
@click.argument("content")
@click.option(
    "--memory-type",
    default="episodic",
    show_default=True,
    type=click.Choice(["episodic", "semantic", "procedural"]),
    help="Type of memory.",
)
@click.option("--session-id", default=None, help="Group memories by session.")
@click.option("--ttl-days", default=None, type=int, help="Expire after N days.")
@click.option("--pinned", is_flag=True, default=False, help="Pin this memory (never expires).")
@click.pass_context
def remember(
    ctx: click.Context,
    agent_id: str,
    content: str,
    memory_type: str,
    session_id: str | None,
    ttl_days: int | None,
    pinned: bool,
) -> None:
    """Store CONTENT as a memory for AGENT_ID."""
    client = _make_client(ctx.obj["api_key"], ctx.obj["base_url"])
    ag = client.agent(agent_id)
    data = ag.remember(
        content,
        memory_type=memory_type,  # type: ignore[arg-type]
        session_id=session_id,
        ttl_days=ttl_days,
        pinned=pinned,
    )
    _output(data, ctx.obj["pretty"])


# ── recall ─────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("agent_id")
@click.argument("query")
@click.option("--top-k", default=5, show_default=True, help="Max results to return.")
@click.option("--session-id", default=None, help="Filter by session ID.")
@click.option(
    "--memory-type",
    default=None,
    type=click.Choice(["episodic", "semantic", "procedural"]),
    help="Filter by memory type.",
)
@click.option("--threshold", default=None, type=float, help="Minimum similarity score (0-1).")
@click.pass_context
def recall(
    ctx: click.Context,
    agent_id: str,
    query: str,
    top_k: int,
    session_id: str | None,
    memory_type: str | None,
    threshold: float | None,
) -> None:
    """Retrieve memories for AGENT_ID matching QUERY."""
    client = _make_client(ctx.obj["api_key"], ctx.obj["base_url"])
    ag = client.agent(agent_id)
    data = ag.recall(
        query,
        top_k=top_k,
        session_id=session_id,
        memory_type=memory_type,  # type: ignore[arg-type]
        threshold=threshold,
    )
    _output(data, ctx.obj["pretty"])
