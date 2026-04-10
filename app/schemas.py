import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

MemoryType = Literal["episodic", "semantic", "procedural", "fact", "preference", "context"]


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    metadata: dict = {}

class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    metadata: dict = {}
    created_at: datetime
    memory_count: int = 0
    model_config = {"from_attributes": True}


class RememberRequest(BaseModel):
    content: str = Field(..., min_length=1)
    session_id: str | None = None
    memory_type: MemoryType = "episodic"
    metadata: dict = {}
    ttl_days: int | None = Field(default=None, ge=1, le=3650)  # None = no expiry
    pinned: bool = False  # Pinned memories never expire

class MemoryResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    content: str
    session_id: str | None = None
    memory_type: str
    metadata: dict = {}
    created_at: datetime
    access_count: int = 0
    expires_at: datetime | None = None
    pinned: bool = False
    agent_memory_count: int | None = None  # populated by /remember, total memories for this agent
    deduplicated: bool = False             # True when an identical memory already existed
    superseded: bool = False               # True when superseded by a newer memory on the same topic
    superseded_by: uuid.UUID | None = None # ID of the memory that superseded this one
    conflict_detected: bool = False      # True when this new memory superseded ≥1 existing memories
    conflicts_resolved: int = 0          # Number of memories superseded by this new memory
    is_meta: bool = False
    consolidation_count: int = 0
    user_id: str | None = None
    model_config = {"from_attributes": True}

class SupersededMemory(BaseModel):
    id: uuid.UUID
    content: str
    memory_type: str
    superseded_at: datetime
    superseded_by: uuid.UUID
    created_at: datetime
    model_config = {"from_attributes": True}

class ConflictsResponse(BaseModel):
    agent_id: uuid.UUID
    total: int
    memories: list[SupersededMemory]

class RecallRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    session_id: str | None = None
    memory_type: MemoryType | None = None
    context_messages: list[dict] = Field(default_factory=list)  # A4: optional re-ranking context

class RecallResult(BaseModel):
    memory: MemoryResponse
    similarity: float
    confidence: float  # Composite: similarity×0.6 + recency×0.2 + frequency×0.2

class RecallResponse(BaseModel):
    query: str
    results: list[RecallResult]
    total_found: int

class InjectContextRequest(BaseModel):
    message: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)

class InjectContextResponse(BaseModel):
    context_block: str
    memories_used: int
    memories: list[RecallResult]


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)

class ApiKeyDemoCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=3, max_length=255)
    usecase: str = Field(..., min_length=10, max_length=1000)

class ApiKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    is_active: bool
    is_demo: bool
    plan: str = "demo"
    memory_limit: int | None
    agent_limit: int | None = None
    memories_used: int = 0
    deleted_memories_count: int = 0
    created_at: datetime
    last_used_at: datetime | None = None
    model_config = {"from_attributes": True}

class ApiKeyCreatedResponse(ApiKeyResponse):
    full_key: str

class DemoKeyCreatedResponse(BaseModel):
    full_key: str
    agent_id: str          # Agent créé automatiquement
    memory_limit: int
    message: str
    temp_password: str | None = None   # For auto-login to dashboard
    email: str | None = None


class BulkDeleteRequest(BaseModel):
    memory_ids: list[str] | None = None
    memory_type: str | None = None
    before_date: datetime | None = None


class AgentUpdateRequest(BaseModel):
    name: str | None = None
    metadata: dict | None = None


# ── WEBHOOK CONFIG ─────────────────────────────────────────────────────────────

VALID_WEBHOOK_EVENTS = {"memory.stored", "quota.warning", "quota.reached"}

class WebhookCreate(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    events: list[str] = Field(..., min_length=1)

    def model_post_init(self, _context):
        invalid = set(self.events) - VALID_WEBHOOK_EVENTS
        if invalid:
            raise ValueError(f"Unknown event(s): {invalid}. Valid: {VALID_WEBHOOK_EVENTS}")
        if not (self.url.startswith("http://") or self.url.startswith("https://")):
            raise ValueError("url must start with http:// or https://")

class WebhookResponse(BaseModel):
    id: uuid.UUID
    url: str
    events: list[str]
    created_at: datetime
    model_config = {"from_attributes": True}


# ── BULK IMPORT ────────────────────────────────────────────────────────────────

class BulkMemoryItem(BaseModel):
    content: str = Field(..., min_length=1)
    memory_type: MemoryType = "episodic"
    metadata: dict = {}
    ttl_days: int | None = Field(default=None, ge=1, le=3650)

class BulkImportRequest(BaseModel):
    memories: list[BulkMemoryItem] = Field(..., min_length=1, max_length=100)

class BulkImportResponse(BaseModel):
    imported: int
    failed: int
    errors: list[str]


# ── MEMORY RESTORE ─────────────────────────────────────────────────────────────

class MemoryRestoreResponse(BaseModel):
    id: str
    restored: bool


# ── KEY ROTATION ───────────────────────────────────────────────────────────────

class RotateKeyResponse(BaseModel):
    full_key: str
    key_prefix: str
    message: str


class HealthResponse(BaseModel):
    """A5 — Memory health score for an agent."""
    coverage_score: float
    freshness_score: float
    coherence_score: float
    utilization_score: float
    recommendations: list[str]


# ── INGEST ────────────────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    content: str = Field(..., min_length=10, description="Raw text or markdown to extract memories from")
    source: str | None = Field(None, description="Optional label (e.g. filename)")
    memory_type: MemoryType = Field("semantic", description="episodic | semantic | procedural")
    max_memories: int = Field(20, ge=1, le=500, description="Max memories to extract")

class IngestResponse(BaseModel):
    extracted: int
    memories: list[MemoryResponse]
    tokens_used: int
    source: str | None = None


class ReviewCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    role: str | None = Field(default=None, max_length=200)
    stars: int | None = Field(default=None, ge=1, le=5)
    message: str = Field(..., min_length=10, max_length=2000)
    review_type: str = Field(default="review", pattern="^(review|project|question)$")

class ReviewPublic(BaseModel):
    id: uuid.UUID
    name: str
    role: str | None = None
    stars: int | None = None
    message: str
    review_type: str
    created_at: datetime
    model_config = {"from_attributes": True}


# ── GRAPH MEMORY ──────────────────────────────────────────────────────────────

class EntityOut(BaseModel):
    id: uuid.UUID
    label: str
    entity_type: str  # person | organization | preference | fact | procedure
    memory_id: uuid.UUID
    created_at: datetime
    model_config = {"from_attributes": True}

class EntityRelationOut(BaseModel):
    id: uuid.UUID
    source_entity_id: uuid.UUID
    relation: str
    target_entity_id: uuid.UUID
    created_at: datetime
    model_config = {"from_attributes": True}

class GraphResponse(BaseModel):
    agent_id: uuid.UUID
    entities: list[EntityOut]
    relations: list[EntityRelationOut]
    total_entities: int
    total_relations: int
