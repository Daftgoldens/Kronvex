# Kronvex — EU-Native Memory API for AI Agents

> Persistent, semantically searchable memory.  
> Three endpoints. GDPR-compliant. Data stays in Europe.

[![PyPI](https://img.shields.io/pypi/v/kronvex?color=blue)](https://pypi.org/project/kronvex/)
[![npm](https://img.shields.io/npm/v/kronvex?color=blue)](https://www.npmjs.com/package/kronvex)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![EU Frankfurt](https://img.shields.io/badge/data-EU%20Frankfurt-003399)](https://kronvex.io/security)
[![Uptime](https://img.shields.io/badge/uptime-99.9%25-brightgreen)](https://kronvex.io/status)

---

## Why Kronvex?

Every time a user opens a new session with your AI agent, it starts from scratch. No context, no history, no user preferences. You end up injecting entire conversation histories into every prompt — expensive, slow, and context-window-limited.

Kronvex gives your agent **persistent, semantically searchable memory** across sessions. Store interactions, recall relevant context by meaning, inject a ready-to-use context block before each LLM call — and keep all data in Europe.

---

## Performance

| Endpoint          | p50    | p99     |
|-------------------|--------|---------|
| `/remember`       | <30ms  | <180ms  |
| `/recall`         | <45ms  | <280ms  |
| `/inject-context` | <55ms  | <320ms  |

99.9% uptime · EU Frankfurt · GDPR-compliant · pgvector cosine similarity · 1536-dim embeddings

---

## Quick Start

### 1. Get a free API key

```bash
curl -X POST https://api.kronvex.io/auth/demo \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Alice",
    "email": "alice@company.com",
    "usecase": "Customer support bot with memory"
  }'
```

```json
{
  "full_key": "kv-xxxxxxxxxxxxxxxx",
  "agent_id": "uuid-of-your-first-agent",
  "memory_limit": 100,
  "message": "Ready! Your API key and first agent are set up."
}
```

### 2. Store a memory

```bash
curl -X POST https://api.kronvex.io/api/v1/agents/{agent_id}/remember \
  -H "X-API-Key: kv-xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"content": "Alice is a Premium customer since January 2023."}'
```

### 3. Inject context before each LLM call

```bash
curl -X POST https://api.kronvex.io/api/v1/agents/{agent_id}/inject-context \
  -H "X-API-Key: kv-xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"message": "I still have that billing issue"}'
```

```json
{
  "context_block": "[KRONVEX CONTEXT]\n- Alice is a Premium customer since Jan 2023 (similarity: 0.94)",
  "memories_used": 1
}
```

---

## SDKs

**Python**
```bash
pip install kronvex
```
```python
from kronvex import Kronvex

kx = Kronvex("kv-your-api-key")
agent = kx.agent("your-agent-id")

await agent.remember("User prefers concise answers")
context = await agent.inject_context("How should I format this?")
```

**Node.js / TypeScript**
```bash
npm install kronvex
```
```typescript
import { Kronvex } from "kronvex";

const kx = new Kronvex("kv-your-api-key");
const agent = kx.agent("your-agent-id");

await agent.remember("User prefers concise answers");
const context = await agent.injectContext("How should I format this?");
```

**MCP (Claude Desktop)**
```json
{
  "mcpServers": {
    "kronvex": {
      "command": "npx",
      "args": ["kronvex-mcp"],
      "env": { "KRONVEX_API_KEY": "kv-your-api-key" }
    }
  }
}
```

→ [Python SDK on PyPI](https://pypi.org/project/kronvex) · [Node SDK on npm](https://www.npmjs.com/package/kronvex)

---

## How It Works

Memories are ranked by a composite confidence score:

```
confidence = similarity × 0.6 + recency × 0.2 + frequency × 0.2
```

- **Similarity**: pgvector cosine similarity on 1536-dim OpenAI embeddings
- **Recency**: sigmoid with 30-day inflection point
- **Frequency**: log-scaled access count

---

## Self-Hosting

```bash
# Requires Docker
cp .env.example .env
# Edit .env with your OPENAI_API_KEY and DATABASE_URL
docker-compose up --build
```

API available at `http://localhost:8000` · Docs at `http://localhost:8000/docs`

---

## Endpoints

| Method   | Endpoint                             | Description                    |
|----------|--------------------------------------|--------------------------------|
| `POST`   | `/auth/demo`                         | Get a free API key             |
| `POST`   | `/api/v1/agents`                     | Create an agent                |
| `GET`    | `/api/v1/agents`                     | List your agents               |
| `POST`   | `/api/v1/agents/{id}/remember`       | Store a memory                 |
| `POST`   | `/api/v1/agents/{id}/recall`         | Semantic search over memories  |
| `POST`   | `/api/v1/agents/{id}/inject-context` | Get context block              |
| `DELETE` | `/api/v1/agents/{id}/memories/{mid}` | Delete a memory                |
| `GET`    | `/health`                            | Health check                   |

Full interactive docs: **[api.kronvex.io/docs](https://api.kronvex.io/docs)**

---

## Pricing

| Plan       | Price    | Agents    | Memories  |
|------------|----------|-----------|-----------|
| Free       | Free     | 1         | 100       |
| Builder    | €29/mo   | 5         | 20,000    |
| Startup    | €99/mo   | 15        | 75,000    |
| Business   | €349/mo  | 50        | 500,000   |
| Enterprise | Custom   | Unlimited | Unlimited |

→ **[See full pricing](https://kronvex.io/#pricing)**

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

Built in Paris · [kronvex.io](https://kronvex.io) · [hello@kronvex.io](mailto:hello@kronvex.io)
