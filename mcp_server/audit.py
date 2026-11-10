"""Append-only JSONL log of every tool call: who called, arguments, outcome, size, time."""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from pipeline.config import ROOT

AUDIT_PATH = Path(os.environ.get("KESTREL_AUDIT_LOG", ROOT / "logs" / "mcp_audit.jsonl"))


def write(caller: str, tool: str, args: dict, started: float, *, ok: bool,
          rows: int = 0, suppressed: int = 0, warnings: int = 0, error: str | None = None) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "caller": caller,
        "tool": tool,
        "args": {k: v for k, v in args.items() if v is not None},
        "ok": ok,
        "rows": rows,
        "suppressed_groups": suppressed,
        "warnings": warnings,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    if error:
        entry["error"] = error
    with open(AUDIT_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
