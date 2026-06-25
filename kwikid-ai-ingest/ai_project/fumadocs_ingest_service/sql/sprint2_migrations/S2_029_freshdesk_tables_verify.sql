-- S2_029_freshdesk_tables_verify.sql
-- Sprint 2.29.2: Freshdesk table existence check + migration status report.
--
-- PURPOSE: Run this in the Supabase Dashboard SQL Editor to diagnose whether
-- the Sprint 2.28 tables exist.  Read-only — safe to run at any time.
--
-- If either table is missing, apply S2_028_freshdesk_foundation.sql first.
--
-- ── Table existence check ──────────────────────────────────────────────────

SELECT
    table_name,
    CASE WHEN table_name IS NOT NULL THEN 'EXISTS' END AS status
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('freshdesk_webhook_events', 'support_conversation_state')

UNION ALL

SELECT
    t.name AS table_name,
    'MISSING' AS status
FROM (VALUES
    ('freshdesk_webhook_events'),
    ('support_conversation_state')
) AS t(name)
WHERE t.name NOT IN (
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public'
);

-- ── Row counts (only meaningful if tables exist) ───────────────────────────

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'freshdesk_webhook_events'
    ) THEN
        RAISE NOTICE 'freshdesk_webhook_events row count: %',
            (SELECT COUNT(*) FROM freshdesk_webhook_events);
        RAISE NOTICE 'freshdesk_webhook_events by status: %',
            (SELECT json_agg(r) FROM (
                SELECT processing_status, COUNT(*) AS cnt
                FROM freshdesk_webhook_events
                GROUP BY processing_status
                ORDER BY cnt DESC
            ) r);
    ELSE
        RAISE WARNING 'freshdesk_webhook_events does NOT EXIST — run S2_028_freshdesk_foundation.sql';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'support_conversation_state'
    ) THEN
        RAISE NOTICE 'support_conversation_state row count: %',
            (SELECT COUNT(*) FROM support_conversation_state);
    ELSE
        RAISE WARNING 'support_conversation_state does NOT EXIST — run S2_028_freshdesk_foundation.sql';
    END IF;
END $$;
