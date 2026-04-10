-- ═══════════════════════════════════════════════════════════════════════════
-- Migration 005: Security Advisor Fixes
-- Run in Supabase SQL Editor
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. Fix Function Search Path Mutable ──────────────────────────────────────
-- Prevents search_path injection attacks by pinning functions to public schema

ALTER FUNCTION public.purge_old_api_calls()
    SET search_path = public;

-- Two overloads exist: (uuid, text, integer) and (uuid, character varying, integer)
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT pg_get_function_identity_arguments(oid) AS args
        FROM pg_proc
        WHERE proname = 'increment_daily_usage'
          AND pronamespace = 'public'::regnamespace
    LOOP
        EXECUTE format(
            'ALTER FUNCTION public.increment_daily_usage(%s) SET search_path = public',
            r.args
        );
    END LOOP;
END $$;

ALTER FUNCTION public.get_user_organizations(UUID)
    SET search_path = public;

ALTER FUNCTION public.user_has_org_permission(UUID, UUID, TEXT)
    SET search_path = public;

ALTER FUNCTION public.accept_org_invitation(TEXT, UUID)
    SET search_path = public;


-- ── 2. RLS Enabled No Policy → add explicit deny-all policies ────────────────
-- These tables are backend-only (accessed via service_role which bypasses RLS).
-- USING (false) blocks anon/authenticated roles explicitly, silencing the warning.

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'daily_api_usage' AND policyname = 'deny_direct_access'
    ) THEN
        CREATE POLICY "deny_direct_access" ON public.daily_api_usage
            AS RESTRICTIVE USING (false);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'webhook_configs' AND policyname = 'deny_direct_access'
    ) THEN
        CREATE POLICY "deny_direct_access" ON public.webhook_configs
            AS RESTRICTIVE USING (false);
    END IF;
END $$;


-- ── 3. CMO + ancillary tables (backend/service_role only) ────────────────────
-- These tables are accessed only via service_role (which bypasses RLS).
-- Enabling RLS + deny-all blocks anon/authenticated direct access.

ALTER TABLE public.entities         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.entity_relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cmo_leads        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cmo_emails       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reviews          ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'entities' AND policyname = 'deny_direct_access') THEN
        CREATE POLICY "deny_direct_access" ON public.entities AS RESTRICTIVE USING (false);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'entity_relations' AND policyname = 'deny_direct_access') THEN
        CREATE POLICY "deny_direct_access" ON public.entity_relations AS RESTRICTIVE USING (false);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'cmo_leads' AND policyname = 'deny_direct_access') THEN
        CREATE POLICY "deny_direct_access" ON public.cmo_leads AS RESTRICTIVE USING (false);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'cmo_emails' AND policyname = 'deny_direct_access') THEN
        CREATE POLICY "deny_direct_access" ON public.cmo_emails AS RESTRICTIVE USING (false);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'reviews' AND policyname = 'deny_direct_access') THEN
        CREATE POLICY "deny_direct_access" ON public.reviews AS RESTRICTIVE USING (false);
    END IF;
END $$;
