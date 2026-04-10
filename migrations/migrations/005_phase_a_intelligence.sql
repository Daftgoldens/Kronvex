-- Phase A: Intelligence layer
-- NOTE: This migration must be run manually against Supabase via the SQL editor or psql
-- It is not automatically applied by the application startup sequence

-- A1: Memory consolidation columns
ALTER TABLE memories
  ADD COLUMN IF NOT EXISTS is_meta       BOOLEAN     NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS consolidation_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS consolidated_from   JSONB   NOT NULL DEFAULT '[]';

-- A2: Entity tables
CREATE TABLE IF NOT EXISTS entities (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id    UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  memory_id   UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  label       VARCHAR(512) NOT NULL,
  entity_type TEXT NOT NULL CHECK (entity_type IN ('person', 'organization', 'preference', 'fact', 'procedure')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_entities_agent_id    ON entities(agent_id);
CREATE INDEX IF NOT EXISTS ix_entities_memory_id   ON entities(memory_id);
CREATE INDEX IF NOT EXISTS ix_entities_label_agent ON entities(agent_id, label);

CREATE TABLE IF NOT EXISTS entity_relations (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id         UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  source_entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  relation         TEXT NOT NULL,  -- e.g. "has_role", "works_at", "prefers"
  target_entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_entity_relations_agent_id ON entity_relations(agent_id);
CREATE INDEX IF NOT EXISTS ix_entity_relations_source    ON entity_relations(source_entity_id);
CREATE INDEX IF NOT EXISTS ix_entity_relations_target    ON entity_relations(target_entity_id);

-- C4 prep: indexed user_id column on memories for GDPR erasure
ALTER TABLE memories
  ADD COLUMN IF NOT EXISTS user_id TEXT;

CREATE INDEX IF NOT EXISTS ix_memories_user_id ON memories(user_id)
  WHERE user_id IS NOT NULL;
