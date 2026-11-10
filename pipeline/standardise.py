"""Pure cleaning functions. Registered as DuckDB UDFs in silver.py and unit-tested
directly. Each returns None when a value cannot be conformed, so the caller can
quarantine it instead of guessing."""

import re
from datetime import date, datetime, timedelta

from pipeline.reference import (
    BUILD_RELEASE, COMPLIANCE, LENOVO_MTM, MANUFACTURER_PREFIXES, PLANT_ALIASES,
    TRACKER_STATUSES, TRACKER_WAVES,
)

REPORT_YEAR = 2026
MAX_DURATION_S = 600.0     # a boot or logon over 10 minutes is not plausible
SENTINEL_DURATIONS = {99999.0, 99999000.0}

_HOST_RE = re.compile(r"^(ROS|GQB|DBN)[-_ ]?(OF|EN|SF)[-_ ]?([0-9O]{5})$")
_BUILD_RE = re.compile(r"^(?:10\.0\.)?(\d{5})\.(\d+)$")


def std_hostname(raw: str | None) -> str | None:
    """'dbn-sf-17193.kestrel.local', 'ROS-OF37255 ', 'ROS-SF-69O43' -> 'PPP-CC-NNNNN'.

    The letter O typed for zero is only corrected inside the numeric part.
    Hostnames outside the plant/class scheme (e.g. lab machines) return None."""
    if raw is None:
        return None
    s = raw.strip().upper().split(".")[0]
    m = _HOST_RE.match(s)
    if not m:
        return None
    plant, cls, num = m.groups()
    return f"{plant}-{cls}-{num.replace('O', '0')}"


def plant_from_category(raw: str | None) -> str | None:
    if raw is None:
        return None
    return PLANT_ALIASES.get(raw.strip().lower())


def parse_intune_datetime(raw: str | None) -> datetime | None:
    """Intune export timestamps are SAST local time in one of three layouts.

    'YYYY-MM-DD HH:MM:SS' is ISO. A 12-hour clock with AM/PM only appears with
    US month-first dates. A 24-hour 'DD/MM/YYYY HH:MM' is the South African
    day-first layout (profiling found day values above 12 only in that slot)."""
    if raw is None or not raw.strip():
        return None
    s = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_tracker_date(raw: str | None) -> date | None:
    """Tracker dates: ISO, DD/MM/YYYY, Excel serial numbers, or '12 Sep' with
    no year (the tracker only covers the 2026 rollout)."""
    if raw is None or not raw.strip():
        return None
    s = raw.strip()
    if s.isdigit() and 40000 <= int(s) <= 60000:
        return date(1899, 12, 30) + timedelta(days=int(s))
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.strptime(f"{s} {REPORT_YEAR}", "%d %b %Y").date()
    except ValueError:
        return None


def std_os_build(raw: str | None) -> str | None:
    """'26200.6901' and '10.0.26200.6901' -> '10.0.26200.6901'."""
    if raw is None:
        return None
    m = _BUILD_RE.match(raw.strip())
    if not m:
        return None
    return f"10.0.{m.group(1)}.{m.group(2)}"


def os_release(build: str | None) -> str | None:
    if build is None:
        return None
    m = _BUILD_RE.match(build)
    return BUILD_RELEASE.get(m.group(1)) if m else None


def std_model(manufacturer: str | None, model: str | None) -> str | None:
    if model is None:
        return None
    s = model.strip()
    if not s or s.lower() in ("null", "n/a", "none", "unknown"):
        return None
    for prefix in MANUFACTURER_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
    if (manufacturer or "").strip() == "Lenovo" and s[:4] in LENOVO_MTM and len(s) == 10:
        return LENOVO_MTM[s[:4]]
    return s


def std_compliance(raw: str | None) -> str | None:
    if raw is None:
        return None
    return COMPLIANCE.get(raw.strip().lower())


def storage_gb(raw: str | None) -> int | None:
    """Disk size as a nominal decimal GB. '476.83 GB' is 512 GB reported in GiB."""
    if raw is None:
        return None
    s = raw.strip().upper().removesuffix("GB").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    for nominal in (128, 256, 512, 1024, 2048):
        if abs(v - nominal) < 1 or abs(v - nominal / 1.073741824) < 1:
            return nominal
    return round(v)


def _vocab_key(raw: str) -> str:
    return re.sub(r"\s+", "", raw.strip().lower())


def std_wave(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    return TRACKER_WAVES.get(_vocab_key(raw))


def std_status(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    return TRACKER_STATUSES.get(_vocab_key(raw))


def valid_duration(seconds: float | None) -> bool:
    """A duration is usable for percentiles if it is positive, below the
    10-minute ceiling and not the agent's 99999 'unknown' sentinel."""
    if seconds is None:
        return False
    return 0 < seconds <= MAX_DURATION_S and seconds not in SENTINEL_DURATIONS
