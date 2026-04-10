# Using Kronvex with Claude Desktop (MCP)

The Kronvex MCP server lets Claude Desktop remember things across conversations.

## Setup

### 1. Get your API key

```bash
curl -X POST https://api.kronvex.io/auth/demo \
  -H "Content-Type: application/json" \
  -d '{"name": "Your Name", "email": "you@company.com", "usecase": "Personal memory"}'
```

### 2. Configure Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "kronvex": {
      "command": "npx",
      "args": ["kronvex-mcp"],
      "env": {
        "KRONVEX_API_KEY": "kv-your-api-key",
        "KRONVEX_AGENT_ID": "your-agent-id"
      }
    }
  }
}
```

### 3. Restart Claude Desktop

The memory tools are now available. Claude can:
- `remember` — store something for later
- `recall` — search your memories semantically
- `inject_context` — get all relevant context for a topic

## Example usage

> "Remember that my project deadline is April 30th and the client is Acme Corp."

> "What do you know about my current projects?"

> "Summarize everything relevant to the Acme Corp project."
