# Kestrel Assembly Group — Source Systems

> **Ample Roots Digital portfolio · Case study 01.** Kestrel Assembly Group, its people, devices and data are fictional and synthetic. Regenerate everything with `python generate_sources.py`.

## The client

Kestrel Assembly Group makes automotive components across three South African plants:

| Plant code | Plant | Notes |
|---|---|---|
| ROS | Rosslyn, Gauteng | Largest site; also hosts head office staff |
| GQB | Gqeberha, Eastern Cape | Some systems still call it Port Elizabeth (PE) |
| DBN | Durban (Prospecton), KwaZulu-Natal | Smallest site |

Around 18,000 Windows devices in three classes, encoded in the hostname (`ROS-OF-12345`):

| Code | Device class | Typical use |
|---|---|---|
| OF | Office laptop | Admin, planning, management |
| EN | Engineering workstation | CAD/CAM, process engineering |
| SF | Shop-floor terminal | Production line, MES, shared logins |

During the reporting window (Mon 31 Aug – Sun 25 Oct 2026), Plant IT rolled out **Windows 11 25H2** in three waves, and the telemetry agent was upgraded from **version 2.3 to 2.4**. Some shop-floor terminals run **Windows 10 LTSC** and are exempt from the rollout.

## Source 1 — Intune device inventory

`raw/intune/kestrel_intune_devices_20261026.csv`

- **What:** Export from Microsoft Intune, taken Monday 26 Oct 2026, 08:00.
- **Grain:** one row per Intune enrollment record (not necessarily per physical device).
- **Format:** CSV, UTF-8 with BOM.
- **Key fields:** `Device ID` (Intune), `Azure AD Device ID`, `Device name`, `Serial number`.
- **Plant** is not a native Intune field: it comes from `Device category`, which Plant IT fills in by hand.

| Column | Meaning |
|---|---|
| Device ID | Intune device ID (changes if a device is re-enrolled) |
| Device name | Hostname |
| Device category | Plant, entered manually |
| Managed by | Intune or Co-managed |
| Compliance | Intune compliance state |
| OS version | Windows build, e.g. `10.0.26200.6901` |
| Last check-in / Enrollment date | Local time (SAST) |
| Serial number, Manufacturer, Model | Hardware identity |
| Primary user UPN | Assigned user; blank for shared devices |
| Azure AD Device ID | Entra ID device ID, used by the telemetry agent |
| Total storage | Disk size |

## Source 2 — Endpoint reliability telemetry

`raw/telemetry/kestrel_reliability_YYYYMMDD.jsonl.gz`

- **What:** Events from the reliability agent installed on every device, landed as one gzipped JSON-lines file per day.
- **Grain:** one line per event.
- **Event types:** startup (boot and logon duration), application crash, blue screen (bug check).
- **Device key:** the Azure AD device ID.
- **Late files:** if an upload fails, the agent service re-sends the whole day as `…_resend.jsonl.gz`.

The agent upgrade changed the event schema:

| | Agent 2.3 | Agent 2.4 |
|---|---|---|
| Event ID | `event_id` | `eventId` |
| Event type | `startup`, `app_crash`, `bugcheck` | `Startup`, `AppCrash`, `BugCheck` |
| Device | nested `device.deviceId`, `device.computer_name` | flat `deviceId`, `fqdn` |
| Time | `ts`, ISO 8601 string | `timestamp`, epoch milliseconds (UTC) |
| Payload | `payload` | `data` |
| Durations | `boot_duration_ms`, `logon_duration_ms` | `bootDurationSec`, `logonDurationSec` |
| OS build | `os_build` (`10.0.26200.6901`) | `osBuild` (`26200.6901`) |

Startup events from kiosks with auto-logon have no logon duration; that is expected, not missing data.

## Source 3 — Plant IT rollout tracker

`raw/plant_it/Win11_25H2_rollout_tracker.csv`

- **What:** The spreadsheet Plant IT uses to plan and track the 25H2 rollout, exported to CSV. Last updated 24 Oct 2026.
- **Grain:** intended to be one row per device, but maintained by hand.
- **Key field:** `Hostname` only (no device IDs).
- **Columns:** Hostname, Plant, Wave, Scheduled Date, Status, Updated By, Comments.

It is the only record of *planned* waves and *intended* outcome. Treat it as a claim to verify against telemetry, not as truth.

## How the sources relate

```
Intune inventory ──(Azure AD Device ID)──► Telemetry events
       │
       └──(Hostname)──► Plant IT rollout tracker
```

The tracker can only be joined by hostname, which means hostnames need careful standardisation first.

## Business questions this data supports

1. Which plants, device classes and models show elevated blue screen, crash, slow boot or slow logon patterns?
2. Are apparent changes real, or caused by changes in which devices are reporting?
3. Did the 25H2 rollout improve or harm reliability, and for which cohorts?
4. Does the rollout tracker agree with what devices actually report?
5. How complete is the telemetry, by plant and by day?

---

# Business sources (Phase 6)

> These sources stand in for Kestrel's service desk, change management, MES and problem
> management systems. They are written by `generate_business_sources.py`, which reads the device
> sources above so that tickets, changes and line stops refer to the same devices and events.
> **Unlike the device sources, the business pipeline was built by the same author as this
> generator**, so its evaluation checks consistency rather than being a blind test.

## Source 4 — Service desk incidents

`raw/itsm/servicedesk_incidents_20261026.csv`

- **What:** Incident export from the service desk tool, taken 26 Oct 2026.
- **Grain:** one row per incident, but reassigned incidents can appear more than once; keep the row with the latest `sys_updated_on`.
- **Format:** CSV, UTF-8 with BOM. Timestamps in SAST, in more than one layout.
- **Key fields:** `number`; `cmdb_ci` (hostname, often messy or blank); `parent_incident` (child tickets of a major incident); `problem_id`; `caused_by_change`.

| Column | Meaning |
|---|---|
| opened_at, first_response_at, resolved_at, closed_at | Lifecycle timestamps (first response = acknowledged) |
| state, priority, category | Free-text drift, e.g. `P2` / `2 - High` / `High` |
| short_description, caller_id | Free text and people's names. **Sensitive: must not leave Silver.** |
| location | Plant, typed by hand |
| assignment_group | Resolver team |
| close_code, reopen_count | Outcome and number of reopens |

## Source 5 — Change requests and group membership

`raw/change/change_requests_20261026.csv` and `raw/intune/group_membership_20261026.csv`

- **What:** CAB change records (Intune policies and app deployments, Windows updates, network), and an export of the Entra/Intune groups those changes target.
- **Grain:** one row per change; one row per group member.
- **Join:** `assignment_target` (group name, typed with drift) → `group_membership.group_name` → `member_name` (hostname). `SITE-DBN` is a site scope listing every Durban device.
- Planned vs actual start/end. Cancelled changes have no actual dates; one change was rolled back, and one is scheduled after the data window.

## Source 6 — MES production lines and line stops

`raw/mes/production_lines.csv`, `raw/mes/line_terminals.csv`, `raw/mes/line_downtime_events.csv`

- **What:** The MES line master, the station → terminal map, and the line stop log.
- **Plants** are MES codes: `P01` Rosslyn, `P02` Gqeberha, `P03` Durban. `site_name` is typed by hand.
- **Stops** are recorded by line and station. `line_terminals.csv` maps a station to its terminal hostname and says whether the station is **line-critical**: only a fault on a critical station stops the line.
- **Reason codes:**
  - IT: `IT-APP` (MES client), `IT-TERM` (station PC);
  - non-IT: `MAT` material, `MECH` mechanical, `QUAL` quality, `CHGOVR` changeover, `UTIL` utilities;
  - `OTHER`, which operators sometimes use for IT stops (see the comment).
- **Times** are local (SAST). `duration_min` is typed by the operator; start and end are system-recorded.

## Source 7 — Remediation register

`raw/remediation/remediation_register.csv`

- **What:** IT problem management's action tracker: what was done, for which problem, through which change, and whether anyone verified it.
- Like the rollout tracker, it is **a claim to verify**. Status and `verified` are typed by hand; `linked_change` is sometimes missing.

## Cost assumptions

`metrics/cost_assumptions.yaml` (added with the business pipeline) holds **illustrative** finance inputs in South African rand: margin per line-minute, the share of lost output recovered by overtime, loaded hourly rates, and cost per ticket. They are invented for the case study, shown as low/high ranges, and meant to be replaced by finance.

## How all the sources relate

```
                     ┌──(Azure AD Device ID)──► Telemetry events
Intune inventory ────┼──(Hostname)──► Plant IT rollout tracker
   (device key)      ├──(Hostname)──► Group membership ◄──(group)── Change requests ◄── Remediation register
                     ├──(Hostname = cmdb_ci)──► Service desk incidents ──(problem / change)──┘
                     └──(Hostname = terminal)──► MES line terminals ◄──(line + station)── MES line stops
```

## Business questions this data supports

6. What do device problems cost in production downtime, lost working time and support effort?
7. Which logged changes preceded a shift in device health, tickets or line stops, for the devices they targeted?
8. Do ticket influxes line up with telemetry, and what does telemetry see that tickets do not (and the reverse)?
9. How quickly are incidents acknowledged and resolved, by priority and category?
10. Which remediations were actually implemented, and did they change the metrics they were meant to fix?
