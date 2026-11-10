"""Executive report: built only from governed tools, no identifiers, all sections present."""

import ast
import json
import re
from pathlib import Path

import pytest

from mcp_server.tools import FleetTools
from pipeline import config
from reporting import build

SECTIONS = ["What happened and what it cost", "Why: logged changes before the shifts",
            "What's being done, and did it work", "Service desk this week", "How far to trust these numbers",
            "Analyst briefing", "Assumptions and definitions"]
ID = re.compile(r"(ROS|GQB|DBN)-(OF|EN|SF|LAB)-\d{4,5}|[0-9a-f]{8}-[0-9a-f]{4}-|@kestrel", re.I)


def test_builder_has_no_direct_data_access():
    """The report may only read data through FleetTools (no SQL, no warehouse connection)."""
    src = Path(build.__file__).read_text()
    tree = ast.parse(src)
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in (n.names if isinstance(n, ast.Import) else [ast.alias(name=n.module or "")])}
    assert "duckdb" not in imported
    assert ".execute(" not in src and "._q(" not in src and "SELECT " not in src


def test_mini_report_builds_safely(mini_db_path, tmp_path, audit_to_tmp):
    t = FleetTools(mini_db_path, caller="report")
    out = build.build("2026-W36", tools=t, out_dir=tmp_path)
    page = out.read_text()
    assert all(s in page for s in SECTIONS)
    assert not ID.search(page) and "Test Caller" not in page
    assert "<script" not in page and "http" not in page.split("</style>")[1].split("<footer")[0].replace("https://", "")
    calls = [json.loads(x) for x in audit_to_tmp.read_text().splitlines()]
    assert calls and {c["caller"] for c in calls} == {"report"} and all(c["ok"] for c in calls)


@pytest.mark.skipif(not config.DB_PATH.exists(), reason="kestrel.duckdb not built")
def test_w41_report_tells_the_story(tmp_path):
    page = build.build("2026-W41", out_dir=tmp_path).read_text()
    assert not ID.search(page)
    assert "illustrative" in page.lower()
    for fact in ("CHG0041031", "KestrelMES.exe 4.2.0", "Claimed, not implemented", "Durban", "Outage",
                 "INC", "Strong evidence"):
        assert fact in page, fact
    assert "No approved briefing for this week yet" in page or "Approved by" in page
