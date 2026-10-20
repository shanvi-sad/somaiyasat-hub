"""
SomaiyaSat Telemetry Hub - scripts/smoke_test.py
Verification suite for Stage 2 PL/pgSQL business rules, stored procedures, and triggers.

Checks implemented:
1. Inserting an out-of-range reading creates exactly one alert.
2. An illegal phase move (for example LAUNCH -> NOMINAL) is rejected with an error.
3. Confirming the PENDING deployment moves SomaiyaSat LAUNCH -> DEPLOYED -> COMMISSIONING automatically.
4. Moving COMMISSIONING -> NOMINAL is rejected until a TTC transmission has been RECEIVED, and allowed after.
5. Inserting a Battery Voltage reading below its critical minimum with source ORBIT moves the satellite to SAFE_MODE and logs an automatic command and alert.
6. In SAFE_MODE, fn_schedule_window DEFERs every non-TTC item.
7. fn_schedule_window rolls back and raises an error when a queued TTC item cannot fit the window capacity.
8. fn_override_decision refuses to downgrade a TTC decision, but allows overriding a non-TTC decision.
9. Deleting a QUEUED data_item writes a row to audit_log.
"""

import sys
import os
import psycopg2
from psycopg2.extras import RealDictCursor

# Ensure project root in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.db import get_conn


def run_check_1(conn):
    """1. Inserting an out-of-range reading creates exactly one alert."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Sensor: PA Temperature (warning range [-10, 70], critical range [-20, 85])
        cur.execute("SELECT sensor_id FROM sensor WHERE name = 'PA Temperature';")
        sensor_id = cur.fetchone()["sensor_id"]
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]

        # Insert out-of-range reading (75.0 C breaches WARNING threshold)
        cur.execute("""
            INSERT INTO telemetry_reading (spacecraft_id, sensor_id, recorded_at, val, source)
            VALUES (%s, %s, now(), 75.0, 'BENCH')
            RETURNING reading_id;
        """, (sat_id, sensor_id))
        reading_id = cur.fetchone()["reading_id"]

        # Verify exactly one alert was created
        cur.execute("SELECT * FROM alert WHERE reading_id = %s;", (reading_id,))
        alerts = cur.fetchall()

        passed = (len(alerts) == 1 and 
                  alerts[0]["alert_type"] == "TELEMETRY_LIMIT" and 
                  alerts[0]["severity"] == "WARNING")
        return passed, f"Generated {len(alerts)} alert(s) (type: {alerts[0]['alert_type'] if alerts else 'none'})"


def run_check_2(conn):
    """2. An illegal phase move (for example LAUNCH -> NOMINAL) is rejected with an error."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]
        cur.execute("SELECT phase_id FROM mission_phase WHERE name = 'LAUNCH';")
        launch_id = cur.fetchone()["phase_id"]
        cur.execute("SELECT phase_id FROM mission_phase WHERE name = 'NOMINAL';")
        nominal_id = cur.fetchone()["phase_id"]

        # Reset spacecraft to LAUNCH for test scenario
        cur.execute("UPDATE spacecraft SET current_phase_id = %s WHERE spacecraft_id = %s;", (launch_id, sat_id))

        rejected = False
        cur.execute("SAVEPOINT check_2_attempt;")
        try:
            # Attempt illegal direct jump from LAUNCH to NOMINAL
            cur.execute("""
                INSERT INTO phase_transition (spacecraft_id, from_phase_id, to_phase_id, reason, triggered_by)
                VALUES (%s, %s, %s, 'Attempted illegal skip', 'OPERATOR');
            """, (sat_id, launch_id, nominal_id))
        except psycopg2.DatabaseError:
            rejected = True
            cur.execute("ROLLBACK TO SAVEPOINT check_2_attempt;")

        return rejected, ("Rejected with error as expected" if rejected else "Illegal transition was accepted!")


def run_check_3(conn):
    """3. Confirming the PENDING deployment moves SomaiyaSat LAUNCH -> DEPLOYED -> COMMISSIONING automatically."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]
        cur.execute("SELECT phase_id FROM mission_phase WHERE name = 'LAUNCH';")
        launch_id = cur.fetchone()["phase_id"]

        # Set satellite back to LAUNCH and deployment to PENDING within transaction
        cur.execute("UPDATE spacecraft SET current_phase_id = %s WHERE spacecraft_id = %s;", (launch_id, sat_id))
        cur.execute("UPDATE deployment_event SET status = 'PENDING', signal_at = NULL WHERE spacecraft_id = %s;", (sat_id,))
        cur.execute("DELETE FROM phase_transition WHERE spacecraft_id = %s;", (sat_id,))

        # Confirm deployment event
        cur.execute("""
            UPDATE deployment_event
            SET status = 'CONFIRMED', signal_at = now()
            WHERE spacecraft_id = %s AND status = 'PENDING';
        """, (sat_id,))

        # Check satellite current phase is now COMMISSIONING
        cur.execute("""
            SELECT mp.name AS current_phase
            FROM spacecraft sc
            JOIN mission_phase mp ON sc.current_phase_id = mp.phase_id
            WHERE sc.spacecraft_id = %s;
        """, (sat_id,))
        phase = cur.fetchone()["current_phase"]

        # Check two automatic transitions exist
        cur.execute("""
            SELECT mp1.name as from_p, mp2.name as to_p, pt.triggered_by
            FROM phase_transition pt
            JOIN mission_phase mp1 ON pt.from_phase_id = mp1.phase_id
            JOIN mission_phase mp2 ON pt.to_phase_id = mp2.phase_id
            WHERE pt.spacecraft_id = %s
            ORDER BY pt.transition_id ASC;
        """, (sat_id,))
        trans = cur.fetchall()

        auto_chain = [(t["from_p"], t["to_p"], t["triggered_by"]) for t in trans]
        passed = (phase == "COMMISSIONING" and
                  ("LAUNCH", "DEPLOYED", "AUTO") in auto_chain and
                  ("DEPLOYED", "COMMISSIONING", "AUTO") in auto_chain)
        return passed, f"Current phase: {phase}, transitions: {auto_chain}"


def run_check_4(conn):
    """4. Moving COMMISSIONING -> NOMINAL is rejected until a TTC transmission has been RECEIVED, and allowed after."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]

        # Clean isolation: reset to LAUNCH & clear prior transmissions for clean test
        cur.execute("SELECT phase_id FROM mission_phase WHERE name = 'LAUNCH';")
        launch_id = cur.fetchone()["phase_id"]
        cur.execute("UPDATE spacecraft SET current_phase_id = %s WHERE spacecraft_id = %s;", (launch_id, sat_id))
        cur.execute("UPDATE deployment_event SET status = 'PENDING', signal_at = NULL WHERE spacecraft_id = %s;", (sat_id,))
        cur.execute("DELETE FROM transmission WHERE decision_id IN (SELECT decision_id FROM router_decision rd JOIN data_item di ON rd.item_id = di.item_id WHERE di.spacecraft_id = %s);", (sat_id,))

        # Step A: Confirm deployment to reach COMMISSIONING
        cur.execute("""
            UPDATE deployment_event
            SET status = 'CONFIRMED', signal_at = now()
            WHERE spacecraft_id = %s AND status = 'PENDING';
        """, (sat_id,))

        cur.execute("SELECT phase_id FROM mission_phase WHERE name = 'COMMISSIONING';")
        comm_id = cur.fetchone()["phase_id"]
        cur.execute("SELECT phase_id FROM mission_phase WHERE name = 'NOMINAL';")
        nom_id = cur.fetchone()["phase_id"]

        # Step B: Attempt transition without received TTC transmission -> must FAIL
        cur.execute("SAVEPOINT check_4_premature;")
        premature_rejected = False
        try:
            cur.execute("""
                INSERT INTO phase_transition (spacecraft_id, from_phase_id, to_phase_id, reason, triggered_by)
                VALUES (%s, %s, %s, 'Attempt premature nominal transition', 'OPERATOR');
            """, (sat_id, comm_id, nom_id))
        except psycopg2.DatabaseError:
            premature_rejected = True
            cur.execute("ROLLBACK TO SAVEPOINT check_4_premature;")

        if not premature_rejected:
            return False, "Premature transition to NOMINAL was not rejected!"

        # Step C: Satisfy requirement by inserting a received TTC transmission
        cur.execute("SELECT mode_id FROM comm_mode WHERE code = 'TTC';")
        ttc_mode_id = cur.fetchone()["mode_id"]

        # Create window & item
        cur.execute("""
            INSERT INTO downlink_window (spacecraft_id, start_time, duration_s, capacity_bytes, energy_budget_mwh, expected_snr_db, battery_v, status)
            VALUES (%s, now(), 600, 50000, 500.0, 15.0, 4.0, 'SCHEDULED')
            RETURNING window_id;
        """, (sat_id,))
        win_id = cur.fetchone()["window_id"]

        cur.execute("""
            INSERT INTO data_item (spacecraft_id, mode_id, size_bytes, urgency, description, status)
            VALUES (%s, %s, 120, 5, 'Initial commissioning housekeeping', 'SCHEDULED')
            RETURNING item_id;
        """, (sat_id, ttc_mode_id))
        item_id = cur.fetchone()["item_id"]

        cur.execute("""
            INSERT INTO router_decision (window_id, item_id, action, score, rationale, decided_at)
            VALUES (%s, %s, 'SEND', 550.0, 'Scheduled for check', now())
            RETURNING decision_id;
        """, (win_id, item_id))
        dec_id = cur.fetchone()["decision_id"]

        cur.execute("""
            INSERT INTO transmission (decision_id, bytes_received, snr_db, status, received_at)
            VALUES (%s, 120, 16.5, 'RECEIVED', now());
        """, (dec_id,))

        # Step D: Now attempt transition to NOMINAL -> must SUCCEED
        cur.execute("""
            INSERT INTO phase_transition (spacecraft_id, from_phase_id, to_phase_id, reason, triggered_by)
            VALUES (%s, %s, %s, 'Transition after confirmed TTC receipt', 'OPERATOR');
        """, (sat_id, comm_id, nom_id))

        cur.execute("""
            SELECT mp.name as current_phase
            FROM spacecraft sc
            JOIN mission_phase mp ON sc.current_phase_id = mp.phase_id
            WHERE sc.spacecraft_id = %s;
        """, (sat_id,))
        final_phase = cur.fetchone()["current_phase"]

        passed = (final_phase == "NOMINAL")
        return passed, f"Premature rejected: {premature_rejected}, Final phase: {final_phase}"


def run_check_5(conn):
    """5. Inserting a Battery Voltage reading below its critical minimum with source ORBIT moves the satellite to SAFE_MODE and logs an automatic command and alert."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]
        cur.execute("SELECT sensor_id FROM sensor WHERE name = 'Battery Voltage';")
        sensor_id = cur.fetchone()["sensor_id"]
        cur.execute("SELECT min_val FROM threshold_rule WHERE sensor_id = %s AND severity = 'CRITICAL';", (sensor_id,))
        crit_min = cur.fetchone()["min_val"]

        # Ensure satellite is not already in SAFE_MODE
        cur.execute("SELECT phase_id FROM mission_phase WHERE name = 'NOMINAL';")
        nom_id = cur.fetchone()["phase_id"]
        cur.execute("UPDATE spacecraft SET current_phase_id = %s WHERE spacecraft_id = %s;", (nom_id, sat_id))

        # Insert critical low reading in ORBIT (e.g., 3.10 V < 3.30 V)
        cur.execute("""
            INSERT INTO telemetry_reading (spacecraft_id, sensor_id, recorded_at, val, source)
            VALUES (%s, %s, now(), 3.10, 'ORBIT')
            RETURNING reading_id;
        """, (sat_id, sensor_id))
        reading_id = cur.fetchone()["reading_id"]

        # Verify current phase is SAFE_MODE
        cur.execute("""
            SELECT mp.name as current_phase
            FROM spacecraft sc
            JOIN mission_phase mp ON sc.current_phase_id = mp.phase_id
            WHERE sc.spacecraft_id = %s;
        """, (sat_id,))
        phase = cur.fetchone()["current_phase"]

        # Verify automatic command_log entry
        cur.execute("""
            SELECT * FROM command_log
            WHERE spacecraft_id = %s AND command_type = 'ENTER_SAFE_MODE' AND is_automatic = TRUE;
        """, (sat_id,))
        cmd = cur.fetchone()

        # Verify SAFE_MODE alert
        cur.execute("""
            SELECT * FROM alert
            WHERE reading_id = %s AND alert_type = 'SAFE_MODE' AND severity = 'CRITICAL';
        """, (reading_id,))
        alert = cur.fetchone()

        passed = (phase == "SAFE_MODE" and cmd is not None and alert is not None)
        return passed, f"Phase: {phase}, Command: {bool(cmd)}, Alert: {bool(alert)}"


def run_check_6(conn):
    """6. In SAFE_MODE, fn_schedule_window DEFERs every non-TTC item."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]

        # Set spacecraft directly to SAFE_MODE
        cur.execute("SELECT phase_id FROM mission_phase WHERE name = 'SAFE_MODE';")
        safe_id = cur.fetchone()["phase_id"]
        cur.execute("UPDATE spacecraft SET current_phase_id = %s WHERE spacecraft_id = %s;", (safe_id, sat_id))

        # Create a planned window with generous capacity
        cur.execute("""
            INSERT INTO downlink_window (spacecraft_id, start_time, duration_s, capacity_bytes, energy_budget_mwh, expected_snr_db, battery_v, status)
            VALUES (%s, now(), 600, 100000, 1000.0, 15.0, 3.7, 'PLANNED')
            RETURNING window_id;
        """, (sat_id,))
        win_id = cur.fetchone()["window_id"]

        # Queue 1 TTC item and 2 non-TTC items (SSTV, CODEC2)
        cur.execute("SELECT mode_id, code FROM comm_mode;")
        modes = {row["code"]: row["mode_id"] for row in cur.fetchall()}

        cur.execute("""
            INSERT INTO data_item (spacecraft_id, mode_id, size_bytes, urgency, description, status)
            VALUES (%s, %s, 200, 5, 'Safe mode beacon', 'QUEUED')
            RETURNING item_id;
        """, (sat_id, modes["TTC"]))
        ttc_item_id = cur.fetchone()["item_id"]

        cur.execute("""
            INSERT INTO data_item (spacecraft_id, mode_id, size_bytes, urgency, description, status)
            VALUES (%s, %s, 1500, 4, 'SSTV image', 'QUEUED')
            RETURNING item_id;
        """, (sat_id, modes["SSTV"]))
        sstv_item_id = cur.fetchone()["item_id"]

        cur.execute("""
            INSERT INTO data_item (spacecraft_id, mode_id, size_bytes, urgency, description, status)
            VALUES (%s, %s, 800, 3, 'Codec2 voice', 'QUEUED')
            RETURNING item_id;
        """, (sat_id, modes["CODEC2"]))
        codec_item_id = cur.fetchone()["item_id"]

        # Call fn_schedule_window
        cur.execute("SELECT fn_schedule_window(%s);", (win_id,))

        # Check decisions
        cur.execute("SELECT item_id, action, rationale FROM router_decision WHERE window_id = %s;", (win_id,))
        decisions = {row["item_id"]: row for row in cur.fetchall()}

        ttc_action = decisions[ttc_item_id]["action"]
        sstv_action = decisions[sstv_item_id]["action"]
        codec_action = decisions[codec_item_id]["action"]
        sstv_rat = decisions[sstv_item_id]["rationale"]

        passed = (ttc_action == "SEND" and 
                  sstv_action == "DEFER" and 
                  codec_action == "DEFER" and 
                  "TT&C-only" in sstv_rat)
        return passed, f"TTC: {ttc_action}, SSTV: {sstv_action}, CODEC2: {codec_action}"


def run_check_7(conn):
    """7. fn_schedule_window rolls back and raises an error when a queued TTC item cannot fit the window capacity."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]
        cur.execute("SELECT mode_id FROM comm_mode WHERE code = 'TTC';")
        ttc_mode_id = cur.fetchone()["mode_id"]

        # Window with small capacity (50 bytes)
        cur.execute("""
            INSERT INTO downlink_window (spacecraft_id, start_time, duration_s, capacity_bytes, energy_budget_mwh, expected_snr_db, battery_v, status)
            VALUES (%s, now(), 300, 50, 500.0, 15.0, 4.0, 'PLANNED')
            RETURNING window_id;
        """, (sat_id,))
        win_id = cur.fetchone()["window_id"]

        # TTC item larger than window capacity (300 bytes > 50 bytes)
        cur.execute("""
            INSERT INTO data_item (spacecraft_id, mode_id, size_bytes, urgency, description, status)
            VALUES (%s, %s, 300, 5, 'Large TT&C packet', 'QUEUED');
        """, (sat_id, ttc_mode_id))

        # Execution must raise exception and roll back
        raised = False
        cur.execute("SAVEPOINT check_7_call;")
        try:
            cur.execute("SELECT fn_schedule_window(%s);", (win_id,))
        except psycopg2.DatabaseError:
            raised = True
            cur.execute("ROLLBACK TO SAVEPOINT check_7_call;")

        # Verify no router decision committed for this window and window remains PLANNED
        cur.execute("SELECT count(*) as cnt FROM router_decision WHERE window_id = %s;", (win_id,))
        dec_count = cur.fetchone()["cnt"]
        cur.execute("SELECT status FROM downlink_window WHERE window_id = %s;", (win_id,))
        win_status = cur.fetchone()["status"]

        passed = (raised and dec_count == 0 and win_status == "PLANNED")
        return passed, f"Exception raised: {raised}, decisions: {dec_count}, window status: {win_status}"


def run_check_8(conn):
    """8. fn_override_decision refuses to downgrade a TTC decision, but allows overriding a non-TTC decision."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]
        cur.execute("SELECT user_id FROM app_user WHERE username = 'ops_asha';")
        op_user_id = cur.fetchone()["user_id"]
        cur.execute("SELECT mode_id, code FROM comm_mode;")
        modes = {row["code"]: row["mode_id"] for row in cur.fetchall()}

        # Move spacecraft to NOMINAL for full schedule capability
        cur.execute("SELECT phase_id FROM mission_phase WHERE name = 'NOMINAL';")
        nom_id = cur.fetchone()["phase_id"]
        cur.execute("UPDATE spacecraft SET current_phase_id = %s WHERE spacecraft_id = %s;", (nom_id, sat_id))

        # Create planned window & queue items
        cur.execute("""
            INSERT INTO downlink_window (spacecraft_id, start_time, duration_s, capacity_bytes, energy_budget_mwh, expected_snr_db, battery_v, status)
            VALUES (%s, now(), 600, 50000, 500.0, 15.0, 4.0, 'PLANNED')
            RETURNING window_id;
        """, (sat_id,))
        win_id = cur.fetchone()["window_id"]

        cur.execute("""
            INSERT INTO data_item (spacecraft_id, mode_id, size_bytes, urgency, description, status)
            VALUES (%s, %s, 100, 5, 'TT&C telemetry', 'QUEUED')
            RETURNING item_id;
        """, (sat_id, modes["TTC"]))
        ttc_item_id = cur.fetchone()["item_id"]

        cur.execute("""
            INSERT INTO data_item (spacecraft_id, mode_id, size_bytes, urgency, description, status)
            VALUES (%s, %s, 500, 3, 'SSTV image', 'QUEUED')
            RETURNING item_id;
        """, (sat_id, modes["SSTV"]))
        sstv_item_id = cur.fetchone()["item_id"]

        # Schedule window
        cur.execute("SELECT fn_schedule_window(%s);", (win_id,))

        cur.execute("SELECT decision_id, action FROM router_decision WHERE window_id = %s AND item_id = %s;", (win_id, ttc_item_id))
        ttc_dec = cur.fetchone()
        cur.execute("SELECT decision_id, action FROM router_decision WHERE window_id = %s AND item_id = %s;", (win_id, sstv_item_id))
        sstv_dec = cur.fetchone()

        # Attempt 8A: Downgrade TTC decision -> MUST RAISE EXCEPTION
        cur.execute("SAVEPOINT check_8_ttc;")
        ttc_downgrade_rejected = False
        try:
            cur.execute("SELECT fn_override_decision(%s, %s, 'DEFER', 'Attempt to downgrade TTC packet');",
                        (ttc_dec["decision_id"], op_user_id))
        except psycopg2.DatabaseError:
            ttc_downgrade_rejected = True
            cur.execute("ROLLBACK TO SAVEPOINT check_8_ttc;")

        # Attempt 8B: Override non-TTC decision -> MUST SUCCEED
        cur.execute("SELECT fn_override_decision(%s, %s, 'DEFER', 'Operator deferral for test');",
                    (sstv_dec["decision_id"], op_user_id))

        cur.execute("SELECT overridden, override_action, override_reason FROM router_decision WHERE decision_id = %s;",
                    (sstv_dec["decision_id"],))
        sstv_override = cur.fetchone()

        # Verify trg_override_log recorded in command_log
        cur.execute("""
            SELECT * FROM command_log
            WHERE command_type = 'OVERRIDE_DECISION' AND issued_by = %s
            ORDER BY command_id DESC LIMIT 1;
        """, (op_user_id,))
        cmd_log = cur.fetchone()

        passed = (ttc_downgrade_rejected and 
                  sstv_override["overridden"] is True and 
                  sstv_override["override_action"] == "DEFER" and
                  cmd_log is not None)
        return passed, f"TTC downgrade rejected: {ttc_downgrade_rejected}, SSTV overridden: {sstv_override['overridden']}, Logged: {bool(cmd_log)}"


def run_check_9(conn):
    """9. Deleting a QUEUED data_item writes a row to audit_log."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]
        cur.execute("SELECT mode_id FROM comm_mode WHERE code = 'TTC';")
        ttc_mode_id = cur.fetchone()["mode_id"]

        # Insert a temporary queued item
        cur.execute("""
            INSERT INTO data_item (spacecraft_id, mode_id, size_bytes, urgency, description, status)
            VALUES (%s, %s, 150, 2, 'Audit test data item', 'QUEUED')
            RETURNING item_id;
        """, (sat_id, ttc_mode_id))
        item_id = cur.fetchone()["item_id"]

        # Delete the data_item
        cur.execute("DELETE FROM data_item WHERE item_id = %s;", (item_id,))

        # Check audit_log
        cur.execute("""
            SELECT * FROM audit_log
            WHERE table_name = 'data_item' 
              AND action = 'DELETE'
              AND (row_data->>'item_id')::int = %s;
        """, (item_id,))
        audit_entry = cur.fetchone()

        passed = (audit_entry is not None and audit_entry["action"] == "DELETE")
        return passed, f"Audit row found: {bool(audit_entry)}"


def main():
    print("=" * 70)
    print("SomaiyaSat Telemetry Hub - Stage 2 Smoke Test Suite")
    print("=" * 70)

    checks = [
        ("Check 1: Out-of-range reading creates exactly one alert", run_check_1),
        ("Check 2: Illegal phase move is rejected with an error", run_check_2),
        ("Check 3: Confirming deployment moves LAUNCH -> DEPLOYED -> COMMISSIONING", run_check_3),
        ("Check 4: COMMISSIONING -> NOMINAL requires received TTC transmission", run_check_4),
        ("Check 5: Critical low battery reading moves satellite to SAFE_MODE", run_check_5),
        ("Check 6: In SAFE_MODE, fn_schedule_window DEFERs every non-TTC item", run_check_6),
        ("Check 7: fn_schedule_window rolls back when TTC item cannot fit", run_check_7),
        ("Check 8: fn_override_decision protects TTC but allows non-TTC override", run_check_8),
        ("Check 9: Deleting QUEUED data_item writes a row to audit_log", run_check_9),
    ]

    all_passed = True
    for i, (name, check_fn) in enumerate(checks, 1):
        conn = get_conn()
        try:
            passed, detail = check_fn(conn)
            status = "PASS" if passed else "FAIL"
            if not passed:
                all_passed = False
            print(f"[{status}] {i}. {name}")
            print(f"       Details: {detail}")
        except Exception as e:
            all_passed = False
            print(f"[FAIL] {i}. {name}")
            print(f"       Exception: {e}")
        finally:
            conn.rollback()
            conn.close()

    print("=" * 70)
    if all_passed:
        print("ALL 9 STAGE 2 CHECKS PASSED SUCCESSFULLY!")
    else:
        print("SOME CHECKS FAILED - PLEASE REVIEW DETAILS ABOVE.")
    print("=" * 70)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
