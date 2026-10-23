"""
SomaiyaSat Telemetry Hub - app/common.py
Shared UI components, role-based access control, database wrappers,
and dark space-mission theme styling for the Streamlit application.
"""

import sys
import os
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.db import get_conn, query, execute, get_db_config


SPACE_THEME_CSS = """
<style>
/* Space Mission Dark Theme */
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Outfit:wght@300;400;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Outfit', sans-serif;
}

code, pre, .stCodeBlock {
    font-family: 'JetBrains Mono', monospace !important;
}

/* Background & Main Container */
.stApp {
    background: radial-gradient(circle at 15% 15%, #111827 0%, #070a13 50%, #030509 100%);
    color: #e2e8f0;
}

/* Top Header Bar */
header[data-testid="stHeader"] {
    background: rgba(11, 15, 25, 0.8) !important;
    backdrop-filter: blur(10px);
}

/* Sidebar Styling */
section[data-testid="stSidebar"] {
    background: #090d16 !important;
    border-right: 1px solid rgba(255, 255, 255, 0.08);
}

/* Metric KPI Cards */
div[data-testid="stMetric"] {
    background: rgba(17, 24, 39, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 14px 18px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35);
    backdrop-filter: blur(8px);
    transition: transform 0.2s ease, border-color 0.2s ease;
}

div[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    border-color: rgba(56, 189, 248, 0.4);
}

div[data-testid="stMetricLabel"] {
    color: #94a3b8 !important;
    font-size: 0.82rem !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}

div[data-testid="stMetricValue"] {
    color: #f8fafc !important;
    font-weight: 700;
    font-family: 'JetBrains Mono', monospace;
}

/* Mission Phase Badges */
.phase-badge {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 0.82rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-family: 'JetBrains Mono', monospace;
}
.phase-LAUNCH {
    background: rgba(139, 92, 246, 0.2);
    color: #c4b5fd;
    border: 1px solid #8b5cf6;
}
.phase-DEPLOYED {
    background: rgba(14, 165, 233, 0.2);
    color: #7dd3fc;
    border: 1px solid #0ea5e9;
}
.phase-COMMISSIONING {
    background: rgba(245, 158, 11, 0.2);
    color: #fcd34d;
    border: 1px solid #f59e0b;
}
.phase-NOMINAL {
    background: rgba(16, 185, 129, 0.2);
    color: #6ee7b7;
    border: 1px solid #10b981;
    box-shadow: 0 0 12px rgba(16, 185, 129, 0.3);
}
.phase-SAFE_MODE {
    background: rgba(239, 68, 68, 0.25);
    color: #fca5a5;
    border: 1px solid #ef4444;
    box-shadow: 0 0 15px rgba(239, 68, 68, 0.5);
    animation: pulse-red 2s infinite;
}

@keyframes pulse-red {
    0%, 100% { box-shadow: 0 0 10px rgba(239, 68, 68, 0.4); }
    50% { box-shadow: 0 0 22px rgba(239, 68, 68, 0.8); }
}

/* Custom Glass Cards */
.glass-panel {
    background: rgba(15, 23, 42, 0.6);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 18px;
    margin-bottom: 18px;
    backdrop-filter: blur(12px);
}

/* Streamlit Buttons */
.stButton > button {
    background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
    color: #f8fafc;
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 8px;
    font-weight: 600;
    transition: all 0.2s ease;
}

.stButton > button:hover {
    background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
    border-color: #38bdf8;
    color: white;
    box-shadow: 0 0 14px rgba(37, 99, 235, 0.5);
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    background-color: transparent;
    border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    gap: 8px;
}
.stTabs [data-baseweb="tab"] {
    background-color: transparent;
    border-radius: 6px 6px 0 0;
    color: #94a3b8;
    padding: 8px 16px;
    font-weight: 600;
}
.stTabs [aria-selected="true"] {
    color: #38bdf8 !important;
    border-bottom: 2px solid #38bdf8 !important;
}

/* Dataframe Styling */
div[data-testid="stDataFrame"] {
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    overflow: hidden;
}
</style>
"""


def init_page(page_title="SomaiyaSat Hub", page_icon="🛰️"):
    """Initialize page configuration and apply custom dark space mission theme."""
    st.set_page_config(
        page_title=f"SomaiyaSat | {page_title}",
        page_icon=page_icon,
        layout="wide",
        initial_sidebar_state="expanded"
    )
    st.markdown(SPACE_THEME_CSS, unsafe_allow_html=True)


def get_current_phase():
    """Fetch current spacecraft mission phase from database."""
    try:
        rows = query("""
            SELECT mp.name, mp.seq_no, mp.tt_c_only
            FROM spacecraft sc
            JOIN mission_phase mp ON sc.current_phase_id = mp.phase_id
            WHERE sc.name = 'SomaiyaSat';
        """)
        if rows:
            return rows[0]["name"], rows[0]["tt_c_only"]
    except Exception:
        pass
    return "UNKNOWN", False


def render_sidebar():
    """
    Renders the unified mission control sidebar:
      - Spacecraft identification
      - Live phase status badge
      - "Viewing as" user selector backed by app_user table
      - Role-based permissions summary
    """
    phase_name, tt_c_only = get_current_phase()

    with st.sidebar:
        st.markdown(
            """
            <div style="text-align: center; padding: 10px 0 15px 0;">
                <div style="font-size: 2.2rem; filter: drop-shadow(0 0 10px #38bdf8);">🛰️</div>
                <h3 style="margin: 6px 0 2px 0; font-weight: 700; color: #f8fafc; letter-spacing: -0.02em;">SomaiyaSat-1</h3>
                <p style="font-size: 0.78rem; color: #94a3b8; margin: 0; font-family: 'JetBrains Mono', monospace;">1P PocketQube Telemetry Hub</p>
            </div>
            """,
            unsafe_allow_html=True
        )

        # Mission Phase Status Box
        st.markdown(
            f"""
            <div style="background: rgba(15, 23, 42, 0.8); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 12px; margin-bottom: 20px; text-align: center;">
                <div style="font-size: 0.72rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; margin-bottom: 6px;">Live Mission Phase</div>
                <span class="phase-badge phase-{phase_name}">{phase_name}</span>
                {f'<div style="font-size: 0.70rem; color: #ef4444; margin-top: 6px; font-weight: 600;">⚠️ TT&C-ONLY DOWNLINK LOCK</div>' if tt_c_only else ''}
            </div>
            """,
            unsafe_allow_html=True
        )

        st.markdown("---")

        # "Viewing as" User Selection
        st.markdown("<p style='font-size: 0.85rem; font-weight: 600; color: #cbd5e1; margin-bottom: 4px;'>👤 Viewing As (Operator Role)</p>", unsafe_allow_html=True)
        
        users = []
        try:
            users = query("SELECT user_id, username, role, callsign FROM app_user ORDER BY user_id;")
        except Exception as e:
            st.error(f"Failed to fetch users: {e}")

        if users:
            # Build user labels
            user_options = {}
            for u in users:
                cs = f" [{u['callsign']}]" if u["callsign"] else ""
                label = f"{u['username']} ({u['role']}{cs})"
                user_options[label] = u

            # Initialize session state if absent
            if "current_user" not in st.session_state:
                # Default to an operator if available, else first user
                default_user = next((u for u in users if u["role"] == "OPERATOR"), users[0])
                st.session_state["current_user"] = default_user

            current_label = next(
                (lbl for lbl, u in user_options.items() if u["user_id"] == st.session_state["current_user"]["user_id"]),
                list(user_options.keys())[0]
            )

            selected_label = st.selectbox(
                "Select User",
                options=list(user_options.keys()),
                index=list(user_options.keys()).index(current_label),
                label_visibility="collapsed"
            )
            st.session_state["current_user"] = user_options[selected_label]
            user = st.session_state["current_user"]

            # Role capabilities display
            role_colors = {
                "ADMIN": "#a855f7",
                "OPERATOR": "#38bdf8",
                "HAM_VOLUNTEER": "#10b981",
                "VIEWER": "#94a3b8"
            }
            color = role_colors.get(user["role"], "#94a3b8")
            
            st.markdown(
                f"""
                <div style="font-size: 0.78rem; background: rgba(255,255,255,0.03); border-radius: 6px; padding: 8px; margin-top: 8px;">
                    <div>Role: <span style="color: {color}; font-weight: 700;">{user['role']}</span></div>
                    {f"<div>Callsign: <code style='color: #6ee7b7;'>{user['callsign']}</code></div>" if user['callsign'] else ""}
                </div>
                """,
                unsafe_allow_html=True
            )

            # Role permissions guidance
            with st.expander("Role Authority Matrix", expanded=False):
                st.markdown("""
                - **ADMIN**: Unrestricted control, grant authority, database administration.
                - **OPERATOR**: Queue packets, schedule passes, execute overrides, acknowledge alerts.
                - **HAM_VOLUNTEER**: Read-only delivery statistics & pass summaries.
                - **VIEWER**: Read-only public dashboard & alert monitoring.
                """)
        else:
            st.warning("No database users available.")

        st.markdown("---")
        st.markdown(
            """
            <div style="font-size: 0.72rem; color: #64748b; text-align: center;">
                SomaiyaSat Telemetry Hub v1.0<br>
                DBMS Mini Project | SomaiyaPod Mission
            </div>
            """,
            unsafe_allow_html=True
        )

    return st.session_state.get("current_user", {"user_id": 1, "username": "admin", "role": "ADMIN"})


def get_current_user():
    """Retrieve active user dictionary from session state."""
    return st.session_state.get("current_user", {"user_id": 1, "username": "admin", "role": "ADMIN"})


def is_admin():
    """Check if current active user is an ADMIN."""
    return get_current_user().get("role") == "ADMIN"


def can_operate():
    """Check if current active user has operational write privileges (ADMIN or OPERATOR)."""
    return get_current_user().get("role") in ("ADMIN", "OPERATOR")


def check_permission(required="OPERATOR", action_name="perform this operation"):
    """
    Enforces role-based permissions in Python.
    Returns True if permitted, otherwise displays warning and returns False.
    """
    user = get_current_user()
    role = user.get("role", "VIEWER")
    
    if required == "ADMIN" and role != "ADMIN":
        st.error(f"⛔ **Permission Denied**: Only **ADMIN** can {action_name}. You are logged in as `{user['username']}` ({role}).")
        return False
        
    if required == "OPERATOR" and role not in ("ADMIN", "OPERATOR"):
        st.error(f"⛔ **Permission Denied**: Only **OPERATORS** or **ADMINS** can {action_name}. `{user['username']}` ({role}) is read-only.")
        return False

    return True


def run_query(sql, params=None):
    """
    Executes a SELECT query and returns the results as a pandas DataFrame.
    Displays user-friendly error messages if the query fails.
    """
    try:
        rows = query(sql, params)
        return pd.DataFrame(rows)
    except Exception as e:
        st.error(f"Database Query Error: {e}")
        return pd.DataFrame()


def run_write(sql, params=None, show_code=True, success_message=None):
    """
    Executes an INSERT, UPDATE, DELETE, or function call inside a transaction.
    Optionally displays the executed SQL query in an st.code block for lab demonstration.
    
    Returns:
        tuple (bool, any): (Success flag, rowcount or error message)
    """
    if show_code:
        with st.expander("Executed SQL Command", expanded=False):
            formatted_sql = sql.strip()
            if params:
                formatted_sql += f"\n-- Parameters: {params}"
            st.code(formatted_sql, language="sql")

    try:
        rowcount = execute(sql, params)
        if success_message:
            st.success(success_message)
        return True, rowcount
    except Exception as e:
        err_msg = str(e).strip()
        st.error(f"Database Execution Error: {err_msg}")
        return False, err_msg


def format_bytes(num_bytes):
    """Helper to format byte counts into human-readable strings."""
    if num_bytes is None or pd.isna(num_bytes):
        return "0 B"
    num_bytes = float(num_bytes)
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024*1024):.2f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.2f} KB"
    return f"{int(num_bytes)} B"


def get_plotly_layout(title=""):
    """Returns a standardized dark space-mission theme layout for Plotly figures."""
    return dict(
        title=dict(text=title, font=dict(family="Outfit, sans-serif", size=16, color="#f8fafc")),
        paper_bgcolor="rgba(15, 23, 42, 0.4)",
        plot_bgcolor="rgba(15, 23, 42, 0.6)",
        font=dict(family="JetBrains Mono, monospace", color="#cbd5e1", size=11),
        xaxis=dict(
            gridcolor="rgba(255, 255, 255, 0.08)",
            zerolinecolor="rgba(255, 255, 255, 0.15)",
            tickfont=dict(color="#94a3b8")
        ),
        yaxis=dict(
            gridcolor="rgba(255, 255, 255, 0.08)",
            zerolinecolor="rgba(255, 255, 255, 0.15)",
            tickfont=dict(color="#94a3b8")
        ),
        legend=dict(
            bgcolor="rgba(15, 23, 42, 0.7)",
            bordercolor="rgba(255, 255, 255, 0.1)",
            borderwidth=1,
            font=dict(color="#e2e8f0")
        ),
        margin=dict(l=40, r=30, t=50, b=40)
    )
