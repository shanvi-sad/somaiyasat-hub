-- ============================================================================
-- SomaiyaSat Telemetry Hub - 01_schema.sql
-- ============================================================================
-- Lab Experiments Covered:
--   - Experiment 1: Problem Definition, ER and EER Modeling
--   - Experiment 2: ER/EER to Relational Schema Mapping
--   - Experiment 3: Data Definition Language (DDL), Constraints & Schema Integrity
--   - Experiment 6: Views (Virtual Tables) and Query Formulation
--   - Experiment 8: Secondary Indexes & Physical Schema Design
-- ============================================================================
-- Re-runnable script: Drops all existing views and tables before recreating.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Drop Views (in reverse dependency order)
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_phase_timeline CASCADE;
DROP VIEW IF EXISTS v_open_alerts CASCADE;
DROP VIEW IF EXISTS v_latest_telemetry CASCADE;
DROP VIEW IF EXISTS v_mode_delivery_rate CASCADE;
DROP VIEW IF EXISTS v_window_summary CASCADE;

-- ----------------------------------------------------------------------------
-- 2. Drop Tables (in reverse foreign key dependency order)
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS audit_log CASCADE;
DROP TABLE IF EXISTS command_log CASCADE;
DROP TABLE IF EXISTS alert CASCADE;
DROP TABLE IF EXISTS telemetry_reading CASCADE;
DROP TABLE IF EXISTS threshold_rule CASCADE;
DROP TABLE IF EXISTS sensor CASCADE;
DROP TABLE IF EXISTS transmission CASCADE;
DROP TABLE IF EXISTS router_decision CASCADE;
DROP TABLE IF EXISTS voice_clip CASCADE;
DROP TABLE IF EXISTS sstv_image CASCADE;
DROP TABLE IF EXISTS data_item CASCADE;
DROP TABLE IF EXISTS downlink_window CASCADE;
DROP TABLE IF EXISTS comm_mode CASCADE;
DROP TABLE IF EXISTS deployment_event CASCADE;
DROP TABLE IF EXISTS phase_transition CASCADE;
DROP TABLE IF EXISTS spacecraft CASCADE;
DROP TABLE IF EXISTS model_run CASCADE;
DROP TABLE IF EXISTS app_user CASCADE;
DROP TABLE IF EXISTS mission_phase CASCADE;

-- ============================================================================
-- 3. Table Definitions (19 Tables)
-- ============================================================================

-- Table 1: mission_phase
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Strong Entity / State Machine Domain Lookup Entity
CREATE TABLE mission_phase (
    phase_id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    seq_no INT NOT NULL,
    tt_c_only BOOLEAN NOT NULL DEFAULT FALSE
);

-- Table 2: app_user
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Specialization hierarchy via a single-table discriminator column (role).
--              The HAM_VOLUNTEER subtype requires a callsign (mandatory attribute for that subtype).
CREATE TABLE app_user (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('ADMIN', 'OPERATOR', 'HAM_VOLUNTEER', 'VIEWER')),
    callsign VARCHAR(20) UNIQUE,
    CONSTRAINT chk_app_user_callsign CHECK (role <> 'HAM_VOLUNTEER' OR callsign IS NOT NULL)
);

-- Table 3: model_run
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Metadata Entity tracking machine learning model versioning and metrics
CREATE TABLE model_run (
    model_version VARCHAR(50) PRIMARY KEY,
    trained_at TIMESTAMP NOT NULL DEFAULT now(),
    accuracy NUMERIC(5, 4) NOT NULL,
    rows_used INT NOT NULL,
    notes TEXT
);

-- Table 4: spacecraft
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Unary (Recursive) Relationship: deployer_id is a self-referential foreign key
--              to spacecraft(spacecraft_id), linking deployer (SomaiyaPod) to satellite (SomaiyaSat).
CREATE TABLE spacecraft (
    spacecraft_id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('SATELLITE', 'DEPLOYER')),
    deployer_id INT REFERENCES spacecraft(spacecraft_id) ON DELETE SET NULL,
    current_phase_id INT REFERENCES mission_phase(phase_id) ON DELETE RESTRICT,
    launch_date TIMESTAMP
);

-- Table 5: phase_transition
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Temporal Historical Entity recording state changes with dual foreign keys
--              referencing from_phase and to_phase in mission_phase.
CREATE TABLE phase_transition (
    transition_id SERIAL PRIMARY KEY,
    spacecraft_id INT NOT NULL REFERENCES spacecraft(spacecraft_id) ON DELETE CASCADE,
    from_phase_id INT NOT NULL REFERENCES mission_phase(phase_id) ON DELETE RESTRICT,
    to_phase_id INT NOT NULL REFERENCES mission_phase(phase_id) ON DELETE RESTRICT,
    reason TEXT NOT NULL,
    triggered_by VARCHAR(20) NOT NULL CHECK (triggered_by IN ('OPERATOR', 'AUTO')),
    user_id INT REFERENCES app_user(user_id) ON DELETE SET NULL,
    at TIMESTAMP NOT NULL DEFAULT now()
);

-- Table 6: deployment_event
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Physical Operational Event Entity tracking satellite deployment release status
CREATE TABLE deployment_event (
    event_id SERIAL PRIMARY KEY,
    spacecraft_id INT NOT NULL REFERENCES spacecraft(spacecraft_id) ON DELETE CASCADE,
    method VARCHAR(20) NOT NULL CHECK (method IN ('SWITCH', 'TIMER', 'BEACON')),
    status VARCHAR(20) NOT NULL CHECK (status IN ('PENDING', 'CONFIRMED', 'PARTIAL', 'FAILED')),
    signal_at TIMESTAMP,
    notes TEXT
);

-- Table 7: comm_mode
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Strong Entity / RF Mode Catalog specifying operational radio characteristics
CREATE TABLE comm_mode (
    mode_id SERIAL PRIMARY KEY,
    code VARCHAR(20) UNIQUE NOT NULL CHECK (code IN ('TTC', 'SSTV', 'M17', 'CODEC2')),
    base_priority INT NOT NULL,
    bitrate_bps INT NOT NULL,
    tx_power_mw INT NOT NULL
);

-- Table 8: downlink_window
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Operational Scheduling Entity representing ground station contact passes
CREATE TABLE downlink_window (
    window_id SERIAL PRIMARY KEY,
    spacecraft_id INT NOT NULL REFERENCES spacecraft(spacecraft_id) ON DELETE CASCADE,
    start_time TIMESTAMP NOT NULL,
    duration_s INT NOT NULL CHECK (duration_s > 0),
    capacity_bytes INT NOT NULL,
    energy_budget_mwh NUMERIC(10, 2) NOT NULL,
    expected_snr_db NUMERIC(5, 2) NOT NULL,
    battery_v NUMERIC(4, 2) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('PLANNED', 'SCHEDULED', 'COMPLETED'))
);

-- Table 9: data_item
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Supertype Entity in a Specialization/Generalization hierarchy.
--              Subtypes sstv_image and voice_clip extend this table via shared primary key.
CREATE TABLE data_item (
    item_id SERIAL PRIMARY KEY,
    spacecraft_id INT NOT NULL REFERENCES spacecraft(spacecraft_id) ON DELETE CASCADE,
    mode_id INT NOT NULL REFERENCES comm_mode(mode_id) ON DELETE RESTRICT,
    size_bytes INT NOT NULL CHECK (size_bytes > 0),
    urgency SMALLINT NOT NULL CHECK (urgency BETWEEN 1 AND 5),
    description TEXT NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('QUEUED', 'SCHEDULED', 'SENT', 'FAILED', 'DROPPED')),
    predicted_success NUMERIC(5, 4),
    model_version VARCHAR(50) REFERENCES model_run(model_version) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

-- Table 10: sstv_image
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Specialization Subtype Entity inheriting from data_item via primary key / foreign key (item_id).
CREATE TABLE sstv_image (
    item_id INT PRIMARY KEY REFERENCES data_item(item_id) ON DELETE CASCADE,
    resolution VARCHAR(20) NOT NULL,
    sstv_format VARCHAR(20) NOT NULL,
    captured_at TIMESTAMP NOT NULL
);

-- Table 11: voice_clip
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Specialization Subtype Entity inheriting from data_item via primary key / foreign key (item_id).
CREATE TABLE voice_clip (
    item_id INT PRIMARY KEY REFERENCES data_item(item_id) ON DELETE CASCADE,
    codec VARCHAR(20) NOT NULL CHECK (codec IN ('CODEC2', 'M17')),
    duration_s NUMERIC(6, 2) NOT NULL
);

-- Table 12: router_decision
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Associative Entity / M:N Junction between downlink_window and data_item
--              storing autonomous decision rationale, predictive scores, and operator override authority.
CREATE TABLE router_decision (
    decision_id SERIAL PRIMARY KEY,
    window_id INT NOT NULL REFERENCES downlink_window(window_id) ON DELETE CASCADE,
    item_id INT NOT NULL REFERENCES data_item(item_id) ON DELETE CASCADE,
    action VARCHAR(10) NOT NULL CHECK (action IN ('SEND', 'DEFER', 'DROP')),
    score NUMERIC(10, 2) NOT NULL,
    predicted_success NUMERIC(5, 4),
    rationale TEXT NOT NULL,
    model_version VARCHAR(50),
    fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
    decided_at TIMESTAMP NOT NULL DEFAULT now(),
    overridden BOOLEAN NOT NULL DEFAULT FALSE,
    override_action VARCHAR(10) CHECK (override_action IS NULL OR override_action IN ('SEND', 'DEFER', 'DROP')),
    override_by INT REFERENCES app_user(user_id) ON DELETE SET NULL,
    override_reason TEXT,
    override_at TIMESTAMP,
    CONSTRAINT uq_router_decision_window_item UNIQUE (window_id, item_id),
    CONSTRAINT chk_router_decision_override CHECK (
        overridden = FALSE OR (
            override_action IS NOT NULL AND
            override_by IS NOT NULL AND
            override_reason IS NOT NULL
        )
    )
);

-- Table 13: transmission
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: 1:1 Specialization/Execution Entity linked directly to router_decision via unique foreign key,
--              capturing radio link telemetry and HAM volunteer reception.
CREATE TABLE transmission (
    transmission_id SERIAL PRIMARY KEY,
    decision_id INT UNIQUE NOT NULL REFERENCES router_decision(decision_id) ON DELETE CASCADE,
    bytes_received INT NOT NULL,
    snr_db NUMERIC(5, 2) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('RECEIVED', 'PARTIAL', 'LOST')),
    received_by INT REFERENCES app_user(user_id) ON DELETE SET NULL,
    received_at TIMESTAMP NOT NULL DEFAULT now()
);

-- Table 14: sensor
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Strong Identifying Entity for the weak entity threshold_rule
CREATE TABLE sensor (
    sensor_id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    subsystem VARCHAR(20) NOT NULL CHECK (subsystem IN ('POWER', 'THERMAL', 'OBC', 'RF')),
    sensor_type VARCHAR(20) NOT NULL CHECK (sensor_type IN ('VOLTAGE', 'TEMP', 'CURRENT')),
    unit VARCHAR(20) NOT NULL
);

-- Table 15: threshold_rule
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Weak Entity dependent on sensor. Uses a composite primary key (sensor_id, rule_no)
--              and ON DELETE CASCADE foreign key constraint.
CREATE TABLE threshold_rule (
    sensor_id INT NOT NULL REFERENCES sensor(sensor_id) ON DELETE CASCADE,
    rule_no INT NOT NULL,
    min_val NUMERIC(8, 2) NOT NULL,
    max_val NUMERIC(8, 2) NOT NULL,
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('WARNING', 'CRITICAL')),
    PRIMARY KEY (sensor_id, rule_no),
    CONSTRAINT chk_threshold_min_max CHECK (min_val < max_val)
);

-- Table 16: telemetry_reading
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: High-Volume Time-Series Observation Entity tracking spacecraft telemetry
CREATE TABLE telemetry_reading (
    reading_id BIGSERIAL PRIMARY KEY,
    spacecraft_id INT NOT NULL REFERENCES spacecraft(spacecraft_id) ON DELETE CASCADE,
    sensor_id INT NOT NULL REFERENCES sensor(sensor_id) ON DELETE CASCADE,
    recorded_at TIMESTAMP NOT NULL,
    val NUMERIC(8, 2) NOT NULL,
    source VARCHAR(20) NOT NULL CHECK (source IN ('ORBIT', 'BENCH', 'EMI_EMC', 'THERMOVAC', 'VIBRATION', 'HIL'))
);

-- Table 17: alert
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Reactive Exception Event Entity with polymorphic foreign references to telemetry and downlink
CREATE TABLE alert (
    alert_id SERIAL PRIMARY KEY,
    alert_type VARCHAR(30) NOT NULL CHECK (alert_type IN ('TELEMETRY_LIMIT', 'CRITICAL_DATA_MISSED', 'DEPLOYMENT_ISSUE', 'SAFE_MODE')),
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('WARNING', 'CRITICAL', 'INFO')),
    message TEXT NOT NULL,
    reading_id BIGINT REFERENCES telemetry_reading(reading_id) ON DELETE SET NULL,
    window_id INT REFERENCES downlink_window(window_id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'ACK')),
    ack_by INT REFERENCES app_user(user_id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    ack_at TIMESTAMP
);

-- Table 18: command_log
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: System Audit/Transaction Log Entity recording manual and automated telecommands
CREATE TABLE command_log (
    command_id SERIAL PRIMARY KEY,
    spacecraft_id INT NOT NULL REFERENCES spacecraft(spacecraft_id) ON DELETE CASCADE,
    command_type VARCHAR(30) NOT NULL CHECK (command_type IN ('OVERRIDE_DECISION', 'ENTER_SAFE_MODE', 'EXIT_SAFE_MODE', 'REQUEST_PHASE', 'SET_PRIORITY')),
    issued_by INT REFERENCES app_user(user_id) ON DELETE SET NULL,
    is_automatic BOOLEAN NOT NULL DEFAULT FALSE,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    issued_at TIMESTAMP NOT NULL DEFAULT now()
);

-- Table 19: audit_log
-- Lab: Exp 1, Exp 2, Exp 3
-- EER Feature: Temporal Audit Trail Entity capturing historical row state (DML CDC) as JSONB
CREATE TABLE audit_log (
    log_id BIGSERIAL PRIMARY KEY,
    table_name VARCHAR(50) NOT NULL,
    action VARCHAR(20) NOT NULL,
    row_data JSONB NOT NULL,
    restored BOOLEAN NOT NULL DEFAULT FALSE,
    logged_at TIMESTAMP NOT NULL DEFAULT now()
);

-- ============================================================================
-- 4. Secondary Indexes (Exp 8: Index Analysis & Physical Storage)
-- ============================================================================
CREATE INDEX idx_telemetry_reading_sensor_time ON telemetry_reading (sensor_id, recorded_at);
CREATE INDEX idx_data_item_status_mode ON data_item (status, mode_id);
CREATE INDEX idx_router_decision_window ON router_decision (window_id);
CREATE INDEX idx_alert_status_type ON alert (status, alert_type);

-- ============================================================================
-- 5. Views (Exp 6: Views, Nested Queries & Aggregations)
-- ============================================================================

-- View 1: v_window_summary (planned vs delivered bytes per window)
CREATE OR REPLACE VIEW v_window_summary AS
SELECT
    w.window_id,
    w.spacecraft_id,
    sc.name AS spacecraft_name,
    w.start_time,
    w.duration_s,
    w.status AS window_status,
    w.capacity_bytes,
    w.energy_budget_mwh,
    w.expected_snr_db,
    w.battery_v,
    COALESCE(SUM(CASE WHEN COALESCE(rd.override_action, rd.action) = 'SEND' THEN di.size_bytes ELSE 0 END), 0) AS scheduled_bytes,
    COALESCE(SUM(CASE WHEN t.status = 'RECEIVED' THEN t.bytes_received ELSE 0 END), 0) AS delivered_bytes,
    COUNT(DISTINCT rd.decision_id) AS total_decisions,
    COUNT(DISTINCT CASE WHEN COALESCE(rd.override_action, rd.action) = 'SEND' THEN rd.decision_id END) AS send_decisions,
    COUNT(DISTINCT t.transmission_id) AS transmissions_count,
    COUNT(DISTINCT CASE WHEN t.status = 'RECEIVED' THEN t.transmission_id END) AS received_count
FROM downlink_window w
JOIN spacecraft sc ON w.spacecraft_id = sc.spacecraft_id
LEFT JOIN router_decision rd ON w.window_id = rd.window_id
LEFT JOIN data_item di ON rd.item_id = di.item_id
LEFT JOIN transmission t ON rd.decision_id = t.decision_id
GROUP BY w.window_id, w.spacecraft_id, sc.name, w.start_time, w.duration_s, w.status, w.capacity_bytes, w.energy_budget_mwh, w.expected_snr_db, w.battery_v;

-- View 2: v_mode_delivery_rate (per mode: sent, received, success %)
CREATE OR REPLACE VIEW v_mode_delivery_rate AS
SELECT
    cm.mode_id,
    cm.code AS mode_code,
    cm.base_priority,
    cm.bitrate_bps,
    cm.tx_power_mw,
    COUNT(DISTINCT di.item_id) AS total_items,
    COUNT(DISTINCT CASE WHEN di.status IN ('SENT', 'FAILED') OR t.transmission_id IS NOT NULL THEN di.item_id END) AS total_sent,
    COUNT(DISTINCT CASE WHEN t.status = 'RECEIVED' THEN di.item_id END) AS total_received,
    COUNT(DISTINCT CASE WHEN t.status = 'LOST' THEN di.item_id END) AS total_lost,
    COUNT(DISTINCT CASE WHEN t.status = 'PARTIAL' THEN di.item_id END) AS total_partial,
    CASE 
        WHEN COUNT(DISTINCT CASE WHEN di.status IN ('SENT', 'FAILED') OR t.transmission_id IS NOT NULL THEN di.item_id END) = 0 THEN 0.00
        ELSE ROUND(
            (COUNT(DISTINCT CASE WHEN t.status = 'RECEIVED' THEN di.item_id END)::NUMERIC / 
             COUNT(DISTINCT CASE WHEN di.status IN ('SENT', 'FAILED') OR t.transmission_id IS NOT NULL THEN di.item_id END)::NUMERIC) * 100.0, 
            2
        )
    END AS success_rate_pct
FROM comm_mode cm
LEFT JOIN data_item di ON cm.mode_id = di.mode_id
LEFT JOIN router_decision rd ON di.item_id = rd.item_id
LEFT JOIN transmission t ON rd.decision_id = t.decision_id
GROUP BY cm.mode_id, cm.code, cm.base_priority, cm.bitrate_bps, cm.tx_power_mw;

-- View 3: v_latest_telemetry (most recent reading per sensor)
CREATE OR REPLACE VIEW v_latest_telemetry AS
SELECT DISTINCT ON (s.sensor_id)
    s.sensor_id,
    s.name AS sensor_name,
    s.subsystem,
    s.sensor_type,
    s.unit,
    tr.reading_id,
    tr.spacecraft_id,
    sc.name AS spacecraft_name,
    tr.recorded_at,
    tr.val AS latest_value,
    tr.source
FROM sensor s
LEFT JOIN telemetry_reading tr ON s.sensor_id = tr.sensor_id
LEFT JOIN spacecraft sc ON tr.spacecraft_id = sc.spacecraft_id
ORDER BY s.sensor_id, tr.recorded_at DESC NULLS LAST;

-- View 4: v_open_alerts (active open alerts with contextual reading & window details)
CREATE OR REPLACE VIEW v_open_alerts AS
SELECT
    a.alert_id,
    a.alert_type,
    a.severity,
    a.message,
    a.status,
    a.created_at,
    a.reading_id,
    tr.sensor_id,
    s.name AS sensor_name,
    tr.val AS reading_value,
    tr.source AS reading_source,
    a.window_id,
    w.start_time AS window_start_time
FROM alert a
LEFT JOIN telemetry_reading tr ON a.reading_id = tr.reading_id
LEFT JOIN sensor s ON tr.sensor_id = s.sensor_id
LEFT JOIN downlink_window w ON a.window_id = w.window_id
WHERE a.status = 'OPEN';

-- View 5: v_phase_timeline (chronological mission phase transitions)
CREATE OR REPLACE VIEW v_phase_timeline AS
SELECT
    pt.transition_id,
    pt.spacecraft_id,
    sc.name AS spacecraft_name,
    pt.from_phase_id,
    mp_from.name AS from_phase_name,
    pt.to_phase_id,
    mp_to.name AS to_phase_name,
    pt.reason,
    pt.triggered_by,
    pt.user_id,
    u.username AS triggered_by_user,
    pt.at AS transition_time
FROM phase_transition pt
JOIN spacecraft sc ON pt.spacecraft_id = sc.spacecraft_id
JOIN mission_phase mp_from ON pt.from_phase_id = mp_from.phase_id
JOIN mission_phase mp_to ON pt.to_phase_id = mp_to.phase_id
LEFT JOIN app_user u ON pt.user_id = u.user_id
ORDER BY pt.at DESC;
