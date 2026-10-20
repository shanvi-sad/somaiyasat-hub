"""
SomaiyaSat Telemetry Hub - scripts/router_model.py
Trains and evaluates the autonomous downlink router predictive decision tree model:
  - Builds training table joining router_decision, transmission, data_item,
    downlink_window, and comm_mode (decisions that were actually sent)
  - Features: expected_snr_db, battery_v, size_bytes, urgency, mode base_priority
  - Target: 1 if transmission.status = 'RECEIVED' else 0
  - Trains DecisionTreeClassifier(max_depth=5) with an 80/20 train/test split
  - Computes and prints accuracy, precision, recall, confusion matrix, and feature importances
  - Records model metadata in model_run table
  - Predicts and writes predicted_success and model_version into all currently QUEUED items
  - Evaluates and prints an ESTIMATE comparing expected delivered bytes (predicted_success * size)
    between model-based scoring and fallback rule-based ordering on held-out windows
"""

import sys
import os
from datetime import datetime
import pandas as pd
import numpy as np
from sklearn.tree import DecisionTreeClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, confusion_matrix

# Ensure project root in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.db import get_conn, query, execute


def load_training_data():
    """Extract historical downlink transmissions and join related entities."""
    sql = """
        SELECT
            rd.decision_id,
            rd.window_id,
            rd.item_id,
            w.expected_snr_db,
            w.battery_v,
            w.capacity_bytes,
            di.size_bytes,
            di.urgency,
            cm.base_priority,
            cm.code as mode_code,
            t.status AS tx_status,
            CASE WHEN t.status = 'RECEIVED' THEN 1 ELSE 0 END AS target
        FROM router_decision rd
        JOIN transmission t ON rd.decision_id = t.decision_id
        JOIN data_item di ON rd.item_id = di.item_id
        JOIN comm_mode cm ON di.mode_id = cm.mode_id
        JOIN downlink_window w ON rd.window_id = w.window_id
        WHERE COALESCE(rd.override_action, rd.action) = 'SEND';
    """
    rows = query(sql)
    df = pd.DataFrame(rows)
    # Ensure proper data types
    df['expected_snr_db'] = df['expected_snr_db'].astype(float)
    df['battery_v'] = df['battery_v'].astype(float)
    df['size_bytes'] = df['size_bytes'].astype(int)
    df['urgency'] = df['urgency'].astype(int)
    df['base_priority'] = df['base_priority'].astype(int)
    df['capacity_bytes'] = df['capacity_bytes'].astype(int)
    df['target'] = df['target'].astype(int)
    return df


def train_and_evaluate_model(df):
    """Trains DecisionTreeClassifier(max_depth=5) and logs performance metrics."""
    features = ['expected_snr_db', 'battery_v', 'size_bytes', 'urgency', 'base_priority']
    X = df[features]
    y = df['target']

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )

    clf = DecisionTreeClassifier(max_depth=5, random_state=42)
    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_test)

    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)
    rec = recall_score(y_test, y_pred, zero_division=0)
    cm = confusion_matrix(y_test, y_pred)

    print("\n" + "=" * 65)
    print("ROUTER PREDICTIVE MODEL EVALUATION (DecisionTree, max_depth=5)")
    print("=" * 65)
    print(f"Total historical transmission samples : {len(df):,}")
    print(f"Training set samples (80%)            : {len(X_train):,}")
    print(f"Held-out test samples (20%)           : {len(X_test):,}")
    print("-" * 65)
    print(f"Accuracy                              : {acc:.4f} ({acc*100:.2f}%)")
    print(f"Precision (Class 1: RECEIVED)         : {prec:.4f}")
    print(f"Recall (Class 1: RECEIVED)            : {rec:.4f}")
    print("-" * 65)
    print("Confusion Matrix:")
    print("                      Predicted LOST   Predicted RECEIVED")
    print(f"  Actual LOST               {cm[0][0]:>8}             {cm[0][1]:>8}")
    print(f"  Actual RECEIVED           {cm[1][0]:>8}             {cm[1][1]:>8}")
    print("-" * 65)
    print("Feature Importances:")
    importances = clf.feature_importances_
    sorted_idx = np.argsort(importances)[::-1]
    for idx in sorted_idx:
        print(f"  - {features[idx]:<18}: {importances[idx]:.4f} ({importances[idx]*100:.1f}%)")
    print("=" * 65)

    # Get distinct held-out window IDs from the test split
    test_window_ids = df.loc[X_test.index, 'window_id'].unique().tolist()
    return clf, features, acc, prec, rec, test_window_ids


def record_model_run(acc, prec, rec, total_rows):
    """Insert a record into the model_run table."""
    model_version = f"v1-{datetime.now().strftime('%Y%m%d')}"
    notes = f"DecisionTreeClassifier(max_depth=5) | Precision: {prec:.4f} | Recall: {rec:.4f}"
    
    sql = """
        INSERT INTO model_run (model_version, trained_at, accuracy, rows_used, notes)
        VALUES (%s, now(), %s, %s, %s)
        ON CONFLICT (model_version) DO UPDATE
        SET trained_at = EXCLUDED.trained_at,
            accuracy = EXCLUDED.accuracy,
            rows_used = EXCLUDED.rows_used,
            notes = EXCLUDED.notes;
    """
    execute(sql, (model_version, round(float(acc), 4), total_rows, notes))
    print(f"\nModel record saved to database: model_version = '{model_version}'")
    return model_version


def score_queued_items(clf, features, model_version):
    """Predicts success probability for all QUEUED items and updates data_item table."""
    sql = """
        SELECT di.item_id, di.size_bytes, di.urgency, cm.base_priority
        FROM data_item di
        JOIN comm_mode cm ON di.mode_id = cm.mode_id
        WHERE di.status = 'QUEUED';
    """
    queued_items = query(sql)
    if not queued_items:
        print("No items currently in QUEUED status to score.")
        return

    # Nominal pass channel conditions (median expected SNR 12.0 dB, nominal battery 3.85 V)
    nominal_snr = 12.0
    nominal_battery = 3.85

    # Vectorized inference with proper DataFrame column names to eliminate sklearn warnings
    feature_df = pd.DataFrame([{
        'expected_snr_db': nominal_snr,
        'battery_v': nominal_battery,
        'size_bytes': item["size_bytes"],
        'urgency': item["urgency"],
        'base_priority': item["base_priority"]
    } for item in queued_items])

    probs = clf.predict_proba(feature_df)[:, 1]

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                for idx, item in enumerate(queued_items):
                    prob_val = round(float(probs[idx]), 4)
                    cur.execute("""
                        UPDATE data_item
                        SET predicted_success = %s, model_version = %s
                        WHERE item_id = %s;
                    """, (prob_val, model_version, item["item_id"]))
    finally:
        conn.close()

    print(f"Scored {len(queued_items)} active QUEUED items with predicted_success probabilities.")


def estimate_delivered_bytes_comparison(clf, features, test_window_ids):
    """
    Computes and prints a clearly labelled ESTIMATE comparing expected delivered
    bytes (predicted_success * size) for model-based vs rule-based ordering
    across held-out passes, evaluating all queued candidates under contention.
    """
    print("\n" + "=" * 65)
    print("ESTIMATE: MODEL-BASED VS RULE-BASED EXPECTED DELIVERED BYTES")
    print("=" * 65)

    if not test_window_ids:
        print("No held-out windows available for estimation.")
        return

    # Query all candidate decisions (both SEND and DEFER) for held-out windows
    sql = """
        SELECT
            rd.window_id,
            rd.item_id,
            rd.action,
            w.expected_snr_db,
            w.battery_v,
            w.capacity_bytes,
            di.size_bytes,
            di.urgency,
            cm.base_priority,
            cm.code as mode_code
        FROM router_decision rd
        JOIN data_item di ON rd.item_id = di.item_id
        JOIN comm_mode cm ON di.mode_id = cm.mode_id
        JOIN downlink_window w ON rd.window_id = w.window_id
        WHERE rd.window_id = ANY(%s);
    """
    candidates = pd.DataFrame(query(sql, (test_window_ids,)))
    if candidates.empty:
        print("No candidates found for held-out windows.")
        return

    candidates['expected_snr_db'] = candidates['expected_snr_db'].astype(float)
    candidates['battery_v'] = candidates['battery_v'].astype(float)
    candidates['size_bytes'] = candidates['size_bytes'].astype(int)
    candidates['urgency'] = candidates['urgency'].astype(int)
    candidates['base_priority'] = candidates['base_priority'].astype(int)
    candidates['capacity_bytes'] = candidates['capacity_bytes'].astype(int)

    total_expected_model_bytes = 0.0
    total_expected_rule_bytes = 0.0
    windows_evaluated = 0

    for win_id, group in candidates.groupby('window_id'):
        if len(group) < 2:
            continue

        capacity = group['capacity_bytes'].iloc[0]

        # Compute predicted success probability for each candidate
        group = group.copy()
        group['predicted_success'] = clf.predict_proba(group[features])[:, 1]

        # Model-based scoring: (6 - base_priority)*100 + urgency*10 + predicted_success*10
        group['score_model'] = (
            (6 - group['base_priority']) * 100.0 +
            (group['urgency'] * 10.0) +
            (group['predicted_success'] * 10.0)
        )

        # Rule-based fallback scoring: (6 - base_priority)*100 + urgency*10
        group['score_rule'] = (
            (6 - group['base_priority']) * 100.0 +
            (group['urgency'] * 10.0)
        )

        # 1. Greedy packing under Model-based ordering
        model_sorted = group.sort_values(by=['score_model', 'size_bytes'], ascending=[False, True])
        rem_cap_model = capacity
        win_model_bytes = 0.0
        for _, row in model_sorted.iterrows():
            if row['size_bytes'] <= rem_cap_model:
                rem_cap_model -= row['size_bytes']
                win_model_bytes += (row['predicted_success'] * row['size_bytes'])

        # 2. Greedy packing under Rule-based ordering
        rule_sorted = group.sort_values(by=['score_rule', 'size_bytes'], ascending=[False, True])
        rem_cap_rule = capacity
        win_rule_bytes = 0.0
        for _, row in rule_sorted.iterrows():
            if row['size_bytes'] <= rem_cap_rule:
                rem_cap_rule -= row['size_bytes']
                win_rule_bytes += (row['predicted_success'] * row['size_bytes'])

        total_expected_model_bytes += win_model_bytes
        total_expected_rule_bytes += win_rule_bytes
        windows_evaluated += 1

    diff_bytes = total_expected_model_bytes - total_expected_rule_bytes
    pct_gain = (diff_bytes / total_expected_rule_bytes * 100.0) if total_expected_rule_bytes > 0 else 0.0

    print(f"Held-out downlink passes evaluated    : {windows_evaluated}")
    print(f"Rule-based expected delivered payload  : {total_expected_rule_bytes / 1024.0:,.2f} KB ({total_expected_rule_bytes:,.0f} bytes)")
    print(f"Model-based expected delivered payload : {total_expected_model_bytes / 1024.0:,.2f} KB ({total_expected_model_bytes:,.0f} bytes)")
    print("-" * 65)
    print(f"Expected Throughput Gain (Bytes)       : +{diff_bytes / 1024.0:,.2f} KB (+{diff_bytes:,.0f} bytes)")
    print(f"Relative Throughput Improvement        : +{pct_gain:.2f}%")
    print("=" * 65)


def main():
    print("=" * 70)
    print("SomaiyaSat Telemetry Hub - Machine Learning Router Model Training")
    print("=" * 70)

    print("Loading training dataset from PostgreSQL...")
    df = load_training_data()
    if len(df) == 0:
        print("Error: No historical transmission data found. Please run scripts/gen_data.py first.")
        sys.exit(1)

    print(f"Extracted {len(df):,} sent router decisions with confirmed ground reception statuses.")

    clf, features, acc, prec, rec, test_window_ids = train_and_evaluate_model(df)
    model_version = record_model_run(acc, prec, rec, len(df))
    score_queued_items(clf, features, model_version)
    estimate_delivered_bytes_comparison(clf, features, test_window_ids)

    print("\nRouter model pipeline completed successfully.")


if __name__ == "__main__":
    main()
