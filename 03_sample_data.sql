-- ============================================================================
-- SomaiyaSat Telemetry Hub - 03_sample_data.sql
-- ============================================================================
-- Lab Experiments Covered:
--   - Experiment 4: Data Manipulation Language (DML) - Data Insertion & Populating Reference Tables
-- ============================================================================
-- Note: Reference data only (Stage 1). Operational and historical time-series
-- data are generated via scripts/gen_data.py in Stage 2.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Mission Phases (Lookup / State Machine Domain)
-- ----------------------------------------------------------------------------
INSERT INTO mission_phase (name, seq_no, tt_c_only) VALUES
    ('LAUNCH', 1, FALSE),
    ('DEPLOYED', 2, FALSE),
    ('COMMISSIONING', 3, FALSE),
    ('NOMINAL', 4, FALSE),
    ('SAFE_MODE', 5, TRUE);

-- ----------------------------------------------------------------------------
-- 2. Spacecraft (Unary Relationship: SomaiyaPod deployer -> SomaiyaSat satellite)
-- ----------------------------------------------------------------------------
-- First insert the deployer (SomaiyaPod)
INSERT INTO spacecraft (name, kind, deployer_id, current_phase_id, launch_date) VALUES
    ('SomaiyaPod', 'DEPLOYER', NULL, (SELECT phase_id FROM mission_phase WHERE name = 'LAUNCH'), '2026-03-15 06:30:00');

-- Then insert the PocketQube satellite (SomaiyaSat), referencing its deployer
INSERT INTO spacecraft (name, kind, deployer_id, current_phase_id, launch_date) VALUES
    ('SomaiyaSat', 'SATELLITE', (SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaPod'), (SELECT phase_id FROM mission_phase WHERE name = 'LAUNCH'), '2026-03-15 06:30:00');

-- ----------------------------------------------------------------------------
-- 3. Communication Modes (RF Catalog)
-- Priorities: TTC (1, highest) > SSTV (2) > CODEC2 (3) > M17 (4)
-- ----------------------------------------------------------------------------
INSERT INTO comm_mode (code, base_priority, bitrate_bps, tx_power_mw) VALUES
    ('TTC', 1, 1200, 500),
    ('SSTV', 2, 9600, 1000),
    ('CODEC2', 3, 3200, 800),
    ('M17', 4, 4800, 800);

-- ----------------------------------------------------------------------------
-- 4. Application Users (Specialization via role discriminator)
-- 8 users: 1 Admin, 2 Operators, 3 HAM Volunteers (with callsigns), 2 Viewers
-- ----------------------------------------------------------------------------
INSERT INTO app_user (username, role, callsign) VALUES
    ('admin', 'ADMIN', NULL),
    ('ops_asha', 'OPERATOR', NULL),
    ('ops_rohan', 'OPERATOR', NULL),
    ('ham_vu2som', 'HAM_VOLUNTEER', 'VU2SOM'),
    ('ham_vu3kjs', 'HAM_VOLUNTEER', 'VU3KJS'),
    ('ham_oh2reo', 'HAM_VOLUNTEER', 'OH2REO'),
    ('viewer1', 'VIEWER', NULL),
    ('viewer2', 'VIEWER', NULL);

-- ----------------------------------------------------------------------------
-- 5. Sensors (12 Subsystem Sensors) & Threshold Rules (Weak Entity)
-- For each sensor:
--   Rule 1 (WARNING): narrower operational limits
--   Rule 2 (CRITICAL): wider allowable limits before mission risk
--   Battery Voltage CRITICAL min is set to exactly 3.30 V per specification.
-- ----------------------------------------------------------------------------
-- POWER Subsystem
INSERT INTO sensor (name, subsystem, sensor_type, unit) VALUES
    ('Battery Voltage', 'POWER', 'VOLTAGE', 'V'),
    ('Battery Current', 'POWER', 'CURRENT', 'mA'),
    ('Solar Panel Voltage', 'POWER', 'VOLTAGE', 'V'),
    ('Solar Current', 'POWER', 'CURRENT', 'mA'),
    -- THERMAL Subsystem
    ('Battery Temp', 'THERMAL', 'TEMP', 'C'),
    ('Panel Temp +Y', 'THERMAL', 'TEMP', 'C'),
    ('Panel Temp -Y', 'THERMAL', 'TEMP', 'C'),
    -- OBC Subsystem
    ('OBC Core Temp', 'OBC', 'TEMP', 'C'),
    ('OBC Supply Voltage', 'OBC', 'VOLTAGE', 'V'),
    -- RF Subsystem
    ('PA Temperature', 'RF', 'TEMP', 'C'),
    ('RF Supply Voltage', 'RF', 'VOLTAGE', 'V'),
    ('TX Current', 'RF', 'CURRENT', 'mA');

-- Threshold Rules (Weak Entity of sensor)
-- 1. Battery Voltage: Warning [3.60, 4.15], Critical [3.30, 4.25]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'Battery Voltage'), 1, 3.60, 4.15, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'Battery Voltage'), 2, 3.30, 4.25, 'CRITICAL');

-- 2. Battery Current (mA): Warning [-800, 800], Critical [-1200, 1200]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'Battery Current'), 1, -800.00, 800.00, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'Battery Current'), 2, -1200.00, 1200.00, 'CRITICAL');

-- 3. Solar Panel Voltage (V): Warning [0.00, 5.20], Critical [-0.20, 6.00]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'Solar Panel Voltage'), 1, 0.00, 5.20, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'Solar Panel Voltage'), 2, -0.20, 6.00, 'CRITICAL');

-- 4. Solar Current (mA): Warning [0, 350], Critical [-10, 450]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'Solar Current'), 1, 0.00, 350.00, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'Solar Current'), 2, -10.00, 450.00, 'CRITICAL');

-- 5. Battery Temp (C): Warning [-5, 45], Critical [-15, 55]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'Battery Temp'), 1, -5.00, 45.00, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'Battery Temp'), 2, -15.00, 55.00, 'CRITICAL');

-- 6. Panel Temp +Y (C): Warning [-30, 70], Critical [-45, 85]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'Panel Temp +Y'), 1, -30.00, 70.00, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'Panel Temp +Y'), 2, -45.00, 85.00, 'CRITICAL');

-- 7. Panel Temp -Y (C): Warning [-30, 70], Critical [-45, 85]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'Panel Temp -Y'), 1, -30.00, 70.00, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'Panel Temp -Y'), 2, -45.00, 85.00, 'CRITICAL');

-- 8. OBC Core Temp (C): Warning [-10, 65], Critical [-25, 80]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'OBC Core Temp'), 1, -10.00, 65.00, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'OBC Core Temp'), 2, -25.00, 80.00, 'CRITICAL');

-- 9. OBC Supply Voltage (V): Warning [3.15, 3.45], Critical [3.00, 3.60]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'OBC Supply Voltage'), 1, 3.15, 3.45, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'OBC Supply Voltage'), 2, 3.00, 3.60, 'CRITICAL');

-- 10. PA Temperature (C): Warning [-10, 70], Critical [-20, 85]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'PA Temperature'), 1, -10.00, 70.00, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'PA Temperature'), 2, -20.00, 85.00, 'CRITICAL');

-- 11. RF Supply Voltage (V): Warning [3.10, 3.50], Critical [2.95, 3.65]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'RF Supply Voltage'), 1, 3.10, 3.50, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'RF Supply Voltage'), 2, 2.95, 3.65, 'CRITICAL');

-- 12. TX Current (mA): Warning [0, 450], Critical [-5, 600]
INSERT INTO threshold_rule (sensor_id, rule_no, min_val, max_val, severity) VALUES
    ((SELECT sensor_id FROM sensor WHERE name = 'TX Current'), 1, 0.00, 450.00, 'WARNING'),
    ((SELECT sensor_id FROM sensor WHERE name = 'TX Current'), 2, -5.00, 600.00, 'CRITICAL');

-- ----------------------------------------------------------------------------
-- 6. Initial Deployment Event
-- One PENDING deployment_event (method SWITCH) for SomaiyaSat
-- ----------------------------------------------------------------------------
INSERT INTO deployment_event (spacecraft_id, method, status, signal_at, notes) VALUES
    ((SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat'), 'SWITCH', 'PENDING', NULL, 'Pre-deployment interlock engaged. Awaiting mechanical spring switch release from SomaiyaPod deployer.');
