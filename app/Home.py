"""
SomaiyaSat Telemetry Hub - app/Home.py
Mission Dashboard: Key performance indicators, delivery rates per RF mode,
active system alerts, and real-time battery voltage trends.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from common import (
    init_page, render_sidebar, run_query, format_bytes, get_plotly_layout, get_current_phase
)

# Initialize page and theme
init_page("Mission Dashboard", "🛰️")
current_user = render_sidebar()

# Page Header
st.markdown(
    """
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
        <div>
            <h1 style="margin: 0; font-size: 2.1rem; font-weight: 700; color: #f8fafc; letter-spacing: -0.02em;">
                SomaiyaSat Telemetry Hub
            </h1>
            <p style="margin: 4px 0 0 0; color: #94a3b8; font-size: 0.95rem;">
                Flight Operations Ground Station & Autonomous Telemetry Decision Center
            </p>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

# ----------------------------------------------------------------------------
# 1. Top KPI Metrics Row
# ----------------------------------------------------------------------------
phase_name, tt_c_only = get_current_phase()

# Fetch summary stats
kpi_data = run_query("""
    SELECT
        (SELECT count(*) FROM alert WHERE status = 'OPEN') AS open_alerts,
        (SELECT count(*) FROM alert WHERE status = 'OPEN' AND severity = 'CRITICAL') AS critical_alerts,
        (SELECT count(*) FROM data_item WHERE status = 'QUEUED') AS queue_depth,
        (SELECT count(*) FROM telemetry_reading) AS total_telemetry,
        (SELECT count(*) FROM transmission WHERE status = 'RECEIVED') AS received_tx,
        (SELECT count(*) FROM transmission) AS total_tx,
        (SELECT count(*) FROM downlink_window) AS total_windows;
""")

open_alerts = 0
critical_alerts = 0
queue_depth = 0
total_telemetry = 0
overall_delivery_rate = 0.0

if not kpi_data.empty:
    row = kpi_data.iloc[0]
    open_alerts = int(row["open_alerts"])
    critical_alerts = int(row["critical_alerts"])
    queue_depth = int(row["queue_depth"])
    total_telemetry = int(row["total_telemetry"])
    tot_tx = int(row["total_tx"])
    rx_tx = int(row["received_tx"])
    if tot_tx > 0:
        overall_delivery_rate = round((rx_tx / tot_tx) * 100.0, 1)

c1, c2, c3, c4, c5 = st.columns(5)

with c1:
    st.markdown(
        f"""
        <div data-testid="stMetric">
            <div data-testid="stMetricLabel">Current Mission Phase</div>
            <div style="margin-top: 8px;">
                <span class="phase-badge phase-{phase_name}">{phase_name}</span>
            </div>
            <div style="font-size: 0.72rem; color: #94a3b8; margin-top: 6px;">
                {'⚠️ Safe mode lock: TT&C only' if tt_c_only else 'Normal operational state'}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

with c2:
    alert_color = "#ef4444" if critical_alerts > 0 else ("#f59e0b" if open_alerts > 0 else "#10b981")
    st.metric(
        label="Active Alerts",
        value=f"{open_alerts}",
        delta=f"{critical_alerts} Critical" if critical_alerts > 0 else "Normal Limits",
        delta_color="inverse" if critical_alerts > 0 else "normal"
    )

with c3:
    st.metric(
        label="Queue Depth",
        value=f"{queue_depth:,}",
        delta="Pending Downlink"
    )

with c4:
    st.metric(
        label="Link Delivery Rate",
        value=f"{overall_delivery_rate}%",
        delta=f"{tot_tx:,} Packets Sent"
    )

with c5:
    st.metric(
        label="Telemetry Samples",
        value=f"{total_telemetry:,}",
        delta="12 Subsystem Sensors"
    )

st.markdown("<div style='margin-bottom: 25px;'></div>", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# 2. Communication Modes Delivery Statistics (v_mode_delivery_rate)
# ----------------------------------------------------------------------------
st.markdown("### 📡 Communication Modes Delivery Performance")
mode_df = run_query("SELECT * FROM v_mode_delivery_rate ORDER BY base_priority ASC;")

col_chart, col_table = st.columns([3, 2])

with col_chart:
    if not mode_df.empty:
        fig = go.Figure()
        
        # Bars for Sent vs Received
        fig.add_trace(go.Bar(
            name="Packets Sent",
            x=mode_df["mode_code"],
            y=mode_df["total_sent"],
            marker_color="rgba(56, 189, 248, 0.4)",
            marker_line_color="#38bdf8",
            marker_line_width=1.5
        ))
        
        fig.add_trace(go.Bar(
            name="Received (Ground)",
            x=mode_df["mode_code"],
            y=mode_df["total_received"],
            marker_color="rgba(16, 185, 129, 0.5)",
            marker_line_color="#10b981",
            marker_line_width=1.5
        ))
        
        # Secondary line for Success Rate %
        fig.add_trace(go.Scatter(
            name="Success Rate %",
            x=mode_df["mode_code"],
            y=mode_df["success_rate_pct"],
            yaxis="y2",
            mode="lines+markers+text",
            text=[f"{val}%" for val in mode_df["success_rate_pct"]],
            textposition="top center",
            line=dict(color="#fbbf24", width=2.5),
            marker=dict(size=8, color="#f59e0b")
        ))
        
        layout = get_plotly_layout("Downlink Volume & Reception Efficiency by RF Mode")
        layout["barmode"] = "group"
        layout["yaxis2"] = dict(
            title=dict(text="Success Rate (%)", font=dict(color="#f59e0b")),
            tickfont=dict(color="#f59e0b"),
            overlaying="y",
            side="right",
            range=[0, 115],
            showgrid=False
        )
        layout["yaxis"]["title"] = "Total Packet Count"
        fig.update_layout(layout)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No transmission performance data available.")

with col_table:
    st.markdown("<p style='font-size: 0.88rem; color: #94a3b8;'>Summary from <code>v_mode_delivery_rate</code>:</p>", unsafe_allow_html=True)
    if not mode_df.empty:
        display_df = mode_df[[
            "mode_code", "base_priority", "bitrate_bps", "tx_power_mw",
            "total_sent", "total_received", "success_rate_pct"
        ]].rename(columns={
            "mode_code": "Mode",
            "base_priority": "Priority",
            "bitrate_bps": "Bitrate (bps)",
            "tx_power_mw": "TX (mW)",
            "total_sent": "Sent",
            "total_received": "Received",
            "success_rate_pct": "Success %"
        })
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        st.caption("TT&C priority (1) uses robust FSK modulation with highest delivery reliability.")

st.markdown("<div style='margin-bottom: 25px;'></div>", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# 3. In-Orbit Battery Voltage Trend
# ----------------------------------------------------------------------------
st.markdown("### ⚡ Power Subsystem: Battery Voltage Trend")

batt_df = run_query("""
    SELECT recorded_at, val, source
    FROM telemetry_reading tr
    JOIN sensor s ON tr.sensor_id = s.sensor_id
    WHERE s.name = 'Battery Voltage'
    ORDER BY recorded_at DESC
    LIMIT 250;
""")

if not batt_df.empty:
    batt_df = batt_df.sort_values("recorded_at")
    
    fig_batt = go.Figure()
    
    # Main telemetry trace
    fig_batt.add_trace(go.Scatter(
        x=batt_df["recorded_at"],
        y=batt_df["val"],
        mode="lines",
        name="Battery Voltage (V)",
        line=dict(color="#38bdf8", width=2),
        fill="tozeroy",
        fillcolor="rgba(56, 189, 248, 0.05)"
    ))
    
    # Warning Lower Limit (3.60 V)
    fig_batt.add_hline(
        y=3.60,
        line_dash="dash",
        line_color="#f59e0b",
        annotation_text="Warning Limit (3.60 V)",
        annotation_position="bottom right"
    )
    
    # Critical Safe Mode Limit (3.30 V)
    fig_batt.add_hline(
        y=3.30,
        line_dash="solid",
        line_color="#ef4444",
        annotation_text="Critical Safe Mode Threshold (3.30 V)",
        annotation_position="bottom right"
    )

    layout_batt = get_plotly_layout("Sinusoidal In-Orbit Battery Cycle (Day/Night Illumination)")
    layout_batt["yaxis"]["title"] = "Voltage (V)"
    layout_batt["yaxis"]["range"] = [3.1, 4.3]
    fig_batt.update_layout(layout_batt)
    st.plotly_chart(fig_batt, use_container_width=True)
else:
    st.info("No battery voltage telemetry records available.")

# ----------------------------------------------------------------------------
# 4. Active System Alerts View (v_open_alerts)
# ----------------------------------------------------------------------------
st.markdown("### 🚨 Active Operational Alerts")
alerts_df = run_query("SELECT * FROM v_open_alerts ORDER BY created_at DESC LIMIT 10;")

if not alerts_df.empty:
    alerts_display = alerts_df[[
        "alert_id", "alert_type", "severity", "message", "sensor_name",
        "reading_value", "reading_source", "created_at"
    ]].rename(columns={
        "alert_id": "ID",
        "alert_type": "Type",
        "severity": "Severity",
        "message": "Message",
        "sensor_name": "Sensor",
        "reading_value": "Reading",
        "reading_source": "Source",
        "created_at": "Timestamp"
    })
    st.dataframe(alerts_display, use_container_width=True, hide_index=True)
    st.caption("Active alerts can be acknowledged by authorized Operators in the **Alerts** page.")
else:
    st.success("✅ All telemetry systems operating within nominal limits. Zero active alerts.")
