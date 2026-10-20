-- ============================================================================
-- SomaiyaSat Telemetry Hub - 02_functions_triggers.sql
-- ============================================================================
-- Lab Experiments Covered:
--   - Experiment 3 / Advanced PL/pgSQL: Stored Functions, Business Rules,
--     Integrity Triggers, and Automated State Transitions
-- ============================================================================
-- Single Source of Truth: SPEC.md Business Rules
-- All trigger functions that write to other tables are created as SECURITY DEFINER.
-- ============================================================================

-- Drop existing triggers and functions to allow clean re-runs
DROP TRIGGER IF EXISTS trg_audit_data_item ON data_item;
DROP TRIGGER IF EXISTS trg_audit_command_log ON command_log;
DROP TRIGGER IF EXISTS trg_audit_phase_transition ON phase_transition;
DROP FUNCTION IF EXISTS fn_trg_audit_log CASCADE;

DROP TRIGGER IF EXISTS trg_low_battery_safe_mode ON telemetry_reading;
DROP FUNCTION IF EXISTS fn_trg_low_battery_safe_mode CASCADE;

DROP TRIGGER IF EXISTS trg_telemetry_check ON telemetry_reading;
DROP FUNCTION IF EXISTS fn_trg_telemetry_check CASCADE;

DROP TRIGGER IF EXISTS trg_deployment_confirm ON deployment_event;
DROP FUNCTION IF EXISTS fn_trg_deployment_confirm CASCADE;

DROP TRIGGER IF EXISTS trg_phase_update_spacecraft ON phase_transition;
DROP FUNCTION IF EXISTS fn_trg_phase_update_spacecraft CASCADE;

DROP TRIGGER IF EXISTS trg_phase_guard ON phase_transition;
DROP FUNCTION IF EXISTS fn_trg_phase_guard CASCADE;

DROP TRIGGER IF EXISTS trg_override_log ON router_decision;
DROP FUNCTION IF EXISTS fn_trg_override_log CASCADE;

DROP FUNCTION IF EXISTS fn_override_decision(INT, INT, VARCHAR, TEXT) CASCADE;
DROP FUNCTION IF EXISTS fn_complete_window(INT) CASCADE;
DROP FUNCTION IF EXISTS fn_schedule_window(INT) CASCADE;
DROP FUNCTION IF EXISTS fn_fallback_rank(INT) CASCADE;

-- ============================================================================
-- 1. fn_fallback_rank(item_id)
-- ============================================================================
-- Rule Explanation: Computes a deterministic rule-based priority score from the
-- communication mode's base priority and packet urgency when model predictions
-- are unavailable.
-- Case Study Requirement: Router must fall back to rule-based logic if model
-- output is missing or low-confidence, preserving strict TT&C > SSTV > CODEC2/M17 ordering.
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_fallback_rank(p_item_id INT)
RETURNS NUMERIC AS $$
DECLARE
    v_base_priority INT;
    v_urgency INT;
    v_score NUMERIC;
BEGIN
    SELECT cm.base_priority, di.urgency
    INTO v_base_priority, v_urgency
    FROM data_item di
    JOIN comm_mode cm ON di.mode_id = cm.mode_id
    WHERE di.item_id = p_item_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Item ID % not found in data_item catalog', p_item_id;
    END IF;

    -- Strict hierarchy: (6 - base_priority)*100 ensures TTC (500) > SSTV (400) > CODEC2 (300) > M17 (200)
    -- Urgency (1..5) provides sub-tier ordering within each mode.
    v_score := ((6 - v_base_priority) * 100.0) + (v_urgency * 10.0);
    RETURN v_score;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================================
-- 2. fn_schedule_window(window_id)
-- ============================================================================
-- Rule Explanation: Executes a single atomic scheduling pass over queued items,
-- packing data into capacity and energy budgets according to hybrid model/fallback
-- scoring, deferring non-TT&C items in safe mode, and enforcing zero dropped TT&C packets.
-- Case Study Requirement: TT&C is always the highest priority and must never be dropped;
-- safe mode restricts downlinks to TT&C only; every decision must record a readable rationale.
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_schedule_window(p_window_id INT)
RETURNS VOID AS $$
DECLARE
    v_window RECORD;
    v_item RECORD;
    v_rem_capacity INT;
    v_rem_energy NUMERIC;
    v_item_energy NUMERIC;
    v_action VARCHAR(10);
    v_rationale TEXT;
    v_unscheduled_ttc BOOLEAN := FALSE;
    v_unscheduled_ttc_id INT := NULL;
    v_rank INT := 0;
BEGIN
    -- Lock and retrieve downlink window details
    SELECT w.*, sc.name AS spacecraft_name, sc.current_phase_id, mp.name AS phase_name, mp.tt_c_only
    INTO v_window
    FROM downlink_window w
    JOIN spacecraft sc ON w.spacecraft_id = sc.spacecraft_id
    JOIN mission_phase mp ON sc.current_phase_id = mp.phase_id
    WHERE w.window_id = p_window_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Downlink window ID % not found', p_window_id;
    END IF;

    IF v_window.status <> 'PLANNED' THEN
        RAISE EXCEPTION 'Window % cannot be scheduled: current status is % (must be PLANNED)',
            p_window_id, v_window.status;
    END IF;

    v_rem_capacity := v_window.capacity_bytes;
    v_rem_energy := v_window.energy_budget_mwh;

    -- Score queued items and iterate in descending order of priority
    FOR v_item IN
        WITH scored_queue AS (
            SELECT
                di.item_id,
                di.size_bytes,
                di.urgency,
                di.predicted_success,
                di.model_version,
                cm.code AS mode_code,
                cm.base_priority,
                cm.bitrate_bps,
                cm.tx_power_mw,
                CASE
                    WHEN di.predicted_success IS NOT NULL THEN
                        ((6 - cm.base_priority) * 100.0) + (di.urgency * 10.0) + (di.predicted_success * 10.0)
                    ELSE
                        fn_fallback_rank(di.item_id)
                END AS calculated_score,
                (di.predicted_success IS NULL) AS fallback_flag
            FROM data_item di
            JOIN comm_mode cm ON di.mode_id = cm.mode_id
            WHERE di.spacecraft_id = v_window.spacecraft_id
              AND di.status = 'QUEUED'
            FOR UPDATE OF di SKIP LOCKED
        )
        SELECT *
        FROM scored_queue
        ORDER BY calculated_score DESC, urgency DESC, item_id ASC
    LOOP
        v_rank := v_rank + 1;
        -- Energy required (mWh) = (size_bytes * 8 / bitrate_bps / 3600) * tx_power_mw
        v_item_energy := (v_item.size_bytes * 8.0 / v_item.bitrate_bps / 3600.0) * v_item.tx_power_mw;

        -- Decision Logic:
        -- 1. If phase enforces TT&C only and item is non-TTC, DEFER immediately
        IF v_window.tt_c_only = TRUE AND v_item.mode_code <> 'TTC' THEN
            v_action := 'DEFER';
            v_rationale := format(
                'Rank %s (score %s): Deferred because current mission phase (%s) enforces TT&C-only downlink.',
                v_rank, round(v_item.calculated_score, 2), v_window.phase_name
            );
        -- 2. Check resource fit (capacity bytes and energy budget mWh)
        ELSIF v_item.size_bytes <= v_rem_capacity AND v_item_energy <= v_rem_energy THEN
            v_action := 'SEND';
            v_rem_capacity := v_rem_capacity - v_item.size_bytes;
            v_rem_energy := v_rem_energy - v_item_energy;
            v_rationale := format(
                'Rank %s (score %s): Scheduled for SEND. Fits capacity (rem: %s B) and energy budget (rem: %s mWh).',
                v_rank, round(v_item.calculated_score, 2), v_rem_capacity, round(v_rem_energy, 2)
            );
        -- 3. Insufficient capacity or energy
        ELSE
            v_action := 'DEFER';
            v_rationale := format(
                'Rank %s (score %s): Deferred due to resource constraints (requires %s B, %s mWh; available: %s B, %s mWh).',
                v_rank, round(v_item.calculated_score, 2), v_item.size_bytes, round(v_item_energy, 2),
                v_rem_capacity, round(v_rem_energy, 2)
            );
        END IF;

        -- Safety Flag: If a queued TT&C item does not get SEND, note it for the mandatory rollback check
        IF v_item.mode_code = 'TTC' AND v_action <> 'SEND' THEN
            v_unscheduled_ttc := TRUE;
            v_unscheduled_ttc_id := v_item.item_id;
        END IF;

        -- Record router decision with human-readable rationale
        INSERT INTO router_decision (
            window_id,
            item_id,
            action,
            score,
            predicted_success,
            rationale,
            model_version,
            fallback_used,
            decided_at,
            overridden
        ) VALUES (
            p_window_id,
            v_item.item_id,
            v_action,
            v_item.calculated_score,
            v_item.predicted_success,
            v_rationale,
            v_item.model_version,
            v_item.fallback_flag,
            now(),
            FALSE
        );

        -- Update successfully scheduled items to SCHEDULED status
        IF v_action = 'SEND' THEN
            UPDATE data_item
            SET status = 'SCHEDULED'
            WHERE item_id = v_item.item_id;
        END IF;
    END LOOP;

    -- SAFETY CHECK: TT&C is always the highest priority and must NEVER be dropped or deferred!
    IF v_unscheduled_ttc THEN
        RAISE EXCEPTION 'Safety check failed: Queued TT&C item % could not be scheduled for SEND in window % due to window constraints. Transaction rolled back.',
            v_unscheduled_ttc_id, p_window_id;
    END IF;

    -- Advance window to SCHEDULED status
    UPDATE downlink_window
    SET status = 'SCHEDULED'
    WHERE window_id = p_window_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- 3. fn_complete_window(window_id)
-- ============================================================================
-- Rule Explanation: Simulates RF downlink transmissions based on expected SNR,
-- assigns volunteer reception credit, updates packet statuses, and raises critical
-- alerts if all TT&C packets were lost.
-- Case Study Requirement: Simulating RF contact results, logging amateur radio
-- volunteer participation, and triggering immediate alerts for communication blackouts.
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_complete_window(p_window_id INT)
RETURNS VOID AS $$
DECLARE
    v_window RECORD;
    v_dec RECORD;
    v_prob_success NUMERIC;
    v_rnd NUMERIC;
    v_tx_status VARCHAR(20);
    v_bytes_rx INT;
    v_sim_snr NUMERIC;
    v_ham_user_id INT;
    v_ttc_sent_count INT := 0;
    v_ttc_recv_count INT := 0;
BEGIN
    SELECT * INTO v_window
    FROM downlink_window
    WHERE window_id = p_window_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Window % not found', p_window_id;
    END IF;

    IF v_window.status <> 'SCHEDULED' THEN
        RAISE EXCEPTION 'Window % cannot be completed: status is % (must be SCHEDULED)',
            p_window_id, v_window.status;
    END IF;

    -- Fetch a random HAM volunteer for credit on received packets
    SELECT user_id INTO v_ham_user_id
    FROM app_user
    WHERE role = 'HAM_VOLUNTEER'
    ORDER BY random()
    LIMIT 1;

    -- Process all effective SEND decisions (considering operator overrides)
    FOR v_dec IN
        SELECT
            rd.decision_id,
            rd.item_id,
            COALESCE(rd.override_action, rd.action) AS effective_action,
            di.size_bytes,
            cm.code AS mode_code
        FROM router_decision rd
        JOIN data_item di ON rd.item_id = di.item_id
        JOIN comm_mode cm ON di.mode_id = cm.mode_id
        WHERE rd.window_id = p_window_id
          AND COALESCE(rd.override_action, rd.action) = 'SEND'
    LOOP
        -- Base link success probability derived from SNR (higher SNR = higher probability)
        -- TT&C uses robust low-bitrate FSK modulation, giving +25% reliability boost
        v_prob_success := 0.50 + (v_window.expected_snr_db / 40.0);
        IF v_dec.mode_code = 'TTC' THEN
            v_prob_success := v_prob_success + 0.25;
        END IF;

        -- Clamp probability between 10% and 98%
        v_prob_success := GREATEST(0.10, LEAST(0.98, v_prob_success));
        v_rnd := random();
        v_sim_snr := round((v_window.expected_snr_db + ((random() * 4.0) - 2.0))::numeric, 2);

        IF v_rnd < v_prob_success THEN
            v_tx_status := 'RECEIVED';
            v_bytes_rx := v_dec.size_bytes;
        ELSIF v_rnd < (v_prob_success + ((1.0 - v_prob_success) * 0.35)) THEN
            v_tx_status := 'PARTIAL';
            v_bytes_rx := round(v_dec.size_bytes * (0.30 + (random() * 0.40)));
        ELSE
            v_tx_status := 'LOST';
            v_bytes_rx := 0;
        END IF;

        -- Track TT&C reception statistics
        IF v_dec.mode_code = 'TTC' THEN
            v_ttc_sent_count := v_ttc_sent_count + 1;
            IF v_tx_status = 'RECEIVED' THEN
                v_ttc_recv_count := v_ttc_recv_count + 1;
            END IF;
        END IF;

        -- Record physical transmission reception record
        INSERT INTO transmission (
            decision_id,
            bytes_received,
            snr_db,
            status,
            received_by,
            received_at
        ) VALUES (
            v_dec.decision_id,
            v_bytes_rx,
            v_sim_snr,
            v_tx_status,
            CASE WHEN v_tx_status = 'RECEIVED' THEN v_ham_user_id ELSE NULL END,
            now()
        );

        -- Update data_item status: SENT on success/partial, re-queue if lost
        IF v_tx_status IN ('RECEIVED', 'PARTIAL') THEN
            UPDATE data_item
            SET status = 'SENT'
            WHERE item_id = v_dec.item_id;
        ELSE
            UPDATE data_item
            SET status = 'QUEUED'
            WHERE item_id = v_dec.item_id;
        END IF;
    END LOOP;

    -- Raise critical alert if TT&C packets were transmitted but none were received
    IF v_ttc_sent_count > 0 AND v_ttc_recv_count = 0 THEN
        INSERT INTO alert (
            alert_type,
            severity,
            message,
            reading_id,
            window_id,
            status,
            created_at
        ) VALUES (
            'CRITICAL_DATA_MISSED',
            'CRITICAL',
            format('Pass completed: %s TT&C packet(s) were transmitted in window %s but 0 were successfully received on ground.',
                   v_ttc_sent_count, p_window_id),
            NULL,
            p_window_id,
            'OPEN',
            now()
        );
    END IF;

    -- Mark window completed
    UPDATE downlink_window
    SET status = 'COMPLETED'
    WHERE window_id = p_window_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- 4. fn_override_decision(decision_id, user_id, new_action, reason)
-- ============================================================================
-- Rule Explanation: Enables authenticated operators to modify scheduling decisions
-- in scheduled windows while strictly blocking any downgrade of TT&C packets from SEND.
-- Case Study Requirement: Ground operators keep override authority, but TT&C priority
-- protection is inviolable.
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_override_decision(
    p_decision_id INT,
    p_user_id INT,
    p_new_action VARCHAR,
    p_reason TEXT
)
RETURNS VOID AS $$
DECLARE
    v_user_role VARCHAR(20);
    v_dec RECORD;
BEGIN
    -- Validate user permissions
    SELECT role INTO v_user_role
    FROM app_user
    WHERE user_id = p_user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'User ID % does not exist', p_user_id;
    END IF;

    IF v_user_role NOT IN ('ADMIN', 'OPERATOR') THEN
        RAISE EXCEPTION 'Unauthorized: User % has role % (ADMIN or OPERATOR required for override)',
            p_user_id, v_user_role;
    END IF;

    -- Validate target decision and window state
    SELECT rd.*, w.status AS window_status, cm.code AS mode_code
    INTO v_dec
    FROM router_decision rd
    JOIN downlink_window w ON rd.window_id = w.window_id
    JOIN data_item di ON rd.item_id = di.item_id
    JOIN comm_mode cm ON di.mode_id = cm.mode_id
    WHERE rd.decision_id = p_decision_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Decision ID % not found', p_decision_id;
    END IF;

    IF v_dec.window_status <> 'SCHEDULED' THEN
        RAISE EXCEPTION 'Cannot override decision %: parent window % has status % (must be SCHEDULED)',
            p_decision_id, v_dec.window_id, v_dec.window_status;
    END IF;

    IF p_new_action NOT IN ('SEND', 'DEFER', 'DROP') THEN
        RAISE EXCEPTION 'Invalid override action %: must be SEND, DEFER, or DROP', p_new_action;
    END IF;

    IF p_reason IS NULL OR trim(p_reason) = '' THEN
        RAISE EXCEPTION 'An override reason is mandatory';
    END IF;

    -- Strict Inviolable Rule: Refuse to override a TT&C item to anything other than SEND
    IF v_dec.mode_code = 'TTC' AND p_new_action <> 'SEND' THEN
        RAISE EXCEPTION 'Safety Violation: Refusing to override TT&C item to % (TT&C can never be downgraded from SEND)',
            p_new_action;
    END IF;

    -- Apply operator override
    UPDATE router_decision
    SET overridden = TRUE,
        override_action = p_new_action,
        override_by = p_user_id,
        override_reason = p_reason,
        override_at = now()
    WHERE decision_id = p_decision_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- 5. trg_override_log
-- ============================================================================
-- Rule Explanation: Writes an automatic telecommand log entry whenever a ground
-- operator overrides an autonomous router scheduling decision.
-- Case Study Requirement: Full traceability and auditability of operator interventions
-- in spacecraft operations.
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_trg_override_log()
RETURNS TRIGGER AS $$
DECLARE
    v_spacecraft_id INT;
BEGIN
    IF NEW.overridden = TRUE AND (OLD.overridden IS DISTINCT FROM TRUE OR NEW.override_action IS DISTINCT FROM OLD.override_action) THEN
        SELECT spacecraft_id INTO v_spacecraft_id
        FROM downlink_window
        WHERE window_id = NEW.window_id;

        INSERT INTO command_log (
            spacecraft_id,
            command_type,
            issued_by,
            is_automatic,
            details,
            issued_at
        ) VALUES (
            v_spacecraft_id,
            'OVERRIDE_DECISION',
            NEW.override_by,
            FALSE,
            jsonb_build_object(
                'decision_id', NEW.decision_id,
                'window_id', NEW.window_id,
                'item_id', NEW.item_id,
                'original_action', NEW.action,
                'override_action', NEW.override_action,
                'reason', NEW.override_reason,
                'override_at', NEW.override_at
            ),
            now()
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_override_log
AFTER UPDATE ON router_decision
FOR EACH ROW
EXECUTE FUNCTION fn_trg_override_log();

-- ============================================================================
-- 6. trg_phase_guard & companion trg_phase_update_spacecraft
-- ============================================================================
-- Rule Explanation: Validates that all phase transitions follow permitted state-machine
-- paths and conditions (confirmed deployment and verified TT&C reception before nominal operations),
-- updating spacecraft state upon validation.
-- Case Study Requirement: Mission phase lifecycle safety, deployment verification
-- before normal operations, and restricted exit from safe mode.
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_trg_phase_guard()
RETURNS TRIGGER AS $$
DECLARE
    v_curr_phase_id INT;
    v_from_name VARCHAR(50);
    v_to_name VARCHAR(50);
    v_has_confirmed_deploy BOOLEAN;
    v_has_recv_ttc BOOLEAN;
    v_is_legal BOOLEAN := FALSE;
    v_user_role VARCHAR(20);
BEGIN
    -- Check that from_phase matches current spacecraft phase
    SELECT current_phase_id INTO v_curr_phase_id
    FROM spacecraft
    WHERE spacecraft_id = NEW.spacecraft_id;

    IF NEW.from_phase_id <> v_curr_phase_id THEN
        RAISE EXCEPTION 'Invalid transition: from_phase (%) does not match spacecraft current phase (%)',
            NEW.from_phase_id, v_curr_phase_id;
    END IF;

    SELECT name INTO v_from_name FROM mission_phase WHERE phase_id = NEW.from_phase_id;
    SELECT name INTO v_to_name FROM mission_phase WHERE phase_id = NEW.to_phase_id;

    -- Evaluate legal moves:
    -- 1. LAUNCH -> DEPLOYED
    IF v_from_name = 'LAUNCH' AND v_to_name = 'DEPLOYED' THEN
        v_is_legal := TRUE;

    -- 2. DEPLOYED -> COMMISSIONING
    ELSIF v_from_name = 'DEPLOYED' AND v_to_name = 'COMMISSIONING' THEN
        v_is_legal := TRUE;

    -- 3. COMMISSIONING -> NOMINAL (requires confirmed deployment AND received TT&C transmission)
    ELSIF v_from_name = 'COMMISSIONING' AND v_to_name = 'NOMINAL' THEN
        -- Check confirmed deployment
        SELECT EXISTS (
            SELECT 1 FROM deployment_event
            WHERE spacecraft_id = NEW.spacecraft_id AND status = 'CONFIRMED'
        ) INTO v_has_confirmed_deploy;

        IF NOT v_has_confirmed_deploy THEN
            RAISE EXCEPTION 'Illegal phase transition from COMMISSIONING to NOMINAL: Spacecraft % does not have a CONFIRMED deployment event.',
                NEW.spacecraft_id;
        END IF;

        -- Check at least one received TT&C transmission
        SELECT EXISTS (
            SELECT 1
            FROM transmission t
            JOIN router_decision rd ON t.decision_id = rd.decision_id
            JOIN data_item di ON rd.item_id = di.item_id
            JOIN comm_mode cm ON di.mode_id = cm.mode_id
            WHERE di.spacecraft_id = NEW.spacecraft_id
              AND cm.code = 'TTC'
              AND t.status = 'RECEIVED'
        ) INTO v_has_recv_ttc;

        IF NOT v_has_recv_ttc THEN
            RAISE EXCEPTION 'Illegal phase transition from COMMISSIONING to NOMINAL: Spacecraft % has no verified RECEIVED TT&C transmission.',
                NEW.spacecraft_id;
        END IF;

        v_is_legal := TRUE;

    -- 4. Any phase -> SAFE_MODE
    ELSIF v_to_name = 'SAFE_MODE' THEN
        v_is_legal := TRUE;

    -- 5. SAFE_MODE -> COMMISSIONING or NOMINAL (OPERATOR only)
    ELSIF v_from_name = 'SAFE_MODE' AND v_to_name IN ('COMMISSIONING', 'NOMINAL') THEN
        IF NEW.triggered_by <> 'OPERATOR' THEN
            RAISE EXCEPTION 'Illegal phase transition from SAFE_MODE to %: Exit from SAFE_MODE must be triggered by an OPERATOR (found: %)',
                v_to_name, NEW.triggered_by;
        END IF;

        IF NEW.user_id IS NOT NULL THEN
            SELECT role INTO v_user_role FROM app_user WHERE user_id = NEW.user_id;
            IF v_user_role NOT IN ('ADMIN', 'OPERATOR') THEN
                RAISE EXCEPTION 'User % (role %) is not authorized to transition out of SAFE_MODE (ADMIN or OPERATOR required)',
                    NEW.user_id, v_user_role;
            END IF;
        END IF;

        v_is_legal := TRUE;
    END IF;

    IF NOT v_is_legal THEN
        RAISE EXCEPTION 'Illegal phase transition from % to % for spacecraft %: Move not permitted by mission flight rules.',
            v_from_name, v_to_name, NEW.spacecraft_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_phase_guard
BEFORE INSERT ON phase_transition
FOR EACH ROW
EXECUTE FUNCTION fn_trg_phase_guard();

-- Companion AFTER trigger to update spacecraft current phase
CREATE OR REPLACE FUNCTION fn_trg_phase_update_spacecraft()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE spacecraft
    SET current_phase_id = NEW.to_phase_id
    WHERE spacecraft_id = NEW.spacecraft_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_phase_update_spacecraft
AFTER INSERT ON phase_transition
FOR EACH ROW
EXECUTE FUNCTION fn_trg_phase_update_spacecraft();

-- ============================================================================
-- 7. trg_deployment_confirm
-- ============================================================================
-- Rule Explanation: Advances spacecraft through LAUNCH -> DEPLOYED -> COMMISSIONING
-- upon confirmed deployer release, or raises critical alerts if deployment is partial or failed.
-- Case Study Requirement: Automated mission activation following physical separation
-- from SomaiyaPod, and immediate anomaly notification on deployment failures.
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_trg_deployment_confirm()
RETURNS TRIGGER AS $$
DECLARE
    v_curr_phase_name VARCHAR(50);
    v_launch_id INT;
    v_deployed_id INT;
    v_comm_id INT;
BEGIN
    -- If deployment confirmed and satellite in LAUNCH, advance to DEPLOYED then COMMISSIONING
    IF NEW.status = 'CONFIRMED' AND (TG_OP = 'INSERT' OR OLD.status <> 'CONFIRMED') THEN
        SELECT mp.name INTO v_curr_phase_name
        FROM spacecraft sc
        JOIN mission_phase mp ON sc.current_phase_id = mp.phase_id
        WHERE sc.spacecraft_id = NEW.spacecraft_id;

        IF v_curr_phase_name = 'LAUNCH' THEN
            SELECT phase_id INTO v_launch_id FROM mission_phase WHERE name = 'LAUNCH';
            SELECT phase_id INTO v_deployed_id FROM mission_phase WHERE name = 'DEPLOYED';
            SELECT phase_id INTO v_comm_id FROM mission_phase WHERE name = 'COMMISSIONING';

            -- 1. AUTO transition LAUNCH -> DEPLOYED
            INSERT INTO phase_transition (
                spacecraft_id, from_phase_id, to_phase_id, reason, triggered_by, user_id, at
            ) VALUES (
                NEW.spacecraft_id,
                v_launch_id,
                v_deployed_id,
                'Automatic transition: Deployment confirmed via mechanical switch release signal from SomaiyaPod.',
                'AUTO',
                NULL,
                now()
            );

            -- 2. AUTO transition DEPLOYED -> COMMISSIONING
            INSERT INTO phase_transition (
                spacecraft_id, from_phase_id, to_phase_id, reason, triggered_by, user_id, at
            ) VALUES (
                NEW.spacecraft_id,
                v_deployed_id,
                v_comm_id,
                'Automatic transition: Advancing to initial commissioning and health checkout.',
                'AUTO',
                NULL,
                now()
            );
        END IF;
    END IF;

    -- If deployment status is PARTIAL or FAILED, raise an alert
    IF NEW.status IN ('PARTIAL', 'FAILED') AND (TG_OP = 'INSERT' OR OLD.status NOT IN ('PARTIAL', 'FAILED')) THEN
        INSERT INTO alert (
            alert_type,
            severity,
            message,
            reading_id,
            window_id,
            status,
            created_at
        ) VALUES (
            'DEPLOYMENT_ISSUE',
            'CRITICAL',
            format('Deployment anomaly on spacecraft %s (method %s): Status is %s. Notes: %s',
                   NEW.spacecraft_id, NEW.method, NEW.status, COALESCE(NEW.notes, 'None')),
            NULL,
            NULL,
            'OPEN',
            now()
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_deployment_confirm
AFTER INSERT OR UPDATE ON deployment_event
FOR EACH ROW
EXECUTE FUNCTION fn_trg_deployment_confirm();

-- ============================================================================
-- 8. trg_telemetry_check
-- ============================================================================
-- Rule Explanation: Compares new telemetry readings against sensor threshold rules
-- and generates exactly one prioritized alert (favoring critical over warning) when limits are breached.
-- Case Study Requirement: Continuous telemetry monitoring against operational bands
-- with context-aware provenance tracking (e.g. test bench vs in-orbit).
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_trg_telemetry_check()
RETURNS TRIGGER AS $$
DECLARE
    v_breach RECORD;
BEGIN
    -- Query for breached threshold rules, prioritizing CRITICAL over WARNING
    SELECT tr.rule_no, tr.severity, tr.min_val, tr.max_val, s.name AS sensor_name, s.unit
    INTO v_breach
    FROM threshold_rule tr
    JOIN sensor s ON tr.sensor_id = s.sensor_id
    WHERE tr.sensor_id = NEW.sensor_id
      AND (NEW.val < tr.min_val OR NEW.val > tr.max_val)
    ORDER BY (CASE WHEN tr.severity = 'CRITICAL' THEN 1 ELSE 2 END) ASC
    LIMIT 1;

    -- Generate exactly ONE alert if a rule is breached
    IF FOUND THEN
        INSERT INTO alert (
            alert_type,
            severity,
            message,
            reading_id,
            window_id,
            status,
            created_at
        ) VALUES (
            'TELEMETRY_LIMIT',
            v_breach.severity,
            format('[%s] %s limit breached on %s: value %s %s outside range [%s, %s] %s',
                   NEW.source, v_breach.severity, v_breach.sensor_name, NEW.val,
                   v_breach.unit, v_breach.min_val, v_breach.max_val, v_breach.unit),
            NEW.reading_id,
            NULL,
            'OPEN',
            now()
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_telemetry_check
AFTER INSERT ON telemetry_reading
FOR EACH ROW
EXECUTE FUNCTION fn_trg_telemetry_check();

-- ============================================================================
-- 9. trg_low_battery_safe_mode
-- ============================================================================
-- Rule Explanation: Detects critical in-orbit battery voltage drops, autonomously
-- placing the satellite into safe mode while logging an automated command and alert.
-- Case Study Requirement: "a critical battery reading triggers safe mode (TT&C only)".
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_trg_low_battery_safe_mode()
RETURNS TRIGGER AS $$
DECLARE
    v_sensor_name VARCHAR(50);
    v_crit_min NUMERIC;
    v_curr_phase_id INT;
    v_curr_phase_name VARCHAR(50);
    v_safe_phase_id INT;
BEGIN
    -- Only evaluate in-orbit readings
    IF NEW.source = 'ORBIT' THEN
        SELECT name INTO v_sensor_name
        FROM sensor
        WHERE sensor_id = NEW.sensor_id;

        IF v_sensor_name = 'Battery Voltage' THEN
            SELECT min_val INTO v_crit_min
            FROM threshold_rule
            WHERE sensor_id = NEW.sensor_id AND severity = 'CRITICAL';

            IF NEW.val < v_crit_min THEN
                SELECT sc.current_phase_id, mp.name
                INTO v_curr_phase_id, v_curr_phase_name
                FROM spacecraft sc
                JOIN mission_phase mp ON sc.current_phase_id = mp.phase_id
                WHERE sc.spacecraft_id = NEW.spacecraft_id;

                -- If not already in SAFE_MODE, trigger autonomous entry
                IF v_curr_phase_name <> 'SAFE_MODE' THEN
                    SELECT phase_id INTO v_safe_phase_id
                    FROM mission_phase
                    WHERE name = 'SAFE_MODE';

                    -- 1. AUTO phase transition to SAFE_MODE
                    INSERT INTO phase_transition (
                        spacecraft_id, from_phase_id, to_phase_id, reason, triggered_by, user_id, at
                    ) VALUES (
                        NEW.spacecraft_id,
                        v_curr_phase_id,
                        v_safe_phase_id,
                        format('Autonomous safe mode entry: in-orbit battery voltage %s V dropped below critical threshold %s V.',
                               NEW.val, v_crit_min),
                        'AUTO',
                        NULL,
                        now()
                    );

                    -- 2. Log automatic command
                    INSERT INTO command_log (
                        spacecraft_id, command_type, issued_by, is_automatic, details, issued_at
                    ) VALUES (
                        NEW.spacecraft_id,
                        'ENTER_SAFE_MODE',
                        NULL,
                        TRUE,
                        jsonb_build_object(
                            'trigger', 'CRITICAL_LOW_BATTERY',
                            'reading_id', NEW.reading_id,
                            'voltage_v', NEW.val,
                            'threshold_v', v_crit_min,
                            'source', NEW.source
                        ),
                        now()
                    );

                    -- 3. Raise SAFE_MODE alert
                    INSERT INTO alert (
                        alert_type, severity, message, reading_id, window_id, status, created_at
                    ) VALUES (
                        'SAFE_MODE',
                        'CRITICAL',
                        format('Spacecraft %s entered SAFE_MODE autonomously due to critical battery voltage depletion (%s V < %s V). Downlink locked to TT&C only.',
                               NEW.spacecraft_id, NEW.val, v_crit_min),
                        NEW.reading_id,
                        NULL,
                        'OPEN',
                        now()
                    );
                END IF;
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_low_battery_safe_mode
AFTER INSERT ON telemetry_reading
FOR EACH ROW
EXECUTE FUNCTION fn_trg_low_battery_safe_mode();

-- ============================================================================
-- 10. trg_audit_* (on data_item, command_log, phase_transition)
-- ============================================================================
-- Rule Explanation: Captures historical row snapshots as JSONB into the audit_log
-- whenever records in key operational tables are updated or deleted.
-- Case Study Requirement: Lab Experiment scope (Recovery, CDC Change Data Capture,
-- and forensic transaction auditing).
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_trg_audit_log()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO audit_log (
        table_name,
        action,
        row_data,
        restored,
        logged_at
    ) VALUES (
        TG_TABLE_NAME,
        TG_OP,
        to_jsonb(OLD),
        FALSE,
        now()
    );
    RETURN OLD;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_audit_data_item
AFTER UPDATE OR DELETE ON data_item
FOR EACH ROW
EXECUTE FUNCTION fn_trg_audit_log();

CREATE TRIGGER trg_audit_command_log
AFTER UPDATE OR DELETE ON command_log
FOR EACH ROW
EXECUTE FUNCTION fn_trg_audit_log();

CREATE TRIGGER trg_audit_phase_transition
AFTER UPDATE OR DELETE ON phase_transition
FOR EACH ROW
EXECUTE FUNCTION fn_trg_audit_log();
