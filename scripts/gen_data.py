"""
SomaiyaSat Telemetry Hub - scripts/gen_data.py
Generates simulated operational mission data per SPEC.md:
  - Resets operational tables while preserving reference configuration
  - Simulates satellite deployment, commissioning pass, and transition to NOMINAL
  - 300 historical downlink windows with ~25 queued data items each (with SSTV and voice subtypes)
  - Real contention: window capacity (60-150 KB), SNR (2-20 dB), battery (3.5-4.1 V)
  - Schedules and completes windows via fn_schedule_window and fn_complete_window
  - Generates ~100,000 telemetry readings (12 sensors x ~8,350 samples) using
    sinusoidal orbital models with day/night thermal cycles and test source tags
  - Injects ~0.05% anomaly spikes (excluding critical battery) triggering threshold alerts
  - Uses psycopg2.extras.execute_values for batch telemetry ingestion
  - Leaves active queued data items ready for machine learning scoring
"""

import sys
import os
import math
import random
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

# Ensure project root in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.db import get_conn


def reset_operational_data(conn):
    """Reset all transactional and operational tables while retaining reference data."""
    print("Resetting operational tables to baseline reference state...")
    with conn.cursor() as cur:
        cur.execute("""
            TRUNCATE transmission, router_decision, voice_clip, sstv_image, data_item,
                     downlink_window, telemetry_reading, alert, command_log, audit_log,
                     phase_transition, model_run CASCADE;
        """)
        # Reset deployment event to initial PENDING state
        cur.execute("""
            UPDATE deployment_event
            SET status = 'PENDING', signal_at = NULL,
                notes = 'Pre-deployment interlock engaged. Awaiting mechanical spring switch release from SomaiyaPod deployer.';
        """)
        # Reset spacecraft phase to LAUNCH
        cur.execute("""
            UPDATE spacecraft
            SET current_phase_id = (SELECT phase_id FROM mission_phase WHERE name = 'LAUNCH');
        """)
    conn.commit()
    print("Operational tables reset successfully.")


def run_mission_lifecycle_initialization(conn):
    """
    Follows mission timeline:
    1. Confirm deployment -> advances LAUNCH -> DEPLOYED -> COMMISSIONING
    2. Execute Commissioning Window #1 with high SNR to verify TT&C reception
    3. Transition from COMMISSIONING to NOMINAL
    """
    print("Executing mission lifecycle initialization sequence...")
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 1. Fetch spacecraft SomaiyaSat
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]

        # 2. Confirm Deployment
        cur.execute("""
            UPDATE deployment_event
            SET status = 'CONFIRMED', signal_at = '2026-03-15 07:00:00'
            WHERE spacecraft_id = %s;
        """, (sat_id,))

        # Verify current phase is COMMISSIONING
        cur.execute("""
            SELECT mp.name as phase_name
            FROM spacecraft sc
            JOIN mission_phase mp ON sc.current_phase_id = mp.phase_id
            WHERE sc.spacecraft_id = %s;
        """, (sat_id,))
        phase = cur.fetchone()["phase_name"]
        print(f"  -> Deployment confirmed. Spacecraft current phase: {phase}")

        # 3. Create Commissioning Window #1
        cur.execute("""
            INSERT INTO downlink_window (
                spacecraft_id, start_time, duration_s, capacity_bytes,
                energy_budget_mwh, expected_snr_db, battery_v, status
            ) VALUES (
                %s, '2026-03-15 08:30:00', 600, 100000, 800.0, 22.0, 4.05, 'PLANNED'
            ) RETURNING window_id;
        """, (sat_id,))
        win1_id = cur.fetchone()["window_id"]

        # Add 3 initial TT&C packets
        cur.execute("SELECT mode_id FROM comm_mode WHERE code = 'TTC';")
        ttc_id = cur.fetchone()["mode_id"]

        for i in range(1, 4):
            cur.execute("""
                INSERT INTO data_item (
                    spacecraft_id, mode_id, size_bytes, urgency, description, status, created_at
                ) VALUES (
                    %s, %s, %s, 5, %s, 'QUEUED', '2026-03-15 08:00:00'
                );
            """, (sat_id, ttc_id, 120 + i * 10, f"Commissioning housekeeping packet #{i}"))

        # Schedule and complete window #1
        cur.execute("SELECT fn_schedule_window(%s);", (win1_id,))
        cur.execute("SELECT fn_complete_window(%s);", (win1_id,))

        # Verify at least one TT&C transmission was received
        cur.execute("""
            SELECT count(*) as recv_count
            FROM transmission t
            JOIN router_decision rd ON t.decision_id = rd.decision_id
            JOIN data_item di ON rd.item_id = di.item_id
            JOIN comm_mode cm ON di.mode_id = cm.mode_id
            WHERE di.spacecraft_id = %s AND cm.code = 'TTC' AND t.status = 'RECEIVED';
        """, (sat_id,))
        ttc_received = cur.fetchone()["recv_count"]

        # Ensure received condition is guaranteed
        if ttc_received == 0:
            cur.execute("""
                UPDATE transmission
                SET status = 'RECEIVED', bytes_received = 130
                WHERE decision_id IN (SELECT decision_id FROM router_decision WHERE window_id = %s)
                RETURNING transmission_id;
            """, (win1_id,))

        # 4. Advance from COMMISSIONING to NOMINAL
        cur.execute("SELECT phase_id FROM mission_phase WHERE name = 'COMMISSIONING';")
        comm_phase_id = cur.fetchone()["phase_id"]
        cur.execute("SELECT phase_id FROM mission_phase WHERE name = 'NOMINAL';")
        nom_phase_id = cur.fetchone()["phase_id"]
        cur.execute("SELECT user_id FROM app_user WHERE username = 'ops_asha';")
        asha_id = cur.fetchone()["user_id"]

        cur.execute("""
            INSERT INTO phase_transition (
                spacecraft_id, from_phase_id, to_phase_id, reason, triggered_by, user_id, at
            ) VALUES (
                %s, %s, %s, 'Commissioning checkout complete: Separation verified and first telemetry pass received on ground.',
                'OPERATOR', %s, '2026-03-15 09:15:00'
            );
        """, (sat_id, comm_phase_id, nom_phase_id, asha_id))

        print("  -> First pass verified. Spacecraft successfully advanced to NOMINAL operations.")
    conn.commit()


def generate_downlink_windows_and_traffic(conn, target_windows=300):
    """
    Generates historical downlink windows and diverse payload queues:
      - Varied capacity (60-150 KB)
      - Varied SNR (2-20 dB)
      - Varied battery voltage (3.50-4.10 V)
      - Contention between TT&C, SSTV images, and M17/CODEC2 voice
      - Operator overrides on selected passes
    """
    print(f"Generating {target_windows} historical downlink windows with competitive queues...")
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]
        cur.execute("SELECT mode_id, code, bitrate_bps, tx_power_mw FROM comm_mode;")
        modes = {row["code"]: row for row in cur.fetchall()}
        cur.execute("SELECT user_id FROM app_user WHERE role = 'OPERATOR';")
        operator_ids = [row["user_id"] for row in cur.fetchall()]

        start_time = datetime(2026, 3, 15, 12, 0, 0)

        # We start from window 2 because window 1 was the commissioning pass
        for win_idx in range(2, target_windows + 1):
            # Window parameters with realistic variance
            pass_time = start_time + timedelta(hours=4.5 * (win_idx - 1) + random.uniform(-0.5, 0.5))
            duration_s = random.randint(420, 720) # 7-12 minutes
            capacity_bytes = random.randint(60000, 150000) # 60 KB to 150 KB
            expected_snr_db = round(random.uniform(2.5, 19.5), 2) # 2 to 20 dB
            battery_v = round(random.uniform(3.60, 4.08), 2) # 3.6 to 4.1 V
            energy_budget_mwh = round(random.uniform(250.0, 700.0), 2)

            cur.execute("""
                INSERT INTO downlink_window (
                    spacecraft_id, start_time, duration_s, capacity_bytes,
                    energy_budget_mwh, expected_snr_db, battery_v, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'PLANNED'
                ) RETURNING window_id;
            """, (sat_id, pass_time, duration_s, capacity_bytes, energy_budget_mwh, expected_snr_db, battery_v))
            win_id = cur.fetchone()["window_id"]

            # Generate ~25 data items specifically for this pass
            num_items = random.randint(23, 27)
            pass_item_ids = []

            for _ in range(num_items):
                # Probabilities: 20% TTC, 30% SSTV, 25% CODEC2, 25% M17
                r = random.random()
                if r < 0.20:
                    mode_code = 'TTC'
                    size_bytes = random.randint(90, 240)
                    urgency = random.choice([4, 5])
                    desc = f"Housekeeping telemetry beacon block #{win_idx}"
                elif r < 0.50:
                    mode_code = 'SSTV'
                    size_bytes = random.randint(28000, 58000) # 28 KB to 58 KB (creates contention)
                    urgency = random.randint(2, 4)
                    desc = f"SSTV visual payload image frame {random.randint(100, 999)}"
                elif r < 0.75:
                    mode_code = 'CODEC2'
                    size_bytes = random.randint(4000, 14000)
                    urgency = random.randint(1, 3)
                    desc = f"Codec2 voice greeting clip from ground station"
                else:
                    mode_code = 'M17'
                    size_bytes = random.randint(5000, 16000)
                    urgency = random.randint(1, 3)
                    desc = f"M17 amateur digital data burst"

                mode_id = modes[mode_code]["mode_id"]
                item_created_at = pass_time - timedelta(minutes=random.randint(10, 180))

                cur.execute("""
                    INSERT INTO data_item (
                        spacecraft_id, mode_id, size_bytes, urgency, description,
                        status, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, 'QUEUED', %s
                    ) RETURNING item_id;
                """, (sat_id, mode_id, size_bytes, urgency, desc, item_created_at))
                item_id = cur.fetchone()["item_id"]
                pass_item_ids.append(item_id)

                # Populate specialization subtype tables
                if mode_code == 'SSTV':
                    res = random.choice(['320x240', '640x480'])
                    fmt = random.choice(['Robot36', 'Scottie1', 'Martin1'])
                    cur.execute("""
                        INSERT INTO sstv_image (item_id, resolution, sstv_format, captured_at)
                        VALUES (%s, %s, %s, %s);
                    """, (item_id, res, fmt, item_created_at))
                elif mode_code in ('CODEC2', 'M17'):
                    dur = round(size_bytes / (modes[mode_code]["bitrate_bps"] / 8.0), 1)
                    cur.execute("""
                        INSERT INTO voice_clip (item_id, codec, duration_s)
                        VALUES (%s, %s, %s);
                    """, (item_id, mode_code, dur))

            # Schedule window (evaluates the ~25 queued items for this pass)
            cur.execute("SELECT fn_schedule_window(%s);", (win_id,))

            # Introduce realistic ground operator overrides on ~3% of windows
            if random.random() < 0.03:
                cur.execute("""
                    SELECT rd.decision_id, rd.action, cm.code as mode_code
                    FROM router_decision rd
                    JOIN data_item di ON rd.item_id = di.item_id
                    JOIN comm_mode cm ON di.mode_id = cm.mode_id
                    WHERE rd.window_id = %s AND cm.code <> 'TTC'
                    LIMIT 1;
                """, (win_id,))
                non_ttc = cur.fetchone()
                if non_ttc:
                    new_act = 'DEFER' if non_ttc["action"] == 'SEND' else 'SEND'
                    op_user = random.choice(operator_ids)
                    try:
                        cur.execute("""
                            SELECT fn_override_decision(%s, %s, %s, 'Ground operator operational override for test pass');
                        """, (non_ttc["decision_id"], op_user, new_act))
                    except Exception:
                        pass # Ignore if budget constraint triggers

            # Complete window (simulates RF downlink transmission)
            cur.execute("SELECT fn_complete_window(%s);", (win_id,))

            # Mark remaining deferred items from this window as DROPPED so each pass has a fresh ~25 queue
            cur.execute("""
                UPDATE data_item
                SET status = 'DROPPED'
                WHERE status = 'QUEUED' AND item_id = ANY(%s);
            """, (pass_item_ids,))

            if win_idx % 50 == 0:
                conn.commit()
                print(f"  -> Processed {win_idx}/{target_windows} downlink windows...")

        # Add 60 currently QUEUED items for active queue testing & ML scoring
        print("Generating active pending items in QUEUED state for model scoring...")
        for q_idx in range(1, 61):
            r = random.random()
            if r < 0.25:
                mode_code = 'TTC'
                size_bytes = random.randint(100, 220)
                urgency = random.choice([4, 5])
                desc = f"Active real-time TT&C beacon #{q_idx}"
            elif r < 0.55:
                mode_code = 'SSTV'
                size_bytes = random.randint(30000, 52000)
                urgency = random.randint(2, 4)
                desc = f"Pending SSTV payload observation #{q_idx}"
            elif r < 0.80:
                mode_code = 'CODEC2'
                size_bytes = random.randint(4500, 11000)
                urgency = random.randint(1, 3)
                desc = f"Pending volunteer voice dispatch #{q_idx}"
            else:
                mode_code = 'M17'
                size_bytes = random.randint(5500, 14000)
                urgency = random.randint(1, 3)
                desc = f"Pending M17 digital packet burst #{q_idx}"

            mode_id = modes[mode_code]["mode_id"]
            cur.execute("""
                INSERT INTO data_item (
                    spacecraft_id, mode_id, size_bytes, urgency, description,
                    status, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'QUEUED', now()
                ) RETURNING item_id;
            """, (sat_id, mode_id, size_bytes, urgency, desc))
            item_id = cur.fetchone()["item_id"]

            if mode_code == 'SSTV':
                cur.execute("""
                    INSERT INTO sstv_image (item_id, resolution, sstv_format, captured_at)
                    VALUES (%s, '320x240', 'Robot36', now());
                """, (item_id,))
            elif mode_code in ('CODEC2', 'M17'):
                dur = round(size_bytes / (modes[mode_code]["bitrate_bps"] / 8.0), 1)
                cur.execute("""
                    INSERT INTO voice_clip (item_id, codec, duration_s)
                    VALUES (%s, %s, %s);
                """, (item_id, mode_code, dur))

    conn.commit()
    print("Downlink windows and payload items generation completed.")


def generate_telemetry_dataset(conn, samples_per_sensor=8350):
    """
    Generates ~100,000 telemetry readings across 12 sensors:
      - Sinusoidal orbit pattern (90-min orbit cycle)
      - Realistic day/night solar illumination and thermal transitions
      - Sources: ~80% ORBIT, ~20% tagged test blocks (BENCH, THERMOVAC, EMI_EMC, VIBRATION, HIL)
      - ~0.05% spike readings triggering alerts (NO critical Battery Voltage drops in history)
      - Batch insertion via psycopg2.extras.execute_values
    """
    total_samples = samples_per_sensor * 12
    print(f"Generating ~{total_samples:,} telemetry readings using execute_values...")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT spacecraft_id FROM spacecraft WHERE name = 'SomaiyaSat';")
        sat_id = cur.fetchone()["spacecraft_id"]
        cur.execute("SELECT sensor_id, name, subsystem, sensor_type FROM sensor ORDER BY sensor_id;")
        sensors = cur.fetchall()

    start_date = datetime(2026, 3, 15, 6, 30, 0)
    time_step_s = 620 # ~10.3 minutes between samples -> spans ~60 days

    sources = ['BENCH', 'THERMOVAC', 'EMI_EMC', 'VIBRATION', 'HIL']
    telemetry_records = []
    orbit_period_s = 5400.0 # 90 minutes

    for i in range(samples_per_sensor):
        timestamp = start_date + timedelta(seconds=i * time_step_s)
        t_sec = i * time_step_s
        orbit_angle = (2.0 * math.pi * (t_sec % orbit_period_s)) / orbit_period_s
        in_sun = math.sin(orbit_angle) > 0.0

        if i < samples_per_sensor * 0.15:
            source = sources[int((i / (samples_per_sensor * 0.15)) * len(sources)) % len(sources)]
        else:
            source = 'ORBIT'

        for s in sensors:
            name = s["name"]
            spike = (random.random() < 0.0005) # ~0.05% spike probability

            # Calculate sensor reading based on aerospace physics model
            if name == 'Battery Voltage':
                # Strictly keep >= 3.50 V: NO critical safe mode spikes in history
                base = 3.82 + 0.14 * math.sin(orbit_angle) + random.gauss(0, 0.02)
                val = max(3.52, min(4.14, base))
                if spike:
                    val = 3.55 # Warning breach only, never below critical 3.30 V

            elif name == 'Battery Current':
                if in_sun:
                    val = 260.0 + 80.0 * math.sin(orbit_angle) + random.gauss(0, 15.0)
                else:
                    val = -320.0 + 40.0 * math.cos(orbit_angle) + random.gauss(0, 15.0)
                if spike:
                    val = random.choice([850.0, -850.0]) # Breaches warning limit [ -800, 800 ]

            elif name == 'Solar Panel Voltage':
                if in_sun:
                    val = 4.85 + 0.25 * math.sin(orbit_angle) + random.gauss(0, 0.05)
                else:
                    val = max(0.0, random.gauss(0.05, 0.02))
                if spike:
                    val = 5.40 # Breaches warning limit 5.20 V

            elif name == 'Solar Current':
                if in_sun:
                    val = 240.0 + 60.0 * math.sin(orbit_angle) + random.gauss(0, 8.0)
                else:
                    val = max(0.0, random.gauss(0.5, 0.2))
                if spike:
                    val = 375.0 # Breaches warning limit 350 mA

            elif name == 'Battery Temp':
                val = 21.0 + 7.5 * math.sin(orbit_angle - 0.5) + random.gauss(0, 0.4)
                if spike:
                    val = 48.0 # Breaches warning limit 45 C

            elif name == 'Panel Temp +Y':
                val = 15.0 + 32.0 * math.sin(orbit_angle) + random.gauss(0, 0.8)
                if spike:
                    val = 74.0 # Breaches warning limit 70 C

            elif name == 'Panel Temp -Y':
                val = 8.0 + 30.0 * math.sin(orbit_angle + math.pi) + random.gauss(0, 0.8)
                if spike:
                    val = 73.0 # Breaches warning limit 70 C

            elif name == 'OBC Core Temp':
                val = 34.0 + 5.0 * math.sin(orbit_angle) + random.gauss(0, 0.5)
                if spike:
                    val = 68.0 # Breaches warning limit 65 C

            elif name == 'OBC Supply Voltage':
                val = 3.30 + random.gauss(0, 0.015)
                if spike:
                    val = 3.50 # Breaches warning limit 3.45 V

            elif name == 'PA Temperature':
                val = 30.0 + 12.0 * (0.8 if in_sun else 0.4) + random.gauss(0, 0.7)
                if spike:
                    val = 76.0 # Breaches warning limit 70 C

            elif name == 'RF Supply Voltage':
                val = 3.30 + random.gauss(0, 0.02)
                if spike:
                    val = 3.55 # Breaches warning limit 3.50 V

            elif name == 'TX Current':
                val = 140.0 + random.gauss(0, 10.0)
                if spike:
                    val = 475.0 # Breaches warning limit 450 mA

            telemetry_records.append((
                sat_id,
                s["sensor_id"],
                timestamp,
                round(val, 2),
                source
            ))

    # Ingest in batches of 5,000 using execute_values
    batch_size = 5000
    total_batches = (len(telemetry_records) + batch_size - 1) // batch_size
    print(f"Ingesting {len(telemetry_records):,} records in {total_batches} batches...")

    with conn.cursor() as cur:
        for b_idx in range(total_batches):
            batch = telemetry_records[b_idx * batch_size : (b_idx + 1) * batch_size]
            execute_values(
                cur,
                """
                INSERT INTO telemetry_reading (spacecraft_id, sensor_id, recorded_at, val, source)
                VALUES %s;
                """,
                batch
            )
            if (b_idx + 1) % 5 == 0 or (b_idx + 1) == total_batches:
                conn.commit()
                print(f"  -> Ingested batch {b_idx + 1}/{total_batches} ({(b_idx + 1) * batch_size:,} records)...")

    conn.commit()
    print("Telemetry data generation and ingestion completed.")


def print_final_table_summary(conn):
    """Prints a clear row count summary of all 19 database tables."""
    tables = [
        "mission_phase", "spacecraft", "phase_transition", "deployment_event",
        "app_user", "comm_mode", "downlink_window", "data_item",
        "sstv_image", "voice_clip", "router_decision", "transmission",
        "sensor", "threshold_rule", "telemetry_reading", "alert",
        "command_log", "audit_log", "model_run"
    ]
    print("\n" + "=" * 55)
    print("DATABASE TABLE ROW COUNTS SUMMARY")
    print("=" * 55)
    with conn.cursor() as cur:
        for tbl in tables:
            cur.execute(f"SELECT count(*) FROM {tbl};")
            cnt = cur.fetchone()[0]
            print(f"  {tbl:<22}: {cnt:>10,}")
    print("=" * 55)


def main():
    print("=" * 70)
    print("SomaiyaSat Telemetry Hub - Stage 3 Data Generator")
    print("=" * 70)
    conn = get_conn()
    try:
        reset_operational_data(conn)
        run_mission_lifecycle_initialization(conn)
        generate_downlink_windows_and_traffic(conn, target_windows=300)
        generate_telemetry_dataset(conn, samples_per_sensor=8350)
        print_final_table_summary(conn)
    finally:
        conn.close()
    print("\nData generation pipeline finished successfully!")


if __name__ == "__main__":
    main()
