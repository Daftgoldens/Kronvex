# @kronvex/mcp

MCP server for [Kronvex](https://kronvex.io) — persistent memory for AI agents.

Exposes three tools to any MCP-compatible client (Claude Desktop, Cursor, Cline, Windsurf, Copilot...):

| Tool | Description |
|------|-------------|
| `remember` | Store a memory for an agent |
| `recall` | Semantic search over stored memories |
| `inject_context` | Build a context block from relevant memories |

## Setup

The fastest way is the auto-wizard:

```bash
npx @kronvex/setup
```

It detects your MCP client config and injects the server automatically.

## Manual config

Add this to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "kronvex": {
      "command": "npx",
      "args": ["-y", "@kronvex/mcp"],
      "env": {
        "KRONVEX_API_KEY": "kv-your-api-key",
        "KRONVEX_AGENT_ID": "your-agent-uuid"
      }
    }
  }
}
```

Get your API key at [kronvex.io](https://kronvex.io).

## Links

- [Documentation](https://kronvex.io/docs)
- [Dashboard](https://kronvex.io/dashboard)
- [npm: @kronvex/setup](https://www.npmjs.com/package/@kronvex/setup)
