"""
SomaiyaSat Telemetry Hub - Commands & Mission History
Inspects automated and operator-issued telecommands, historical packet router decisions,
and ground station telemetry transmissions credited to amateur radio (HAM) volunteers.
"""

import streamlit as st
import pandas as pd
import json

from app.common import (
    init_page,
    render_sidebar,
    run_query,
    format_bytes
)

init_page("Commands & History", "📜")
user = render_sidebar()

st.title("📜 Commands & Mission Audit Trail")
st.caption("Immutable telecommand audit log, algorithmic router decision history, and HAM ground station reception records.")

tab_cmds, tab_decisions, tab_ham = st.tabs([
    "📡 Telecommand Audit Log",
    "🤖 Router Decision History",
    "📻 HAM Station Reception Log"
])

# =============================================================================
# Tab 1: Telecommand Audit Log (command_log)
# =============================================================================
with tab_cmds:
    st.markdown("### 📡 Telecommand Dispatch History")
    st.caption("Tracks both autonomous on-board commands (e.g. low-battery safe mode entry) and manual flight operator interventions.")

    # Top metrics
    cmd_stats_df = run_query("""
        SELECT
            COUNT(*) AS total_cmds,
            COUNT(CASE WHEN is_automatic THEN 1 END) AS auto_cmds,
            COUNT(CASE WHEN NOT is_automatic THEN 1 END) AS op_cmds
        FROM command_log;
    """)
    if not cmd_stats_df.empty:
        c_tot = cmd_stats_df.iloc[0]["total_cmds"]
        c_auto = cmd_stats_df.iloc[0]["auto_cmds"]
        c_op = cmd_stats_df.iloc[0]["op_cmds"]
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Commands", f"{c_tot:,}")
        m2.metric("Automatic (Autonomous)", f"{c_auto:,}", help="Issued autonomously by spacecraft PL/pgSQL triggers")
        m3.metric("Operator Issued", f"{c_op:,}", help="Manually initiated by authorized flight controllers")

    col_cf1, col_cf2 = st.columns(2)
    type_options = ["ALL", "OVERRIDE_DECISION", "ENTER_SAFE_MODE", "EXIT_SAFE_MODE", "REQUEST_PHASE", "SET_PRIORITY"]
    sel_type = col_cf1.selectbox("Filter Command Type", options=type_options, index=0)
    origin_options = ["ALL", "OPERATOR ONLY", "AUTOMATIC ONLY"]
    sel_origin = col_cf2.selectbox("Filter Origin", options=origin_options, index=0)

    cmd_query = """
        SELECT
            c.command_id,
            sc.name AS spacecraft_name,
            c.command_type,
            c.is_automatic,
            COALESCE(u.username, 'AUTONOMOUS_OBC') AS issued_by,
            u.role AS user_role,
            c.issued_at,
            c.details::text AS details_json
        FROM command_log c
        JOIN spacecraft sc ON c.spacecraft_id = sc.spacecraft_id
        LEFT JOIN app_user u ON c.issued_by = u.user_id
        WHERE 1=1
    """
    cmd_params = []
    if sel_type != "ALL":
        cmd_query += " AND c.command_type = %s"
        cmd_params.append(sel_type)
    if sel_origin == "OPERATOR ONLY":
        cmd_query += " AND c.is_automatic = FALSE"
    elif sel_origin == "AUTOMATIC ONLY":
        cmd_query += " AND c.is_automatic = TRUE"

    cmd_query += " ORDER BY c.issued_at DESC LIMIT 50;"

    commands_df = run_query(cmd_query, tuple(cmd_params) if cmd_params else None)

    if not commands_df.empty:
        # Format origin column
        commands_df["origin"] = commands_df["is_automatic"].apply(lambda x: "🤖 AUTO" if x else "👤 OPERATOR")
        
        st.dataframe(
            commands_df[["command_id", "spacecraft_name", "command_type", "origin", "issued_by", "issued_at", "details_json"]],
            column_config={
                "command_id": st.column_config.NumberColumn("ID", width=70),
                "spacecraft_name": st.column_config.TextColumn("Spacecraft", width=120),
                "command_type": st.column_config.TextColumn("Command Type", width=160),
                "origin": st.column_config.TextColumn("Origin", width=110),
                "issued_by": st.column_config.TextColumn("Issued By", width=130),
                "issued_at": st.column_config.DatetimeColumn("Issued At", format="YYYY-MM-DD HH:mm:ss"),
                "details_json": st.column_config.TextColumn("Parameters / Telecommand Payload", width="large")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No telecommands match the selected criteria.")

# =============================================================================
# Tab 2: Router Decision History (router_decision)
# =============================================================================
with tab_decisions:
    st.markdown("### 🤖 Historical Router Scheduling Decisions")
    st.caption("Audit log of AI / rule-based scheduler determinations, fallback events, and operator override interventions.")

    col_df1, col_df2, col_df3 = st.columns(3)
    sel_action = col_df1.selectbox("Filter Action", options=["ALL", "SEND", "DEFER", "DROP"], index=0)
    sel_fallback = col_df2.selectbox("Filter Fallback Logic", options=["ALL", "Fallback Used Only", "Model Used Only"], index=0)
    sel_override = col_df3.selectbox("Filter Operator Overrides", options=["ALL", "Overridden Decisions Only"], index=0)

    dec_query = """
        SELECT
            rd.decision_id,
            w.window_no,
            rd.item_id,
            cm.code AS mode_code,
            di.size_bytes,
            rd.action,
            rd.score,
            rd.predicted_success,
            rd.fallback_used,
            rd.overridden,
            rd.override_action,
            u_ov.username AS override_by,
            rd.override_reason,
            rd.rationale,
            rd.decided_at
        FROM router_decision rd
        JOIN downlink_window w ON rd.window_id = w.window_id
        JOIN data_item di ON rd.item_id = di.item_id
        JOIN comm_mode cm ON di.mode_id = cm.mode_id
        LEFT JOIN app_user u_ov ON rd.override_by = u_ov.user_id
        WHERE 1=1
    """
    dec_params = []
    if sel_action != "ALL":
        dec_query += " AND rd.action = %s"
        dec_params.append(sel_action)
    if sel_fallback == "Fallback Used Only":
        dec_query += " AND rd.fallback_used = TRUE"
    elif sel_fallback == "Model Used Only":
        dec_query += " AND rd.fallback_used = FALSE"
    if sel_override == "Overridden Decisions Only":
        dec_query += " AND rd.overridden = TRUE"

    dec_query += " ORDER BY rd.decided_at DESC LIMIT 50;"

    dec_df = run_query(dec_query, tuple(dec_params) if dec_params else None)

    if not dec_df.empty:
        st.dataframe(
            dec_df,
            column_config={
                "decision_id": st.column_config.NumberColumn("ID", width=70),
                "window_no": st.column_config.NumberColumn("Pass #", width=80),
                "item_id": st.column_config.NumberColumn("Item #", width=80),
                "mode_code": st.column_config.TextColumn("Mode", width=90),
                "size_bytes": st.column_config.NumberColumn("Bytes", format="%d B", width=90),
                "action": st.column_config.TextColumn("Action", width=90),
                "score": st.column_config.NumberColumn("Score", format="%.2f", width=90),
                "predicted_success": st.column_config.NumberColumn("Pred Succ", format="%.2f", width=90),
                "fallback_used": st.column_config.CheckboxColumn("Fallback?", width=80),
                "overridden": st.column_config.CheckboxColumn("Overridden?", width=90),
                "override_action": st.column_config.TextColumn("New Action", width=90),
                "override_by": st.column_config.TextColumn("Overridden By", width=120),
                "override_reason": st.column_config.TextColumn("Override Justification", width="medium"),
                "rationale": st.column_config.TextColumn("Routing Rationale", width="large"),
                "decided_at": st.column_config.DatetimeColumn("Decided At", format="YYYY-MM-DD HH:mm:ss")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No router decisions found matching filters.")

# =============================================================================
# Tab 3: Amateur Radio Volunteer Transmissions (HAM Leaderboard & Log)
# =============================================================================
with tab_ham:
    st.markdown("### 📻 Amateur Radio (HAM) Station Reception Log")
    st.caption("Recognizing volunteer ground stations decoding downlinked PocketQube packets across global passes.")

    # Volunteer Leaderboard
    ham_leaderboard = run_query("""
        SELECT
            u.callsign,
            u.username,
            COUNT(t.transmission_id) AS total_packets_received,
            SUM(t.bytes_received) AS total_bytes_received,
            ROUND(AVG(t.snr_db), 2) AS avg_snr_db,
            MAX(t.received_at) AS latest_reception
        FROM transmission t
        JOIN app_user u ON t.received_by = u.user_id
        WHERE t.status = 'RECEIVED'
          AND u.role = 'HAM_VOLUNTEER'
        GROUP BY u.callsign, u.username
        ORDER BY total_packets_received DESC;
    """)

    if not ham_leaderboard.empty:
        st.markdown("#### 🏆 Ground Volunteer Leaderboard")
        cols = st.columns(len(ham_leaderboard))
        for idx, row in ham_leaderboard.iterrows():
            with cols[idx]:
                st.markdown(
                    f"""
                    <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 10px; padding: 12px; text-align: center;">
                        <div style="font-size: 1.4rem; font-weight: 700; color: #6ee7b7; font-family: 'JetBrains Mono';">{row['callsign']}</div>
                        <div style="font-size: 0.8rem; color: #94a3b8; margin-top: 2px;">{row['username']}</div>
                        <div style="font-size: 1.1rem; font-weight: 700; color: #f8fafc; margin-top: 6px;">{row['total_packets_received']:,} pkts</div>
                        <div style="font-size: 0.75rem; color: #38bdf8;">{format_bytes(row['total_bytes_received'])} | Avg {row['avg_snr_db']} dB</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

    st.markdown("---")
    st.markdown("#### 📡 Verified Transmission Feed")

    ham_transmissions_df = run_query("""
        SELECT
            t.transmission_id,
            w.window_no,
            u.callsign AS volunteer_callsign,
            u.username AS station_name,
            cm.code AS mode_code,
            di.description AS item_description,
            t.bytes_received,
            t.snr_db,
            t.received_at
        FROM transmission t
        JOIN router_decision rd ON t.decision_id = rd.decision_id
        JOIN data_item di ON rd.item_id = di.item_id
        JOIN comm_mode cm ON di.mode_id = cm.mode_id
        JOIN downlink_window w ON rd.window_id = w.window_id
        JOIN app_user u ON t.received_by = u.user_id
        WHERE t.status = 'RECEIVED'
        ORDER BY t.received_at DESC
        LIMIT 50;
    """)

    if not ham_transmissions_df.empty:
        st.dataframe(
            ham_transmissions_df,
            column_config={
                "transmission_id": st.column_config.NumberColumn("Tx #", width=70),
                "window_no": st.column_config.NumberColumn("Pass #", width=80),
                "volunteer_callsign": st.column_config.TextColumn("Callsign", width=110),
                "station_name": st.column_config.TextColumn("Volunteer", width=120),
                "mode_code": st.column_config.TextColumn("RF Mode", width=90),
                "item_description": st.column_config.TextColumn("Payload Description", width="large"),
                "bytes_received": st.column_config.NumberColumn("Bytes Rx", format="%d B", width=100),
                "snr_db": st.column_config.NumberColumn("SNR", format="%.2f dB", width=90),
                "received_at": st.column_config.DatetimeColumn("Reception Time (UTC)", format="YYYY-MM-DD HH:mm:ss")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No volunteer-credited transmissions found.")
