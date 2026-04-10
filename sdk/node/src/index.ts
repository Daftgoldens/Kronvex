/**
 * Kronvex — Persistent Memory API for AI Agents
 * TypeScript / Node.js SDK v0.2.0
 *
 * https://kronvex.io · https://api.kronvex.io/docs
 *
 * @example
 * import { Kronvex } from "kronvex";
 *
 * const kx = new Kronvex(process.env.KRONVEX_API_KEY!);
 * const agent = kx.agent("your-agent-id");
 *
 * await agent.remember("User prefers dark mode", { memory_type: "semantic" });
 * const ctx = await agent.injectContext("user preferences");
 * // → Ready-to-use context string for your system prompt
 */

const SDK_VERSION = "0.2.0";
const BASE_URL = "https://api.kronvex.io";

// ── Types ────────────────────────────────────────────────────────────────────

export type MemoryType = "episodic" | "semantic" | "procedural";

export interface Memory {
  id: string;
  content: string;
  memory_type: MemoryType;
  session_id: string | null;
  score?: number;
  access_count: number;
  pinned: boolean;
  created_at: string;
  expires_at: string | null;
  metadata: Record<string, unknown>;
}

export interface AgentData {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  memory_count: number;
}

export interface RememberOptions {
  /** Memory type — defaults to "episodic" */
  memory_type?: MemoryType;
  /** Scope this memory to a specific session/user */
  session_id?: string;
  /** Expire this memory after N days */
  ttl_days?: number;
  /** Pin this memory so it always appears in context */
  pinned?: boolean;
  /** Arbitrary metadata key-value pairs */
  metadata?: Record<string, unknown>;
}

export interface RecallOptions {
  /** Number of memories to return — defaults to 5 */
  top_k?: number;
  /** Filter by memory type */
  memory_type?: MemoryType;
  /** Filter to a specific session */
  session_id?: string;
  /** Minimum similarity score (0–1) */
  threshold?: number;
}

export interface InjectContextOptions {
  /** Number of memories to include — defaults to 5 */
  top_k?: number;
  /** Filter to a specific session */
  session_id?: string;
  /** Filter by memory type */
  memory_type?: MemoryType;
}

export interface MemoriesOptions {
  session_id?: string;
  memory_type?: MemoryType;
  /** Full-text search on memory content */
  q?: string;
  /** Sort order: "date_desc" | "date_asc" | "access_count" */
  sort?: "date_desc" | "date_asc" | "access_count";
  /** Page size — max 100 */
  limit?: number;
  offset?: number;
}

export interface MemoriesPage {
  memories: Memory[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface AgentAnalytics {
  agent_id: string;
  total_memories: number;
  pinned_memories: number;
  top_memories: Array<{
    id: string;
    content: string;
    memory_type: MemoryType;
    access_count: number;
    session_id: string | null;
    created_at: string;
  }>;
  by_type: Record<MemoryType, number>;
  sessions: Array<{
    session_id: string;
    memory_count: number;
    last_at: string | null;
  }>;
  daily_memories: Array<{ date: string; count: number }>;
}

export interface DailyUsage {
  today: {
    recalls: number;
    stores: number;
    injects: number;
    tokens: number;
  };
  limits: {
    recalls_day: number | null;
    stores_day: number | null;
  };
  cost_estimate_today_eur: number;
}

// ── Errors ───────────────────────────────────────────────────────────────────

export class KronvexError extends Error {
  constructor(message: string, public readonly statusCode?: number) {
    super(message);
    this.name = "KronvexError";
  }
}
export class AuthenticationError extends KronvexError {
  constructor(m: string) { super(m, 401); this.name = "AuthenticationError"; }
}
export class MemoryLimitError extends KronvexError {
  constructor(m: string) { super(m, 402); this.name = "MemoryLimitError"; }
}
export class RateLimitError extends KronvexError {
  constructor(m: string, public readonly retryAfter?: number) {
    super(m, 429); this.name = "RateLimitError";
  }
}
export class DailyQuotaError extends RateLimitError {
  constructor(m: string, public readonly plan?: string) {
    super(m, 86400); this.name = "DailyQuotaError";
  }
}
export class AgentNotFoundError extends KronvexError {
  constructor(m: string) { super(m, 404); this.name = "AgentNotFoundError"; }
}

// ── Agent ────────────────────────────────────────────────────────────────────

export class Agent {
  readonly id: string;
  readonly name?: string;
  private readonly _client: Kronvex;

  constructor(agentId: string, client: Kronvex, data?: Partial<AgentData>) {
    this.id = agentId;
    this.name = data?.name;
    this._client = client;
  }

  /**
   * Store a memory for this agent.
   *
   * @example
   * await agent.remember("User prefers concise answers", {
   *   memory_type: "semantic",
   *   session_id: userId,
   * });
   */
  async remember(content: string, options: RememberOptions = {}): Promise<Memory> {
    return this._client._request<Memory>("POST", `/api/v1/agents/${this.id}/remember`, {
      content,
      memory_type: options.memory_type ?? "episodic",
      session_id: options.session_id ?? null,
      ttl_days: options.ttl_days ?? null,
      pinned: options.pinned ?? false,
      metadata: options.metadata ?? {},
    });
  }

  /**
   * Recall semantically relevant memories using vector similarity.
   *
   * @example
   * const memories = await agent.recall("user tone preferences", { top_k: 5 });
   * memories.forEach(m => console.log(`[${m.score?.toFixed(2)}] ${m.content}`));
   */
  async recall(query: string, options: RecallOptions = {}): Promise<Memory[]> {
    const body: Record<string, unknown> = { query, top_k: options.top_k ?? 5 };
    if (options.memory_type) body.memory_type = options.memory_type;
    if (options.session_id)  body.session_id  = options.session_id;
    if (options.threshold !== undefined) body.threshold = options.threshold;
    const result = await this._client._request<{ memories: Memory[] } | Memory[]>(
      "POST", `/api/v1/agents/${this.id}/recall`, body
    );
    return Array.isArray(result) ? result : result.memories;
  }

  /**
   * Get a ready-to-inject context block for a system prompt.
   *
   * @example
   * const context = await agent.injectContext(userMessage, { session_id: userId });
   * const messages = [
   *   { role: "system", content: `${context}\n\nYou are a helpful assistant.` },
   *   { role: "user",   content: userMessage },
   * ];
   */
  async injectContext(message: string, options: InjectContextOptions = {}): Promise<string> {
    const body: Record<string, unknown> = { message, top_k: options.top_k ?? 5 };
    if (options.session_id)  body.session_id  = options.session_id;
    if (options.memory_type) body.memory_type = options.memory_type;
    const result = await this._client._request<{ context: string } | string>(
      "POST", `/api/v1/agents/${this.id}/inject-context`, body
    );
    return typeof result === "string" ? result : result.context;
  }

  /**
   * List stored memories with optional filters and full-text search.
   *
   * @example
   * const page = await agent.memories({ q: "dark mode", limit: 25 });
   * console.log(`${page.total} total, showing ${page.memories.length}`);
   */
  async memories(options: MemoriesOptions = {}): Promise<MemoriesPage> {
    const params = new URLSearchParams({
      limit:  String(options.limit  ?? 25),
      offset: String(options.offset ?? 0),
    });
    if (options.session_id)  params.set("session_id",  options.session_id);
    if (options.memory_type) params.set("memory_type", options.memory_type);
    if (options.sort)        params.set("sort",         options.sort);
    if (options.q)           params.set("q",            options.q);
    const result = await this._client._request<MemoriesPage | Memory[]>(
      "GET", `/api/v1/agents/${this.id}/memories?${params}`
    );
    if (Array.isArray(result)) {
      return { memories: result, total: result.length, limit: options.limit ?? 25, offset: options.offset ?? 0, has_more: false };
    }
    return result as MemoriesPage;
  }

  /** List all sessions (conversation IDs) for this agent. */
  async sessions(): Promise<Array<{ session_id: string; count: number }>> {
    const result = await this._client._request<{ sessions: Array<{ session_id: string; count: number }> }>(
      "GET", `/api/v1/agents/${this.id}/sessions`
    );
    return result.sessions;
  }

  /** Get detailed analytics for this agent. */
  async analytics(days = 30): Promise<AgentAnalytics> {
    return this._client._request<AgentAnalytics>(
      "GET", `/api/v1/agents/${this.id}/analytics?days=${days}`
    );
  }

  /** Delete a specific memory by ID. */
  async deleteMemory(memoryId: string): Promise<void> {
    await this._client._request("DELETE", `/api/v1/agents/${this.id}/memories/${memoryId}`);
  }

  /** Delete ALL memories for this agent. Irreversible. */
  async clear(): Promise<{ deleted: number }> {
    return this._client._request("DELETE", `/api/v1/agents/${this.id}/memories`);
  }

  toString(): string {
    return `Agent(id="${this.id}"${this.name ? `, name="${this.name}"` : ""})`;
  }
}

// ── Kronvex Client ───────────────────────────────────────────────────────────

export interface KronvexOptions {
  /** Override the default API base URL */
  baseUrl?: string;
  /** Request timeout in milliseconds — defaults to 30s */
  timeout?: number;
}

export class Kronvex {
  private readonly _apiKey: string;
  private readonly _baseUrl: string;
  private readonly _timeout: number;

  /**
   * Create a Kronvex client.
   *
   * @example
   * import { Kronvex } from "kronvex";
   *
   * const kx = new Kronvex(process.env.KRONVEX_API_KEY!);
   *
   * // Get an agent handle
   * const agent = kx.agent("agent-id");
   *
   * // Or create a new agent
   * const agent = await kx.createAgent("support-bot", "Customer support agent");
   */
  constructor(apiKey: string, options: KronvexOptions = {}) {
    if (!apiKey) throw new AuthenticationError("apiKey is required");
    this._apiKey  = apiKey;
    this._baseUrl = (options.baseUrl ?? BASE_URL).replace(/\/$/, "");
    this._timeout = options.timeout ?? 30_000;
  }

  /** Get an Agent handle by ID (does not make a network request). */
  agent(agentId: string): Agent {
    return new Agent(agentId, this);
  }

  /** List all agents for this API key. */
  async listAgents(): Promise<AgentData[]> {
    return this._request<AgentData[]>("GET", "/api/v1/agents");
  }

  /**
   * Create a new agent and return an Agent handle.
   *
   * @example
   * const agent = await kx.createAgent("support-bot", "Handles tier-1 support");
   */
  async createAgent(name: string, description?: string): Promise<Agent> {
    const data = await this._request<AgentData>("POST", "/api/v1/agents", {
      name,
      description: description ?? "",
    });
    return new Agent(data.id, this, data);
  }

  /** Get today's API usage and quota status. */
  async usage(): Promise<DailyUsage> {
    return this._request<DailyUsage>("GET", "/api/v1/usage/today");
  }

  /** @internal */
  async _request<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
    const url = `${this._baseUrl}${path}`;
    const isBodyless = method === "GET" || method === "DELETE";

    const controller = new AbortController();
    const timeoutId  = setTimeout(() => controller.abort(), this._timeout);

    let resp: Response;
    try {
      resp = await fetch(url, {
        method,
        headers: {
          "X-API-Key":    this._apiKey,
          "Content-Type": "application/json",
          "User-Agent":   `kronvex-node/${SDK_VERSION}`,
        },
        body: !isBodyless && body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") {
        throw new KronvexError("Request timed out after " + this._timeout + "ms");
      }
      throw new KronvexError(`Network error: ${err}`);
    } finally {
      clearTimeout(timeoutId);
    }

    if (resp.status === 204 || resp.headers.get("content-length") === "0") {
      return undefined as T;
    }

    let json: unknown;
    try { json = await resp.json(); } catch { json = {}; }

    if (resp.ok) return json as T;

    const detail = (json as Record<string, unknown>)?.detail;
    const msg = typeof detail === "string" ? detail :
                typeof detail === "object" && detail !== null ? JSON.stringify(detail) :
                String(json);

    if (resp.status === 401) throw new AuthenticationError(msg);
    if (resp.status === 404) throw new AgentNotFoundError(msg);
    if (resp.status === 402 || msg.toLowerCase().includes("memory limit")) throw new MemoryLimitError(msg);
    if (resp.status === 429) {
      if (msg.includes("daily_quota")) throw new DailyQuotaError(msg);
      throw new RateLimitError(msg, parseInt(resp.headers.get("Retry-After") ?? "60"));
    }
    throw new KronvexError(msg, resp.status);
  }
}

export default Kronvex;

// ── LangChain Integration ────────────────────────────────────────────────────

/**
 * LangChain BaseMemory-compatible adapter.
 * Persists conversation history to Kronvex across sessions.
 *
 * @example
 * import { KronvexMemory } from "kronvex/langchain";
 * import { ConversationChain } from "langchain/chains";
 * import { ChatOpenAI } from "langchain/chat_models/openai";
 *
 * const memory = new KronvexMemory({
 *   apiKey: process.env.KRONVEX_API_KEY!,
 *   agentId: "your-agent-id",
 *   sessionId: userId,
 * });
 *
 * const chain = new ConversationChain({ llm: new ChatOpenAI(), memory });
 */
export interface KronvexMemoryOptions {
  apiKey: string;
  agentId: string;
  sessionId?: string;
  topK?: number;
  inputKey?: string;
  outputKey?: string;
}

export class KronvexMemory {
  readonly memoryKeys: string[];
  private readonly _client: Kronvex;
  private readonly _agent: Agent;
  private readonly _sessionId?: string;
  private readonly _topK: number;
  readonly inputKey: string;
  readonly outputKey: string;

  constructor(options: KronvexMemoryOptions) {
    this._client    = new Kronvex(options.apiKey);
    this._agent     = this._client.agent(options.agentId);
    this._sessionId = options.sessionId;
    this._topK      = options.topK ?? 5;
    this.inputKey   = options.inputKey  ?? "input";
    this.outputKey  = options.outputKey ?? "output";
    this.memoryKeys = ["history"];
  }

  async loadMemoryVariables(values: Record<string, unknown>): Promise<{ history: string }> {
    const query = String(values[this.inputKey] ?? "");
    if (!query) return { history: "" };
    const context = await this._agent.injectContext(query, {
      top_k: this._topK,
      session_id: this._sessionId,
    });
    return { history: context };
  }

  async saveContext(
    inputValues: Record<string, unknown>,
    outputValues: Record<string, unknown>
  ): Promise<void> {
    const input  = String(inputValues[this.inputKey]   ?? "");
    const output = String(outputValues[this.outputKey] ?? "");
    await Promise.all([
      this._agent.remember(`User: ${input}`,  { memory_type: "episodic", session_id: this._sessionId }),
      this._agent.remember(`AI: ${output}`,    { memory_type: "episodic", session_id: this._sessionId }),
    ]);
  }

  async clear(): Promise<void> {
    // Only clear session memories to avoid deleting cross-session knowledge
    // For full clear use agent.clear()
  }
}
