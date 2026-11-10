"""SQL builders for the business metrics, shared by Gold and the MCP tools (the same pattern
as pipeline/metrics.py). Dimension names are checked against allowlists; values are bound."""

TICKET_DIMS = ("iso_week", "plant_code", "device_class", "category", "priority")
DOWNTIME_DIMS = ("iso_week", "plant_code", "line_id", "it_type")
COST_DIMS = ("iso_week", "plant_code", "cost_type", "driver")
_DENOMINATOR_DIMS = ("iso_week", "plant_code", "device_class")


def _where(filters: dict, allowed: tuple, week_from, week_to) -> tuple[str, list]:
    where, params = ["true"], []
    if week_from:
        where.append("iso_week >= ?"); params.append(week_from)
    if week_to:
        where.append("iso_week <= ?"); params.append(week_to)
    for col, val in (filters or {}).items():
        if col not in allowed:
            raise ValueError(f"not a dimension: {col}")
        where.append(f"{col} = ?"); params.append(val)
    return " AND ".join(where), params


def _check(dims, allowed):
    for d in dims:
        if d not in allowed:
            raise ValueError(f"not a dimension: {d}")


def ticket_sql(dims: list[str], filters: dict | None = None, week_from=None, week_to=None) -> tuple[str, list]:
    """Incident volumes, rate per 1,000 Intune-active devices, MTTA/MTTR, reopens and SLA breaches.
    MTTR, reopens and SLA exclude major-incident children (closed in bulk with the parent)."""
    _check(dims, TICKET_DIMS)
    where, params = _where(filters, TICKET_DIMS, week_from, week_to)
    den_dims = [d for d in dims if d in _DENOMINATOR_DIMS]
    den_filters = {k: v for k, v in (filters or {}).items() if k in _DENOMINATOR_DIMS}
    den_where, den_params = _where(den_filters, _DENOMINATOR_DIMS, week_from, week_to)
    sel = "".join(f"{d}, " for d in dims)
    grp = f"GROUP BY {', '.join(dims)}" if dims else ""
    den_sel = "".join(f"{d}, " for d in den_dims)
    den_grp = f"GROUP BY {', '.join(den_dims)}" if den_dims else ""
    join = f"USING ({', '.join(den_dims)})" if den_dims else "ON true"
    sql = f"""
WITH t AS (SELECT * FROM gold_ticket_fact WHERE {where}),
agg AS (
  SELECT {sel}
         count(*) AS tickets,
         count(*) FILTER (WHERE is_child) AS child_tickets,
         count(*) FILTER (WHERE resolved_at IS NULL) AS open_tickets,
         count(DISTINCT device_key) AS devices,
         median(mtta_min) FILTER (WHERE NOT is_child) AS mtta_p50_min,
         median(mttr_min) FILTER (WHERE NOT is_child) / 60 AS mttr_p50_hours,
         quantile_cont(mttr_min, 0.9) FILTER (WHERE NOT is_child) / 60 AS mttr_p90_hours,
         avg((reopen_count > 0)::INT) FILTER (WHERE NOT is_child) AS reopen_rate,
         avg(sla_breached::INT) FILTER (WHERE NOT is_child AND sla_breached IS NOT NULL) AS sla_breach_rate
  FROM t {grp}
),
den AS (
  SELECT {den_sel} count(*) FILTER (WHERE intune_active) AS active_device_weeks
  FROM gold_device_week WHERE {den_where} {den_grp}
)
SELECT agg.*, den.active_device_weeks,
       1000.0 * agg.tickets / nullif(den.active_device_weeks, 0) AS tickets_per_1k_devices
FROM agg LEFT JOIN den {join}"""
    return sql, params + den_params


def downtime_sql(dims: list[str], filters: dict | None = None, week_from=None, week_to=None) -> tuple[str, list]:
    _check(dims, DOWNTIME_DIMS)
    where, params = _where(filters, DOWNTIME_DIMS, week_from, week_to)
    sel = "".join(f"{d}, " for d in dims)
    grp = f"GROUP BY {', '.join(dims)}" if dims else ""
    sql = f"""
SELECT {sel}
       count(*) AS stops,
       count(DISTINCT line_id) AS lines,
       round(sum(minutes), 1) AS downtime_minutes,
       count(*) FILTER (WHERE is_it) AS it_stops,
       round(coalesce(sum(minutes) FILTER (WHERE is_it), 0), 1) AS it_minutes,
       round(coalesce(sum(minutes) FILTER (WHERE it_type = 'IT-APP'), 0), 1) AS it_app_minutes,
       round(coalesce(sum(minutes) FILTER (WHERE it_type = 'IT-TERM'), 0), 1) AS it_terminal_minutes,
       coalesce(sum(minutes) FILTER (WHERE is_it), 0) / nullif(sum(minutes), 0) AS it_downtime_share,
       count(*) FILTER (WHERE it_reclassified) AS it_reclassified_stops
FROM gold_downtime_fact WHERE {where} {grp}"""
    return sql, params


def cost_sql(dims: list[str], filters: dict | None = None, week_from=None, week_to=None) -> tuple[str, list]:
    _check(dims, COST_DIMS)
    where, params = _where(filters, COST_DIMS, week_from, week_to)
    sel = "".join(f"{d}, " for d in dims)
    grp = f"GROUP BY {', '.join(dims)}" if dims else ""
    sql = f"""
SELECT {sel} round(sum(cost_low)) AS cost_low_zar, round(sum(cost_high)) AS cost_high_zar,
       sum(quantity) AS quantity, any_value(quantity_unit) AS quantity_unit
FROM gold_cost_weekly WHERE {where} {grp}"""
    return sql, params
