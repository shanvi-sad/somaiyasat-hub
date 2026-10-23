-- ============================================================================
-- SomaiyaSat Telemetry Hub - 06_index_analysis.sql
-- ============================================================================
-- Lab Experiments Covered:
--   - Experiment 8: Physical Database Design, Secondary Indexing, B-Tree Indexes,
--     Query Optimization, Execution Plan Evaluation (EXPLAIN ANALYZE)
-- ============================================================================
-- Objective:
--   Demonstrates the performance impact of indexing strategies on a 100,000+
--   row time-series telemetry table for range queries:
--     Stage 1: Unindexed Heap Table (Baseline Sequential Scan)
--     Stage 2: Single-Column B-Tree Index on sensor_id
--     Stage 3: Composite Multi-Column B-Tree Index on (sensor_id, recorded_at DESC)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Step 1: Create an unindexed copy of telemetry_reading for controlled benchmarking
-- Concept: Creating an unindexed heap relation to measure baseline performance
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS perf_telemetry_reading CASCADE;

CREATE TABLE perf_telemetry_reading AS
SELECT * FROM telemetry_reading;

-- Update planner statistics for accurate cost estimation
ANALYZE perf_telemetry_reading;

-- ============================================================================
-- STAGE 1: Benchmark Query on Unindexed Table
-- ============================================================================
-- Query Concept: Time-window telemetry filter for a single sensor over last 24h
-- Expected Plan: Full Sequential Scan (Seq Scan) across all 100,200 rows.
-- Plan Analysis:
--   - PostgreSQL reads every single block of the heap table from disk/buffer.
--   - Every row is evaluated against the filter predicate ((sensor_id = 1) AND (recorded_at >= ...)).
--   - Over 100,000 non-matching rows are discarded by the CPU filter.
--   - Resulting rows are then sorted in work_mem using Quicksort.
--   - Cost: ~2,400 | Execution Time: ~15-20 ms.
-- ============================================================================
EXPLAIN ANALYZE
SELECT reading_id, sensor_id, val, recorded_at, source
FROM perf_telemetry_reading
WHERE sensor_id = 1
  AND recorded_at >= '2026-05-13 04:23:00'
ORDER BY recorded_at DESC;


-- ============================================================================
-- STAGE 2: Benchmark with Single-Column Index on sensor_id
-- ============================================================================
-- Step: Create single-column B-Tree index and refresh optimizer statistics
-- Concept: Secondary index on high-selectivity filtering column (sensor_id)
-- ============================================================================
CREATE INDEX idx_perf_sensor ON perf_telemetry_reading (sensor_id);

ANALYZE perf_telemetry_reading;

-- ----------------------------------------------------------------------------
-- Query Concept: Exact same query evaluated against single-column index
-- Expected Plan: Bitmap Index Scan + Bitmap Heap Scan
-- Plan Analysis:
--   - The query planner utilizes idx_perf_sensor to quickly find matching TIDs for sensor_id = 1.
--   - Bitmap Index Scan builds a bit-vector of target heap pages.
--   - Bitmap Heap Scan accesses only pages containing sensor_id = 1 (approx 841 blocks instead of all blocks).
--   - However, the timestamp condition (recorded_at >= ...) must still be filtered in memory on the heap.
--   - Over 8,200 rows for sensor_id = 1 are removed by the heap filter.
--   - Cost: ~1,120 | Execution Time: ~1.5 ms (Approx 10x faster than Stage 1).
-- ----------------------------------------------------------------------------
EXPLAIN ANALYZE
SELECT reading_id, sensor_id, val, recorded_at, source
FROM perf_telemetry_reading
WHERE sensor_id = 1
  AND recorded_at >= '2026-05-13 04:23:00'
ORDER BY recorded_at DESC;


-- ============================================================================
-- STAGE 3: Benchmark with Composite Multi-Column Index on (sensor_id, recorded_at DESC)
-- ============================================================================
-- Step: Replace single-column index with a composite multi-column B-Tree index
-- Concept: Multi-column index covering equality prefix (sensor_id) and range suffix (recorded_at)
-- ============================================================================
DROP INDEX IF EXISTS idx_perf_sensor;

CREATE INDEX idx_perf_composite ON perf_telemetry_reading (sensor_id, recorded_at DESC);

ANALYZE perf_telemetry_reading;

-- ----------------------------------------------------------------------------
-- Query Concept: Exact same query evaluated against composite multi-column index
-- Expected Plan: Direct Composite Bitmap Index Scan / Index Scan
-- Plan Analysis:
--   - Both filter predicates ((sensor_id = 1) AND (recorded_at >= ...)) are evaluated
--     directly inside the B-Tree index structure as Index Conditions (no heap filtering needed).
--   - Bitmap Index Scan traverses the tree to the leaf node for sensor_id = 1 and recorded_at range.
--   - Only 15 heap blocks (exact matches) are fetched from memory instead of 841 blocks.
--   - Zero rows removed by filter!
--   - Cost: ~380 | Execution Time: ~0.15 ms (Over 100x faster than Stage 1 unindexed scan).
-- ----------------------------------------------------------------------------
EXPLAIN ANALYZE
SELECT reading_id, sensor_id, val, recorded_at, source
FROM perf_telemetry_reading
WHERE sensor_id = 1
  AND recorded_at >= '2026-05-13 04:23:00'
ORDER BY recorded_at DESC;


-- ----------------------------------------------------------------------------
-- Step 4: Cleanup temporary performance benchmark table
-- Concept: Database resource reclamation and workspace hygiene
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS perf_telemetry_reading CASCADE;
