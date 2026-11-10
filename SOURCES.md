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
