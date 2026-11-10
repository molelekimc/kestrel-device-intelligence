"""Read-only MCP server over the Kestrel Gold layer (stdio).

    python -m mcp_server.server

Each tool is a thin wrapper over FleetTools: argument validation, suppression,
identifier checks and audit logging all happen there, so the MCP surface, the
weekly briefing and the evals share one governed code path."""

from functools import lru_cache
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_server.guardrails import IdentifierLeak, ToolInputError
from mcp_server.tools import FleetTools
from pipeline.metrics import metric_names

INSTRUCTIONS = """\
Kestrel Assembly Group (fictional client, synthetic data) Windows fleet reliability metrics.
You can only see governed aggregates: no hostnames, device IDs, serials or users, and groups
under 10 devices are suppressed. Every response carries the metric definition, the period,
device counts and data quality warnings.

When answering:
- Quote the exact ISO week window and the device counts behind every number.
- Always surface the warnings that come back, especially coverage, outage and cohort-mix
  warnings: a KPI change during low coverage may be caused by which devices report.
- Before calling a change real, check it like-for-like with compare_cohorts.
- To credit the Win11 25H2 upgrade with an improvement, use compare_cohorts with
  group_by='rollout_cohort' and compare against a cohort that stayed on its release.
- Label explanations as hypotheses unless the data directly shows them. Never present a
  root cause as proven. Recommend the next check instead.
- Use get_rollout_status to verify the rollout tracker; it is a manual claim.
- For business impact use get_business_impact (line downtime and an ILLUSTRATIVE cost range; always
  say the cost is illustrative), get_ticket_metrics, get_change_correlation (evidence, not proof:
  check mechanism and competing changes), get_remediation_status and get_timeline.
"""

server = MCPServer("kestrel-fleet", instructions=INSTRUCTIONS, version="0.1.0")
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)

Metric = Literal[tuple(metric_names())]
Week = Annotated[str | None, Field(description="ISO week 'YYYY-Www', e.g. 2026-W41 (2026-W36 to 2026-W43).")]
Plant = Annotated[str | None, Field(description="ROS (Rosslyn), GQB (Gqeberha) or DBN (Durban). Names accepted.")]
DeviceClass = Annotated[str | None, Field(description="OF (office laptop), EN (engineering workstation) or SF (shop-floor terminal).")]
Model = Annotated[str | None, Field(description="Hardware model, e.g. 'Elite Mini 800 G9'. See list_allowed_values.")]
OsBuild = Annotated[str | None, Field(description="Windows release or build: 'Win11 25H2', '25H2', '26200', '10.0.26200.6901'.")]


class _Governed:
    """Turns anticipated guardrail failures into ToolError so the model reads the reason
    (e.g. the allowed values). Any other exception stays opaque to the client."""

    def __init__(self, inner: FleetTools):
        self._inner = inner

    def __getattr__(self, name):
        fn = getattr(self._inner, name)

        def call(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (ToolInputError, IdentifierLeak) as e:
                raise ToolError(str(e)) from e
        return call


@lru_cache(maxsize=1)
def tools() -> FleetTools:
    return _Governed(FleetTools(caller="mcp"))


@server.tool(annotations=READ_ONLY)
def list_allowed_values() -> dict:
    """List metrics, dimension values (plants, classes, models, OS releases), ISO weeks and
    other valid arguments. Call this first if unsure what to pass."""
    return tools().list_allowed_values()


@server.tool(annotations=READ_ONLY)
def get_metric_definition(metric: Metric) -> dict:
    """Definition of one metric: formula, unit, grain, direction, caveats and alert threshold."""
    return tools().get_metric_definition(metric)


@server.tool(annotations=READ_ONLY)
def get_kpi(metric: Metric, plant: Plant = None, device_class: DeviceClass = None, model: Model = None,
            os_build: OsBuild = None, week_from: Week = None, week_to: Week = None,
            group_by: Annotated[Literal["plant", "device_class", "model", "os_release"] | None,
                                Field(description="Optional dimension to break the KPI down by.")] = None) -> dict:
    """A KPI per ISO week plus a period total for a slice of the fleet, optionally broken down
    by one dimension. Defaults to all weeks and the whole fleet."""
    return tools().get_kpi(metric, plant, device_class, model, os_build, week_from, week_to, group_by)


@server.tool(annotations=READ_ONLY)
def compare_cohorts(metric: Metric,
                    group_by: Annotated[Literal["none", "plant", "device_class", "model", "os_release", "rollout_cohort"],
                                        Field(description="'rollout_cohort' groups devices by OS release at the end of each period, e.g. 'Win11 24H2 -> Win11 25H2' vs 'stayed on Win11 24H2'.")],
                    period_a: Annotated[str, Field(description="'2026-W36' or a range '2026-W36..2026-W38'.")],
                    period_b: Annotated[str, Field(description="'2026-W42' or a range '2026-W42..2026-W43'.")],
                    like_for_like: Annotated[bool, Field(description="Restrict both periods to devices that reported in both. Default true.")] = True,
                    plant: Plant = None, device_class: DeviceClass = None, model: Model = None,
                    os_build: OsBuild = None) -> dict:
    """Compare a metric between two periods per group. With like_for_like (default) both periods
    use the same devices, so changes in who reports cannot masquerade as a trend; the
    all-reporting comparison is returned alongside for contrast."""
    return tools().compare_cohorts(metric, group_by, period_a, period_b, like_for_like,
                                   plant, device_class, model, os_build)


@server.tool(annotations=READ_ONLY)
def get_coverage(plant: Plant = None, device_class: DeviceClass = None,
                 date_from: Annotated[str | None, Field(description="YYYY-MM-DD, from 2026-08-31.")] = None,
                 date_to: Annotated[str | None, Field(description="YYYY-MM-DD, to 2026-10-25.")] = None,
                 granularity: Annotated[Literal["week", "day"] | None, Field(description="Default 'week'.")] = None) -> dict:
    """Telemetry coverage: devices reporting vs devices active in Intune, by plant (all plants
    if none given), per week or per day. Day granularity reveals outages."""
    return tools().get_coverage(plant, device_class, date_from, date_to, granularity)


@server.tool(annotations=READ_ONLY)
def get_data_quality_status(week: Week = None) -> dict:
    """Pipeline data quality: checks that changed or held back data, quarantine counts by reason,
    and the coverage / outage / cohort warnings for a week (or all weeks)."""
    return tools().get_data_quality_status(week)


@server.tool(annotations=READ_ONLY)
def list_threshold_breaches(week: Annotated[str, Field(description="ISO week, e.g. 2026-W41.")]) -> dict:
    """Week-over-week KPI changes beyond the governed thresholds, per plant, class, model and OS
    release, each with any coverage or cohort warnings that could explain it."""
    return tools().list_threshold_breaches(week)


@server.tool(annotations=READ_ONLY)
def get_rollout_status(plant: Plant = None,
                       wave: Annotated[str | None, Field(description="Pilot, Wave 1, Wave 2, Wave 3, Exempt, TBC or Not tracked.")] = None) -> dict:
    """Win11 25H2 rollout: the Plant IT tracker's claims reconciled against what device telemetry
    shows, plus the weekly OS release mix of reporting devices."""
    return tools().get_rollout_status(plant, wave)


@server.tool(annotations=READ_ONLY)
def get_top_events(event_type: Literal["app_crash", "bugcheck"], week_from: Week = None, week_to: Week = None,
                   plant: Plant = None, device_class: DeviceClass = None, model: Model = None,
                   os_build: OsBuild = None,
                   limit: Annotated[int | None, Field(description="1-20, default 10.")] = None) -> dict:
    """Top crashing applications (name and version) or blue screen bug check codes for a slice,
    with event and device counts and a per-week breakdown."""
    return tools().get_top_events(event_type, week_from, week_to, plant, device_class, model, os_build, limit)


# ------------------------------------------------------------------ business layer (Phase 6)

@server.tool(annotations=READ_ONLY)
def get_business_impact(week_from: Week = None, week_to: Week = None, plant: Plant = None) -> dict:
    """Production and cost impact: MES line stop minutes and the share with an IT cause, plus an
    ILLUSTRATIVE cost range (ZAR) by cost type and driver, with the assumptions used."""
    return tools().get_business_impact(week_from, week_to, plant)


@server.tool(annotations=READ_ONLY)
def get_ticket_metrics(week_from: Week = None, week_to: Week = None, plant: Plant = None,
                       category: Annotated[str | None, Field(description="e.g. MES, Display / blue screen, Performance. See list_allowed_values.")] = None,
                       priority: Annotated[str | None, Field(description="1-4, P1-P4, Critical, High, Moderate or Low.")] = None,
                       group_by: Annotated[Literal["week", "plant", "category", "priority", "device_class"] | None,
                                           Field(description="Default 'week'.")] = None) -> dict:
    """Service desk incidents: volumes, incidents per 1,000 devices, time to acknowledge and resolve,
    reopen and SLA breach rates. Tickets reflect what people report, not device health."""
    return tools().get_ticket_metrics(week_from, week_to, plant, category, priority, group_by)


@server.tool(annotations=READ_ONLY)
def get_change_correlation(week_from: Week = None, week_to: Week = None,
                           change_id: Annotated[str | None, Field(description="e.g. CHG0041031 for full per-metric detail.")] = None,
                           metric: Annotated[Literal["app_crash_rate", "bsod_rate", "boot_time_p50", "coverage",
                                                     "tickets_per_1k", "it_downtime_per_100"] | None,
                                             Field(description="Show every change's evidence on one metric.")] = None,
                           include_no_evidence: bool = False) -> dict:
    """Logged changes ranked by how strongly a metric shifted in the devices they targeted versus
    devices they did not, with the app version or bug check code carrying the shift and any
    competing changes. Evidence, not proof."""
    return tools().get_change_correlation(week_from, week_to, change_id, metric, include_no_evidence)


@server.tool(annotations=READ_ONLY)
def get_remediation_status(verdict: Annotated[str | None, Field(description="effective, no_detectable_effect, claimed_not_implemented, not_yet_measurable, open ...")] = None) -> dict:
    """Remediation actions from the problem register, checked against changes and telemetry: was
    each one implemented, and did the metric it targeted improve (before vs after, vs a comparison group)?"""
    return tools().get_remediation_status(verdict)


@server.tool(annotations=READ_ONLY)
def get_timeline(week_from: Week = None, week_to: Week = None, plant: Plant = None) -> dict:
    """Dated changes, major incidents, monitoring tickets, problem records, telemetry outages and
    remediation actions, to line up what happened when."""
    return tools().get_timeline(week_from, week_to, plant)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
