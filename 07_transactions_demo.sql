-- ============================================================================
-- SomaiyaSat Telemetry Hub - 07_transactions_demo.sql
-- ============================================================================
-- Lab Experiments Covered:
--   - Course Outcomes / Advanced DBMS: Transaction Processing, ACID Properties
--     (Atomicity, Consistency, Isolation, Durability), Concurrency Control,
--     Pessimistic Row-Level Locking, Savepoints, and Audit-Based Disaster Recovery
-- ============================================================================
-- Contains 4 runnable demonstrations:
--   (a) COMMIT vs ROLLBACK (Durability vs Atomicity)
--   (b) SAVEPOINT and ROLLBACK TO SAVEPOINT (Sub-transaction recovery)
--   (c) Two-Session Pessimistic Locking Demo (SELECT ... FOR UPDATE vs NOWAIT)
--   (d) Database Recovery: Restoring deleted operational data from audit_log
-- ============================================================================

-- ============================================================================
-- DEMO (a): COMMIT vs ROLLBACK
-- ============================================================================
-- Concept: Atomicity & Durability. Changes committed with COMMIT persist to disk;
-- changes aborted with ROLLBACK leave the database in its exact prior state.
-- ============================================================================

-- Step A1: Transaction that commits successfully
BEGIN;

-- Insert a ground telecommand log entry
INSERT INTO command_log (spacecraft_id, command_type, issued_by, is_automatic, details)
VALUES (
    (SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat'),
    'SET_PRIORITY',
    (SELECT user_id FROM app_user WHERE username = 'ops_asha'),
    FALSE,
    '{"mode": "SSTV", "target_priority": 1, "note": "Commit verification test"}'::jsonb
);

-- Commit transaction: changes become durable and visible to all other sessions
COMMIT;

-- Verification A1: Confirm row exists
SELECT command_id, command_type, issued_at, details
FROM command_log
WHERE details->>'note' = 'Commit verification test';

-- Step A2: Transaction that is rolled back
BEGIN;

-- Insert another telecommand log entry
INSERT INTO command_log (spacecraft_id, command_type, issued_by, is_automatic, details)
VALUES (
    (SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat'),
    'SET_PRIORITY',
    (SELECT user_id FROM app_user WHERE username = 'ops_rohan'),
    FALSE,
    '{"mode": "M17", "target_priority": 2, "note": "Rollback test that should vanish"}'::jsonb
);

-- Rollback transaction: completely aborts all modifications made since BEGIN
ROLLBACK;

-- Verification A2: Confirm rolled-back row does NOT exist (returns 0 rows)
SELECT count(*) AS should_be_zero
FROM command_log
WHERE details->>'note' = 'Rollback test that should vanish';


-- ============================================================================
-- DEMO (b): SAVEPOINT and ROLLBACK TO SAVEPOINT
-- ============================================================================
-- Concept: Partial Transaction Rollback. Savepoints establish intermediate check-points
-- allowing errors or speculative changes to be discarded without aborting the entire transaction.
-- ============================================================================

BEGIN;

-- Operation 1: Insert an initial mission telecommand
INSERT INTO command_log (spacecraft_id, command_type, issued_by, is_automatic, details)
VALUES (
    (SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat'),
    'REQUEST_PHASE',
    (SELECT user_id FROM app_user WHERE username = 'ops_asha'),
    FALSE,
    '{"target_phase": "NOMINAL", "step": "Stage 1 confirmed"}'::jsonb
);

-- Establish an intermediate savepoint
SAVEPOINT sp_after_op1;

-- Operation 2: Speculative / erroneous operation inside savepoint
INSERT INTO command_log (spacecraft_id, command_type, issued_by, is_automatic, details)
VALUES (
    (SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat'),
    'REQUEST_PHASE',
    (SELECT user_id FROM app_user WHERE username = 'ops_rohan'),
    FALSE,
    '{"target_phase": "SAFE_MODE", "step": "Erroneous speculative command"}'::jsonb
);

-- Revert exclusively to the savepoint: Operation 2 is undone, Operation 1 remains active!
ROLLBACK TO SAVEPOINT sp_after_op1;

-- Operation 3: Proceed with valid subsequent command
INSERT INTO command_log (spacecraft_id, command_type, issued_by, is_automatic, details)
VALUES (
    (SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat'),
    'REQUEST_PHASE',
    (SELECT user_id FROM app_user WHERE username = 'ops_asha'),
    FALSE,
    '{"target_phase": "NOMINAL", "step": "Stage 2 confirmed after safe rollback"}'::jsonb
);

-- Commit transaction
COMMIT;

-- Verification (b): Operation 1 and 3 are present, Operation 2 was discarded
SELECT command_id, details->>'step' AS execution_step, issued_at
FROM command_log
WHERE details->>'step' LIKE '%confirmed%'
ORDER BY command_id ASC;


-- ============================================================================
-- DEMO (c): Two-Session Pessimistic Locking Demo (SELECT ... FOR UPDATE vs NOWAIT)
-- ============================================================================
-- Concept: Isolation & Concurrency Control.
-- SELECT ... FOR UPDATE acquires an exclusive row-level lock on the selected row.
-- A concurrent session requesting FOR UPDATE NOWAIT on the same row is immediately
-- rejected with an error instead of blocking.
-- ============================================================================
/*
-------------------------------------------------------------------------------
INSTRUCTIONS TO RUN CONCURRENT TWO-SESSION LOCK DEMO IN TWO TERMINALS:
-------------------------------------------------------------------------------

TERMINAL 1 (Session 1 - Acquires Lock):
-------------------------------------------------------------------------------
psql -d somaiyasat
somaiyasat=# BEGIN;
somaiyasat=# SELECT alert_id, alert_type, status, message
             FROM alert
             WHERE status = 'OPEN'
             ORDER BY alert_id LIMIT 1
             FOR UPDATE;
-- (Session 1 now holds an exclusive row-level lock on this alert)
-- DO NOT COMMIT YET! Leave Terminal 1 open.

TERMINAL 2 (Session 2 - Tries to acquire lock without waiting):
-------------------------------------------------------------------------------
psql -d somaiyasat
somaiyasat=# BEGIN;
somaiyasat=# SELECT alert_id, alert_type, status
             FROM alert
             WHERE alert_id = 1 -- (or the alert_id locked in Session 1)
             FOR UPDATE NOWAIT;
-- OUTPUT IN TERMINAL 2:
-- ERROR:  could not obtain lock on row in relation "alert"
-- Demonstrates non-blocking pessimistic concurrency control!
somaiyasat=# ROLLBACK;

BACK IN TERMINAL 1 (Session 1 completes work):
-------------------------------------------------------------------------------
somaiyasat=# UPDATE alert SET status = 'ACK', ack_at = now() WHERE alert_id = 1;
somaiyasat=# COMMIT;
-------------------------------------------------------------------------------
*/

-- Automated in-session lock demonstration (validates syntax and row-level locking):
BEGIN;
SELECT alert_id, alert_type, severity, message
FROM alert
WHERE status = 'OPEN'
ORDER BY alert_id ASC
LIMIT 1
FOR UPDATE;

-- Release lock
COMMIT;


-- ============================================================================
-- DEMO (d): Disaster Recovery: Restoring Deleted Operational Data from audit_log
-- ============================================================================
-- Concept: Change Data Capture (CDC) and System Recovery.
-- When records are deleted from data_item, trg_audit_data_item copies the complete
-- pre-image (OLD) as JSONB into audit_log. We reconstruct and restore the lost row.
-- ============================================================================

-- Step D1: Create a critical queued data item
-- Clean up any prior test rows with this test description for clean re-runs
DELETE FROM data_item WHERE description = 'Critical health telemetry: OBC system recovery beacon #999';

DROP TABLE IF EXISTS tmp_recovery_target;
CREATE TEMP TABLE tmp_recovery_target (item_id INT);

WITH inserted_item AS (
    INSERT INTO data_item (
        spacecraft_id, mode_id, size_bytes, urgency, description,
        status, predicted_success, model_version, created_at
    ) VALUES (
        (SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat'),
        (SELECT mode_id FROM comm_mode WHERE code = 'TTC'),
        185,
        5,
        'Critical health telemetry: OBC system recovery beacon #999',
        'QUEUED',
        0.9850,
        (SELECT model_version FROM model_run LIMIT 1),
        now()
    ) RETURNING item_id
)
INSERT INTO tmp_recovery_target (item_id)
SELECT item_id FROM inserted_item;

-- Verification D1: Verify item exists in data_item table
SELECT item_id, description, status, created_at
FROM data_item
WHERE item_id = (SELECT item_id FROM tmp_recovery_target LIMIT 1);

-- Step D2: Simulate accidental data loss (DELETE statement)
DELETE FROM data_item
WHERE item_id = (SELECT item_id FROM tmp_recovery_target LIMIT 1);

-- Verification D2: Verify item is deleted from data_item (returns 0 rows)
SELECT count(*) AS data_item_count_after_deletion
FROM data_item
WHERE item_id = (SELECT item_id FROM tmp_recovery_target LIMIT 1);

-- Verification D3: Check that trg_audit_data_item captured the deletion in audit_log
SELECT log_id, table_name, action, (row_data->>'item_id')::int AS deleted_item_id, logged_at
FROM audit_log
WHERE table_name = 'data_item'
  AND action = 'DELETE'
  AND (row_data->>'item_id')::int = (SELECT item_id FROM tmp_recovery_target LIMIT 1)
ORDER BY log_id DESC
LIMIT 1;

-- Step D3: RECOVERY EXECUTION - Reconstruct the deleted row from audit_log JSONB
INSERT INTO data_item (
    item_id, spacecraft_id, mode_id, size_bytes, urgency, description,
    status, predicted_success, model_version, created_at
)
SELECT
    (row_data->>'item_id')::INT,
    (row_data->>'spacecraft_id')::INT,
    (row_data->>'mode_id')::INT,
    (row_data->>'size_bytes')::INT,
    (row_data->>'urgency')::SMALLINT,
    row_data->>'description',
    row_data->>'status',
    (row_data->>'predicted_success')::NUMERIC,
    row_data->>'model_version',
    (row_data->>'created_at')::TIMESTAMP
FROM audit_log
WHERE table_name = 'data_item'
  AND action = 'DELETE'
  AND (row_data->>'item_id')::int = (SELECT item_id FROM tmp_recovery_target LIMIT 1)
ORDER BY log_id DESC
LIMIT 1;

-- Update audit record to flag recovery status
UPDATE audit_log
SET restored = TRUE
WHERE table_name = 'data_item'
  AND action = 'DELETE'
  AND (row_data->>'item_id')::int = (SELECT item_id FROM tmp_recovery_target LIMIT 1);

-- Verification D4: Confirm successful restoration into operational table
SELECT item_id, description, status, predicted_success, model_version
FROM data_item
WHERE item_id = (SELECT item_id FROM tmp_recovery_target LIMIT 1);

-- Cleanup temporary demo tracking table
DROP TABLE IF EXISTS tmp_recovery_target;
