"""
SomaiyaSat Telemetry Hub - app/pages/1_Phase_and_Deployment.py
Mission Phase Lifecycle, SomaiyaPod Deployment Timeline, and
Autonomous Guard Trigger State Transition Requests.
"""

import streamlit as st
import pandas as pd
from datetime import datetime
from common import (
    init_page, render_sidebar, run_query, run_write, check_permission, get_current_user
)

init_page("Phase & Deployment", "🚀")
user = render_sidebar()

st.title("🚀 Phase Progression & Deployment Tracker")
st.markdown("Track physical separation from **SomaiyaPod** and manage satellite lifecycle state transitions.")

# ----------------------------------------------------------------------------
# 1. SomaiyaPod Deployment State
# ----------------------------------------------------------------------------
st.markdown("### 🛰️ SomaiyaPod Deployer Separation Status")

deploy_data = run_query("""
    SELECT
        de.event_id,
        sc.name as spacecraft_name,
        sc.kind,
        dep.name as deployer_name,
        de.method,
        de.status as deployment_status,
        de.signal_at,
        de.notes
    FROM spacecraft sc
    LEFT JOIN spacecraft dep ON sc.deployer_id = dep.spacecraft_id
    LEFT JOIN deployment_event de ON sc.spacecraft_id = de.spacecraft_id
    WHERE sc.name = 'SomaiyaSat';
""")

if not deploy_data.empty:
    row = deploy_data.iloc[0]
    status = row["deployment_status"] or "UNKNOWN"
    
    status_colors = {
        "CONFIRMED": "#10b981",
        "PENDING": "#f59e0b",
        "PARTIAL": "#ef4444",
        "FAILED": "#b91c1c"
    }
    badge_color = status_colors.get(status, "#94a3b8")

    st.markdown(
        f"""
        <div class="glass-panel" style="border-left: 4px solid {badge_color};">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h3 style="margin: 0; color: #f8fafc;">Deployment Status: <span style="color: {badge_color};">{status}</span></h3>
                    <p style="margin: 4px 0 0 0; color: #94a3b8; font-size: 0.9rem;">
                        Deployer: <strong>{row['deployer_name']}</strong> &rarr; Satellite: <strong>{row['spacecraft_name']}</strong> (Method: <code>{row['method']}</code>)
                    </p>
                </div>
                <div style="text-align: right; font-family: 'JetBrains Mono', monospace; font-size: 0.82rem; color: #cbd5e1;">
                    Signal Timestamp:<br><strong>{row['signal_at'] or 'No Signal Received Yet'}</strong>
                </div>
            </div>
            <div style="margin-top: 12px; font-size: 0.85rem; color: #cbd5e1; background: rgba(0,0,0,0.25); padding: 8px 12px; border-radius: 6px;">
                <strong>Engineering Log:</strong> {row['notes'] or 'No notes logged.'}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

# ----------------------------------------------------------------------------
# 2. Phase Transition History (v_phase_timeline)
# ----------------------------------------------------------------------------
st.markdown("### 📜 Mission Phase History (<code>v_phase_timeline</code>)", unsafe_allow_html=True)
timeline_df = run_query("SELECT * FROM v_phase_timeline ORDER BY transition_time ASC;")

if not timeline_df.empty:
    display_timeline = timeline_df[[
        "transition_id", "from_phase_name", "to_phase_name", "triggered_by",
        "triggered_by_user", "reason", "transition_time"
    ]].rename(columns={
        "transition_id": "ID",
        "from_phase_name": "From Phase",
        "to_phase_name": "To Phase",
        "triggered_by": "Trigger",
        "triggered_by_user": "User",
        "reason": "Rationale / Event",
        "transition_time": "Timestamp"
    })
    st.dataframe(display_timeline, use_container_width=True, hide_index=True)
else:
    st.info("No phase transitions recorded yet.")

st.markdown("---")

# ----------------------------------------------------------------------------
# 3. Operational Forms (Role Protected)
# ----------------------------------------------------------------------------
col_form1, col_form2 = st.columns(2)

# Form A: Log / Update Deployment Event
with col_form1:
    st.markdown("#### ⚙️ Record Deployment Event")
    st.caption("Triggers automated state progression to COMMISSIONING upon CONFIRMED status.")
    
    with st.form("form_deployment"):
        method = st.selectbox("Release Sensing Method", ["SWITCH", "TIMER", "BEACON"])
        new_status = st.selectbox("Deployment Status", ["CONFIRMED", "PARTIAL", "FAILED", "PENDING"])
        notes = st.text_area("Engineering Notes", value="Ground station telemetry confirms mechanical switch deployment.")
        submit_deploy = st.form_submit_button("Update Deployment Event")

    if submit_deploy:
        if check_permission("OPERATOR", "update deployment events"):
            # Check if an existing event exists
            existing = run_query("SELECT event_id FROM deployment_event WHERE spacecraft_id = 2;")
            if existing.empty:
                sql = """
                    INSERT INTO deployment_event (spacecraft_id, method, status, signal_at, notes)
                    VALUES (2, %s, %s, now(), %s);
                """
                params = (method, new_status, notes)
            else:
                sql = """
                    UPDATE deployment_event
                    SET method = %s, status = %s, signal_at = now(), notes = %s
                    WHERE spacecraft_id = 2;
                """
                params = (method, new_status, notes)

            success, msg = run_write(
                sql, params,
                success_message=f"Deployment event updated to {new_status}!"
            )
            if success:
                st.rerun()

# Form B: Request Phase Transition (Demonstrates Guard Trigger)
with col_form2:
    st.markdown("#### 🔀 Request Mission Phase Transition")
    st.caption("Evaluated live by <code>trg_phase_guard</code> against flight rules.", unsafe_allow_html=True)

    phases_df = run_query("SELECT phase_id, name FROM mission_phase ORDER BY seq_no;")
    phase_dict = dict(zip(phases_df["name"], phases_df["phase_id"])) if not phases_df.empty else {}

    curr_phase_row = run_query("""
        SELECT mp.phase_id, mp.name 
        FROM spacecraft sc 
        JOIN mission_phase mp ON sc.current_phase_id = mp.phase_id 
        WHERE sc.name = 'SomaiyaSat';
    """)
    current_phase_id = curr_phase_row.iloc[0]["phase_id"] if not curr_phase_row.empty else 1
    current_phase_name = curr_phase_row.iloc[0]["name"] if not curr_phase_row.empty else "UNKNOWN"

    st.markdown(f"Current Spacecraft Phase: **`{current_phase_name}`**")

    with st.form("form_transition"):
        target_phase_name = st.selectbox("Target Mission Phase", options=list(phase_dict.keys()))
        reason = st.text_area("Flight Rationale", value="Operator commanded flight phase transition.")
        submit_trans = st.form_submit_button("Command Phase Transition")

    if submit_trans:
        if check_permission("OPERATOR", "request phase transitions"):
            target_phase_id = phase_dict[target_phase_name]
            user_id = user.get("user_id", 1)

            sql = """
                INSERT INTO phase_transition (spacecraft_id, from_phase_id, to_phase_id, reason, triggered_by, user_id, at)
                VALUES (2, %s, %s, %s, 'OPERATOR', %s, now());
            """
            params = (current_phase_id, target_phase_id, reason, user_id)

            success, msg = run_write(sql, params, show_code=True)
            if success:
                st.success(f"Phase successfully transitioned from **{current_phase_name}** to **{target_phase_name}**!")
                st.rerun()
            else:
                st.error(f"🛑 **Guard Trigger Rejection (`trg_phase_guard`)**:\n\n{msg}")
                st.info("💡 **Mission Rule:** Moves must follow `LAUNCH -> DEPLOYED -> COMMISSIONING -> NOMINAL`. Moving to `NOMINAL` requires confirmed deployment AND at least one received TT&C pass.")
