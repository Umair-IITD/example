-- =============================================================================
-- B1_009_sop_unique_constraint.sql
-- Add index_version to rag_sop_chunks UNIQUE constraint.
--
-- WHY:
--   The original constraint UNIQUE(sop_id, chunk_index, sop_version) does not
--   include index_version. This causes a duplicate-key violation when
--   reingest_v2.py attempts to INSERT v2 rows alongside existing v1 rows:
--
--     reingest_v2 chunk ID  = uuid5("v2:sop:{sop_id}:{idx}:{hash}")   ← new UUID
--     sop_pipeline chunk ID = uuid5("sop_chunk:{sop_id}:{idx}:{ver}") ← original UUID
--
--   Because the two IDs differ, the ON CONFLICT(id) upsert in reingest_v2
--   falls through to INSERT. Without index_version in the UNIQUE boundary,
--   that INSERT conflicts with the existing v1 row.
--
-- FIX:
--   Drop the 3-column constraint and recreate it as 4-column:
--     UNIQUE (sop_id, chunk_index, sop_version, index_version)
--
--   This mirrors the uniqueness boundary already used by:
--     rag_ticket_chunks:    UNIQUE (ticket_id, chunk_index, index_version)
--     rag_knowledge_chunks: UNIQUE (article_id, chunk_index, index_version)
--
-- SAFETY:
--   All existing rows have index_version = 'v1' from original ingestion.
--   No existing data can violate the new constraint because no two rows
--   shared (sop_id, chunk_index, sop_version) before this migration
--   (the old constraint enforced that). Adding index_version = 'v1' to the
--   tuple is always unique for the existing data set.
--
-- IDEMPOTENT:
--   Wrapped in DO block — safe to re-run. If the 4-column constraint already
--   exists, the migration is a no-op.
--
-- Prerequisites: B1_002_sop_library.sql already applied.
-- =============================================================================

DO $$
DECLARE
    v_has_index_version BOOLEAN;
BEGIN
    -- Check whether the current constraint already includes index_version.
    -- pg_constraint.conkey is an array of attribute numbers; we join against
    -- pg_attribute to resolve names.
    SELECT EXISTS (
        SELECT 1
        FROM   pg_constraint  c
        JOIN   pg_attribute   a
               ON  a.attrelid = c.conrelid
               AND a.attnum   = ANY(c.conkey)
        WHERE  c.conname   = 'rag_sop_chunks_unique_position'
          AND  c.conrelid  = 'public.rag_sop_chunks'::regclass
          AND  a.attname   = 'index_version'
    ) INTO v_has_index_version;

    IF v_has_index_version THEN
        RAISE NOTICE 'B1_009: rag_sop_chunks_unique_position already includes index_version — skipping';

    ELSIF EXISTS (
        SELECT 1
        FROM   pg_constraint
        WHERE  conname  = 'rag_sop_chunks_unique_position'
          AND  conrelid = 'public.rag_sop_chunks'::regclass
    ) THEN
        -- Old 3-column constraint exists — drop and recreate with 4 columns.
        ALTER TABLE public.rag_sop_chunks
            DROP CONSTRAINT rag_sop_chunks_unique_position;

        ALTER TABLE public.rag_sop_chunks
            ADD CONSTRAINT rag_sop_chunks_unique_position
            UNIQUE (sop_id, chunk_index, sop_version, index_version);

        RAISE NOTICE 'B1_009: Replaced rag_sop_chunks_unique_position with (sop_id, chunk_index, sop_version, index_version)';

    ELSE
        -- Constraint does not exist at all (fresh database) — create it.
        ALTER TABLE public.rag_sop_chunks
            ADD CONSTRAINT rag_sop_chunks_unique_position
            UNIQUE (sop_id, chunk_index, sop_version, index_version);

        RAISE NOTICE 'B1_009: Created rag_sop_chunks_unique_position with (sop_id, chunk_index, sop_version, index_version)';
    END IF;
END$$;

-- =============================================================================
-- Verification
-- =============================================================================
-- Confirm the new constraint definition after applying:
--
-- SELECT c.conname, array_agg(a.attname ORDER BY a.attnum) AS columns
-- FROM   pg_constraint  c
-- JOIN   pg_attribute   a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
-- WHERE  c.conname  = 'rag_sop_chunks_unique_position'
--   AND  c.conrelid = 'public.rag_sop_chunks'::regclass
-- GROUP  BY c.conname;
--
-- Expected result:
--   conname                         | columns
--   rag_sop_chunks_unique_position  | {sop_id,chunk_index,sop_version,index_version}
-- =============================================================================
