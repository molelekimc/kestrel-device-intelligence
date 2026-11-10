"""Deterministic checks that a briefing follows briefing_contract.md."""

import re
from pathlib import Path

from mcp_server.guardrails import _EMAIL, _GUID, _HOST

CONTRACT_PATH = Path(__file__).with_name("briefing_contract.md")

SECTIONS = ["1. Observed change", "2. Supporting evidence", "3. Data quality warnings",
            "4. Hypotheses", "5. Recommended next checks"]
CAUSAL = re.compile(r"\b(root cause|caused by|due to|because of|proves?|proven)\b", re.IGNORECASE)
CHECK_VERBS = ("check", "confirm", "compare", "verify", "review", "ask", "investigate",
               "validate", "re-run", "rerun", "inspect", "monitor", "quantify", "segment", "look")
# Imperative remediation phrases. Mentions such as the tracker status "Rolled back" are fine.
REMEDIATION = re.compile(r"\b(roll back (?:the|devices|25H2|to)|reinstall|redeploy|uninstall|re-?image|"
                         r"push (?:a|the) fix|block (?:the )?update|force (?:a )?reboot)\b", re.IGNORECASE)

# Warning codes and the words that show the briefing acknowledged them.
WARNING_WORDS = {
    "telemetry_outage": ("outage",),
    "critical_coverage": ("coverage",),
    "low_coverage": ("coverage",),
    "coverage_drop": ("coverage",),
    "cohort_mix_shift": ("cohort", "mix", "like-for-like", "like for like"),
}


def contract_text() -> str:
    return CONTRACT_PATH.read_text()


def split_sections(text: str) -> dict[str, str]:
    parts = re.split(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE)
    return {parts[i].strip(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def _bullets(body: str) -> list[str]:
    return [re.sub(r"^\s*[-*]\s+", "", line).strip()
            for line in body.splitlines() if re.match(r"^\s*[-*]\s+", line)]


def check(text: str, week: str, critical_codes: set[str] | None = None) -> list[str]:
    """Return a list of violations (empty when the briefing meets the contract)."""
    violations = []
    sections = split_sections(text)
    missing = [s for s in SECTIONS if s not in sections]
    if missing:
        violations.append(f"missing sections: {', '.join(missing)}")
    order = [s for s in sections if s in SECTIONS]
    if order != [s for s in SECTIONS if s in sections]:
        violations.append("sections out of order")

    if week not in text:
        violations.append(f"the week {week} is not named")

    for rx, name in ((_HOST, "hostname"), (_GUID, "device id"), (_EMAIL, "user principal name")):
        if rx.search(text):
            violations.append(f"contains a {name}")

    for name in ("1. Observed change", "2. Supporting evidence", "5. Recommended next checks"):
        hit = CAUSAL.search(sections.get(name, ""))
        if hit:
            violations.append(f"causal language '{hit.group(0)}' outside Hypotheses (in '{name}')")

    hyp = _bullets(sections.get("4. Hypotheses", ""))
    if not hyp:
        violations.append("no hypotheses listed as bullets")
    for b in hyp:
        if not re.match(r"^\**hypothesis\**\s*:", b, re.IGNORECASE):
            violations.append(f"hypothesis not labelled: '{b[:60]}'")

    checks = _bullets(sections.get("5. Recommended next checks", ""))
    if not checks:
        violations.append("no recommended next checks")
    for b in checks:
        first = re.sub(r"^\W+", "", b).split()[0].lower() if b.split() else ""
        if first not in CHECK_VERBS:
            violations.append(f"next check does not start with a check verb: '{b[:60]}'")
        if REMEDIATION.search(b):
            violations.append(f"next check recommends remediation: '{b[:60]}'")

    dq = sections.get("3. Data quality warnings", "").lower()
    if not dq:
        violations.append("data quality section is empty")
    for code in sorted(critical_codes or ()):
        words = WARNING_WORDS.get(code)
        if words and not any(w in dq for w in words):
            violations.append(f"critical warning '{code}' not reflected in the data quality section")
    return violations
