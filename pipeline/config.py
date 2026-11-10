import os
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Where source files land. The live demo points this at landing/, which the replay
# simulator fills over simulated time (Phase 7).
RAW = Path(os.environ.get("KESTREL_RAW", ROOT / "raw"))
STATE_DIR = Path(os.environ.get("KESTREL_STATE", ROOT / "state"))
FRESHNESS_PATH = ROOT / "metrics" / "freshness.yaml"
DB_PATH = Path(os.environ.get("KESTREL_DB", ROOT / "kestrel.duckdb"))
DEFINITIONS_PATH = ROOT / "metrics" / "definitions.yaml"

INTUNE_GLOB = str(RAW / "intune" / "kestrel_intune_devices_*.csv")
TELEMETRY_GLOB = str(RAW / "telemetry" / "*.jsonl.gz")
TRACKER_PATH = str(RAW / "plant_it" / "Win11_25H2_rollout_tracker.csv")

# Business sources (Phase 6). Bronze table name -> file.
BUSINESS_SOURCES = {
    "bronze_itsm_incidents": str(RAW / "itsm" / "servicedesk_incidents_20261026.csv"),
    "bronze_change_requests": str(RAW / "change" / "change_requests_20261026.csv"),
    "bronze_group_membership": str(RAW / "intune" / "group_membership_20261026.csv"),
    "bronze_mes_lines": str(RAW / "mes" / "production_lines.csv"),
    "bronze_mes_terminals": str(RAW / "mes" / "line_terminals.csv"),
    "bronze_mes_downtime": str(RAW / "mes" / "line_downtime_events.csv"),
    "bronze_remediation": str(RAW / "remediation" / "remediation_register.csv"),
}
COST_ASSUMPTIONS_PATH = ROOT / "metrics" / "cost_assumptions.yaml"

# Intune export time (SAST). Lifecycle is judged relative to this.
SNAPSHOT_AT = datetime(2026, 10, 26, 8, 0)
STALE_AFTER_DAYS = 30

# "Now" for freshness. Historical runs use the Intune export time; the live replay
# passes its simulated clock in KESTREL_AS_OF (ISO datetime, SAST).
AS_OF = datetime.fromisoformat(os.environ["KESTREL_AS_OF"]) if os.environ.get("KESTREL_AS_OF") else SNAPSHOT_AT

# Reporting window, Monday 2026-W36 to Sunday 2026-W43 (SAST dates). During a replay
# it ends on the last day whose telemetry file has arrived (files land at 02:00 the next day).
WINDOW_START = date(2026, 8, 31)
WINDOW_END = min(date(2026, 10, 25), (AS_OF - timedelta(hours=2)).date() - timedelta(days=1))

# South Africa has no daylight saving, so SAST is a fixed UTC+2. Using a fixed
# offset keeps the pipeline free of a time zone database dependency.
SAST_OFFSET_HOURS = 2
