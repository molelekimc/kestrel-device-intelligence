# Answer Key — Kestrel Assembly Group (private)

> Keep this folder out of the public repo until your pipeline and analysis are done. Use it afterwards to check your Silver layer and your findings against the ground truth in `device_truth.csv`. If you publish it later, frame it as "how I validated the pipeline".

Numbers below are from the default run (`SEED = 20261109`, `N_DEVICES = 18000`). Approximate medians come from raw data with only basic cleaning.

## Planted storylines

### A. Blue screens: a model × build problem that looks like a plant problem

- **What happened:** HP Elite Mini 800 G9 terminals start blue-screening about 20× more often after moving to 25H2. Mostly `0x00000116 VIDEO_TDR_FAILURE` in `igdkmd64.sys` (Intel graphics driver).
- **Why it's a trap:** Gqeberha uses this model on 70% of its shop floor, so the naive view says "Gqeberha has a problem" (weekly blue screens rise from ~10 to ~55). Durban's HP Minis show the same pattern on a smaller base. Rosslyn barely has them.
- **Correct finding:** the cause sits with model + build + driver, not with the plant. Recommendation: pause 25H2 for HP Elite Mini 800 G9, update the graphics driver, pilot again.
- **Breadcrumb:** some tracker comments say "BSOD after update??".

### B. Rosslyn's logon "improvement" is a reporting artefact

- **What happened:** Agent 2.4 silently fails on Windows 10 LTSC. As LTSC shop-floor terminals get the new agent (weeks 5–7), they stop sending telemetry. They still check in to Intune.
- **Why it's a trap:** Rosslyn has the most LTSC terminals (70% of its shop floor), and they have the slowest logons. Rosslyn shop-floor median logon "improves" from ~65 s to ~53 s; reporting devices drop from ~2,800 to ~1,000.
- **Correct finding:** like-for-like (devices reporting in both periods), logon barely moves (~56 s → ~54 s). The real issue is a telemetry coverage gap on LTSC. Coverage = devices reporting ÷ active devices in Intune.

### C. Durban telemetry outage

- **What happened:** No Durban events arrive Mon 5 – Fri 9 Oct 2026.
- **Why it matters:** Durban KPIs for that week are understated, and it overlaps storyline E, hiding Durban's share of the MES crash spike.
- **Correct finding:** flag the gap with a data-coverage check; don't report Durban week 6 as normal.

### D. 25H2 genuinely improves boot time

- **What happened:** after migration, boot time drops ~12% and logon ~7% for the same device.
- **Evidence:** migrated office laptops ~41.8 s → ~36.9 s median boot; non-migrated office laptops flat (~42 s) over the same weeks, giving a natural comparison group.
- **Correct finding:** a real improvement, backed by pre/post per device plus a control group, which is exactly the evidence the AI summary should demand before crediting the migration.

### E. MES release regression

- **What happened:** KestrelMES 4.2.0 (deployed Mon 5 Oct) crashes about 8× more than 4.1.3, until 4.2.1 ships Mon 12 Oct.
- **Why it matters:** a time-based, all-plant spike, unlike storyline A. Crashes line up with `app_version`, not with OS build or model.
- **Twist:** Rosslyn's MES crash counts in weeks 7–8 also fall partly because of storyline B (fewer terminals reporting).

## Planted data quality issues

### Intune inventory
- File starts with a UTF-8 BOM.
- ~2% of devices were re-enrolled: two rows share a serial number and hostname; the stale row has an old check-in, a different Intune ID and a different Azure AD ID.
- 270 retired devices still listed (last check-in 100+ days ago, no telemetry).
- `Device category` (plant) has many spellings: `Rosslyn`, `ROS`, `rosslyn `, `KAG-Rosslyn`, `Port Elizabeth`, `PE Plant`, `Prospecton`, `DURBAN`… and ~3% blank. The hostname prefix is the reliable source.
- Three date formats: `2026-10-25 14:03:11`, `25/10/2026 14:03`, and US-style `10/25/2026 2:03:11 PM`.
- Hostnames: ~4% lowercase, ~2% trailing space.
- `Model`: friendly names, names without the manufacturer, and Lenovo machine-type codes (`21HD5852ZA` = ThinkPad T14 Gen 4; `21ML` = T14 Gen 5; `11T3` = M70q Gen 3; `30FM` = P360).
- `Compliance` casing drift; null stand-ins `""`, `N/A`, `null` in Compliance and Model.
- ~2% of `OS version` values lack the `10.0.` prefix.
- `Total storage` mixes `256 GB`, `238.41 GB`, `256` and `238.41`.

### Telemetry
- Two schemas (see SOURCES.md); durations in **ms** for 2.3 and **seconds** for 2.4. Averaging them raw is the classic mistake.
- Device ID casing and braces differ by agent version; 2.4 sends FQDN instead of hostname.
- ~5% of 2.3 timestamps carry a `+02:00` offset (local time) instead of `Z`.
- ~0.25% impossible boot durations: `-1`, `0`, and sentinel `99999` s (`99999000` ms).
- ~0.4% duplicate events (same event ID) within a day's file.
- 28 Sep file is truncated (last line cut off); the `_resend` file has the full day, so most events appear twice across the two files.
- 15 malformed JSON lines across the period.
- 140 test-lab devices (`ROS-LAB-…`, `GQB-LAB-…`) send telemetry but are not in Intune: orphans to quarantine.

### Rollout tracker
- Three title/blank rows above the header; blank rows and a `TOTAL` row at the bottom.
- Wave labels: `Wave 2`, `W2`, `WAVE 2`, `wave1`, `Pilot`, `wave 3 `, `TBC`, `N/A`.
- Date formats: `15/09/2026`, `2026-09-15`, Excel serial numbers (`46280`), and `15 Sep` without a year.
- Status variants: `Done`, `done `, `Complete`, `Completed`; `Failed`, `Failed - retry`, `Rollback`.
- ~3% of failed migrations are marked done; telemetry shows the old build.
- ~4% of wave 3 devices also have a leftover wave 2 row marked `Rescheduled`.
- ~0.6% hostname typos (letter O for zero, missing dash).
- `Gqeberha` sometimes recorded as `Port Elizabeth`.

## Ground truth file

`device_truth.csv`, one row per device (including lab devices): hostname, Azure AD device ID, plant, device class, model, LTSC flag, lab flag, rollout wave, scheduled date, actual migration date, and agent 2.4 upgrade date.
