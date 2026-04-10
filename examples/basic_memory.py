"""
Basic memory example — store, recall, and inject context.

Requirements:
  pip install kronvex

Usage:
  KRONVEX_API_KEY=kv-... python examples/basic_memory.py
"""

import asyncio
import os
from kronvex import Kronvex

API_KEY = os.environ["KRONVEX_API_KEY"]


async def main():
    kx = Kronvex(API_KEY)

    # Create an agent for a user
    agent = await kx.create_agent("user-alice")
    print(f"Agent created: {agent.id}")

    # Store memories
    await agent.remember("Alice is a Premium customer since January 2023.")
    await agent.remember("Alice filed a billing dispute on February 28.")
    await agent.remember("Alice prefers concise, bullet-point answers.")
    print("3 memories stored.")

    # Recall semantically relevant memories
    results = await agent.recall("billing issue")
    for m in results:
        print(f"  [{m.confidence:.2f}] {m.content}")

    # Inject context before an LLM call
    ctx = await agent.inject_context("I still have that billing issue")
    print("\nContext block for system prompt:")
    print(ctx.context_block)


if __name__ == "__main__":
    asyncio.run(main())
