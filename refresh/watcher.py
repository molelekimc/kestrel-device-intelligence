"""Refresh automation: watch the landing zone and rebuild the warehouse when files change.

    python -m refresh.watcher                 # watch landing/ (default) and refresh on change
    python -m refresh.watcher --once          # one refresh now, then exit

- Change detection: a manifest of file name, size and modification time, re-read every
  `--poll` seconds; a refresh starts once the manifest has been stable for `--debounce`
  seconds, so a half-delivered batch is never loaded.
- Safety: the pipeline builds into a temporary file and swaps it in atomically. A failed
  run leaves the last good warehouse in place and is logged as failed.
- Manual refresh: the API writes <state>/refresh_request.json; the watcher picks it up.
- Every run is appended to <state>/refresh_log.jsonl."""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pipeline import config

LOG_PATH = config.STATE_DIR / "refresh_log.jsonl"
REQUEST_PATH = config.STATE_DIR / "refresh_request.json"


def manifest(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file() and not p.name.endswith(".tmp")}


def changed_files(old: dict, new: dict) -> list[str]:
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def _as_of() -> str | None:
    clock = config.STATE_DIR / "clock.json"
    return json.loads(clock.read_text())["now"] if clock.exists() else None


def _summary(db: Path) -> dict:
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    try:
        q = lambda sql: con.execute(sql).fetchone()[0]
        return {"telemetry_events": q("SELECT count(*) FROM silver_telemetry_event"),
                "tickets": q("SELECT count(*) FROM silver_ticket"),
                "line_stops": q("SELECT count(*) FROM silver_downtime"),
                "data_to": str(q("SELECT max(week_end) FROM gold_week")),
                "not_fresh": [f"{r[0]}:{r[1]}={r[2]}" for r in con.execute(
                    "SELECT source, scope, status FROM gold_freshness WHERE status <> 'fresh'").fetchall()]}
    finally:
        con.close()


def log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def read_log(limit: int = 50) -> list[dict]:
    if not LOG_PATH.exists():
        return []
    return [json.loads(x) for x in LOG_PATH.read_text().splitlines()[-limit:]]


def refresh(root: Path, trigger: str, changed: list[str] | None = None, timeout: int = 1800) -> dict:
    """Run the pipeline on `root` as of the simulated clock (if any). Never raises."""
    as_of = _as_of()
    env = dict(os.environ, KESTREL_RAW=str(root), KESTREL_DB=str(config.DB_PATH))
    if as_of:
        env["KESTREL_AS_OF"] = as_of
    entry = {"run_id": uuid.uuid4().hex[:8], "trigger": trigger, "as_of": as_of,
             "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "changed": (changed or [])[:20], "changed_count": len(changed or [])}
    t0 = time.perf_counter()
    try:
        r = subprocess.run([sys.executable, "-m", "pipeline.run"], env=env, cwd=config.ROOT,
                           capture_output=True, text=True, timeout=timeout)
        ok = r.returncode == 0
        entry["status"] = "ok" if ok else "failed"
        if not ok:
            entry["error"] = (r.stderr or r.stdout).strip().splitlines()[-1][:500] if (r.stderr or r.stdout) else "failed"
    except subprocess.TimeoutExpired:
        entry["status"], entry["error"] = "failed", f"timed out after {timeout}s"
    finally:
        config.DB_PATH.with_suffix(".building.duckdb").unlink(missing_ok=True)
    entry["duration_s"] = round(time.perf_counter() - t0, 1)
    entry["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if entry["status"] == "ok":
        try:
            entry["warehouse"] = _summary(config.DB_PATH)
        except Exception as e:                     # summary is informational only
            entry["warehouse"] = {"error": str(e)[:200]}
    log(entry)
    return entry


def watch(root: Path, poll: float, debounce: float, max_runs: int | None = None) -> None:
    last_loaded: dict = {}
    seen, seen_at, runs = manifest(root), time.monotonic(), 0
    while max_runs is None or runs < max_runs:
        time.sleep(poll)
        current = manifest(root)
        if current != seen:
            seen, seen_at = current, time.monotonic()
            continue
        if REQUEST_PATH.exists():
            REQUEST_PATH.unlink(missing_ok=True)
            e = refresh(root, "manual", changed_files(last_loaded, current))
        elif current != last_loaded and current and time.monotonic() - seen_at >= debounce:
            e = refresh(root, "files changed", changed_files(last_loaded, current))
        else:
            continue
        last_loaded, runs = current, runs + 1
        print(f"[{e['finished_at']}] refresh {e['run_id']} {e['status']} in {e['duration_s']}s "
              f"(as of {e['as_of'] or 'snapshot'}, {e['changed_count']} files changed)", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Refresh the warehouse when source files change.")
    ap.add_argument("--root", type=Path, default=Path(os.environ.get("KESTREL_RAW", config.ROOT / "landing")))
    ap.add_argument("--poll", type=float, default=2.0, help="seconds between manifest checks")
    ap.add_argument("--debounce", type=float, default=5.0, help="seconds the manifest must be stable")
    ap.add_argument("--once", action="store_true", help="refresh once now and exit")
    args = ap.parse_args(argv)
    if args.once:
        e = refresh(args.root, "manual (cli)", list(manifest(args.root)))
        print(json.dumps(e, indent=1, default=str))
        return 0 if e["status"] == "ok" else 1
    print(f"Watching {args.root} (poll {args.poll}s, debounce {args.debounce}s). Log: {LOG_PATH}", flush=True)
    watch(args.root, args.poll, args.debounce)
    return 0


if __name__ == "__main__":
    sys.exit(main())
