"""Input validation, small-group suppression and the outbound identifier check.

Every value that reaches SQL is either matched against an allowlist loaded from
the warehouse or the metric definitions, or passed as a bound parameter. There
is no path from a tool argument to free-form SQL."""

import re
from datetime import date

from pipeline.metrics import definitions
from pipeline.reference import BUILD_RELEASE, DEVICE_CLASSES, PLANTS

WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")

# Public dimension names -> Gold column names.
DIMENSIONS = {"plant": "plant_code", "device_class": "device_class", "model": "model",
              "os_release": "os_release"}


class ToolInputError(ValueError):
    """Raised for any argument outside the allowed values. The message lists them."""


def min_devices() -> int:
    return int(definitions()["min_group_devices"])


def _pick(value: str, allowed: dict[str, str], what: str) -> str:
    """Case-insensitive match of value against allowed {alias: canonical}."""
    key = str(value).strip().lower()
    if key in allowed:
        return allowed[key]
    choices = sorted(set(allowed.values()))
    raise ToolInputError(f"Unknown {what} {value!r}. Allowed: {', '.join(choices)}")


class Validator:
    """Holds the allowlists, loaded once from the read-only warehouse."""

    def __init__(self, con):
        self.weeks = [r[0] for r in con.execute(
            "SELECT iso_week FROM gold_week ORDER BY 1").fetchall()]
        self.week_dates = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT iso_week, week_start, week_end FROM gold_week").fetchall()}
        self.models = [r[0] for r in con.execute(
            "SELECT DISTINCT model FROM gold_device_week ORDER BY 1").fetchall()]
        self.releases = [r[0] for r in con.execute(
            "SELECT DISTINCT os_release FROM gold_device_week ORDER BY 1").fetchall()]
        self.metrics = list(definitions()["metrics"])
        # Business layer (Phase 6)
        self.categories = [r[0] for r in con.execute(
            "SELECT DISTINCT category FROM gold_ticket_fact ORDER BY 1").fetchall()]
        self.change_ids = [r[0] for r in con.execute(
            "SELECT change_id FROM silver_change ORDER BY 1").fetchall()]
        self.verdicts = [r[0] for r in con.execute(
            "SELECT DISTINCT verdict FROM gold_remediation_effect ORDER BY 1").fetchall()]

    # ------------------------------------------------------------- single values
    def metric(self, value: str) -> str:
        return _pick(value, {m.lower(): m for m in self.metrics}, "metric")

    def plant(self, value: str | None) -> str | None:
        if value is None:
            return None
        aliases = {code.lower(): code for code in PLANTS}
        aliases |= {name.lower(): code for code, name in PLANTS.items()}
        aliases |= {"port elizabeth": "GQB", "pe": "GQB", "prospecton": "DBN"}
        return _pick(value, aliases, "plant")

    def device_class(self, value: str | None) -> str | None:
        if value is None:
            return None
        aliases = {code.lower(): code for code in DEVICE_CLASSES}
        aliases |= {name.lower(): code for code, name in DEVICE_CLASSES.items()}
        aliases |= {"office": "OF", "engineering": "EN", "shop-floor": "SF", "shop floor": "SF",
                    "shopfloor": "SF", "kiosk": "SF"}
        return _pick(value, aliases, "device_class")

    def model(self, value: str | None) -> str | None:
        if value is None:
            return None
        return _pick(value, {m.lower(): m for m in self.models}, "model")

    def os_release(self, value: str | None) -> str | None:
        """Accepts a release label ('Win11 25H2', '25H2') or a build ('26200', '10.0.26200.6901')."""
        if value is None:
            return None
        aliases = {r.lower(): r for r in self.releases}
        aliases |= {r.split()[-1].lower(): r for r in self.releases if r != "Unknown"}
        for build, release in BUILD_RELEASE.items():
            if release in self.releases:
                aliases[build] = release
        v = str(value).strip().lower()
        m = re.match(r"^(?:10\.0\.)?(\d{5})(?:\.\d+)?$", v)
        try:
            return _pick(m.group(1) if m else v, aliases, "os_build / os_release")
        except ToolInputError:
            # Echo what the caller typed, not the normalised lookup key.
            raise ToolInputError(f"Unknown os_build / os_release {value!r}. "
                                 f"Allowed: {', '.join(sorted(set(aliases.values())))}") from None

    def week(self, value: str | None, default: str | None = None) -> str | None:
        if value is None:
            return default
        v = str(value).strip().upper()
        if not WEEK_RE.match(v) or v not in self.weeks:
            raise ToolInputError(f"Week {value!r} is not an ISO week in the data. "
                                 f"Use YYYY-Www between {self.weeks[0]} and {self.weeks[-1]}.")
        return v

    def week_range(self, week_from: str | None, week_to: str | None) -> tuple[str, str]:
        a = self.week(week_from, self.weeks[0])
        b = self.week(week_to, self.weeks[-1])
        if a > b:
            raise ToolInputError(f"week_from {a} is after week_to {b}.")
        return a, b

    def period(self, value: str) -> tuple[str, str]:
        """'2026-W41' or '2026-W36..2026-W38'."""
        parts = [p.strip() for p in str(value).split("..")]
        if len(parts) == 1:
            w = self.week(parts[0])
            return w, w
        if len(parts) == 2:
            return self.week_range(parts[0], parts[1])
        raise ToolInputError(f"Period {value!r} must be 'YYYY-Www' or 'YYYY-Www..YYYY-Www'.")

    def day(self, value: str | None, default: date) -> date:
        if value is None:
            return default
        try:
            d = date.fromisoformat(str(value).strip())
        except ValueError:
            raise ToolInputError(f"Date {value!r} must be YYYY-MM-DD.") from None
        lo, hi = self.week_dates[self.weeks[0]][0], self.week_dates[self.weeks[-1]][1]
        if not lo <= d <= hi:
            raise ToolInputError(f"Date {d} is outside the data window {lo} to {hi}.")
        return d

    def choice(self, value: str | None, allowed: list[str], what: str, default: str | None = None) -> str | None:
        if value is None:
            return default
        return _pick(value, {a.lower(): a for a in allowed}, what)

    def limit(self, value: int | None, default: int = 10, maximum: int = 20) -> int:
        if value is None:
            return default
        if not isinstance(value, int) or not 1 <= value <= maximum:
            raise ToolInputError(f"limit must be an integer from 1 to {maximum}.")
        return value

    def priority(self, value) -> int | None:
        if value is None:
            return None
        from pipeline.standardise import std_priority
        text = str(value).strip()
        p = int(text) if text in ("1", "2", "3", "4") else std_priority(text)
        if p is None:
            raise ToolInputError(f"Unknown priority {value!r}. Allowed: 1-4, P1-P4, Critical, High, Moderate, Low")
        return p

    def filters(self, plant=None, device_class=None, model=None, os_build=None) -> dict[str, str]:
        f = {"plant_code": self.plant(plant), "device_class": self.device_class(device_class),
             "model": self.model(model), "os_release": self.os_release(os_build)}
        return {k: v for k, v in f.items() if v is not None}


# ----------------------------------------------------------------- outbound check

_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HOST = re.compile(r"\b(ROS|GQB|DBN)[-_ ]?(OF|EN|SF|LAB)[-_ ]?[0-9O]{4,5}\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class IdentifierLeak(RuntimeError):
    pass


class OutboundCheck:
    """Fails closed if a response contains a device-level identifier. Serial
    numbers have no fixed shape, so they are matched exactly against inventory."""

    def __init__(self, con):
        # Ticket callers and anyone named in ticket text (restricted Silver table).
        self.people = {r[0].strip() for r in con.execute(
            "SELECT DISTINCT caller_id FROM silver_ticket_text WHERE caller_id NOT LIKE 'Service Desk%'").fetchall()
                       if r[0] and r[0].strip()}
        self.serials = {r[0] for r in con.execute(
            "SELECT DISTINCT serial FROM silver_intune_enrollment WHERE serial IS NOT NULL").fetchall()}

    def check(self, payload) -> None:
        for text in _strings(payload):
            for name, rx in (("device id", _GUID), ("hostname", _HOST), ("user principal name", _EMAIL)):
                if rx.search(text):
                    raise IdentifierLeak(f"Response blocked: it contained a {name}.")
            if any(name in text for name in self.people):
                raise IdentifierLeak("Response blocked: it contained a person's name.")
            for token in re.findall(r"[A-Z0-9]{6,12}", text):
                if token in self.serials:
                    raise IdentifierLeak("Response blocked: it contained a serial number.")


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)
