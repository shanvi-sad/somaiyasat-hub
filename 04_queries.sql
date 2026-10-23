-- ============================================================================
-- SomaiyaSat Telemetry Hub - 04_queries.sql
-- ============================================================================
-- Lab Experiments Covered:
--   - Experiment 5: Advanced SQL - Aggregations, GROUP BY, HAVING, ORDER BY,
--     Pattern Matching (LIKE), and Set Operations (UNION, INTERSECT, EXCEPT)
--   - Experiment 6: Nested Queries, Subqueries, Relational Joins, and Views
-- ============================================================================
-- Contains 16 labelled queries (Q1-Q9 for Exp 5, Q10-Q16 for Exp 6)
-- Each query is preceded by an explanatory comment highlighting its concept.
-- ============================================================================

-- ============================================================================
-- EXPERIMENT 5: Aggregates, GROUP BY, HAVING, LIKE & SET Operations (Q1 - Q9)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Q1: Total scheduled payload bytes, item count, and average item size per communication mode
-- Concept: Multi-column aggregation (COUNT, SUM, AVG) with GROUP BY and ORDER BY descending
-- ----------------------------------------------------------------------------
SELECT
    cm.code AS mode_code,
    cm.base_priority,
    COUNT(di.item_id) AS total_items_generated,
    COUNT(CASE WHEN rd.action = 'SEND' THEN 1 END) AS total_scheduled_items,
    COALESCE(SUM(CASE WHEN rd.action = 'SEND' THEN di.size_bytes ELSE 0 END), 0) AS total_scheduled_bytes,
    ROUND(AVG(di.size_bytes), 2) AS avg_item_size_bytes
FROM comm_mode cm
LEFT JOIN data_item di ON cm.mode_id = di.mode_id
LEFT JOIN router_decision rd ON di.item_id = rd.item_id
GROUP BY cm.code, cm.base_priority
ORDER BY total_scheduled_bytes DESC;

-- ----------------------------------------------------------------------------
-- Q2: Subsystem telemetry metrics (Min, Max, Avg reading and sample count per subsystem)
-- Concept: Relational multi-table aggregation across parent-child entities with GROUP BY
-- ----------------------------------------------------------------------------
SELECT
    s.subsystem,
    COUNT(tr.reading_id) AS total_samples,
    ROUND(MIN(tr.val), 2) AS min_reading,
    ROUND(MAX(tr.val), 2) AS max_reading,
    ROUND(AVG(tr.val), 2) AS avg_reading
FROM sensor s
JOIN telemetry_reading tr ON s.sensor_id = tr.sensor_id
GROUP BY s.subsystem
ORDER BY total_samples DESC;

-- ----------------------------------------------------------------------------
-- Q3: Communication modes with a reception delivery success rate below 90%
-- Concept: Post-aggregation filtering using the HAVING clause on calculated ratios
-- ----------------------------------------------------------------------------
SELECT
    cm.code AS mode_code,
    COUNT(t.transmission_id) AS total_transmissions_sent,
    COUNT(CASE WHEN t.status = 'RECEIVED' THEN 1 END) AS successful_receptions,
    ROUND(
        (COUNT(CASE WHEN t.status = 'RECEIVED' THEN 1 END)::NUMERIC /
         NULLIF(COUNT(t.transmission_id), 0)::NUMERIC) * 100.0,
        2
    ) AS delivery_rate_pct
FROM comm_mode cm
JOIN data_item di ON cm.mode_id = di.mode_id
JOIN router_decision rd ON di.item_id = rd.item_id
JOIN transmission t ON rd.decision_id = t.decision_id
GROUP BY cm.code
HAVING (COUNT(CASE WHEN t.status = 'RECEIVED' THEN 1 END)::NUMERIC / NULLIF(COUNT(t.transmission_id), 0)::NUMERIC) < 0.90
ORDER BY delivery_rate_pct ASC;

-- ----------------------------------------------------------------------------
-- Q4: Amateur radio volunteer stations with callsigns originating in India (prefix 'VU%')
-- Concept: Pattern matching using the LIKE operator with wildcards on specialized subtypes
-- ----------------------------------------------------------------------------
SELECT
    user_id,
    username,
    role,
    callsign
FROM app_user
WHERE role = 'HAM_VOLUNTEER'
  AND callsign LIKE 'VU%'
ORDER BY callsign ASC;

-- ----------------------------------------------------------------------------
-- Q5: Telemetry limit alert messages filtered by specific test source tags
-- Concept: Case-sensitive substring search using the LIKE operator with multiple wildcards
-- ----------------------------------------------------------------------------
SELECT
    alert_id,
    alert_type,
    severity,
    message,
    created_at
FROM alert
WHERE alert_type = 'TELEMETRY_LIMIT'
  AND message LIKE '%[BENCH]%'
ORDER BY created_at DESC
LIMIT 5;

-- ----------------------------------------------------------------------------
-- Q6: Downlink windows with high traffic contention where delivered bytes exceeded 50 KB
-- Concept: Aggregation with multi-table JOIN, GROUP BY, and a HAVING filter on SUM
-- ----------------------------------------------------------------------------
SELECT
    w.window_id,
    w.start_time,
    w.capacity_bytes,
    COUNT(t.transmission_id) AS packets_transmitted,
    SUM(t.bytes_received) AS total_bytes_received
FROM downlink_window w
JOIN router_decision rd ON w.window_id = rd.window_id
JOIN transmission t ON rd.decision_id = t.decision_id
WHERE t.status = 'RECEIVED'
GROUP BY w.window_id, w.start_time, w.capacity_bytes
HAVING SUM(t.bytes_received) > 50000
ORDER BY total_bytes_received DESC
LIMIT 5;

-- ----------------------------------------------------------------------------
-- Q7: Downlink passes that transmitted high-bandwidth payload (SSTV images OR M17 digital voice)
-- Concept: Set Operation - UNION (combines distinct window IDs across two modes, removing duplicates)
-- ----------------------------------------------------------------------------
SELECT DISTINCT rd.window_id, 'SSTV_CARRIED' AS payload_type
FROM router_decision rd
JOIN data_item di ON rd.item_id = di.item_id
JOIN comm_mode cm ON di.mode_id = cm.mode_id
WHERE cm.code = 'SSTV' AND rd.action = 'SEND'
UNION
SELECT DISTINCT rd.window_id, 'M17_CARRIED' AS payload_type
FROM router_decision rd
JOIN data_item di ON rd.item_id = di.item_id
JOIN comm_mode cm ON di.mode_id = cm.mode_id
WHERE cm.code = 'M17' AND rd.action = 'SEND'
ORDER BY window_id ASC
LIMIT 10;

-- ----------------------------------------------------------------------------
-- Q8: Downlink passes that successfully scheduled BOTH SSTV image AND M17 voice traffic
-- Concept: Set Operation - INTERSECT (computes intersection of distinct windows between two modes)
-- ----------------------------------------------------------------------------
SELECT rd.window_id
FROM router_decision rd
JOIN data_item di ON rd.item_id = di.item_id
JOIN comm_mode cm ON di.mode_id = cm.mode_id
WHERE cm.code = 'SSTV' AND rd.action = 'SEND'
INTERSECT
SELECT rd.window_id
FROM router_decision rd
JOIN data_item di ON rd.item_id = di.item_id
JOIN comm_mode cm ON di.mode_id = cm.mode_id
WHERE cm.code = 'M17' AND rd.action = 'SEND'
ORDER BY window_id ASC
LIMIT 10;

-- ----------------------------------------------------------------------------
-- Q9: Downlink passes that scheduled SSTV visual frames BUT EXCLUDED M17 digital voice
-- Concept: Set Operation - EXCEPT (computes set difference, isolating windows unique to first set)
-- ----------------------------------------------------------------------------
SELECT rd.window_id
FROM router_decision rd
JOIN data_item di ON rd.item_id = di.item_id
JOIN comm_mode cm ON di.mode_id = cm.mode_id
WHERE cm.code = 'SSTV' AND rd.action = 'SEND'
EXCEPT
SELECT rd.window_id
FROM router_decision rd
JOIN data_item di ON rd.item_id = di.item_id
JOIN comm_mode cm ON di.mode_id = cm.mode_id
WHERE cm.code = 'M17' AND rd.action = 'SEND'
ORDER BY window_id ASC
LIMIT 10;


-- ============================================================================
-- EXPERIMENT 6: Nested Queries, Subqueries, Joins & Views (Q10 - Q16)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Q10: Downlink windows that processed high-urgency (urgency = 5) priority items
-- Concept: Nested subquery using the IN operator with an independent inner query
-- ----------------------------------------------------------------------------
SELECT
    w.window_id,
    w.start_time,
    w.duration_s,
    w.capacity_bytes,
    w.expected_snr_db,
    w.status
FROM downlink_window w
WHERE w.window_id IN (
    SELECT DISTINCT rd.window_id
    FROM router_decision rd
    JOIN data_item di ON rd.item_id = di.item_id
    WHERE di.urgency = 5
      AND rd.action = 'SEND'
)
ORDER BY w.start_time DESC
LIMIT 5;

-- ----------------------------------------------------------------------------
-- Q11: Payload data items whose byte size strictly exceeds their communication mode's average size
-- Concept: Correlated scalar subquery in the WHERE clause comparing rows against category averages
-- ----------------------------------------------------------------------------
SELECT
    di.item_id,
    cm.code AS mode_code,
    di.size_bytes,
    di.description,
    ROUND((SELECT AVG(di2.size_bytes) FROM data_item di2 WHERE di2.mode_id = di.mode_id), 2) AS mode_avg_bytes
FROM data_item di
JOIN comm_mode cm ON di.mode_id = cm.mode_id
WHERE di.size_bytes > (
    SELECT AVG(di2.size_bytes)
    FROM data_item di2
    WHERE di2.mode_id = di.mode_id
)
ORDER BY di.size_bytes DESC
LIMIT 5;

-- ----------------------------------------------------------------------------
-- Q12: Scheduled downlink windows where NO TT&C packet was successfully received on ground
-- Concept: Correlated subquery using NOT EXISTS for existence-check filtering
-- ----------------------------------------------------------------------------
SELECT
    w.window_id,
    w.start_time,
    w.expected_snr_db,
    w.battery_v
FROM downlink_window w
WHERE w.status = 'COMPLETED'
  AND NOT EXISTS (
      SELECT 1
      FROM router_decision rd
      JOIN transmission t ON rd.decision_id = t.decision_id
      JOIN data_item di ON rd.item_id = di.item_id
      JOIN comm_mode cm ON di.mode_id = cm.mode_id
      WHERE rd.window_id = w.window_id
        AND cm.code = 'TTC'
        AND t.status = 'RECEIVED'
  )
ORDER BY w.window_id ASC;

-- ----------------------------------------------------------------------------
-- Q13: Spacecraft hierarchy showing deployer vehicle and deployed satellite relationship
-- Concept: Unary self-join on spacecraft table resolving parent-child recursive relationship
-- ----------------------------------------------------------------------------
SELECT
    sat.spacecraft_id AS satellite_id,
    sat.name AS satellite_name,
    sat.kind AS satellite_kind,
    dep.spacecraft_id AS deployer_id,
    dep.name AS deployer_name,
    dep.kind AS deployer_kind,
    sat.launch_date
FROM spacecraft sat
LEFT JOIN spacecraft dep ON sat.deployer_id = dep.spacecraft_id
WHERE sat.kind = 'SATELLITE';

-- ----------------------------------------------------------------------------
-- Q14: End-to-end transmission audit trail joining decisions, transmissions, items, and modes
-- Concept: Multi-table relational inner join across 4 core operational tables
-- ----------------------------------------------------------------------------
SELECT
    t.transmission_id,
    rd.decision_id,
    di.item_id,
    cm.code AS mode_code,
    di.description,
    di.size_bytes AS scheduled_bytes,
    t.bytes_received,
    t.snr_db AS measured_snr_db,
    t.status AS rx_status,
    u.callsign AS received_by_callsign
FROM router_decision rd
JOIN transmission t ON rd.decision_id = t.decision_id
JOIN data_item di ON rd.item_id = di.item_id
JOIN comm_mode cm ON di.mode_id = cm.mode_id
LEFT JOIN app_user u ON t.received_by = u.user_id
ORDER BY t.transmission_id DESC
LIMIT 5;

-- ----------------------------------------------------------------------------
-- Q15: High-level window throughput and mode performance statistics queried from virtual views
-- Concept: Querying user-defined views (v_window_summary and v_mode_delivery_rate)
-- ----------------------------------------------------------------------------
SELECT
    vws.window_id,
    vws.spacecraft_name,
    vws.capacity_bytes,
    vws.scheduled_bytes,
    vws.delivered_bytes,
    ROUND((vws.delivered_bytes::NUMERIC / NULLIF(vws.scheduled_bytes, 0)::NUMERIC) * 100.0, 2) AS window_efficiency_pct
FROM v_window_summary vws
WHERE vws.window_status = 'COMPLETED'
ORDER BY vws.delivered_bytes DESC
LIMIT 5;

-- ----------------------------------------------------------------------------
-- Q16: Mission operations status monitor querying telemetry, alert, and timeline views
-- Concept: Composite operational dashboard query utilizing virtual views (v_latest_telemetry, v_open_alerts)
-- ----------------------------------------------------------------------------
SELECT
    lt.sensor_name,
    lt.subsystem,
    lt.latest_value,
    lt.unit,
    lt.source,
    (SELECT COUNT(*) FROM v_open_alerts) AS total_open_alerts,
    (SELECT to_phase_name FROM v_phase_timeline LIMIT 1) AS active_mission_phase
FROM v_latest_telemetry lt
WHERE lt.subsystem = 'POWER'
ORDER BY lt.sensor_name ASC;
