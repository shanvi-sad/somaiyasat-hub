"""
SomaiyaSat Telemetry Hub - scripts/db.py
Database connection and transactional query helpers using psycopg2.

Configuration priority:
  1. Streamlit secrets  (st.secrets["postgres"]  — used on Streamlit Cloud)
  2. Environment variables per SPEC.md (DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME)
"""

import os
import getpass
import psycopg2
from psycopg2.extras import RealDictCursor


def _try_streamlit_secrets():
    """Attempt to read DB config from Streamlit secrets; return dict or None."""
    try:
        import streamlit as st
        sec = st.secrets["postgres"]
        config = {
            "host":   sec["host"],
            "port":   int(sec.get("port", 5432)),
            "user":   sec["user"],
            "password": sec["password"],
            "dbname": sec.get("dbname", "somaiyasat"),
        }
        if sec.get("sslmode"):
            config["sslmode"] = sec["sslmode"]
        return config
    except Exception:
        return None


def get_db_config():
    """Retrieve database connection parameters (Streamlit secrets → env vars)."""
    # 1. Try Streamlit secrets first (Streamlit Cloud deployment)
    cfg = _try_streamlit_secrets()
    if cfg:
        return cfg

    # 2. Fallback to environment variables (local development)
    current_user = os.environ.get("USER") or getpass.getuser()
    
    config = {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", 5432)),
        "user": os.environ.get("DB_USER", current_user),
        "dbname": os.environ.get("DB_NAME", "somaiyasat"),
    }
    
    password = os.environ.get("DB_PASS", "")
    if password:
        config["password"] = password
        
    return config


def get_conn():
    """Establish and return a new psycopg2 connection."""
    config = get_db_config()
    return psycopg2.connect(**config)


def query(sql, params=None):
    """
    Execute a SELECT or data-fetching SQL query and return rows as a list of dicts.
    
    Args:
        sql (str): SQL query to execute.
        params (tuple or dict, optional): Query parameters.
        
    Returns:
        list[dict]: Query results as dictionaries.
    """
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(row) for row in rows]
    finally:
        conn.close()


def execute(sql, params=None):
    """
    Execute an INSERT, UPDATE, DELETE, or DDL SQL statement inside a transaction.
    Commits automatically on success, rolls back on exception.
    
    Args:
        sql (str): SQL statement to execute.
        params (tuple or dict, optional): Statement parameters.
        
    Returns:
        int: Number of rows affected (cur.rowcount).
    """
    conn = get_conn()
    try:
        with conn:  # Enters transaction; commits on exit or rolls back on error
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.rowcount
    finally:
        conn.close()


def execute_script(sql_script):
    """
    Execute a raw multi-statement SQL script (e.g., schema DDL or migration) inside a transaction.
    
    Args:
        sql_script (str): Full SQL script content.
    """
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql_script)
    finally:
        conn.close()


if __name__ == "__main__":
    cfg = get_db_config()
    safe_cfg = {k: ("***" if k == "password" else v) for k, v in cfg.items()}
    print(f"Connecting to database with config: {safe_cfg}")
    try:
        with get_conn() as test_conn:
            with test_conn.cursor() as c:
                c.execute("SELECT version();")
                ver = c.fetchone()[0]
                print(f"Connected successfully: {ver}")
    except Exception as e:
        print(f"Connection failed: {e}")
