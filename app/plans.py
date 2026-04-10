"""
Kronvex — Plans & quotas.
Aligned with pricing.html / index.html pricing section.

Stripe Price IDs (test mode) — update CLAUDE.md when live IDs are created:
  builder  → price_REPLACE_BUILDER_29
  startup  → price_REPLACE_STARTUP_99
  business → price_REPLACE_BUSINESS_349

Note: "demo" is kept as an alias for "free" — stripe_router.py downgrades
subscriptions back to plan="demo", and auth_router.py creates demo keys.
Both resolve to the same Free plan config via get_plan().
"""
from typing import TypedDict


class Plan(TypedDict):
    name: str
    price_eur: int | None
    agents: int | None       # None = unlimited
    memories: int | None     # None = unlimited
    session_filtering: bool
    custom_embeddings: bool
    gdpr_dpa: bool
    sla_pct: float | None
    audit_trail: bool
    custom_ttl: bool


PLANS: dict[str, Plan] = {
    "free": {
        "name": "Free",
        "price_eur": 0,
        "agents": 1,
        "memories": 100,
        "session_filtering": False,
        "custom_embeddings": False,
        "gdpr_dpa": False,
        "sla_pct": None,
        "audit_trail": False,
        "custom_ttl": False,
    },
    "builder": {
        "name": "Builder",
        "price_eur": 29,
        "agents": 5,
        "memories": 20_000,
        "session_filtering": True,
        "custom_embeddings": False,
        "gdpr_dpa": False,
        "sla_pct": None,
        "audit_trail": False,
        "custom_ttl": False,
    },
    "startup": {
        "name": "Startup",
        "price_eur": 99,
        "agents": 15,
        "memories": 75_000,
        "session_filtering": True,
        "custom_embeddings": False,
        "gdpr_dpa": False,      # GDPR basics, no full DPA
        "sla_pct": None,
        "audit_trail": True,
        "custom_ttl": False,
    },
    "business": {
        "name": "Business",
        "price_eur": 349,
        "agents": 50,
        "memories": 500_000,
        "session_filtering": True,
        "custom_embeddings": False,
        "gdpr_dpa": True,
        "sla_pct": 99.9,
        "audit_trail": True,
        "custom_ttl": True,
    },
    "enterprise": {
        "name": "Enterprise",
        "price_eur": None,
        "agents": None,          # unlimited
        "memories": None,        # unlimited
        "session_filtering": True,
        "custom_embeddings": True,
        "gdpr_dpa": True,
        "sla_pct": None,         # custom SLA negotiated
        "audit_trail": True,
        "custom_ttl": True,
    },
}

# "demo" is a legacy alias for "free" (used by stripe_router on subscription cancel)
PLANS["demo"] = PLANS["free"]


INGEST_LIMITS = {
    "free":       {"max_chars": 5_000,   "max_memories": 10},
    "demo":       {"max_chars": 5_000,   "max_memories": 10},   # alias
    "builder":    {"max_chars": 100_000, "max_memories": 100},
    "startup":    {"max_chars": 150_000, "max_memories": 200},
    # Business and above share the same ceiling
    "business":   {"max_chars": 200_000, "max_memories": 500},
    "enterprise": {"max_chars": 200_000, "max_memories": 500},
}


def get_plan(plan_name: str) -> Plan:
    """Return plan config. Defaults to free if unknown."""
    return PLANS.get(plan_name, PLANS["free"])
