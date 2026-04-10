"""
LangChain integration — inject Kronvex memory into a LangChain chat agent.

Requirements:
  pip install kronvex langchain langchain-openai

Usage:
  KRONVEX_API_KEY=kv-... OPENAI_API_KEY=sk-... python examples/langchain_integration.py
"""

import asyncio
import os
from kronvex import Kronvex
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

API_KEY = os.environ["KRONVEX_API_KEY"]
AGENT_ID = os.environ.get("KRONVEX_AGENT_ID", "default-agent")


async def chat(user_message: str, kx_agent) -> str:
    # 1. Get relevant memory context
    ctx = await kx_agent.inject_context(user_message)

    # 2. Build system prompt with memory
    system = f"{ctx.context_block}\n\nYou are a helpful assistant." if ctx.memories_used > 0 \
             else "You are a helpful assistant."

    # 3. Call LLM
    llm = ChatOpenAI(model="gpt-4o-mini")
    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user_message)])
    answer = response.content

    # 4. Store both turns in memory
    await kx_agent.remember(f"User: {user_message}")
    await kx_agent.remember(f"Assistant: {answer}")

    return answer


async def main():
    kx = Kronvex(API_KEY)
    agent = kx.agent(AGENT_ID)

    questions = [
        "What's my name?",
        "I have a billing issue, can you help?",
        "What did we talk about last time?",
    ]

    for q in questions:
        print(f"\nUser: {q}")
        answer = await chat(q, agent)
        print(f"Assistant: {answer}")


if __name__ == "__main__":
    asyncio.run(main())
