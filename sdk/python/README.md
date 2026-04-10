# Kronvex Python SDK

Persistent memory for AI agents. Three endpoints, one API key, production-ready.

## Installation

```bash
pip install kronvex                        # core SDK
pip install "kronvex[langchain]"           # + LangChain integration
pip install "kronvex[crewai]"              # + CrewAI integration
pip install "kronvex[langgraph]"           # + LangGraph integration
pip install "kronvex[openai-agents]"       # + OpenAI Agents SDK integration
pip install "kronvex[autogen]"             # + AutoGen integration
pip install "kronvex[all-integrations]"    # all integrations at once
```

## Quick start

```python
from kronvex import Kronvex

kx = Kronvex("kv-your-api-key")
agent = kx.agent("your-agent-id")

# Store a memory
agent.remember("User prefers concise answers", memory_type="preference")

# Recall relevant memories
memories = agent.recall("what does the user prefer?", top_k=5)
for m in memories:
    print(f"[{m['score']:.2f}] {m['content']}")

# Inject context into your prompt
context = agent.inject_context("How should I respond?")
# → "Relevant memories:\n- User prefers concise answers\n..."
```

## Async support

```python
import asyncio
from kronvex import AsyncKronvex

async def main():
    async with AsyncKronvex("kv-your-api-key") as kx:
        agent = kx.agent("your-agent-id")
        await agent.remember("User is based in Paris", memory_type="semantic")
        memories = await agent.recall("where is the user?")

asyncio.run(main())
```

## Framework integrations

### LangChain

```bash
pip install "kronvex[langchain]"
```

```python
from kronvex.integrations.langchain import KronvexMemory
from langchain_openai import ChatOpenAI
from langchain.chains import ConversationChain

memory = KronvexMemory(api_key="kv-your-key", agent_id="your-agent-id")
chain = ConversationChain(llm=ChatOpenAI(), memory=memory)
chain.predict(input="I prefer concise answers.")
```

### CrewAI

```bash
pip install "kronvex[crewai]"
```

```python
import os
os.environ["KRONVEX_API_KEY"] = "kv-your-key"
os.environ["KRONVEX_AGENT_ID"] = "your-agent-id"

from kronvex.integrations.crewai import recall_memory, store_memory, get_context
from crewai import Agent

researcher = Agent(role="Researcher", goal="...", tools=[recall_memory, store_memory])
```

### LangGraph

```bash
pip install "kronvex[langgraph]"
```

```python
from kronvex.integrations.langgraph import make_recall_node, make_store_node

recall_node = make_recall_node("kv-your-key", "your-agent-id")
store_node  = make_store_node("kv-your-key", "your-agent-id")

builder.add_node("recall", recall_node)
builder.add_node("store",  store_node)
```

### OpenAI Agents SDK

```bash
pip install "kronvex[openai-agents]"
```

```python
from agents import Agent, Runner
from kronvex.integrations.openai_agents import KronvexHooks

hooks = KronvexHooks(api_key="kv-your-key", agent_id="your-agent-id", session_id="user-42")

result = await Runner.run(
    agent,
    messages=[{"role": "user", "content": "Hello"}],
    hooks=hooks,
)
```

### AutoGen

```bash
pip install "kronvex[autogen]"
```

```python
from kronvex.integrations.autogen import KronvexMemory

mem = KronvexMemory(api_key="kv-your-key", agent_id="your-agent-id")

context = mem.inject_context(user_message)
system_msg = f"You are a helpful assistant.\n\n{context}"

mem.remember(f"User: {user_message}")
mem.remember(f"Assistant: {ai_response}")
```

## API reference

### `Kronvex(api_key, *, base_url, timeout)`

| Method | Description |
|--------|-------------|
| `.agent(agent_id)` | Get an Agent handle |
| `.create_agent(name)` | Create a new agent |
| `.list_agents()` | List all agents |

### `Agent`

| Method | Description |
|--------|-------------|
| `.remember(content, *, memory_type, session_id, ttl_days, pinned, metadata)` | Store a memory |
| `.recall(query, *, top_k, memory_type, session_id, threshold)` | Semantic search |
| `.inject_context(message, *, top_k, session_id)` | Get prompt-ready context block |
| `.sessions()` | List session IDs |
| `.memories(*, session_id, memory_type, limit, offset)` | List stored memories |
| `.delete_memory(memory_id)` | Delete one memory |
| `.clear()` | Delete all memories |

## Links

- [Website](https://kronvex.io)
- [Documentation](https://kronvex.io/docs)
- [Dashboard](https://kronvex.io/dashboard)
- [API Docs](https://api.kronvex.io/docs)
- [Node.js SDK](https://www.npmjs.com/package/kronvex)
