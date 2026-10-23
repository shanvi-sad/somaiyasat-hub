# Experiment Map

## Experiment 1 – 8 → Project Artifacts
| Experiment | Course Outcome | Demonstrated By |
|------------|----------------|-----------------|
| **Exp 1** (EER diagram) | Understand ER modeling, specialization, weak entities, unary relationships | `docs/er_diagram.md` (Mermaid diagram) |
| **Exp 2** (Relational mapping) | Translate ER to relational schema, define PK/FK, indexes | `docs/relational_mapping.md` |
| **Exp 3** (Normalization) | Apply 1NF/2NF/3NF to derive normalized tables | `docs/normalization.md` |
| **Exp 4** (SQL queries) | Write complex SELECTs with GROUP BY, HAVING, UNION, INTERSECT, subqueries, joins | `04_queries.sql` (Q1‑Q16) |
| **Exp 5** (Security & DCL) | Implement role‑based privileges, GRANT/REVOKE, test security blocks | `05_security_dcl.sql` |
| **Exp 6** (Index analysis) | Compare query performance with/without indexes, use EXPLAIN | `06_index_analysis.sql` |
| **Exp 7** (Transaction demo) | Demonstrate ACID properties, savepoints, rollback, error handling | `07_transactions_demo.sql` |
| **Exp 8** (Streamlit UI) | Build a multi‑page web app, role‑based UI components, data visualisation | Streamlit pages in `app/` (`Home.py`, `pages/*.py`) |

## Case‑Study Requirements → Implemented Features
| Requirement (from SPEC.md) | Feature / File implementing it |
|-----------------------------|------------------------------|
| TT&C never dropped | Trigger `fn_ensure_ttc` in `02_functions_triggers.sql` and routing logic in `router_model.py` |
| Model fallback on low confidence | Logic in `router_model.py` (confidence check) |
| Human‑readable rationale stored | `router_decision.rationale` column, populated by `router_model.py` |
| Operator override authority | Role‑based UI controls in `app/pages/4_Router.py` and privilege checks in `app/common.py` |
| Critical battery triggers safe mode | Trigger `fn_battery_critical` in `02_functions_triggers.sql` |
| Phase transition guards | Triggers in `02_functions_triggers.sql` enforcing allowed phase moves |
| Alert generation on out‑of‑range reading | `fn_check_thresholds` trigger & `alert` table |
| Schedule window deferral in safe mode | `fn_schedule_window` logic in `02_functions_triggers.sql` |
| Logging of automatic commands | `command` table inserts via triggers |
| Secure role hierarchy (ADMIN, OPERATOR, VIEWER, HAM_VOLUNTEER) | `05_security_dcl.sql` roles and grants |

*All mappings are consistent with the SPEC and the implemented code base.*
