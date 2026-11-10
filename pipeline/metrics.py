"""Single source of metric SQL. Gold uses it to materialise weekly KPIs, and the
MCP server uses it for arbitrary periods and cohorts, so a metric is computed
the same way everywhere. Formulas come from metrics/definitions.yaml."""

from functools import lru_cache

import yaml

from pipeline import config

DIM_COLUMNS = ("iso_week", "plant_code", "device_class", "model", "os_release")


@lru_cache(maxsize=1)
def definitions() -> dict:
    with open(config.DEFINITIONS_PATH) as f:
        return yaml.safe_load(f)


def metric_names() -> list[str]:
    return list(definitions()["metrics"])


def kpi_sql(group_by: list[str], filters: dict[str, str] | None = None,
            week_from: str | None = None, week_to: str | None = None,
            cohort_sql: str | None = None) -> tuple[str, list]:
    """SQL returning one row per group with base aggregates and every metric.

    group_by and filter keys must be DIM_COLUMNS (callers validate values).
    cohort_sql, if given, is a trusted SELECT of device_key values built by code
    (never from user text) that restricts the devices included."""
    for col in [*group_by, *(filters or {})]:
        if col not in DIM_COLUMNS:
            raise ValueError(f"not a dimension column: {col}")
    where, params = ["true"], []
    if week_from:
        where.append("iso_week >= ?"); params.append(week_from)
    if week_to:
        where.append("iso_week <= ?"); params.append(week_to)
    for col, value in (filters or {}).items():
        where.append(f"{col} = ?"); params.append(value)
    if cohort_sql:
        where.append(f"device_key IN ({cohort_sql})")

    dims = ", ".join(group_by)
    sel = f"{dims}, " if dims else ""
    grp = f"GROUP BY {dims}" if dims else ""
    join = f"USING ({dims})" if dims else "ON true"
    metric_cols = ",\n  ".join(
        f"({m['expression']}) AS {name}" for name, m in definitions()["metrics"].items())

    sql = f"""
WITH dw AS (
  SELECT * FROM gold_device_week WHERE {' AND '.join(where)}
), dev AS (
  SELECT {sel}
         count(*) FILTER (WHERE intune_active)               AS intune_active_device_weeks,
         count(*) FILTER (WHERE reporting)                   AS reporting_device_weeks,
         count(*) FILTER (WHERE reporting AND intune_active) AS covered_device_weeks,
         count(DISTINCT device_key) FILTER (WHERE intune_active) AS devices_active,
         count(DISTINCT device_key) FILTER (WHERE reporting)     AS devices_reporting,
         coalesce(sum(startups), 0)    AS startups,
         coalesce(sum(app_crashes), 0) AS app_crashes,
         coalesce(sum(bugchecks), 0)   AS bugchecks
  FROM dw {grp}
), ev AS (
  SELECT {sel}
         quantile_cont(e.boot_s, 0.5)  AS boot_p50,
         quantile_cont(e.boot_s, 0.9)  AS boot_p90,
         quantile_cont(e.logon_s, 0.5) AS logon_p50,
         quantile_cont(e.logon_s, 0.9) AS logon_p90,
         count(e.boot_s)  AS boot_samples,
         count(e.logon_s) AS logon_samples
  FROM gold_event_fact e JOIN dw USING (device_key, iso_week)
  WHERE e.event_type = 'startup' {grp}
)
SELECT {sel}
  intune_active_device_weeks, reporting_device_weeks, covered_device_weeks,
  devices_active, devices_reporting, startups, app_crashes, bugchecks,
  boot_p50, boot_p90, logon_p50, logon_p90, boot_samples, logon_samples,
  {metric_cols}
FROM dev LEFT JOIN ev {join}"""
    return sql, params
