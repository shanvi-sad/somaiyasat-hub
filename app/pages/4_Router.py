"""
SomaiyaSat Telemetry Hub - app/pages/4_Router.py
Autonomous Downlink Router Control Center: Window planning, hybrid model/fallback
scheduling (fn_schedule_window), pass simulation (fn_complete_window),
and operator override authority with inviolable TT&C priority protection.
"""

import streamlit as st
import pandas as pd
from datetime import datetime
from common import (
    init_page, render_sidebar, run_query, run_write, format_bytes,
    check_permission, get_current_user, can_operate
)

init_page("Autonomous Router", "🧠")
user = render_sidebar()

st.title("🧠 Autonomous Downlink Router")
st.markdown("Autonomous on-board telemetry scheduling, priority optimization, pass execution, and operator override control.")

# ----------------------------------------------------------------------------
# 1. Create a New PLANNED Downlink Window Form
# ----------------------------------------------------------------------------
with st.expander("➕ Plan New Ground Station Downlink Window", expanded=False):
    with st.form("form_create_window"):
        st.markdown("##### Contact Pass Parameters")
        c1, c2, c3 = st.columns(3)
        with c1:
            duration_s = st.number_input("Pass Duration (Seconds)", min_value=60, max_value=1800, value=600, step=30)
            capacity_kb = st.number_input("Pass Capacity (KB)", min_value=10, max_value=500, value=90, step=10)
            capacity_bytes = int(capacity_kb * 1024)
        with c2:
            energy_budget = st.number_input("Energy Budget (mWh)", min_value=50.0, max_value=2000.0, value=500.0, step=50.0)
            expected_snr = st.number_input("Expected RF SNR (dB)", min_value=-5.0, max_value=30.0, value=14.5, step=0.5)
        with c3:
            battery_v = st.number_input("Estimated Pass Battery (V)", min_value=3.0, max_value=4.3, value=3.85, step=0.05)
            pass_start = datetime.now()

        submit_win = st.form_submit_button("Create PLANNED Window")

    if submit_win:
        if check_permission("OPERATOR", "create new downlink windows"):
            sql = """
                INSERT INTO downlink_window (
                    spacecraft_id, start_time, duration_s, capacity_bytes,
                    energy_budget_mwh, expected_snr_db, battery_v, status
                ) VALUES (2, %s, %s, %s, %s, %s, %s, 'PLANNED')
                RETURNING window_id;
            """
            params = (pass_start, duration_s, capacity_bytes, energy_budget, expected_snr, battery_v)
            res = run_query(sql, params)
            if not res.empty:
                new_win_id = int(res.iloc[0]["window_id"])
                st.session_state["active_window_id"] = new_win_id
                st.success(f"Downlink window #{new_win_id} created successfully with status PLANNED!")
                st.rerun()

# ----------------------------------------------------------------------------
# 2. Window Selector & Status Card
# ----------------------------------------------------------------------------
st.markdown("### 🎯 Active Downlink Window Selection")

# Query windows prioritized by PLANNED, then SCHEDULED, then recent COMPLETED
win_list = run_query("""
    SELECT window_id, start_time, capacity_bytes, status, expected_snr_db, battery_v
    FROM downlink_window
    ORDER BY 
        CASE status WHEN 'PLANNED' THEN 1 WHEN 'SCHEDULED' THEN 2 ELSE 3 END ASC,
        window_id DESC
    LIMIT 60;
""")

if not win_list.empty:
    win_options = {}
    for _, w in win_list.iterrows():
        label = f"Pass #{w['window_id']} | Status: {w['status']} | Cap: {format_bytes(w['capacity_bytes'])} | SNR: {w['expected_snr_db']} dB ({str(w['start_time'])[:16]})"
        win_options[label] = int(w["window_id"])

    # Determine default selection
    default_id = st.session_state.get("active_window_id", list(win_options.values())[0])
    default_label = next((k for k, v in win_options.items() if v == default_id), list(win_options.keys())[0])

    selected_label = st.selectbox("Select Target Pass for Autonomous Scheduling", list(win_options.keys()), index=list(win_options.keys()).index(default_label))
    active_win_id = win_options[selected_label]
    st.session_state["active_window_id"] = active_win_id

    # Fetch active window details
    win_curr = run_query("SELECT * FROM downlink_window WHERE window_id = %s;", (active_win_id,)).iloc[0]
    win_status = win_curr["status"]

    # Display status card
    status_badge_colors = {"PLANNED": "#38bdf8", "SCHEDULED": "#f59e0b", "COMPLETED": "#10b981"}
    badge_col = status_badge_colors.get(win_status, "#94a3b8")

    st.markdown(
        f"""
        <div class="glass-panel" style="border-left: 4px solid {badge_col}; margin-top: 10px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h3 style="margin: 0; color: #f8fafc;">Pass #{active_win_id} &mdash; <span style="color: {badge_col};">{win_status}</span></h3>
                    <p style="margin: 4px 0 0 0; color: #94a3b8; font-size: 0.9rem;">
                        Start: <code>{win_curr['start_time']}</code> | Duration: <strong>{win_curr['duration_s']}s</strong>
                    </p>
                </div>
                <div style="text-align: right; font-family: 'JetBrains Mono', monospace; font-size: 0.9rem;">
                    Capacity: <strong>{format_bytes(win_curr['capacity_bytes'])}</strong><br>
                    Energy: <strong>{win_curr['energy_budget_mwh']} mWh</strong> | SNR: <strong>{win_curr['expected_snr_db']} dB</strong>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # ----------------------------------------------------------------------------
    # 3. Action Controls: Run Scheduler & Complete Window
    # ----------------------------------------------------------------------------
    st.markdown("#### ⚡ Autonomous Pipeline Execution")
    col_act1, col_act2 = st.columns(2)

    with col_act1:
        st.markdown("**Step 1: Execute Autonomous Scheduler**")
        st.caption("Packs queued items into budget using hybrid model/fallback priority. Guaranteed TT&C delivery.")
        btn_schedule = st.button("🚀 Run Scheduler (fn_schedule_window)", disabled=(win_status != "PLANNED"))

    with col_act2:
        st.markdown("**Step 2: Simulate Pass Downlink**")
        st.caption("Simulates RF link reception based on pass SNR and assigns HAM volunteer credits.")
        btn_complete = st.button("📡 Complete Window (fn_complete_window)", disabled=(win_status != "SCHEDULED"))

    # Execution 1: Schedule Window
    if btn_schedule:
        if check_permission("OPERATOR", "execute the autonomous downlink scheduler"):
            sql_sched = "SELECT fn_schedule_window(%s);"
            with st.expander("Executed Procedure Call", expanded=True):
                st.code(f"SELECT fn_schedule_window({active_win_id});", language="sql")

            success, msg = run_write(sql_sched, (active_win_id,), show_code=False)
            if success:
                st.success(f"Pass #{active_win_id} successfully scheduled! Decisions written for all queued items.")
                st.rerun()
            else:
                if "Safety check failed" in str(msg):
                    st.error(f"🚨 **MANDATORY SAFETY CHECK ROLLBACK TRIGGERED**:\n\n{msg}\n\n*Transaction rolled back: A queued TT&C item could not fit into the window capacity!*")
                else:
                    st.error(f"Scheduling failed: {msg}")

    # Execution 2: Complete Window
    if btn_complete:
        if check_permission("OPERATOR", "complete the downlink window pass"):
            sql_comp = "SELECT fn_complete_window(%s);"
            with st.expander("Executed Procedure Call", expanded=True):
                st.code(f"SELECT fn_complete_window({active_win_id});", language="sql")

            success, msg = run_write(sql_comp, (active_win_id,), show_code=False)
            if success:
                st.success(f"Pass #{active_win_id} completed! Link simulation executed and transmissions logged.")
                st.rerun()
            else:
                st.error(f"Pass completion error: {msg}")

    st.markdown("---")

    # ----------------------------------------------------------------------------
    # 4. Router Decisions Table for Active Window
    # ----------------------------------------------------------------------------
    st.markdown(f"### 📋 Decisions Log for Pass #{active_win_id}")
    
    decisions_df = run_query("""
        SELECT
            rd.decision_id,
            di.item_id,
            cm.code as mode,
            di.size_bytes,
            di.urgency,
            rd.action as auto_action,
            rd.overridden,
            rd.override_action,
            COALESCE(rd.override_action, rd.action) as effective_action,
            rd.score,
            rd.predicted_success,
            rd.fallback_used,
            rd.rationale,
            u.username as overridden_by_user,
            rd.override_reason
        FROM router_decision rd
        JOIN data_item di ON rd.item_id = di.item_id
        JOIN comm_mode cm ON di.mode_id = cm.mode_id
        LEFT JOIN app_user u ON rd.override_by = u.user_id
        WHERE rd.window_id = %s
        ORDER BY rd.score DESC;
    """, (active_win_id,))

    if not decisions_df.empty:
        display_dec = decisions_df.copy()
        display_dec["Size"] = display_dec["size_bytes"].apply(format_bytes)
        display_dec["ML Success"] = display_dec["predicted_success"].apply(
            lambda p: f"{float(p)*100:.1f}%" if pd.notna(p) else "Rule-based"
        )
        display_dec["Decision Mode"] = display_dec["fallback_used"].apply(
            lambda f: "Fallback Rule" if f else "ML Model"
        )
        display_dec["Override"] = display_dec.apply(
            lambda r: f"YES ({r['override_action']} by {r['overridden_by_user']})" if r["overridden"] else "No",
            axis=1
        )

        st.dataframe(
            display_dec[[
                "decision_id", "item_id", "mode", "Size", "urgency", "effective_action",
                "score", "ML Success", "Decision Mode", "Override", "rationale"
            ]].rename(columns={
                "decision_id": "Dec ID",
                "item_id": "Item ID",
                "effective_action": "Action",
                "rationale": "Scheduling Rationale"
            }),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No router decisions exist for this window yet. Click 'Run Scheduler' above to schedule pending items.")

    # ----------------------------------------------------------------------------
    # 5. Ground Operator Override Authority Form
    # ----------------------------------------------------------------------------
    if win_status == "SCHEDULED":
        st.markdown("---")
        st.markdown("### 🛠️ Ground Operator Decision Override Authority")
        st.caption("Authorized flight operators can modify scheduling decisions prior to contact completion. <strong>TT&C items can NEVER be downgraded from SEND.</strong>", unsafe_allow_html=True)

        with st.form("form_override"):
            c_ov1, c_ov2, c_ov3 = st.columns([2, 1, 3])
            with c_ov1:
                # Build candidate decision choices
                dec_choices = {}
                for _, r in decisions_df.iterrows():
                    dec_choices[f"Dec #{r['decision_id']} - Item #{r['item_id']} ({r['mode']}) [Current: {r['effective_action']}]"] = (r['decision_id'], r['mode'])

                selected_dec_label = st.selectbox("Select Decision to Override", list(dec_choices.keys()))
                target_dec_id, target_mode = dec_choices[selected_dec_label]

            with c_ov2:
                new_override_action = st.selectbox("New Action", ["SEND", "DEFER", "DROP"])

            with c_ov3:
                override_reason = st.text_input("Mandatory Override Rationale", value="Operator payload reprioritization for ground test window.")

            submit_override = st.form_submit_button("Apply Override Decision")

        if submit_override:
            if check_permission("OPERATOR", "override router decisions"):
                op_user_id = user.get("user_id", 1)
                override_sql = "SELECT fn_override_decision(%s, %s, %s, %s);"
                params = (target_dec_id, op_user_id, new_override_action, override_reason)

                with st.expander("Executed Procedure Call", expanded=True):
                    st.code(f"SELECT fn_override_decision({target_dec_id}, {op_user_id}, '{new_override_action}', '{override_reason}');", language="sql")

                success, msg = run_write(override_sql, params, show_code=False)
                if success:
                    st.success(f"Decision #{target_dec_id} successfully overridden to {new_override_action}!")
                    st.rerun()
                else:
                    if "Safety Violation" in str(msg):
                        st.error(f"🛑 **INVIOLABLE SAFETY CONSTRAINT ENFORCED**:\n\n{msg}")
                        st.info("💡 **Flight Rule:** TT&C housekeeping telemetry must NEVER be dropped or deferred under any circumstances.")
                    else:
                        st.error(f"Override rejected: {msg}")

else:
    st.info("No downlink windows available. Use the form above to plan a new window.")
