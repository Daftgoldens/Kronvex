"""
Kronvex API - Live Integration Test Suite
Usage: python test_live.py [API_KEY]
       python test_live.py           # creates a fresh demo key
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import time
import uuid
import httpx

# ── Config ────────────────────────────────────────────────────────────────────
BASE = "https://api.kronvex.io"
DEMO_EMAIL = f"test-{uuid.uuid4().hex[:6]}@demo-test.com"

# ── Colors ────────────────────────────────────────────────────────────────────
G  = "\033[92m"   # green
Y  = "\033[93m"   # yellow
R  = "\033[91m"   # red
B  = "\033[94m"   # blue
C  = "\033[96m"   # cyan
M  = "\033[95m"   # magenta
W  = "\033[97m"   # white bold
DIM= "\033[2m"
RST= "\033[0m"

passed = failed = 0

def header(title):
    width = 60
    print(f"\n{B}{'─'*width}{RST}")
    print(f"{W}  {title}{RST}")
    print(f"{B}{'─'*width}{RST}")

def ok(label, detail=""):
    global passed
    passed += 1
    d = f"  {DIM}{detail}{RST}" if detail else ""
    print(f"  {G}✓{RST}  {label}{d}")

def fail(label, detail=""):
    global failed
    failed += 1
    d = f"  {R}{detail}{RST}" if detail else ""
    print(f"  {R}✗{RST}  {label}{d}")

def info(label, val=""):
    v = f"  {C}{val}{RST}" if val else ""
    print(f"  {Y}→{RST}  {label}{v}")

def check(cond, label, detail=""):
    if cond:
        ok(label, detail)
    else:
        fail(label, detail)
    return cond

def req(method, path, *, headers=None, json_body=None):
    url = BASE + path
    h = headers or {}
    t0 = time.monotonic()
    r = httpx.request(method, url, headers=h, json=json_body, timeout=20)
    ms = int((time.monotonic() - t0) * 1000)
    return r, ms


# ══════════════════════════════════════════════════════════════════════════════
# 0 — AUTH / DEMO KEY
# ══════════════════════════════════════════════════════════════════════════════
header("0 · Auth — Create demo key")

if len(sys.argv) > 1:
    API_KEY = sys.argv[1]
    AGENT_ID = None
    info("Using provided API key", API_KEY[:12] + "…")
else:
    r, ms = req("POST", "/auth/demo", json_body={
        "name": "Baptiste Test",
        "email": DEMO_EMAIL,
        "usecase": "Live integration test suite"
    })
    if check(r.status_code in (200, 201), "POST /auth/demo", f"{r.status_code} · {ms}ms"):
        d = r.json()
        API_KEY  = d["full_key"]
        AGENT_ID = d.get("agent_id")
        info("API key", API_KEY[:16] + "…")
        info("Agent ID (pre-created)", AGENT_ID)
        info("Memory limit", str(d.get("memory_limit")))
    else:
        print(f"\n{R}Cannot continue without API key: {r.text}{RST}")
        sys.exit(1)

H = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


# ══════════════════════════════════════════════════════════════════════════════
# 1 — AGENTS
# ══════════════════════════════════════════════════════════════════════════════
header("1 · Agents — CRUD")

# List agents
r, ms = req("GET", "/api/v1/agents", headers=H)
check(r.status_code == 200, "GET /api/v1/agents", f"{ms}ms")
agents = r.json() if r.status_code == 200 else []
info("Existing agents", str(len(agents)))

# Create or reuse agent
if AGENT_ID:
    r, ms = req("GET", f"/api/v1/agents/{AGENT_ID}", headers=H)
    check(r.status_code == 200, f"GET /api/v1/agents/{{id}} (pre-created)", f"{ms}ms")
else:
    r, ms = req("POST", "/api/v1/agents", headers=H, json_body={
        "name": "Kronvex Test Agent",
        "description": "Integration test agent"
    })
    check(r.status_code == 201, "POST /api/v1/agents", f"{r.status_code} · {ms}ms")
    if r.status_code == 201:
        AGENT_ID = r.json()["id"]
        info("Created agent", AGENT_ID)
    else:
        # Reuse first existing agent
        if agents:
            AGENT_ID = agents[0]["id"]
            info("Reusing existing agent", AGENT_ID)
        else:
            print(f"\n{R}No agent available. {r.text}{RST}")
            sys.exit(1)

A = f"/api/v1/agents/{AGENT_ID}"

# Update agent
r, ms = req("PATCH", A, headers=H, json_body={"description": "Updated by test suite"})
check(r.status_code == 200, "PATCH /api/v1/agents/{id}", f"{ms}ms")

# Health
r, ms = req("GET", f"{A}/health", headers=H)
check(r.status_code == 200, "GET /api/v1/agents/{id}/health", f"{ms}ms")
if r.status_code == 200:
    h = r.json()
    info("Health score", str(h.get("score", "?")))


# ══════════════════════════════════════════════════════════════════════════════
# 2 — REMEMBER (store memories)
# ══════════════════════════════════════════════════════════════════════════════
header("2 · Remember — Store memories")

SESSION = str(uuid.uuid4())
memories_stored = []

MEMORIES = [
    ("Alice Martin is a Premium customer since January 2023. She renewed in March 2024.", "semantic"),
    ("Alice had a billing dispute #4821 in November 2023, resolved with a €50 credit.", "episodic"),
    ("Alice prefers email communication and dislikes phone calls.", "preference"),
    ("TechCorp evaluated Mem0, Zep, and Kronvex. Kronvex won on EU hosting.", "semantic"),
    ("Deployment runbook: always run migrations before restarting the API container.", "procedural"),
    ("Marc Dubois joined April 2, Python dev, building a customer support bot with LangChain.", "semantic"),
]

for content, mtype in MEMORIES:
    r, ms = req("POST", f"{A}/remember", headers=H, json_body={
        "content": content,
        "memory_type": mtype,
        "session_id": SESSION,
        "metadata": {"source": "test_suite", "scenario": "integration"}
    })
    if check(r.status_code == 201, f"POST /remember [{mtype}]", f"{ms}ms · {content[:45]}…"):
        memories_stored.append(r.json())
    time.sleep(0.1)

info("Memories stored", str(len(memories_stored)))
MEM_ID = memories_stored[0]["id"] if memories_stored else None


# ══════════════════════════════════════════════════════════════════════════════
# 3 — INGEST (document extraction)
# ══════════════════════════════════════════════════════════════════════════════
header("3 · Ingest — Extract memories from document")

DOC = """
# Q3 Sales Review — July 2024

## Key Accounts
- **Acme Corp**: Upgraded to Pro plan. Annual contract €12,000. Contact: sarah@acme.com
- **DataFlow Ltd**: Renewed Starter, considering upgrade. 3 support tickets in Q3.
- **NovaTech**: New logo won in August. Pilot starts September 1st. Budget: €8,000/year.

## Pipeline
Total pipeline: €340,000. Top opportunity: GlobalBank (€120,000, closing Q4).

## Action Items
1. Follow up with DataFlow about Pro upgrade before September 15th
2. Send NovaTech onboarding docs by August 28th
3. Schedule GlobalBank executive demo for September
"""

r, ms = req("POST", f"{A}/ingest", headers=H, json_body={
    "content": DOC,
    "source": "Q3_sales_review",
    "memory_type": "semantic",
    "max_memories": 8
})
check(r.status_code in (200, 201), "POST /ingest (document extraction)", f"{r.status_code} · {ms}ms")
if r.status_code == 200:
    d = r.json()
    info("Memories extracted", str(d.get("extracted", 0)))
    info("Tokens used", str(d.get("tokens_used", "?")))


# ══════════════════════════════════════════════════════════════════════════════
# 4 — RECALL (semantic search)
# ══════════════════════════════════════════════════════════════════════════════
header("4 · Recall — Semantic search")

QUERIES = [
    ("Who is Alice and what is her tier?", 0.1),
    ("billing dispute credit history", 0.1),
    ("deployment process runbook", 0.1),
    ("EU GDPR data residency compliance", 0.1),
    ("NovaTech pipeline deal size", 0.1),
]

for query, threshold in QUERIES:
    r, ms = req("POST", f"{A}/recall", headers=H, json_body={
        "query": query,
        "top_k": 3,
        "threshold": threshold
    })
    if check(r.status_code == 200, f"POST /recall · \"{query[:40]}\"", f"{ms}ms"):
        d = r.json()
        n = d.get("total_found", 0)
        results = d.get("results", [])
        if results:
            top = results[0]
            conf = top.get("confidence", 0)
            info(f"  {n} found · top confidence {conf:.2f} · \"{top['memory']['content'][:50]}…\"")
        else:
            info(f"  0 results found")
    time.sleep(0.15)


# ══════════════════════════════════════════════════════════════════════════════
# 5 — INJECT CONTEXT
# ══════════════════════════════════════════════════════════════════════════════
header("5 · Inject-context — LLM prompt enrichment")

IC_QUERIES = [
    "What do we know about Alice Martin?",
    "How should I handle the NovaTech onboarding?",
    "What's the status of our top pipeline deals?",
]

for q in IC_QUERIES:
    r, ms = req("POST", f"{A}/inject-context", headers=H, json_body={
        "message": q,
        "top_k": 3,
        "threshold": 0.1
    })
    if check(r.status_code == 200, f"POST /inject-context · \"{q[:40]}\"", f"{ms}ms"):
        d = r.json()
        mu = d.get("memories_used", 0)
        cb = d.get("context_block", "")
        info(f"  {mu} memories injected · {len(cb)} chars context block")
    time.sleep(0.15)


# ══════════════════════════════════════════════════════════════════════════════
# 6 — MEMORY CRUD
# ══════════════════════════════════════════════════════════════════════════════
header("6 · Memory CRUD — List, get, update, delete, restore")

# List memories
r, ms = req("GET", f"{A}/memories?limit=10", headers=H)
check(r.status_code == 200, "GET /memories", f"{ms}ms")
if r.status_code == 200:
    mems = r.json()
    total = len(mems) if isinstance(mems, list) else mems.get("total", "?")
    info("Total memories", str(total))

# List with search filter
r, ms = req("GET", f"{A}/memories?search=Alice&limit=5", headers=H)
check(r.status_code == 200, "GET /memories?search=Alice", f"{ms}ms")

# List sessions
r, ms = req("GET", f"{A}/sessions", headers=H)
check(r.status_code == 200, "GET /sessions", f"{ms}ms")
if r.status_code == 200:
    sessions = r.json()
    info("Sessions found", str(len(sessions)))

# Update a memory
if MEM_ID:
    r, ms = req("PATCH", f"{A}/memories/{MEM_ID}", headers=H, json_body={
        "content": "Alice Martin is a Premium customer since January 2023. Renewed March 2024. High loyalty score.",
        "pinned": True
    })
    check(r.status_code == 200, f"PATCH /memories/{{id}} (update + pin)", f"{ms}ms")

# Export
r, ms = req("GET", f"{A}/memories/export", headers=H)
check(r.status_code == 200, "GET /memories/export", f"{ms}ms")
if r.status_code == 200:
    exported = r.json()
    mems = exported.get("memories", exported) if isinstance(exported, dict) else exported
    info("Exported memories", str(len(mems) if isinstance(mems, list) else exported.get("total", "?")))


# ══════════════════════════════════════════════════════════════════════════════
# 7 — BULK IMPORT
# ══════════════════════════════════════════════════════════════════════════════
header("7 · Bulk import — Batch memory creation")

BULK = [
    {"content": "GlobalBank — Enterprise prospect, €120K ARR, Q4 close. Contact: pierre.martin@globalbank.fr", "memory_type": "semantic"},
    {"content": "Pricing policy: never discount more than 20% without VP approval.", "memory_type": "procedural"},
    {"content": "Kronvex differentiator: only EU-native memory API with Art. 17 GDPR erasure.", "memory_type": "semantic"},
    {"content": "Customer NPS as of July 2024: 72. Target: 80 by end of year.", "memory_type": "fact"},
    {"content": "On-call rotation: Mon-Wed Baptiste, Thu-Fri Thomas. Pagerduty escalation after 5min.", "memory_type": "procedural"},
]

r, ms = req("POST", f"{A}/memories/bulk-import", headers=H, json_body={"memories": BULK})
check(r.status_code in (200, 201), "POST /memories/bulk-import", f"{r.status_code} · {ms}ms")
if r.status_code in (200, 201):
    d = r.json()
    info(f"  Imported {d.get('imported', 0)} · Failed {d.get('failed', 0)}")


# ══════════════════════════════════════════════════════════════════════════════
# 8 — GRAPH + CONFLICTS + CONSOLIDATION
# ══════════════════════════════════════════════════════════════════════════════
header("8 · Advanced — Graph, conflicts, consolidation, analytics")

r, ms = req("GET", f"{A}/graph", headers=H)
check(r.status_code == 200, "GET /graph (entity relationship graph)", f"{ms}ms")
if r.status_code == 200:
    g = r.json()
    info("Entities", str(len(g.get("entities", []))))
    info("Relations", str(len(g.get("relations", []))))

r, ms = req("GET", f"{A}/conflicts", headers=H)
check(r.status_code == 200, "GET /conflicts", f"{ms}ms")
if r.status_code == 200:
    c = r.json()
    info("Conflicts found", str(len(c.get("conflicts", []))))

r, ms = req("POST", f"{A}/consolidate", headers=H, json_body={})
check(r.status_code == 200, "POST /consolidate", f"{ms}ms")

r, ms = req("GET", f"{A}/analytics", headers=H)
check(r.status_code == 200, "GET /analytics", f"{ms}ms")
if r.status_code == 200:
    a = r.json()
    info("Total memories (analytics)", str(a.get("total_memories", "?")))
    info("Memory types breakdown", str(a.get("by_type", {})))


# ══════════════════════════════════════════════════════════════════════════════
# 9 — USAGE & STATS
# ══════════════════════════════════════════════════════════════════════════════
header("9 · Usage & Stats")

r, ms = req("GET", "/api/v1/usage/today", headers=H)
check(r.status_code == 200, "GET /usage/today", f"{ms}ms")
if r.status_code == 200:
    u = r.json()
    today = u.get("today", u)
    info("Recalls today", str(today.get("recalls", "?")))
    info("Stores today", str(today.get("stores", "?")))
    info("Injects today", str(today.get("injects", "?")))

r, ms = req("GET", "/api/v1/stats/weekly", headers=H)
check(r.status_code == 200, "GET /stats/weekly", f"{ms}ms")


# ══════════════════════════════════════════════════════════════════════════════
# 10 — SCENARIO: Customer Support Bot
# ══════════════════════════════════════════════════════════════════════════════
header("10 · End-to-end scenario — Customer Support")

print(f"\n  {M}Scenario:{RST} A support agent receives a ticket from Alice Martin.")
print(f"  {DIM}The agent calls inject-context to retrieve her full history before responding.{RST}\n")

TICKET = "Customer Alice Martin is complaining about a charge on her invoice. She's threatening to cancel."

r, ms = req("POST", f"{A}/inject-context", headers=H, json_body={
    "message": TICKET,
    "top_k": 5,
    "threshold": 0.05
})
if check(r.status_code == 200, "inject-context for support ticket", f"{ms}ms"):
    d = r.json()
    block = d.get("context_block", "")
    mu = d.get("memories_used", 0)
    print(f"\n  {C}Context block injected into LLM ({mu} memories, {len(block)} chars):{RST}")
    for line in block.split("\n")[:8]:
        if line.strip():
            print(f"  {DIM}{line}{RST}")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# 11 — SCENARIO: TTL & Pinned memories
# ══════════════════════════════════════════════════════════════════════════════
header("11 · TTL & Pinned memories")

# Short-lived memory (1 hour TTL)
r, ms = req("POST", f"{A}/remember", headers=H, json_body={
    "content": "Temporary promo: 30% off until end of day — for new signups only.",
    "memory_type": "fact",
    "ttl_seconds": 3600,
    "session_id": SESSION
})
check(r.status_code == 201, "POST /remember with ttl_seconds=3600", f"{ms}ms")
if r.status_code == 201:
    exp = r.json().get("expires_at")
    info("Expires at", str(exp))

# Pinned memory (never expires)
r, ms = req("POST", f"{A}/remember", headers=H, json_body={
    "content": "GDPR Art. 17 — all user data must be erasable within 72h of request.",
    "memory_type": "procedural",
    "pinned": True,
    "session_id": SESSION
})
check(r.status_code == 201, "POST /remember with pinned=True", f"{ms}ms")
if r.status_code == 201:
    pinned = r.json().get("pinned")
    info("Pinned", str(pinned))


# ══════════════════════════════════════════════════════════════════════════════
# 12 — DELETE & RESTORE
# ══════════════════════════════════════════════════════════════════════════════
header("12 · Delete & Restore")

# Create a memory to delete
r, ms = req("POST", f"{A}/remember", headers=H, json_body={
    "content": "This memory will be deleted then restored.",
    "memory_type": "episodic",
    "session_id": SESSION
})
if r.status_code == 201:
    del_id = r.json()["id"]
    # Delete it
    r2, ms2 = req("DELETE", f"{A}/memories/{del_id}", headers=H)
    check(r2.status_code == 204, f"DELETE /memories/{{id}}", f"{ms2}ms")
    # Restore it
    r3, ms3 = req("POST", f"{A}/memories/{del_id}/restore", headers=H)
    check(r3.status_code == 200, f"POST /memories/{{id}}/restore", f"{ms3}ms")
    info("Restored content", r3.json().get("content", "?")[:50] if r3.status_code == 200 else "failed")


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
total = passed + failed
width = 60
print(f"\n{B}{'═'*width}{RST}")
print(f"{W}  RESULTS{RST}")
print(f"{B}{'═'*width}{RST}")
print(f"  {G}✓  {passed} passed{RST}")
if failed:
    print(f"  {R}✗  {failed} failed{RST}")
print(f"  Total: {total} checks")
pct = int(passed / total * 100) if total else 0
bar_filled = int(pct / 2)
bar = G + "█" * bar_filled + DIM + "░" * (50 - bar_filled) + RST
print(f"\n  [{bar}] {pct}%")
print(f"\n  {DIM}API: {BASE}{RST}")
print(f"  {DIM}Agent: {AGENT_ID}{RST}")
if failed == 0:
    print(f"\n  {G}All endpoints healthy ✓{RST}")
else:
    print(f"\n  {Y}{failed} issue(s) to investigate{RST}")
print(f"{B}{'═'*width}{RST}\n")
