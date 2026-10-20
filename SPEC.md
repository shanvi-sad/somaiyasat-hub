# SomaiyaSat Telemetry Hub

## Context
SomaiyaSat is a PocketQube satellite (5 cm cube) deployed by a deployer called SomaiyaPod. It has a short, power-limited downlink and must send four kinds of data: TT&C/housekeeping (TTC), SSTV images (SSTV), M17 digital voice/data (M17) and Codec2 voice (CODEC2). An on-board router decides what to send in each downlink window. Rules from the case study: TT&C is always the highest priority and must never be dropped; the router must fall back to rule-based logic if the model output is missing or low-confidence; every decision must store a human-readable rationale; ground operators keep override authority; a critical battery reading triggers safe mode (TT&C only); deployment must be confirmed before normal operations. The Hub is the DATABASE behind this mission. All orbit/radio data is SIMULATED.

## Stack (do not change)
PostgreSQL 14+ (tested on 16) | PL/pgSQL triggers and functions hold the business logic | Python 3, pandas, scikit-learn, psycopg2 | Streamlit 1.50+ with plotly for charts. Plain SQL via psycopg2, NO ORM.
Database name: somaiyasat. Config via env vars DB_HOST (default localhost), DB_USER (default = current OS user), DB_PASS (default empty), DB_NAME (default somaiyasat). Must run on macOS with Homebrew Postgres (no "postgres" superuser; use the OS user).

## Repository layout
01_schema.sql, 02_functions_triggers.sql, 03_sample_data.sql, 04_queries.sql, 05_security_dcl.sql, 06_index_analysis.sql, 07_transactions_demo.sql, scripts/db.py, scripts/gen_data.py, scripts/router_model.py, scripts/smoke_test.py, app/Home.py, app/common.py, app/pages/*.py, docs/, requirements.txt, rebuild.sh, README.md.
Every SQL file starts with a comment header naming the lab experiment(s) it covers. 01_schema.sql drops and recreates everything so it can be re-run.

## Tables (19)
1. mission_phase(phase_id PK, name UNIQUE, seq_no, tt_c_only BOOLEAN): LAUNCH, DEPLOYED, COMMISSIONING, NOMINAL, SAFE_MODE (SAFE_MODE has tt_c_only = true).
2. spacecraft(spacecraft_id PK, name UNIQUE, kind CHECK IN ('SATELLITE','DEPLOYER'), deployer_id FK to spacecraft (UNARY relationship), current_phase_id FK to mission_phase, launch_date). Rows: SomaiyaPod (DEPLOYER), SomaiyaSat (SATELLITE, deployer = SomaiyaPod).
3. phase_transition(transition_id PK, spacecraft_id FK, from_phase_id FK, to_phase_id FK, reason, triggered_by CHECK IN ('OPERATOR','AUTO'), user_id FK nullable, at TIMESTAMP default now).
4. deployment_event(event_id PK, spacecraft_id FK, method CHECK IN ('SWITCH','TIMER','BEACON'), status CHECK IN ('PENDING','CONFIRMED','PARTIAL','FAILED'), signal_at, notes).
5. app_user(user_id PK, username UNIQUE, role CHECK IN ('ADMIN','OPERATOR','HAM_VOLUNTEER','VIEWER'), callsign UNIQUE nullable, CHECK(role <> 'HAM_VOLUNTEER' OR callsign IS NOT NULL)). This is a SPECIALIZATION via a discriminator column. No passwords.
6. comm_mode(mode_id PK, code UNIQUE IN ('TTC','SSTV','M17','CODEC2'), base_priority (1 = highest), bitrate_bps, tx_power_mw).
7. downlink_window(window_id PK, spacecraft_id FK, start_time, duration_s CHECK > 0, capacity_bytes, energy_budget_mwh, expected_snr_db, battery_v, status CHECK IN ('PLANNED','SCHEDULED','COMPLETED')).
8. data_item(item_id PK, spacecraft_id FK, mode_id FK, size_bytes CHECK > 0, urgency SMALLINT CHECK 1..5, description, status CHECK IN ('QUEUED','SCHEDULED','SENT','FAILED','DROPPED'), predicted_success NUMERIC nullable, model_version nullable, created_at).
9. sstv_image(item_id PK and FK to data_item, resolution, sstv_format, captured_at).  10. voice_clip(item_id PK and FK to data_item, codec CHECK IN ('CODEC2','M17'), duration_s). These two are SPECIALIZATION subtype tables of data_item.
11. router_decision(decision_id PK, window_id FK, item_id FK, UNIQUE(window_id, item_id), action CHECK IN ('SEND','DEFER','DROP'), score NUMERIC, predicted_success NUMERIC nullable, rationale TEXT NOT NULL, model_version, fallback_used BOOLEAN, decided_at, overridden BOOLEAN default false, override_action nullable, override_by FK app_user nullable, override_reason nullable, override_at nullable, CHECK(overridden = false OR (override_action IS NOT NULL AND override_by IS NOT NULL AND override_reason IS NOT NULL))).
12. transmission(transmission_id PK, decision_id UNIQUE FK, bytes_received, snr_db, status CHECK IN ('RECEIVED','PARTIAL','LOST'), received_by FK app_user nullable (a HAM volunteer), received_at).
13. sensor(sensor_id PK, name UNIQUE, subsystem CHECK IN ('POWER','THERMAL','OBC','RF'), sensor_type CHECK IN ('VOLTAGE','TEMP','CURRENT'), unit).
14. threshold_rule(sensor_id FK ON DELETE CASCADE, rule_no, min_val, max_val, severity CHECK IN ('WARNING','CRITICAL'), PK(sensor_id, rule_no), CHECK(min_val < max_val)). This is a WEAK ENTITY of sensor.
15. telemetry_reading(reading_id BIGSERIAL PK, spacecraft_id FK, sensor_id FK, recorded_at, val NUMERIC(8,2), source CHECK IN ('ORBIT','BENCH','EMI_EMC','THERMOVAC','VIBRATION','HIL')).
16. alert(alert_id PK, alert_type CHECK IN ('TELEMETRY_LIMIT','CRITICAL_DATA_MISSED','DEPLOYMENT_ISSUE','SAFE_MODE'), severity, message, reading_id nullable FK, window_id nullable FK, status CHECK IN ('OPEN','ACK') default OPEN, ack_by FK nullable, created_at, ack_at).
17. command_log(command_id PK, spacecraft_id FK, command_type CHECK IN ('OVERRIDE_DECISION','ENTER_SAFE_MODE','EXIT_SAFE_MODE','REQUEST_PHASE','SET_PRIORITY'), issued_by FK nullable, is_automatic BOOLEAN, details JSONB, issued_at).
18. audit_log(log_id PK, table_name, action, row_data JSONB, restored BOOLEAN default false, logged_at).
19. model_run(model_version PK, trained_at, accuracy, rows_used, notes).

Indexes: telemetry_reading(sensor_id, recorded_at); data_item(status, mode_id); router_decision(window_id); alert(status, alert_type).
Views: v_window_summary (planned vs delivered bytes per window), v_mode_delivery_rate (per mode: sent, received, success %), v_latest_telemetry, v_open_alerts, v_phase_timeline.

## Business rules (PL/pgSQL in 02_functions_triggers.sql)
- fn_fallback_rank(item_id): rule-based score from mode base_priority and urgency (TTC > SSTV > CODEC2/M17).
- fn_schedule_window(window_id): ONE transaction. Lock the window and the QUEUED items with FOR UPDATE SKIP LOCKED. Score each item: if predicted_success is not null, score = (6 - base_priority)*100 + urgency*10 + predicted_success*10 and fallback_used = false; otherwise use fn_fallback_rank and fallback_used = true. Walk items by descending score and fill capacity_bytes and energy_budget_mwh (energy = size_bytes*8/bitrate_bps/3600*tx_power_mw). If the current phase has tt_c_only = true, DEFER every non-TTC item with that rationale. Write a router_decision for EVERY item with a readable rationale (rank, score, reason it fit or not). Set items to SCHEDULED, window to SCHEDULED. SAFETY CHECK at the end: if any QUEUED TTC item did not get SEND, RAISE EXCEPTION so the whole schedule rolls back.
- fn_complete_window(window_id): simulate the link. For each effective SEND decision (COALESCE(override_action, action)) create a transmission with status chosen by random() weighted by expected_snr_db (TTC more reliable), assign received_by to a random HAM volunteer for RECEIVED ones, set item SENT, or back to QUEUED if LOST. If TTC items were sent but none RECEIVED, create a CRITICAL_DATA_MISSED alert. Set window COMPLETED.
- fn_override_decision(decision_id, user_id, new_action, reason): only for SCHEDULED windows and OPERATOR/ADMIN users; REFUSE to override a TTC item to anything other than SEND; sets the override fields.
- trg_override_log: on override, write a command_log row (OVERRIDE_DECISION, who, details JSONB).
- trg_phase_guard (BEFORE INSERT on phase_transition) and a companion AFTER trigger that updates spacecraft.current_phase_id. Legal moves: LAUNCH->DEPLOYED, DEPLOYED->COMMISSIONING, COMMISSIONING->NOMINAL, any phase->SAFE_MODE, SAFE_MODE->COMMISSIONING or NOMINAL (OPERATOR only). COMMISSIONING->NOMINAL requires a CONFIRMED deployment_event AND at least one RECEIVED TTC transmission for that spacecraft. Illegal moves RAISE EXCEPTION with a clear message.
- trg_deployment_confirm: when a deployment_event becomes CONFIRMED and the satellite is in LAUNCH, record AUTO transitions LAUNCH->DEPLOYED then DEPLOYED->COMMISSIONING. If status becomes PARTIAL or FAILED, create a DEPLOYMENT_ISSUE alert.
- trg_telemetry_check (AFTER INSERT on telemetry_reading): if the value breaches a threshold_rule, create ONE alert (prefer the critical rule), message includes the reading's source.
- trg_low_battery_safe_mode (AFTER INSERT on telemetry_reading): if source = 'ORBIT', sensor is 'Battery Voltage' and val is below the CRITICAL min, and the phase is not SAFE_MODE, then insert an AUTO transition to SAFE_MODE, an automatic ENTER_SAFE_MODE command_log row, and a SAFE_MODE alert.
- trg_audit_*: on UPDATE and DELETE of data_item, command_log and phase_transition, copy the OLD row as JSONB into audit_log.
- All trigger functions that write to other tables are SECURITY DEFINER.

## Roles (05_security_dcl.sql, PostgreSQL roles, idempotent)
sat_admin (ALL, WITH GRANT OPTION) | sat_operator (SELECT everywhere, INSERT on data_item, sstv_image, voice_clip, deployment_event, EXECUTE on the fn_ functions, UPDATE on alert) | sat_ham (SELECT only on v_mode_delivery_rate and v_window_summary) | sat_viewer (SELECT on views and alert). Include a GRANT followed by a REVOKE example.

## Data volumes (scripts/gen_data.py)
About 300 historical downlink windows with ~25 queued items each, run through fn_schedule_window and fn_complete_window so there are thousands of decisions and transmissions. About 100,000 telemetry readings (12 sensors x ~8,000 samples) with sinusoidal orbit pattern plus noise; mostly source ORBIT, with blocks tagged BENCH, THERMOVAC, EMI_EMC, VIBRATION, HIL. About 0.05% spike readings creating alerts, but NO critical Battery Voltage spikes in history (safe mode is demoed live). Order of history: confirm deployment, run one window so a TTC is received, move to NOMINAL, then the rest.

## Streamlit app (9 pages)
Home (Mission Dashboard) | Phase and Deployment | Downlink Windows | Downlink Queue | Router | Telemetry Monitor | Alerts | Commands and History | DB Lab. A "Viewing as" selectbox in the sidebar (stored in st.session_state) picks a user from app_user; buttons are hidden AND checked in code per role. Every write shows the SQL it ran. Database errors are caught and shown as friendly messages.

## Lab scope this project must cover
Exp 1 problem definition and ER/EER | Exp 2 ER-to-relational mapping | Exp 3 DDL | Exp 4 DML and populating | Exp 5 aggregates with ORDER BY, GROUP BY, LIKE, HAVING and SET operations | Exp 6 nested queries, views, JOINs | Exp 7 DCL | Exp 8 indexing | Course outcomes: relational features, normalization, security, query processing, transactions, concurrency, recovery.
