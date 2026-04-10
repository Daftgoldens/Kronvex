# Kronvex Node.js SDK

Persistent memory for AI agents. Works with any LLM framework — OpenAI, LangChain, Vercel AI SDK, etc.

## Installation

```bash
npm install kronvex
# or
pnpm add kronvex
# or
yarn add kronvex
```

## Quick start

```typescript
import { Kronvex } from "kronvex";

const kx = new Kronvex("kv-your-api-key");
const agent = kx.agent("your-agent-id");

// Store a memory
await agent.remember("User prefers concise answers", { memory_type: "preference" });

// Recall relevant memories
const memories = await agent.recall("what does the user prefer?", { top_k: 5 });
memories.forEach(m => console.log(`[${m.score?.toFixed(2)}] ${m.content}`));

// Inject context into your prompt
const context = await agent.injectContext("How should I respond?");
```

## With Vercel AI SDK

```typescript
import { Kronvex } from "kronvex";
import { openai } from "@ai-sdk/openai";
import { streamText } from "ai";

const kx = new Kronvex(process.env.KRONVEX_API_KEY!);

export async function POST(req: Request) {
  const { messages, agentId, sessionId } = await req.json();
  const agent = kx.agent(agentId);
  const lastMessage = messages.at(-1)?.content ?? "";

  // Get context from past sessions
  const context = await agent.injectContext(lastMessage, { session_id: sessionId });

  const result = streamText({
    model: openai("gpt-4o"),
    system: `You are a helpful assistant with memory.\n\n${context}`,
    messages,
    onFinish: async ({ text }) => {
      // Store the exchange
      await agent.remember(lastMessage, { memory_type: "episodic", session_id: sessionId });
      await agent.remember(text, { memory_type: "episodic", session_id: sessionId });
    },
  });

  return result.toDataStreamResponse();
}
```

## With LangChain

```typescript
import { Kronvex } from "kronvex";
import { ChatOpenAI } from "@langchain/openai";
import { HumanMessage, SystemMessage } from "@langchain/core/messages";

const kx = new Kronvex(process.env.KRONVEX_API_KEY!);
const agent = kx.agent(process.env.KRONVEX_AGENT_ID!);

async function chat(userMessage: string, sessionId: string): Promise<string> {
  const context = await agent.injectContext(userMessage, { session_id: sessionId });

  const llm = new ChatOpenAI({ model: "gpt-4o" });
  const response = await llm.invoke([
    new SystemMessage(`You are a helpful assistant.\n\n${context}`),
    new HumanMessage(userMessage),
  ]);

  // Store for next time
  await agent.remember(`User: ${userMessage}`, { session_id: sessionId });
  await agent.remember(`You: ${response.content}`, { session_id: sessionId });

  return String(response.content);
}
```

## API reference

### `new Kronvex(apiKey, options?)`

| Option | Default | Description |
|--------|---------|-------------|
| `baseUrl` | `https://api.kronvex.io` | API base URL |
| `timeout` | `30000` | Request timeout in ms |

| Method | Returns | Description |
|--------|---------|-------------|
| `.agent(agentId)` | `Agent` | Get an Agent handle |
| `.createAgent(name, description?)` | `Promise<Agent>` | Create a new agent |
| `.listAgents()` | `Promise<AgentData[]>` | List all agents |

### `Agent`

| Method | Returns | Description |
|--------|---------|-------------|
| `.remember(content, options?)` | `Promise<Memory>` | Store a memory |
| `.recall(query, options?)` | `Promise<Memory[]>` | Semantic search |
| `.injectContext(message, options?)` | `Promise<string>` | Get prompt context block |
| `.sessions()` | `Promise<Session[]>` | List session IDs |
| `.memories(options?)` | `Promise<Memory[]>` | List stored memories |
| `.deleteMemory(memoryId)` | `Promise<void>` | Delete one memory |
| `.clear()` | `Promise<{deleted: number}>` | Delete all memories |

## Links

- [Dashboard](https://kronvex.io/dashboard)
- [API Docs](https://api.kronvex.io/docs)
- [Python SDK](https://pypi.org/project/kronvex)
