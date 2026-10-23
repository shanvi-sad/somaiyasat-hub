-- ============================================================================
-- SomaiyaSat Telemetry Hub - 05_security_dcl.sql
-- ============================================================================
-- Lab Experiments Covered:
--   - Experiment 7: Data Control Language (DCL) - Role-Based Access Control (RBAC),
--     Principle of Least Privilege, Security Administration (GRANT & REVOKE)
-- ============================================================================
-- This script is completely IDEMPOTENT and can be re-run safely.
-- It defines four standard database roles matching SPEC.md:
--   1. sat_admin    : Complete control with administrative grant authority
--   2. sat_operator : Full read access, operational data ingestion, stored functions
--   3. sat_ham      : External amateur radio volunteers (telemetry delivery stats)
--   4. sat_viewer   : Read-only observation views and active alert monitoring
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Idempotent Role Creation (Exp 7: Defining Security Principals)
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sat_admin') THEN
        CREATE ROLE sat_admin;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sat_operator') THEN
        CREATE ROLE sat_operator;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sat_ham') THEN
        CREATE ROLE sat_ham;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sat_viewer') THEN
        CREATE ROLE sat_viewer;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 2. Role 1: sat_admin (Full Database Administration)
-- Concept: Granting comprehensive DDL/DML permissions WITH GRANT OPTION
-- ----------------------------------------------------------------------------
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO sat_admin WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO sat_admin WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO sat_admin WITH GRANT OPTION;
GRANT USAGE, CREATE ON SCHEMA public TO sat_admin WITH GRANT OPTION;

-- ----------------------------------------------------------------------------
-- 3. Role 2: sat_operator (Mission Ground Controllers & Flight Engineers)
-- Concept: Granular object-level privileges (SELECT, INSERT, UPDATE, EXECUTE)
-- ----------------------------------------------------------------------------
-- Flight operators can inspect all mission telemetry, windows, and telemetry history
GRANT USAGE ON SCHEMA public TO sat_operator;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sat_operator;
GRANT SELECT, USAGE ON ALL SEQUENCES IN SCHEMA public TO sat_operator;

-- Flight operators can queue payload items, upload SSTV images/voice clips, and log deployment events
GRANT INSERT ON data_item, sstv_image, voice_clip, deployment_event TO sat_operator;

-- Flight operators acknowledge system alerts
GRANT UPDATE ON alert TO sat_operator;

-- Flight operators execute autonomous scheduling, pass completion, and manual override functions
GRANT EXECUTE ON FUNCTION fn_fallback_rank(INT) TO sat_operator;
GRANT EXECUTE ON FUNCTION fn_schedule_window(INT) TO sat_operator;
GRANT EXECUTE ON FUNCTION fn_complete_window(INT) TO sat_operator;
GRANT EXECUTE ON FUNCTION fn_override_decision(INT, INT, VARCHAR, TEXT) TO sat_operator;

-- ----------------------------------------------------------------------------
-- 4. Role 3: sat_ham (Amateur Radio Ground Volunteers)
-- Concept: Principle of Least Privilege - Restricted view-only access to reception stats
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO sat_ham;
-- Revoke all table access by default
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sat_ham;
-- Grant access exclusively to volunteer aggregate views
GRANT SELECT ON v_mode_delivery_rate TO sat_ham;
GRANT SELECT ON v_window_summary TO sat_ham;

-- ----------------------------------------------------------------------------
-- 5. Role 4: sat_viewer (Public Dashboard & Read-Only Observers)
-- Concept: Restricting unprivileged users to sanitized virtual views and alert feeds
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO sat_viewer;
-- Revoke all raw table access by default
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sat_viewer;
-- Grant SELECT on all virtual reporting views and alert table
GRANT SELECT ON v_window_summary, v_mode_delivery_rate, v_latest_telemetry,
                v_open_alerts, v_phase_timeline, alert TO sat_viewer;

-- ----------------------------------------------------------------------------
-- 6. Demonstrating Privilege Grant and Subsequent Revocation
-- Concept: Security policy modification via GRANT followed by REVOKE
-- ----------------------------------------------------------------------------
-- Temporarily grant sat_viewer the ability to insert telemetry alerts
GRANT INSERT ON alert TO sat_viewer;

-- Verify or audit role privileges (e.g. via information_schema or \dp)

-- Revoke the privilege to restore least-privilege compliance
REVOKE INSERT ON alert FROM sat_viewer;

-- ----------------------------------------------------------------------------
-- 7. Verification / Inspection Query
-- Concept: Auditing role-table privilege assignments in system catalogs
-- ----------------------------------------------------------------------------
SELECT
    grantee,
    table_name,
    string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privileges_granted
FROM information_schema.role_table_grants
WHERE grantee IN ('sat_admin', 'sat_operator', 'sat_ham', 'sat_viewer')
  AND table_schema = 'public'
GROUP BY grantee, table_name
ORDER BY grantee, table_name;

/*
-- ============================================================================
-- DCL RBAC Verification Test Block (Commented, but directly runnable)
-- ============================================================================
-- Test 1: Verify sat_viewer permissions
SET ROLE sat_viewer;
SELECT current_user, session_user;
SELECT * FROM v_open_alerts LIMIT 2; -- SUCCEEDS (viewer has view access)

-- The following insert will fail with: permission denied for table data_item
-- INSERT INTO data_item (spacecraft_id, mode_id, size_bytes, urgency, description, status)
-- VALUES (2, 1, 100, 5, 'Unauthorized viewer item', 'QUEUED');
RESET ROLE;

-- Test 2: Verify sat_ham permissions
SET ROLE sat_ham;
SELECT * FROM v_mode_delivery_rate; -- SUCCEEDS (HAM has access to delivery rates)

-- The following select will fail with: permission denied for table telemetry_reading
-- SELECT * FROM telemetry_reading LIMIT 1;
RESET ROLE;

-- Test 3: Verify sat_operator permissions
SET ROLE sat_operator;
SELECT count(*) FROM data_item; -- SUCCEEDS (operator can read all tables)
RESET ROLE;
-- ============================================================================
*/
