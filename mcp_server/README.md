# Kestrel fleet MCP server

A read-only MCP server over the Gold layer in `kestrel.duckdb`. It exposes governed,
aggregate fleet metrics only. Kestrel Assembly Group is a fictional client and all data is synthetic.

## Tools

| Tool | What it answers |
|---|---|
| `list_allowed_values` | Valid metrics, plants, classes, models, OS releases and weeks |
| `get_metric_definition(metric)` | Formula, unit, grain, direction, caveats and threshold |
| `get_kpi(metric, plant?, device_class?, model?, os_build?, week_from?, week_to?, group_by?)` | A KPI per ISO week plus a period total, optionally broken down by one dimension |
| `compare_cohorts(metric, group_by, period_a, period_b, like_for_like=True, …filters)` | Period-over-period change per group. `like_for_like` keeps the same devices in both periods; `group_by="rollout_cohort"` compares upgraded devices against devices that stayed on their release |
| `get_coverage(plant?, device_class?, date_from?, date_to?, granularity?)` | Devices reporting vs devices active in Intune, per week or per day |
| `get_data_quality_status(week?)` | Pipeline checks, quarantine counts, and the warnings for a week |
| `list_threshold_breaches(week)` | Week-over-week KPI changes beyond the thresholds, with confounding warnings |
| `get_rollout_status(plant?, wave?)` | Tracker claims reconciled against telemetry, plus the weekly OS release mix |
| `get_top_events(event_type, …filters, limit?)` | Top crashing apps or blue screen codes, with device counts and a weekly breakdown |
| `get_business_impact(week_from?, week_to?, plant?)` | MES line-stop minutes, IT share of downtime, and an **illustrative** cost range (ZAR) by type and driver, with the assumptions used |
| `get_ticket_metrics(week_from?, week_to?, plant?, category?, priority?, group_by?)` | Incidents, incidents per 1,000 devices, time to acknowledge and resolve, reopen and SLA breach rates |
| `get_change_correlation(week_from?, week_to?, change_id?, metric?, include_no_evidence?)` | Logged changes ranked by evidence that a relevant metric shifted in the devices they targeted, with the mechanism (app version or bug check code) and competing changes. Evidence, not proof |
| `get_remediation_status(verdict?)` | Remediation register checked against changes and telemetry: implemented or not, and whether the target metric improved |
| `get_timeline(week_from?, week_to?, plant?)` | Changes, major incidents, monitoring tickets, problems, outages and remediations in date order |
| `get_floor_status(week?, plant?)` | One week on the plant floor: each production area's IT share of line-stop time (normal / watch / hurting), device states per plant, fixes done vs planned, incidents opened vs resolved |
| `get_data_freshness()` | Each source's age against its SLA (fresh / late / stale), telemetry also per plant with a volume check; every response also carries a short `data_freshness` summary |

Metrics: `startup_count`, `boot_time_p50`, `boot_time_p90`, `logon_time_p50`,
`logon_time_p90`, `app_crash_rate`, `bsod_rate`, `coverage`. They are defined in
[`metrics/definitions.yaml`](../metrics/definitions.yaml).

## Guardrails

- **Aggregates only.** Tools return grouped results. Device-grain Gold tables are aggregated in SQL and never selected out. Ticket text, callers and owners stay in restricted Silver tables.
- **Fail-closed identifier check.** Every response is scanned for hostnames, GUIDs, email addresses and known serial numbers before it leaves; a match blocks the response.
- **Suppression.** Any group with fewer than 10 devices returns `suppressed` with no value and no supporting counts.
- **Allowlisted inputs.** Every argument is matched against values loaded from the warehouse or the metric definitions, and values reach SQL only as bound parameters. There is no free-form SQL, and the DuckDB connection is opened read-only.
- **Every response is an envelope** with the metric definition, the period with exact dates, the filters, device counts per row, data quality status and the warnings for that slice.
- **Audit log.** Every call is appended to `logs/mcp_audit.jsonl` with caller, tool, arguments, outcome, row count, suppressed groups, warning count and duration. Calls whose arguments fail the MCP input schema (for example an unknown metric name) are rejected by the SDK before reaching the tool, so they are not in this log.

## Run it

```bash
python -m pipeline.run          # build kestrel.duckdb first
python -m mcp_server.server     # stdio server (or: .venv/bin/kestrel-mcp)
```

## Claude Desktop

1. Build the warehouse and install the project into its virtual environment:

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
   .venv/bin/python -m pipeline.run
   ```

2. Open `~/Library/Application Support/Claude/claude_desktop_config.json` (Claude Desktop → Settings → Developer → Edit Config).

3. Add the server from [`claude_desktop_config.example.json`](claude_desktop_config.example.json), replacing `/ABSOLUTE/PATH/TO` with the repository's location. Spaces in the path are fine inside JSON strings.

4. Restart Claude Desktop. `kestrel-fleet` appears under the tools icon. All 16 tools are marked read-only.
