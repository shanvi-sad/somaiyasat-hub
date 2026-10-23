"""
SomaiyaSat Telemetry Hub - app/pages/2_Downlink_Windows.py
Downlink contact windows catalog, pass capacity utilization, and v_window_summary metrics.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from common import (
    init_page, render_sidebar, run_query, format_bytes, get_plotly_layout
)

init_page("Downlink Windows", "🪟")
user = render_sidebar()

st.title("🪟 Ground Station Downlink Windows")
st.markdown("Monitor contact pass schedules, RF link budgets, capacity utilization, and delivered telemetry data.")

# ----------------------------------------------------------------------------
# 1. Windows Overview Metrics
# ----------------------------------------------------------------------------
stats_df = run_query("""
    SELECT
        COUNT(*) as total_windows,
        COUNT(CASE WHEN window_status = 'COMPLETED' THEN 1 END) as completed_windows,
        COUNT(CASE WHEN window_status = 'SCHEDULED' THEN 1 END) as scheduled_windows,
        COUNT(CASE WHEN window_status = 'PLANNED' THEN 1 END) as planned_windows,
        COALESCE(SUM(delivered_bytes), 0) as total_delivered_bytes,
        COALESCE(SUM(scheduled_bytes), 0) as total_scheduled_bytes
    FROM v_window_summary;
""")

if not stats_df.empty:
    s = stats_df.iloc[0]
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.metric("Total Contact Passes", f"{s['total_windows']:,}")
    with m2:
        st.metric("Completed Passes", f"{s['completed_windows']:,}")
    with m3:
        st.metric("Scheduled Passes", f"{s['scheduled_windows']:,}")
    with m4:
        st.metric("Planned Passes", f"{s['planned_windows']:,}")
    with m5:
        st.metric("Delivered Payload", format_bytes(s['total_delivered_bytes']))

st.markdown("<div style='margin-bottom: 20px;'></div>", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# 2. Filtering & Search
# ----------------------------------------------------------------------------
col_filter1, col_filter2, col_filter3 = st.columns([2, 2, 2])

with col_filter1:
    status_filter = st.selectbox("Filter by Pass Status", ["ALL", "COMPLETED", "SCHEDULED", "PLANNED"])

with col_filter2:
    sort_order = st.selectbox("Sort Order", ["Newest First (DESC)", "Oldest First (ASC)"])

with col_filter3:
    search_id = st.text_input("Search Window ID", placeholder="e.g. 101")

order_sql = "DESC" if "DESC" in sort_order else "ASC"
where_clauses = []
params = []

if status_filter != "ALL":
    where_clauses.append("window_status = %s")
    params.append(status_filter)

if search_id.strip().isdigit():
    where_clauses.append("window_id = %s")
    params.append(int(search_id.strip()))

where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

windows_df = run_query(f"""
    SELECT
        window_id,
        start_time,
        duration_s,
        capacity_bytes,
        energy_budget_mwh,
        expected_snr_db,
        battery_v,
        window_status,
        scheduled_bytes,
        delivered_bytes,
        total_decisions,
        send_decisions,
        transmissions_count,
        received_count
    FROM v_window_summary
    {where_sql}
    ORDER BY start_time {order_sql}
    LIMIT 200;
""", tuple(params) if params else None)

# ----------------------------------------------------------------------------
# 3. Windows Table Display
# ----------------------------------------------------------------------------
st.markdown("### 📋 Contact Windows View (<code>v_window_summary</code>)", unsafe_allow_html=True)

if not windows_df.empty:
    display_df = windows_df.copy()
    display_df["Capacity"] = display_df["capacity_bytes"].apply(format_bytes)
    display_df["Scheduled"] = display_df["scheduled_bytes"].apply(format_bytes)
    display_df["Delivered"] = display_df["delivered_bytes"].apply(format_bytes)
    display_df["Efficiency"] = display_df.apply(
        lambda r: f"{(r['delivered_bytes']/r['scheduled_bytes']*100):.1f}%" if r['scheduled_bytes'] > 0 else "0.0%",
        axis=1
    )
    
    table_view = display_df[[
        "window_id", "start_time", "duration_s", "Capacity", "energy_budget_mwh",
        "expected_snr_db", "battery_v", "window_status", "Scheduled", "Delivered",
        "Efficiency", "total_decisions"
    ]].rename(columns={
        "window_id": "Pass ID",
        "start_time": "Start Time (UTC)",
        "duration_s": "Duration (s)",
        "energy_budget_mwh": "Energy (mWh)",
        "expected_snr_db": "SNR (dB)",
        "battery_v": "Battery (V)",
        "window_status": "Status",
        "total_decisions": "Decisions"
    })
    
    st.dataframe(table_view, use_container_width=True, hide_index=True)
else:
    st.info("No downlink windows found matching the selected filters.")

st.markdown("---")

# ----------------------------------------------------------------------------
# 4. Detailed Pass Inspector
# ----------------------------------------------------------------------------
st.markdown("### 🔍 Downlink Pass Inspector")
inspect_id = st.number_input(
    "Enter Pass ID to inspect details & transmitted packets",
    min_value=1,
    value=int(windows_df.iloc[0]["window_id"]) if not windows_df.empty else 1,
    step=1
)

if inspect_id:
    win_detail = run_query("SELECT * FROM downlink_window WHERE window_id = %s;", (int(inspect_id),))
    if not win_detail.empty:
        w = win_detail.iloc[0]
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Pass Capacity", format_bytes(w["capacity_bytes"]))
        c2.metric("Energy Budget", f"{w['energy_budget_mwh']} mWh")
        c3.metric("Expected SNR", f"{w['expected_snr_db']} dB")
        c4.metric("Battery Voltage", f"{w['battery_v']} V")
        
        st.markdown("#### Decisions in this Pass:")
        dec_df = run_query("""
            SELECT
                rd.decision_id,
                di.item_id,
                cm.code as mode_code,
                di.size_bytes,
                di.urgency,
                rd.action,
                rd.overridden,
                rd.override_action,
                COALESCE(rd.override_action, rd.action) as effective_action,
                rd.score,
                rd.rationale,
                t.status as tx_status,
                t.bytes_received,
                u.callsign as received_by
            FROM router_decision rd
            JOIN data_item di ON rd.item_id = di.item_id
            JOIN comm_mode cm ON di.mode_id = cm.mode_id
            LEFT JOIN transmission t ON rd.decision_id = t.decision_id
            LEFT JOIN app_user u ON t.received_by = u.user_id
            WHERE rd.window_id = %s
            ORDER BY rd.score DESC;
        """, (int(inspect_id),))
        
        if not dec_df.empty:
            st.dataframe(dec_df, use_container_width=True, hide_index=True)
        else:
            st.info("No router decisions logged for this pass yet.")
    else:
        st.warning(f"Pass ID {inspect_id} not found.")
