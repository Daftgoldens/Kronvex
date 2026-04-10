-- =============================================================================
-- Migration 004: Organizations / Multi-tenant
-- Kronvex — Run in Supabase SQL Editor (Dashboard → SQL Editor → New query)
-- =============================================================================

-- ── Organizations ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS organizations (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                   TEXT NOT NULL,
    slug                   TEXT NOT NULL UNIQUE,           -- URL-safe identifier
    plan                   TEXT NOT NULL DEFAULT 'demo',   -- mirrors api_keys.plan
    owner_id               UUID NOT NULL,                  -- supabase auth.users(id)
    stripe_customer_id     TEXT,
    stripe_subscription_id TEXT,
    billing_email          TEXT,
    custom_mem_limit       INTEGER,                        -- NULL = use plan default
    custom_agent_limit     INTEGER,
    settings               JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_orgs_owner ON organizations(owner_id);
CREATE INDEX IF NOT EXISTS idx_orgs_slug  ON organizations(slug);

-- ── Members ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS organization_members (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id         UUID,                                  -- NULL until invite accepted
    invited_email   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'developer',     -- owner|admin|developer|viewer
    status          TEXT NOT NULL DEFAULT 'pending',       -- pending|active|suspended
    invited_by      UUID NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    accepted_at     TIMESTAMPTZ,
    UNIQUE(organization_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_members_org    ON organization_members(organization_id);
CREATE INDEX IF NOT EXISTS idx_members_user   ON organization_members(user_id);
CREATE INDEX IF NOT EXISTS idx_members_email  ON organization_members(invited_email);

-- ── Invitations ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS org_invitations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    invited_email   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'developer',
    token           TEXT NOT NULL UNIQUE DEFAULT gen_random_uuid()::text,
    invited_by      UUID NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '7 days'),
    accepted_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invites_email ON org_invitations(invited_email);
CREATE INDEX IF NOT EXISTS idx_invites_token ON org_invitations(token);

-- ── Extend api_keys ───────────────────────────────────────────────────────────
ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS organization_id  UUID REFERENCES organizations(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS member_id        UUID REFERENCES organization_members(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS scoped_agent_ids UUID[],  -- NULL = access all agents
    ADD COLUMN IF NOT EXISTS key_name         TEXT;

CREATE INDEX IF NOT EXISTS idx_apikeys_org ON api_keys(organization_id);

-- ── Org-level daily usage aggregation ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS org_daily_usage (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    date            DATE NOT NULL DEFAULT CURRENT_DATE,
    recall_count    INTEGER NOT NULL DEFAULT 0,
    remember_count  INTEGER NOT NULL DEFAULT 0,
    inject_count    INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(organization_id, date)
);

CREATE INDEX IF NOT EXISTS idx_org_usage_date ON org_daily_usage(organization_id, date);

-- ── RLS ───────────────────────────────────────────────────────────────────────
ALTER TABLE organizations        ENABLE ROW LEVEL SECURITY;
ALTER TABLE organization_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_invitations      ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_daily_usage      ENABLE ROW LEVEL SECURITY;

-- Organizations: any active member can SELECT, only owner can UPDATE/DELETE
CREATE POLICY "org_select" ON organizations FOR SELECT USING (
    owner_id = auth.uid() OR
    id IN (
        SELECT organization_id FROM organization_members
        WHERE user_id = auth.uid() AND status = 'active'
    )
);
CREATE POLICY "org_update" ON organizations FOR UPDATE USING (owner_id = auth.uid());
CREATE POLICY "org_delete" ON organizations FOR DELETE USING (owner_id = auth.uid());
CREATE POLICY "org_insert" ON organizations FOR INSERT WITH CHECK (owner_id = auth.uid());

-- Members: user can see their own rows; admins/owners can see all org rows
CREATE POLICY "member_select" ON organization_members FOR SELECT USING (
    user_id = auth.uid() OR
    organization_id IN (
        SELECT organization_id FROM organization_members
        WHERE user_id = auth.uid() AND role IN ('owner','admin') AND status = 'active'
    )
);
CREATE POLICY "member_manage" ON organization_members FOR ALL USING (
    organization_id IN (
        SELECT organization_id FROM organization_members
        WHERE user_id = auth.uid() AND role IN ('owner','admin') AND status = 'active'
    )
);

-- Invitations: org admins can manage
CREATE POLICY "invite_select" ON org_invitations FOR SELECT USING (
    invited_email = (SELECT email FROM auth.users WHERE id = auth.uid()) OR
    organization_id IN (
        SELECT organization_id FROM organization_members
        WHERE user_id = auth.uid() AND role IN ('owner','admin') AND status = 'active'
    )
);
CREATE POLICY "invite_manage" ON org_invitations FOR ALL USING (
    organization_id IN (
        SELECT organization_id FROM organization_members
        WHERE user_id = auth.uid() AND role IN ('owner','admin') AND status = 'active'
    )
);

-- Org usage: members can see their org's usage
CREATE POLICY "org_usage_select" ON org_daily_usage FOR SELECT USING (
    organization_id IN (
        SELECT organization_id FROM organization_members
        WHERE user_id = auth.uid() AND status = 'active'
    )
);

-- ── Helper functions ──────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION get_user_organizations(p_user_id UUID)
RETURNS TABLE(
    org_id   UUID,
    org_name TEXT,
    org_slug TEXT,
    role     TEXT,
    plan     TEXT,
    member_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        o.id,
        o.name,
        o.slug,
        m.role,
        o.plan,
        (SELECT COUNT(*) FROM organization_members
         WHERE organization_id = o.id AND status = 'active')
    FROM organizations o
    JOIN organization_members m ON m.organization_id = o.id
    WHERE m.user_id = p_user_id AND m.status = 'active'
    ORDER BY o.created_at ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE FUNCTION user_has_org_permission(
    p_user_id UUID,
    p_org_id  UUID,
    p_min_role TEXT DEFAULT 'viewer'   -- 'viewer'|'developer'|'admin'|'owner'
) RETURNS BOOLEAN AS $$
DECLARE
    role_order TEXT[] := ARRAY['viewer','developer','admin','owner'];
    user_role  TEXT;
BEGIN
    SELECT role INTO user_role
    FROM organization_members
    WHERE user_id = p_user_id AND organization_id = p_org_id AND status = 'active';

    IF user_role IS NULL THEN RETURN FALSE; END IF;
    RETURN array_position(role_order, user_role) >= array_position(role_order, p_min_role);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Accept an invitation and activate membership
CREATE OR REPLACE FUNCTION accept_org_invitation(p_token TEXT, p_user_id UUID)
RETURNS JSON AS $$
DECLARE
    inv org_invitations%ROWTYPE;
    mem_id UUID;
BEGIN
    -- Fetch and validate token
    SELECT * INTO inv FROM org_invitations
    WHERE token = p_token AND accepted_at IS NULL AND expires_at > now();

    IF NOT FOUND THEN
        RETURN json_build_object('error', 'Invalid or expired invitation token');
    END IF;

    -- Upsert membership
    INSERT INTO organization_members(organization_id, user_id, invited_email, role, status, invited_by, accepted_at)
    VALUES (inv.organization_id, p_user_id, inv.invited_email, inv.role, 'active', inv.invited_by, now())
    ON CONFLICT (organization_id, user_id) DO UPDATE
        SET role = EXCLUDED.role, status = 'active', accepted_at = now()
    RETURNING id INTO mem_id;

    -- Mark invitation as accepted
    UPDATE org_invitations SET accepted_at = now() WHERE id = inv.id;

    RETURN json_build_object('ok', true, 'member_id', mem_id, 'organization_id', inv.organization_id);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON TABLE organizations        IS 'Multi-tenant orgs — B2B accounts with shared agents';
COMMENT ON TABLE organization_members IS 'Org membership: owner|admin|developer|viewer';
COMMENT ON TABLE org_invitations      IS 'Email-based invitation tokens (7-day expiry)';
