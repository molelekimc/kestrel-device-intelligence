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


# ---------------------------------------------------------------- business sources (Phase 6)

def parse_local_datetime(raw: str | None) -> datetime | None:
    """Service desk, change and MES exports: SAST local time, day-first where slashed
    (South African locale; none of these exports use AM/PM)."""
    if raw is None or not raw.strip():
        return None
    s = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_day(raw: str | None) -> date | None:
    """Register dates: tracker layouts plus '14 Oct 2026'."""
    d = parse_tracker_date(raw)
    if d is not None or raw is None:
        return d
    try:
        return datetime.strptime(raw.strip(), "%d %b %Y").date()
    except ValueError:
        return None


def std_priority(raw: str | None) -> int | None:
    if raw is None:
        return None
    from pipeline.reference import TICKET_PRIORITY
    return TICKET_PRIORITY.get(raw.strip().lower())


def std_ticket_category(raw: str | None) -> str | None:
    if raw is None:
        return None
    from pipeline.reference import TICKET_CATEGORY
    return TICKET_CATEGORY.get(raw.strip().lower())


def std_remediation_status(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    from pipeline.reference import REMEDIATION_STATUS
    return REMEDIATION_STATUS.get(raw.strip().lower())


def std_group(raw: str | None) -> str | None:
    """'grp-mes-users ', 'GRP MES USERS' -> 'GRP-MES-USERS'."""
    if raw is None or not raw.strip():
        return None
    return re.sub(r"[\s_]+", "-", raw.strip().upper())


_LINE_RE = re.compile(r"^P0\d-[A-Z]+-L\d{2}$")


def std_line_id(mes_plant: str | None, raw: str | None) -> str | None:
    """'P01-BIW-L01' as is; 'BIW L01' on a P01 row -> 'P01-BIW-L01'."""
    if raw is None or not raw.strip():
        return None
    s = raw.strip().upper()
    if _LINE_RE.match(s):
        return s
    cand = f"{(mes_plant or '').strip().upper()}-{re.sub(r'[\s_]+', '-', s)}"
    return cand if _LINE_RE.match(cand) else None


def downtime_it_type(reason_code: str | None, comment: str | None) -> tuple[str | None, bool]:
    """(IT type or None, reclassified?) for a line stop. OTHER stops whose comment names a
    PC, screen, reboot or MES problem are counted as IT; MES words mean the MES client."""
    from pipeline.reference import DOWNTIME_IT_CODES, IT_COMMENT_WORDS, MES_COMMENT_WORDS
    code = (reason_code or "").strip().upper()
    if code in DOWNTIME_IT_CODES:
        return code, False
    if code == "OTHER":
        words = re.findall(r"[a-z]+", (comment or "").lower())
        hit = lambda keys: any(w.startswith(k) for w in words for k in keys)   # 'rebooted' matches 'reboot'
        if hit(IT_COMMENT_WORDS):
            # MES must be the exact word, so 'message' does not count as the MES client.
            return ("IT-APP" if any(w in MES_COMMENT_WORDS for w in words) else "IT-TERM"), True
    return None, False
