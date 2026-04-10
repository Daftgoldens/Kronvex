-- ═══════════════════════════════════════════════════════════════════════════
-- KRONVEX Sprint 4 Migration
-- 1. api_calls table (real API call tracking)
-- 2. RLS policies on api_keys, agents, memories
-- 3. Leaked password protection (auth config)
-- Run in Supabase SQL Editor
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. API CALLS TABLE ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS api_calls (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id  UUID NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    agent_id    UUID REFERENCES agents(id) ON DELETE SET NULL,
    endpoint    VARCHAR(50) NOT NULL,   -- 'remember' | 'recall' | 'inject_context'
    latency_ms  INTEGER,
    status_code INTEGER NOT NULL DEFAULT 200,
    called_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_api_calls_api_key_id ON api_calls(api_key_id);
CREATE INDEX IF NOT EXISTS ix_api_calls_called_at  ON api_calls(called_at DESC);
CREATE INDEX IF NOT EXISTS ix_api_calls_endpoint   ON api_calls(endpoint);

-- Partition hint: if api_calls grows large, partition by called_at monthly
-- For now, a simple cleanup job is enough:
CREATE OR REPLACE FUNCTION purge_old_api_calls()
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    -- Keep only last 90 days of call logs
    DELETE FROM api_calls WHERE called_at < NOW() - INTERVAL '90 days';
END;
$$;

-- Schedule via pg_cron (enable in Supabase → Database → Extensions):
-- SELECT cron.schedule('purge-api-calls', '0 3 * * 0', 'SELECT purge_old_api_calls()');


-- ── 2. ROW LEVEL SECURITY ────────────────────────────────────────────────────
-- Link is: auth.jwt()->>'email' = api_keys.contact_email
-- (No user_id FK — Kronvex links accounts via email)

-- Enable RLS
ALTER TABLE api_keys  ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents    ENABLE ROW LEVEL SECURITY;
ALTER TABLE memories  ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_calls ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if re-running
DROP POLICY IF EXISTS "api_keys_owner"  ON api_keys;
DROP POLICY IF EXISTS "agents_owner"    ON agents;
DROP POLICY IF EXISTS "memories_owner"  ON memories;
DROP POLICY IF EXISTS "api_calls_owner" ON api_calls;
DROP POLICY IF EXISTS "service_bypass"  ON api_keys;
DROP POLICY IF EXISTS "service_bypass"  ON agents;
DROP POLICY IF EXISTS "service_bypass"  ON memories;
DROP POLICY IF EXISTS "service_bypass"  ON api_calls;

-- ── api_keys: user can only see/edit their own key ──
CREATE POLICY "api_keys_owner" ON api_keys
    FOR ALL
    USING (contact_email = auth.jwt()->>'email');

-- ── agents: user can only see agents under their key ──
CREATE POLICY "agents_owner" ON agents
    FOR ALL
    USING (
        api_key_id IN (
            SELECT id FROM api_keys
            WHERE contact_email = auth.jwt()->>'email'
        )
    );

-- ── memories: user can only see memories under their agents ──
CREATE POLICY "memories_owner" ON memories
    FOR ALL
    USING (
        agent_id IN (
            SELECT a.id FROM agents a
            JOIN api_keys k ON a.api_key_id = k.id
            WHERE k.contact_email = auth.jwt()->>'email'
        )
    );

-- ── api_calls: user can only see their own call logs ──
CREATE POLICY "api_calls_owner" ON api_calls
    FOR ALL
    USING (
        api_key_id IN (
            SELECT id FROM api_keys
            WHERE contact_email = auth.jwt()->>'email'
        )
    );

-- ── Service role bypass (Railway backend uses service key → bypasses RLS) ──
-- This is automatic: Supabase service_role key bypasses RLS by default.
-- No additional policy needed. Just make sure Railway uses SUPABASE_SERVICE_KEY.


-- ── 3. LEAKED PASSWORD PROTECTION ────────────────────────────────────────────
-- Enable via Supabase Dashboard → Authentication → Password Security
-- Or via SQL (requires pg_tle extension, available on Pro plans):
-- SELECT auth.enable_leaked_password_check();
-- 
-- If not available via SQL, go to:
-- Supabase Dashboard → Auth → Settings → "Check for leaked passwords" → Enable


-- ── 4. BONUS: Auth rate limiting ─────────────────────────────────────────────
-- Already configured in Supabase Dashboard → Auth → Rate Limits
-- Recommended: max 5 signups/hour per IP, max 10 OTP/hour per email


-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFY
-- ═══════════════════════════════════════════════════════════════════════════
-- SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';
-- Should show: api_keys=true, agents=true, memories=true, api_calls=true
