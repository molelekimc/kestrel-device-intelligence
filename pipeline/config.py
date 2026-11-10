import os
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"
DB_PATH = Path(os.environ.get("KESTREL_DB", ROOT / "kestrel.duckdb"))
DEFINITIONS_PATH = ROOT / "metrics" / "definitions.yaml"

INTUNE_GLOB = str(RAW / "intune" / "*.csv")
TELEMETRY_GLOB = str(RAW / "telemetry" / "*.jsonl.gz")
TRACKER_PATH = str(RAW / "plant_it" / "Win11_25H2_rollout_tracker.csv")

# Intune export time (SAST). Lifecycle is judged relative to this.
SNAPSHOT_AT = datetime(2026, 10, 26, 8, 0)
STALE_AFTER_DAYS = 30

# Reporting window, Monday 2026-W36 to Sunday 2026-W43 (SAST dates).
WINDOW_START = date(2026, 8, 31)
WINDOW_END = date(2026, 10, 25)

# South Africa has no daylight saving, so SAST is a fixed UTC+2. Using a fixed
# offset keeps the pipeline free of a time zone database dependency.
SAST_OFFSET_HOURS = 2
