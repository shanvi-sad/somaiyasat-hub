"""
SomaiyaSat Telemetry Hub - app/pages/3_Downlink_Queue.py
Downlink payload queue management, backlog analytics, and atomic
multi-table ingestion of SSTV images and voice clip subtypes.
"""

import streamlit as st
import pandas as pd
from common import (
    init_page, render_sidebar, run_query, format_bytes, check_permission, get_conn
)

init_page("Downlink Queue", "📦")
user = render_sidebar()

st.title("📦 Downlink Payload Queue")
st.markdown("Manage data items awaiting transmission passes and ingest telemetry, SSTV images, and digital voice payloads.")

# ----------------------------------------------------------------------------
# 1. Queue Metrics & Breakdown
# ----------------------------------------------------------------------------
# Robust query for status breakdown – handles empty result sets
try:
    status_breakdown = run_query("""
        SELECT status, COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS total_bytes
        FROM data_item
        GROUP BY status
        ORDER BY count DESC;
    """)
except Exception as e:
    st.error(f"Failed to fetch queue metrics: {e}")
    status_breakdown = pd.DataFrame(columns=["status", "count", "total_bytes"])
# Ensure expected columns exist even if query returned no rows
for col in ["status", "count", "total_bytes"]:
    if col not in status_breakdown.columns:
        status_breakdown[col] = []

mode_breakdown = run_query("""
    SELECT cm.code as mode_code, count(di.item_id) as count, COALESCE(SUM(di.size_bytes), 0) as total_bytes
    FROM comm_mode cm
    LEFT JOIN data_item di ON cm.mode_id = di.mode_id
    GROUP BY cm.code
    ORDER BY cm.code ASC;
""")

c1, c2, c3, c4 = st.columns(4)
queued_row = status_breakdown[status_breakdown["status"] == "QUEUED"]
queued_count = int(queued_row["count"].iloc[0]) if not queued_row.empty else 0
queued_bytes = int(queued_row["total_bytes"].iloc[0]) if not queued_row.empty else 0

with c1:
    st.metric("Pending In Queue", f"{queued_count:,}", "Awaiting Pass")
with c2:
    st.metric("Queue Backlog Volume", format_bytes(queued_bytes))
with c3:
    sent_row = status_breakdown[status_breakdown["status"] == "SENT"]
    sent_count = int(sent_row["count"].iloc[0]) if not sent_row.empty else 0
    st.metric("Packets Sent", f"{sent_count:,}")
with c4:
    total_items = status_breakdown["count"].sum() if not status_breakdown.empty else 0
    st.metric("Total Items Generated", f"{total_items:,}")

st.markdown("<div style='margin-bottom: 20px;'></div>", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# 2. Add New Payload Item (Atomic Transaction with Subtype Ingestion)
# ----------------------------------------------------------------------------
st.markdown("### ➕ Enqueue New Payload Data Item")
st.caption("Demonstrates atomic transaction inserting into <code>data_item</code> and specialization subtype tables (<code>sstv_image</code> or <code>voice_clip</code>).", unsafe_allow_html=True)

modes_df = run_query("SELECT mode_id, code, base_priority, bitrate_bps FROM comm_mode ORDER BY base_priority ASC;")
mode_map = {row["code"]: row for _, row in modes_df.iterrows()} if not modes_df.empty else {}

with st.expander("Expand Payload Ingestion Form", expanded=True):
    with st.form("form_add_item"):
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            selected_mode = st.selectbox("Communication Mode", options=list(mode_map.keys()))
            urgency = st.slider("Mission Urgency (1 = Routine, 5 = Critical)", min_value=1, max_value=5, value=3)
        with col_m2:
            description = st.text_input("Packet Description", value=f"Live operator dispatch payload: {selected_mode}")
            
            # Suggest realistic size based on mode
            default_sizes = {"TTC": 150, "SSTV": 35000, "CODEC2": 6500, "M17": 8000}
            size_bytes = st.number_input(
                "Payload Size (Bytes)",
                min_value=1,
                max_value=1000000,
                value=default_sizes.get(selected_mode, 150),
                step=10
            )

        # Subtype specific fields
        st.markdown("##### Subtype Specialization Attributes")
        if selected_mode == "SSTV":
            sub_c1, sub_c2 = st.columns(2)
            with sub_c1:
                resolution = st.selectbox("Image Resolution", ["320x240", "640x480"])
            with sub_c2:
                sstv_format = st.selectbox("SSTV Transmission Format", ["Robot36", "Scottie1", "Martin1"])
        elif selected_mode in ("CODEC2", "M17"):
            sub_c1, sub_c2 = st.columns(2)
            with sub_c1:
                codec_choice = selected_mode
                st.text_input("Audio Codec", value=codec_choice, disabled=True)
            with sub_c2:
                duration_s = st.number_input("Audio Duration (Seconds)", min_value=0.5, max_value=120.0, value=6.0, step=0.5)

        submit_item = st.form_submit_button("Enqueue Item into Database")

    if submit_item:
        if check_permission("OPERATOR", "enqueue new payload data items"):
            mode_id = mode_map[selected_mode]["mode_id"]
            
            # Execute atomic insert in ONE transaction
            conn = get_conn()
            executed_sqls = []
            try:
                with conn:
                    with conn.cursor() as cur:
                        # 1. Supertype Insert: data_item
                        insert_super_sql = """
                            INSERT INTO data_item (
                                spacecraft_id, mode_id, size_bytes, urgency, description, status, created_at
                            ) VALUES (2, %s, %s, %s, %s, 'QUEUED', now())
                            RETURNING item_id;
                        """
                        cur.execute(insert_super_sql, (mode_id, size_bytes, urgency, description))
                        new_item_id = cur.fetchone()[0]
                        executed_sqls.append(f"-- Supertype Insert\nINSERT INTO data_item VALUES (item_id={new_item_id}, mode_id={mode_id}, size={size_bytes}, urgency={urgency});")

                        # 2. Subtype Insert
                        if selected_mode == "SSTV":
                            insert_sub_sql = """
                                INSERT INTO sstv_image (item_id, resolution, sstv_format, captured_at)
                                VALUES (%s, %s, %s, now());
                            """
                            cur.execute(insert_sub_sql, (new_item_id, resolution, sstv_format))
                            executed_sqls.append(f"-- Specialization Subtype: sstv_image\nINSERT INTO sstv_image VALUES (item_id={new_item_id}, resolution='{resolution}', format='{sstv_format}');")
                        elif selected_mode in ("CODEC2", "M17"):
                            insert_sub_sql = """
                                INSERT INTO voice_clip (item_id, codec, duration_s)
                                VALUES (%s, %s, %s);
                            """
                            cur.execute(insert_sub_sql, (new_item_id, selected_mode, duration_s))
                            executed_sqls.append(f"-- Specialization Subtype: voice_clip\nINSERT INTO voice_clip VALUES (item_id={new_item_id}, codec='{selected_mode}', duration={duration_s}s);")

                st.success(f"Successfully enqueued payload item #{new_item_id} ({selected_mode}, {format_bytes(size_bytes)}) in ONE atomic transaction!")
                with st.expander("Inspect Executed Atomic SQL Transaction", expanded=True):
                    st.code("\n".join(executed_sqls), language="sql")
                st.rerun()
            except Exception as e:
                st.error(f"Transaction aborted: {e}")
            finally:
                conn.close()

st.markdown("---")

# ----------------------------------------------------------------------------
# 3. Active Queue Table
# ----------------------------------------------------------------------------
st.markdown("### 📋 Active Pending Queue (<code>status = 'QUEUED'</code>)", unsafe_allow_html=True)

filter_mode = st.radio("Filter Queue by Mode", ["ALL", "TTC", "SSTV", "CODEC2", "M17"], horizontal=True)
mode_clause = ""
q_params = []
if filter_mode != "ALL":
    mode_clause = "AND cm.code = %s"
    q_params.append(filter_mode)

queued_df = run_query(f"""
    SELECT
        di.item_id,
        cm.code as mode_code,
        cm.base_priority,
        di.size_bytes,
        di.urgency,
        di.description,
        di.predicted_success,
        di.model_version,
        di.created_at,
        si.resolution,
        si.sstv_format,
        vc.duration_s
    FROM data_item di
    JOIN comm_mode cm ON di.mode_id = cm.mode_id
    LEFT JOIN sstv_image si ON di.item_id = si.item_id
    LEFT JOIN voice_clip vc ON di.item_id = vc.item_id
    WHERE di.status = 'QUEUED'
    {mode_clause}
    ORDER BY di.urgency DESC, di.created_at ASC
    LIMIT 100;
""", tuple(q_params) if q_params else None)

if not queued_df.empty:
    display_q = queued_df.copy()
    display_q["Payload Size"] = display_q["size_bytes"].apply(format_bytes)
    display_q["ML Predicted Success"] = display_q["predicted_success"].apply(
        lambda p: f"{float(p)*100:.1f}%" if pd.notna(p) else "Rule-based Fallback"
    )
    
    table_q = display_q[[
        "item_id", "mode_code", "base_priority", "Payload Size", "urgency",
        "description", "ML Predicted Success", "model_version", "created_at"
    ]].rename(columns={
        "item_id": "Item ID",
        "mode_code": "Mode",
        "base_priority": "Priority",
        "urgency": "Urgency",
        "description": "Description",
        "model_version": "Model",
        "created_at": "Queued At"
    })
    st.dataframe(table_q, use_container_width=True, hide_index=True)
else:
    st.info("The active transmission queue is currently empty for the selected filter.")
