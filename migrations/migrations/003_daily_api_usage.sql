-- Migration: daily_api_usage counter per api_key
-- Tracks OpenAI embedding calls per day to prevent runaway costs

CREATE TABLE IF NOT EXISTS daily_api_usage (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id  UUID NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    date        DATE NOT NULL DEFAULT CURRENT_DATE,
    recall_count    INTEGER NOT NULL DEFAULT 0,
    remember_count  INTEGER NOT NULL DEFAULT 0,
    inject_count    INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(api_key_id, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_usage_key_date ON daily_api_usage(api_key_id, date);

-- Function to increment usage atomically
CREATE OR REPLACE FUNCTION increment_daily_usage(
    p_api_key_id UUID,
    p_endpoint TEXT,  -- 'recall', 'remember', 'inject'
    p_tokens INTEGER DEFAULT 0
) RETURNS TABLE(recall_count INTEGER, remember_count INTEGER, inject_count INTEGER, total_tokens INTEGER) AS $$
BEGIN
    INSERT INTO daily_api_usage (api_key_id, date, recall_count, remember_count, inject_count, total_tokens)
    VALUES (p_api_key_id, CURRENT_DATE,
        CASE WHEN p_endpoint = 'recall'    THEN 1 ELSE 0 END,
        CASE WHEN p_endpoint = 'remember'  THEN 1 ELSE 0 END,
        CASE WHEN p_endpoint = 'inject'    THEN 1 ELSE 0 END,
        p_tokens
    )
    ON CONFLICT (api_key_id, date) DO UPDATE SET
        recall_count    = daily_api_usage.recall_count    + CASE WHEN p_endpoint = 'recall'   THEN 1 ELSE 0 END,
        remember_count  = daily_api_usage.remember_count  + CASE WHEN p_endpoint = 'remember' THEN 1 ELSE 0 END,
        inject_count    = daily_api_usage.inject_count    + CASE WHEN p_endpoint = 'inject'   THEN 1 ELSE 0 END,
        total_tokens    = daily_api_usage.total_tokens    + p_tokens,
        updated_at      = now();

    RETURN QUERY
        SELECT u.recall_count, u.remember_count, u.inject_count, u.total_tokens
        FROM daily_api_usage u
        WHERE u.api_key_id = p_api_key_id AND u.date = CURRENT_DATE;
END;
$$ LANGUAGE plpgsql;
