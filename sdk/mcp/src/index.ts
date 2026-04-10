#!/usr/bin/env node
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';

const API_KEY   = process.env.KRONVEX_API_KEY ?? '';
const AGENT_ID  = process.env.KRONVEX_AGENT_ID ?? '';
const BASE_URL  = (process.env.KRONVEX_BASE_URL ?? 'https://api.kronvex.io').replace(/\/$/, '');

const server = new Server(
  { name: 'kronvex', version: '1.1.0' },
  { capabilities: { tools: {} } }
);

// agent_id is optional when KRONVEX_AGENT_ID is configured
const agentIdProp = {
  type: 'string',
  description: AGENT_ID
    ? `Agent ID (defaults to "${AGENT_ID}" from config)`
    : 'Agent ID — set KRONVEX_AGENT_ID in your MCP config or pass it here',
};

const TOOLS = [
  {
    name: 'kronvex_remember',
    description: 'Store a new memory for a Kronvex agent.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        content: { type: 'string', description: 'Text content to remember' },
        memory_type: {
          type: 'string',
          enum: ['episodic', 'semantic', 'procedural'],
          description: 'Memory type (default: semantic)',
        },
        agent_id: agentIdProp,
      },
      required: ['content'],
    },
  },
  {
    name: 'kronvex_recall',
    description: 'Recall semantically similar memories for a Kronvex agent.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        query: { type: 'string', description: 'Natural language search query' },
        top_k: { type: 'number', description: 'Max results 1-20 (default: 5)' },
        agent_id: agentIdProp,
      },
      required: ['query'],
    },
  },
  {
    name: 'kronvex_inject_context',
    description: 'Get a formatted memory context block ready to inject into an LLM system prompt.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        message: { type: 'string', description: 'Current user message' },
        agent_id: agentIdProp,
      },
      required: ['message'],
    },
  },
];

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  if (!API_KEY) {
    return {
      content: [{ type: 'text' as const, text: 'Error: KRONVEX_API_KEY is not set. Run npx @kronvex/setup to configure.' }],
      isError: true,
    };
  }

  const a = args as Record<string, unknown>;
  const agentId = (a['agent_id'] as string | undefined) ?? AGENT_ID;

  if (!agentId) {
    return {
      content: [{ type: 'text' as const, text: 'Error: No agent_id provided and KRONVEX_AGENT_ID is not configured. Run npx @kronvex/setup.' }],
      isError: true,
    };
  }

  const headers: Record<string, string> = {
    'X-API-Key': API_KEY,
    'Content-Type': 'application/json',
  };

  try {
    if (name === 'kronvex_remember') {
      const content     = a['content'] as string;
      const memory_type = (a['memory_type'] as string) ?? 'semantic';
      const res = await fetch(`${BASE_URL}/api/v1/agents/${agentId}/remember`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ content, memory_type }),
      });
      const data = await res.json() as Record<string, unknown>;
      if (!res.ok) throw new Error((data['detail'] as string) ?? `HTTP ${res.status}`);
      return { content: [{ type: 'text' as const, text: `Memory stored — id: ${data['id']}` }] };
    }

    if (name === 'kronvex_recall') {
      const query = a['query'] as string;
      const top_k = (a['top_k'] as number) ?? 5;
      const res = await fetch(`${BASE_URL}/api/v1/agents/${agentId}/recall`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ query, top_k }),
      });
      const data = await res.json() as Record<string, unknown>;
      if (!res.ok) throw new Error((data['detail'] as string) ?? `HTTP ${res.status}`);
      return { content: [{ type: 'text' as const, text: JSON.stringify(data, null, 2) }] };
    }

    if (name === 'kronvex_inject_context') {
      const message = a['message'] as string;
      const res = await fetch(`${BASE_URL}/api/v1/agents/${agentId}/inject-context`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ message }),
      });
      const data = await res.json() as Record<string, unknown>;
      if (!res.ok) throw new Error((data['detail'] as string) ?? `HTTP ${res.status}`);
      return { content: [{ type: 'text' as const, text: (data['context_block'] as string) ?? '' }] };
    }

    return {
      content: [{ type: 'text' as const, text: `Unknown tool: ${name}` }],
      isError: true,
    };
  } catch (err) {
    return {
      content: [{ type: 'text' as const, text: `Error: ${(err as Error).message}` }],
      isError: true,
    };
  }
});

const transport = new StdioServerTransport();
server.connect(transport).catch(console.error);
