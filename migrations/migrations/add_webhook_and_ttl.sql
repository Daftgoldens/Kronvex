-- Migration: add webhook_url, webhook_threshold to api_keys
-- Run once on production DB

ALTER TABLE api_keys 
  ADD COLUMN IF NOT EXISTS webhook_url TEXT,
  ADD COLUMN IF NOT EXISTS webhook_threshold INTEGER DEFAULT 80;

-- Index for TTL decay query (already runs on expires_at)
CREATE INDEX IF NOT EXISTS ix_memories_expires_at 
  ON memories(expires_at) 
  WHERE expires_at IS NOT NULL AND pinned = false;
