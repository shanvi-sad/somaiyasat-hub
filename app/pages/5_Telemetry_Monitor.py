"""
SomaiyaSat Telemetry Hub - Telemetry Monitor & Sensor Analysis
Visualizes real-time and historical sensor telemetry against operational thresholds,
displays limit violation alerts, and enables live manual reading ingestion to trigger
in-orbit autonomous safe mode and limit checks.
"""

from datetime import datetime, timezone
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from app.common import (
    init_page,
    render_sidebar,
    run_query,
    run_write,
    can_operate,
    get_plotly_layout
)

init_page("Telemetry Monitor", "📈")
user = render_sidebar()

st.title("📈 Telemetry Monitor & Sensor Analysis")
st.caption("Continuous telemetry stream monitoring, multi-sensor operational threshold bands, and real-time safe mode triggers.")

# -----------------------------------------------------------------------------
# 1. Sensor & Telemetry Source Selection
# -----------------------------------------------------------------------------
sensors = run_query("SELECT sensor_id, name, subsystem, sensor_type, unit FROM sensor ORDER BY subsystem, sensor_id;")
if sensors.empty:
    st.error("No sensor definitions found.")
    st.stop()

col_filt1, col_filt2, col_filt3 = st.columns([2, 1.5, 1])

sensor_dict = {f"{row['name']} ({row['subsystem']} | {row['unit']})": row for _, row in sensors.iterrows()}
selected_sensor_label = col_filt1.selectbox("Select Sensor", options=list(sensor_dict.keys()), index=0)
selected_sensor = sensor_dict[selected_sensor_label]

source_options = ["ALL", "ORBIT", "BENCH", "THERMOVAC", "EMI_EMC", "VIBRATION", "HIL"]
selected_source = col_filt2.selectbox("Telemetry Source", options=source_options, index=0)

sample_limits = [100, 250, 500, 1000, 2500]
selected_limit = col_filt3.selectbox("Sample Limit", options=sample_limits, index=1)

# -----------------------------------------------------------------------------
# 2. Fetch Thresholds & Telemetry Data
# -----------------------------------------------------------------------------
sensor_id = int(selected_sensor["sensor_id"])
sensor_unit = selected_sensor["unit"]

thresholds_df = run_query("""
    SELECT rule_no, severity, min_val, max_val
    FROM threshold_rule
    WHERE sensor_id = %s
    ORDER BY (CASE WHEN severity = 'CRITICAL' THEN 1 ELSE 2 END);
""", (sensor_id,))

# Extract limits
crit_min, crit_max = None, None
warn_min, warn_max = None, None

for _, tr in thresholds_df.iterrows():
    if tr["severity"] == "CRITICAL":
        crit_min = float(tr["min_val"])
        crit_max = float(tr["max_val"])
    elif tr["severity"] == "WARNING":
        warn_min = float(tr["min_val"])
        warn_max = float(tr["max_val"])

# Query readings
query_sql = """
    SELECT reading_id, recorded_at, val, source
    FROM telemetry_reading
    WHERE sensor_id = %s
"""
query_params = [sensor_id]

if selected_source != "ALL":
    query_sql += " AND source = %s"
    query_params.append(selected_source)

query_sql += f" ORDER BY recorded_at DESC LIMIT {selected_limit};"

readings_df = run_query(query_sql, tuple(query_params))

# -----------------------------------------------------------------------------
# 3. Plotly Telemetry Chart with Limit Bands
# -----------------------------------------------------------------------------
if not readings_df.empty:
    readings_df = readings_df.sort_values(by="recorded_at")
    
    # Calculate key metrics
    latest_val = readings_df["val"].iloc[-1]
    min_val = readings_df["val"].min()
    max_val = readings_df["val"].max()
    mean_val = readings_df["val"].mean()

    m_col1, m_col2, m_col3, m_col4, m_col5 = st.columns(5)
    m_col1.metric("Latest Value", f"{latest_val:.2f} {sensor_unit}")
    m_col2.metric("Minimum", f"{min_val:.2f} {sensor_unit}")
    m_col3.metric("Maximum", f"{max_val:.2f} {sensor_unit}")
    m_col4.metric("Mean", f"{mean_val:.2f} {sensor_unit}")
    m_col5.metric("Data Points", f"{len(readings_df)}")

    fig = go.Figure()

    # Telemetry Line Trace
    fig.add_trace(go.Scatter(
        x=readings_df["recorded_at"],
        y=readings_df["val"],
        mode="lines+markers",
        name=f"{selected_sensor['name']} ({sensor_unit})",
        line=dict(color="#38bdf8", width=1.8),
        marker=dict(size=3, color="#38bdf8"),
        hovertemplate="<b>%{x}</b><br>Value: %{y:.2f} " + sensor_unit + "<extra></extra>"
    ))

    # Add Critical Limits
    if crit_max is not None:
        fig.add_hline(
            y=crit_max, line_dash="dash", line_color="#ef4444", line_width=1.5,
            annotation_text=f"Critical Max ({crit_max} {sensor_unit})",
            annotation_position="top right", annotation_font_color="#ef4444"
        )
    if crit_min is not None:
        fig.add_hline(
            y=crit_min, line_dash="dash", line_color="#ef4444", line_width=1.5,
            annotation_text=f"Critical Min ({crit_min} {sensor_unit})",
            annotation_position="bottom right", annotation_font_color="#ef4444"
        )

    # Add Warning Limits
    if warn_max is not None:
        fig.add_hline(
            y=warn_max, line_dash="dot", line_color="#f59e0b", line_width=1.2,
            annotation_text=f"Warning Max ({warn_max} {sensor_unit})",
            annotation_position="top left", annotation_font_color="#f59e0b"
        )
    if warn_min is not None:
        fig.add_hline(
            y=warn_min, line_dash="dot", line_color="#f59e0b", line_width=1.2,
            annotation_text=f"Warning Min ({warn_min} {sensor_unit})",
            annotation_position="bottom left", annotation_font_color="#f59e0b"
        )

    layout = get_plotly_layout(f"{selected_sensor['name']} Trend [{selected_source}]")
    layout["yaxis"]["title"] = f"{selected_sensor['name']} ({sensor_unit})"
    layout["xaxis"]["title"] = "Timestamp (UTC)"
    fig.update_layout(layout)

    st.plotly_chart(fig, use_container_width=True)
else:
    st.info(f"No telemetry readings found for {selected_sensor['name']} with source {selected_source}.")

# -----------------------------------------------------------------------------
# 4. Recent Breaches for Selected Sensor / System
# -----------------------------------------------------------------------------
st.markdown("### ⚠️ Recent Telemetry Limit Violations")
breaches_df = run_query("""
    SELECT
        a.alert_id,
        a.created_at,
        a.severity,
        a.status,
        s.name AS sensor_name,
        tr.val AS reading_val,
        s.unit,
        tr.source,
        a.message
    FROM alert a
    JOIN telemetry_reading tr ON a.reading_id = tr.reading_id
    JOIN sensor s ON tr.sensor_id = s.sensor_id
    WHERE a.alert_type = 'TELEMETRY_LIMIT'
    ORDER BY a.created_at DESC
    LIMIT 10;
""")

if not breaches_df.empty:
    st.dataframe(
        breaches_df,
        column_config={
            "alert_id": st.column_config.NumberColumn("Alert ID", width=80),
            "created_at": st.column_config.DatetimeColumn("Timestamp", format="YYYY-MM-DD HH:mm:ss"),
            "severity": st.column_config.TextColumn("Severity", width=100),
            "status": st.column_config.TextColumn("Status", width=90),
            "sensor_name": st.column_config.TextColumn("Sensor"),
            "reading_val": st.column_config.NumberColumn("Breached Val", format="%.2f"),
            "source": st.column_config.TextColumn("Source", width=110),
            "message": st.column_config.TextColumn("Alert Diagnostics", width="large")
        },
        use_container_width=True,
        hide_index=True
    )
else:
    st.success("✅ Nominal telemetry: No recent limit violations detected.")

# -----------------------------------------------------------------------------
# 5. Live Reading Ingestion Form (Demo Live Triggers & Safe Mode)
# -----------------------------------------------------------------------------
st.markdown("---")
st.markdown("### 🧪 Live Telemetry Ingestion (Demo Lab)")
st.caption("Inject live readings into PostgreSQL to demonstrate `trg_telemetry_check` limit alerts and `trg_low_battery_safe_mode` emergency state transitions.")

with st.expander("ℹ️ How to Trigger Live Business Rules", expanded=True):
    st.markdown("""
    - **Trigger Alert (`trg_telemetry_check`)**: Inject any value outside the WARNING or CRITICAL bands (e.g. `Battery Voltage` = `4.35` V). An alert will be inserted immediately with diagnostic details.
    - **Trigger Safe Mode (`trg_low_battery_safe_mode`)**: Select **SomaiyaSat**, sensor **Battery Voltage**, source **ORBIT**, and enter a value strictly below **3.30 V** (e.g., `3.15` V). The database trigger will autonomously:
      1. Insert a phase transition moving SomaiyaSat into `SAFE_MODE`
      2. Issue an automatic telecommand into `command_log` (`ENTER_SAFE_MODE`)
      3. Raise a `CRITICAL` alert in `alert`
      4. Lock down all future downlink scheduling passes to TT&C packets only
    """)

if not can_operate():
    st.warning("🔒 **Read-Only Access**: Log in as `OPERATOR` or `ADMIN` from the sidebar to inject test telemetry.")
else:
    with st.form("manual_reading_form", clear_on_submit=False):
        f_col1, f_col2, f_col3 = st.columns(3)
        
        spacecraft_df = run_query("SELECT spacecraft_id, name FROM spacecraft ORDER BY spacecraft_id DESC;")
        sc_map = {row["name"]: int(row["spacecraft_id"]) for _, row in spacecraft_df.iterrows()}
        selected_sc_name = f_col1.selectbox("Spacecraft", options=list(sc_map.keys()), index=0)
        selected_sc_id = sc_map[selected_sc_name]

        sensor_input_map = {f"{row['name']} ({row['unit']})": row for _, row in sensors.iterrows()}
        # Default to Battery Voltage
        default_sensor_idx = list(sensor_input_map.keys()).index("Battery Voltage (V)") if "Battery Voltage (V)" in sensor_input_map else 0
        selected_sensor_input_label = f_col2.selectbox("Target Sensor", options=list(sensor_input_map.keys()), index=default_sensor_idx)
        target_sensor_meta = sensor_input_map[selected_sensor_input_label]
        target_sensor_id = int(target_sensor_meta["sensor_id"])

        selected_input_source = f_col3.selectbox("Environment / Source", options=["ORBIT", "BENCH", "THERMOVAC", "EMI_EMC", "VIBRATION", "HIL"], index=0)

        f_col4, f_col5 = st.columns(2)
        val_input = f_col4.number_input(
            f"Sensor Reading ({target_sensor_meta['unit']})",
            value=3.20 if target_sensor_meta["name"] == "Battery Voltage" else 25.0,
            step=0.01,
            format="%.2f"
        )
        timestamp_input = f_col5.text_input("Timestamp (UTC)", value=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

        submit_reading = st.form_submit_button("⚡ Inject Telemetry Reading", use_container_width=True)

        if submit_reading:
            sql = """
                INSERT INTO telemetry_reading (spacecraft_id, sensor_id, recorded_at, val, source)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING reading_id;
            """
            params = (selected_sc_id, target_sensor_id, timestamp_input, val_input, selected_input_source)
            
            # Fetch current phase before injection to detect transition
            prev_phase = run_query("SELECT mp.name FROM spacecraft sc JOIN mission_phase mp ON sc.current_phase_id = mp.phase_id WHERE sc.spacecraft_id = %s;", (selected_sc_id,))
            prev_phase_name = prev_phase.iloc[0]["name"] if not prev_phase.empty else "UNKNOWN"

            # Execute insert
            with st.spinner("Injecting telemetry and evaluating autonomous triggers..."):
                try:
                    from scripts.db import get_conn
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(sql, params)
                            new_reading_id = cur.fetchone()["reading_id"]
                    
                    st.success(f"✅ Ingested telemetry reading #{new_reading_id} successfully!")

                    with st.expander("Executed SQL Command", expanded=False):
                        st.code(f"{sql.strip()}\n-- Params: {params}", language="sql")

                    # Check for triggered alert
                    alert_check = run_query("""
                        SELECT alert_id, alert_type, severity, message
                        FROM alert
                        WHERE reading_id = %s;
                    """, (new_reading_id,))

                    if not alert_check.empty:
                        for _, a_row in alert_check.iterrows():
                            if a_row["severity"] == "CRITICAL":
                                st.error(f"🚨 **CRITICAL ALERT TRIGGERED** (Alert #{a_row['alert_id']}): {a_row['message']}")
                            else:
                                st.warning(f"⚠️ **WARNING ALERT TRIGGERED** (Alert #{a_row['alert_id']}): {a_row['message']}")

                    # Check for Autonomous Safe Mode transition
                    curr_phase = run_query("SELECT mp.name FROM spacecraft sc JOIN mission_phase mp ON sc.current_phase_id = mp.phase_id WHERE sc.spacecraft_id = %s;", (selected_sc_id,))
                    curr_phase_name = curr_phase.iloc[0]["name"] if not curr_phase.empty else "UNKNOWN"

                    if prev_phase_name != "SAFE_MODE" and curr_phase_name == "SAFE_MODE":
                        st.error(f"🛰️ **AUTONOMOUS EMERGENCY TRIGGER**: Satellite transitioned `{prev_phase_name}` ➡️ `SAFE_MODE`! Downlink router is now locked to TT&C payload only.")
                        st.rerun()

                except Exception as e:
                    st.error(f"Database insertion failed: {e}")
