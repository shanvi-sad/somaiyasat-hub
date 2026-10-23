"""
SomaiyaSat Telemetry Hub - Alert Management Console
Real-time monitoring of mission exception events and sensor limit violations.
Provides pessimistic row-level locking (SELECT ... FOR UPDATE) for concurrent operator triage.
"""

from datetime import datetime
import streamlit as st
import pandas as pd

from app.common import (
    init_page,
    render_sidebar,
    run_query,
    can_operate,
    get_current_user
)
from scripts.db import get_conn

init_page("Alerts", "🚨")
user = render_sidebar()

st.title("🚨 Alert Management Console")
st.caption("Exception event monitoring, automated limit breaches, and pessimistic transaction-locked operator acknowledgment.")

# -----------------------------------------------------------------------------
# 1. Top KPI Summary Cards
# -----------------------------------------------------------------------------
kpi_df = run_query("""
    SELECT
        COUNT(*) AS total_count,
        COUNT(CASE WHEN status = 'OPEN' THEN 1 END) AS open_count,
        COUNT(CASE WHEN status = 'OPEN' AND severity = 'CRITICAL' THEN 1 END) AS crit_open_count,
        COUNT(CASE WHEN status = 'ACK' THEN 1 END) AS ack_count
    FROM alert;
""")

if not kpi_df.empty:
    kpi = kpi_df.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open Alerts", f"{kpi['open_count']:,}", delta=f"{kpi['crit_open_count']} Critical" if kpi['crit_open_count'] > 0 else "0 Critical", delta_color="inverse")
    c2.metric("Critical Open", f"{kpi['crit_open_count']:,}")
    c3.metric("Acknowledged", f"{kpi['ack_count']:,}")
    c4.metric("Lifetime Alerts", f"{kpi['total_count']:,}")

# -----------------------------------------------------------------------------
# 2. Operator Triage Action: Pessimistic Locking Acknowledgment
# -----------------------------------------------------------------------------
st.markdown("---")
st.markdown("### 🛡️ Operator Triage: Acknowledge Alert")
st.caption("Demonstrates DBMS Concurrency Control: row-level locking via `SELECT ... FOR UPDATE` ensures idempotent atomic triage without race conditions.")

open_alerts_df = run_query("""
    SELECT
        a.alert_id,
        a.alert_type,
        a.severity,
        a.message,
        a.created_at,
        tr.reading_id,
        s.name AS sensor_name
    FROM alert a
    LEFT JOIN telemetry_reading tr ON a.reading_id = tr.reading_id
    LEFT JOIN sensor s ON tr.sensor_id = s.sensor_id
    WHERE a.status = 'OPEN'
    ORDER BY (CASE WHEN a.severity = 'CRITICAL' THEN 1 ELSE 2 END), a.created_at DESC;
""")

if open_alerts_df.empty:
    st.success("✅ Clean board: No open alerts pending acknowledgment.")
else:
    if not can_operate():
        st.warning("🔒 **Read-Only Mode**: Log in as `OPERATOR` or `ADMIN` in the sidebar to acknowledge open alerts.")
    else:
        # Build alert options for selection
        alert_options = {}
        for _, row in open_alerts_df.iterrows():
            badge = "🚨 [CRITICAL]" if row["severity"] == "CRITICAL" else "⚠️ [WARNING]"
            sensor_info = f" | {row['sensor_name']}" if row['sensor_name'] else ""
            label = f"{badge} #{row['alert_id']} ({row['alert_type']}{sensor_info}) - {row['message'][:70]}..."
            alert_options[label] = int(row["alert_id"])

        col_sel, col_btn = st.columns([3, 1])
        selected_label = col_sel.selectbox("Select Open Alert to Acknowledge", options=list(alert_options.keys()))
        target_alert_id = alert_options[selected_label]

        with col_btn:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            ack_btn = st.button("🔒 Acknowledge Alert (FOR UPDATE)", type="primary", use_container_width=True)

        if ack_btn:
            # Execute pessimistic transaction with SELECT ... FOR UPDATE
            sql_lock = "SELECT alert_id, status FROM alert WHERE alert_id = %s FOR UPDATE;"
            sql_update = """
                UPDATE alert
                SET status = 'ACK',
                    ack_by = %s,
                    ack_at = NOW()
                WHERE alert_id = %s;
            """
            
            with st.expander("Executed Locking SQL Commands", expanded=True):
                st.code(
                    f"BEGIN;\n"
                    f"-- Step 1: Acquire exclusive row-level pessimistic lock\n"
                    f"{sql_lock} -- Parameter: ({target_alert_id},)\n\n"
                    f"-- Step 2: Atomic update under row lock\n"
                    f"{sql_update.strip()} -- Parameters: ({user['user_id']}, {target_alert_id})\n"
                    f"COMMIT;",
                    language="sql"
                )

            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        # 1. Row-level exclusive lock
                        cur.execute(sql_lock, (target_alert_id,))
                        locked_row = cur.fetchone()
                        
                        if not locked_row:
                            st.error(f"Alert #{target_alert_id} not found.")
                        elif locked_row["status"] != "OPEN":
                            st.warning(f"Notice: Alert #{target_alert_id} is already `{locked_row['status']}`.")
                        else:
                            # 2. Update status
                            cur.execute(sql_update, (user["user_id"], target_alert_id))
                            conn.commit()
                            st.success(f"✅ Alert #{target_alert_id} successfully locked and acknowledged by {user['username']}!")
                            st.rerun()

            except Exception as e:
                st.error(f"Pessimistic lock transaction failed: {e}")

# -----------------------------------------------------------------------------
# 3. Filterable Alert Inventory
# -----------------------------------------------------------------------------
st.markdown("---")
st.markdown("### 📋 Alert Catalog")

tab_open, tab_all, tab_ack = st.tabs(["🔥 Active Open Alerts", "📜 All Alert History", "✅ Acknowledged Log"])

with tab_open:
    if not open_alerts_df.empty:
        st.dataframe(
            open_alerts_df,
            column_config={
                "alert_id": st.column_config.NumberColumn("Alert ID", width=80),
                "created_at": st.column_config.DatetimeColumn("Timestamp", format="YYYY-MM-DD HH:mm:ss"),
                "severity": st.column_config.TextColumn("Severity", width=100),
                "alert_type": st.column_config.TextColumn("Type", width=150),
                "sensor_name": st.column_config.TextColumn("Sensor", width=140),
                "reading_id": st.column_config.NumberColumn("Reading ID", width=90),
                "message": st.column_config.TextColumn("Diagnostics Message", width="large")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No active open alerts.")

with tab_all:
    col_f1, col_f2 = st.columns(2)
    sev_filter = col_f1.selectbox("Filter Severity", options=["ALL", "CRITICAL", "WARNING", "INFO"], index=0)
    type_filter = col_f2.selectbox("Filter Type", options=["ALL", "TELEMETRY_LIMIT", "CRITICAL_DATA_MISSED", "DEPLOYMENT_ISSUE", "SAFE_MODE"], index=0)

    base_query = """
        SELECT
            a.alert_id,
            a.created_at,
            a.severity,
            a.alert_type,
            a.status,
            a.message,
            u.username AS ack_by_user,
            a.ack_at
        FROM alert a
        LEFT JOIN app_user u ON a.ack_by = u.user_id
        WHERE 1=1
    """
    params = []
    if sev_filter != "ALL":
        base_query += " AND a.severity = %s"
        params.append(sev_filter)
    if type_filter != "ALL":
        base_query += " AND a.alert_type = %s"
        params.append(type_filter)

    base_query += " ORDER BY a.created_at DESC LIMIT 50;"

    all_alerts_df = run_query(base_query, tuple(params) if params else None)
    
    st.dataframe(
        all_alerts_df,
        column_config={
            "alert_id": st.column_config.NumberColumn("ID", width=70),
            "created_at": st.column_config.DatetimeColumn("Created At", format="YYYY-MM-DD HH:mm:ss"),
            "severity": st.column_config.TextColumn("Severity", width=90),
            "status": st.column_config.TextColumn("Status", width=80),
            "alert_type": st.column_config.TextColumn("Type", width=140),
            "ack_by_user": st.column_config.TextColumn("Triage Operator", width=120),
            "ack_at": st.column_config.DatetimeColumn("Triage Time", format="YYYY-MM-DD HH:mm:ss"),
            "message": st.column_config.TextColumn("Diagnostic Message", width="large")
        },
        use_container_width=True,
        hide_index=True
    )

with tab_ack:
    ack_history_df = run_query("""
        SELECT
            a.alert_id,
            a.alert_type,
            a.severity,
            a.message,
            u.username AS acknowledged_by,
            u.role AS user_role,
            a.created_at,
            a.ack_at
        FROM alert a
        JOIN app_user u ON a.ack_by = u.user_id
        WHERE a.status = 'ACK'
        ORDER BY a.ack_at DESC
        LIMIT 50;
    """)
    if not ack_history_df.empty:
        st.dataframe(
            ack_history_df,
            column_config={
                "alert_id": st.column_config.NumberColumn("ID", width=70),
                "acknowledged_by": st.column_config.TextColumn("Operator", width=120),
                "user_role": st.column_config.TextColumn("Role", width=100),
                "ack_at": st.column_config.DatetimeColumn("Ack Timestamp", format="YYYY-MM-DD HH:mm:ss"),
                "created_at": st.column_config.DatetimeColumn("Raised At", format="YYYY-MM-DD HH:mm:ss"),
                "severity": st.column_config.TextColumn("Severity", width=90),
                "message": st.column_config.TextColumn("Message", width="large")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No acknowledged alerts recorded.")
