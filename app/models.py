import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, JSON, Index, ForeignKey, Boolean, Integer, CheckConstraint, text as sa_text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector

from app.database import Base
from app.config import settings


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Plan commercial
    plan: Mapped[str] = mapped_column(String(50), default="demo")

    # Demo mode
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_usecase: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Quotas (None = illimité)
    memory_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Key rotation — old key stays valid for 24h after rotation
    rotated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_memories_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Monthly billing cycle counter — resets to 0 on invoice.payment_succeeded
    cycle_memories_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cycle_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Subscription lifecycle — set when cancel_at_period_end fires
    subscription_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # RGPD: data deleted 30 days after subscription ends
    data_purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @property
    def memories_used(self) -> int:
        """Alias for cycle_memories_used — used by ApiKeyResponse schema (from_attributes)."""
        return self.cycle_memories_used or 0
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agents: Mapped[list["Agent"]] = relationship(back_populates="api_key", cascade="all, delete-orphan")
    webhook_configs: Mapped[list["WebhookConfig"]] = relationship(back_populates="api_key", cascade="all, delete-orphan")


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    api_key: Mapped["ApiKey"] = relationship(back_populates="agents")
    memories: Mapped[list["Memory"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dimensions), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    memory_type: Mapped[str] = mapped_column(String(50), default="episodic")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    access_count: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)

    # A1 — Consolidation
    is_meta: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    consolidation_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    consolidated_from: Mapped[list] = mapped_column(JSONB, default=list)  # list of source memory UUID strings

    # Contradiction detection — superseded by a newer memory on the same topic
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, default=None)

    # C4 — GDPR erasure by user_id
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="memories")


class WebhookConfig(Base):
    """Per-event webhook subscriptions registered by an API key."""
    __tablename__ = "webhook_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # e.g. ["memory.stored", "quota.warning"]
    secret: Mapped[str] = mapped_column(String(64), nullable=False)  # HMAC secret for X-Kronvex-Signature
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    api_key: Mapped["ApiKey"] = relationship(back_populates="webhook_configs")


Index("ix_webhook_configs_api_key_id", WebhookConfig.api_key_id)


Index("ix_memories_embedding_hnsw", Memory.embedding,
    postgresql_using="hnsw",
    postgresql_with={"m": 16, "ef_construction": 64},
    postgresql_ops={"embedding": "vector_cosine_ops"},
)


class ApiCall(Base):
    __tablename__ = "api_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(50), nullable=False)   # "remember" | "recall" | "inject_context"
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


Index("ix_api_calls_api_key_id", ApiCall.api_key_id)
Index("ix_api_calls_called_at", ApiCall.called_at)


Index("ix_memories_user_id", Memory.user_id, postgresql_where=sa_text("user_id IS NOT NULL"))


# ── CMO ───────────────────────────────────────────────────────────────────────

class CmoLead(Base):
    __tablename__ = "cmo_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    use_case: Mapped[str] = mapped_column(Text, nullable=False, default="")
    signal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    linkedin_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    emails: Mapped[list["CmoEmail"]] = relationship(back_populates="lead", cascade="all, delete-orphan")


class CmoEmail(Base):
    __tablename__ = "cmo_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(Integer, ForeignKey("cmo_leads.id", ondelete="CASCADE"), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_n: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    lead: Mapped["CmoLead"] = relationship(back_populates="emails")


Index("ix_cmo_leads_status", CmoLead.status)
Index("ix_cmo_leads_email", CmoLead.email, postgresql_where=sa_text("email != ''"))


class Entity(Base):
    """Entities extracted from memory content (A2 — Knowledge Graph)."""
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(512), nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('person', 'organization', 'preference', 'fact', 'procedure')",
            name="ck_entity_type_valid"
        ),
    )


class EntityRelation(Base):
    """Relationships between extracted entities (A2 — Knowledge Graph)."""
    __tablename__ = "entity_relations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    relation: Mapped[str] = mapped_column(String(100), nullable=False)
    target_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    review_type: Mapped[str] = mapped_column(String(20), default="review")  # review | project | question
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


Index("ix_entities_agent_id", Entity.agent_id)
Index("ix_entities_memory_id", Entity.memory_id)
Index("ix_entities_label_agent", Entity.agent_id, Entity.label)
Index("ix_entity_relations_agent_id", EntityRelation.agent_id)
Index("ix_entity_relations_source", EntityRelation.source_entity_id)
Index("ix_entity_relations_target", EntityRelation.target_entity_id)
