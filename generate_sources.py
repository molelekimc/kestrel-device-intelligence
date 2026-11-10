"""
Kestrel Assembly Group (fictional) - synthetic source generator
Ample Roots Digital portfolio | Case study 01: Enterprise Device Intelligence

Every company, person, device and value produced here is fictional and synthetic.

Writes three raw sources, the way they would land in a Bronze layer:

  raw/intune/kestrel_intune_devices_20261026.csv
      Intune device inventory export (one snapshot, taken Mon 26 Oct 2026).
  raw/telemetry/kestrel_reliability_YYYYMMDD.jsonl.gz
      Endpoint reliability agent events, one gzipped JSON-lines file per day,
      Mon 31 Aug - Sun 25 Oct 2026 (8 weeks).
  raw/plant_it/Win11_25H2_rollout_tracker.csv
      Plant IT's Windows 11 25H2 rollout spreadsheet, exported to CSV.

It also writes answer_key/device_truth.csv: the ground truth per device, so a
Silver layer can be checked against what really happened. Keep answer_key/
out of the public repo until the pipeline is built.

Change SEED or N_DEVICES to regenerate at another scale.
"""
import csv
import gzip
import json
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

SEED = 20261109
N_DEVICES = 18_000
N_LAB = 140                      # test-lab devices: send telemetry, never enrolled in Intune
N_RETIRED = 270                  # retired devices still listed in Intune
START = date(2026, 8, 31)        # Monday
N_DAYS = 56                      # to Sunday 25 Oct 2026
SNAPSHOT = datetime(2026, 10, 26, 8, 0)   # Intune export time (SAST)
TRACKER_DATE = date(2026, 10, 24)

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
KEY = os.path.join(HERE, "answer_key")

rng = np.random.default_rng(SEED)
SAST = timezone(timedelta(hours=2))
UTC = timezone.utc


def guid():
    return str(uuid.UUID(bytes=rng.bytes(16), version=4))


def day(i):
    return START + timedelta(days=int(i))


def pick(options, p=None):
    return options[rng.choice(len(options), p=p)]


# --------------------------------------------------------------------------
# 1. The real device fleet (ground truth)
# --------------------------------------------------------------------------
PLANTS = ["ROS", "GQB", "DBN"]            # Rosslyn, Gqeberha, Durban (Prospecton)
PLANT_NAME = {"ROS": "Rosslyn", "GQB": "Gqeberha", "DBN": "Durban"}
CLASSES = ["OF", "EN", "SF"]              # office laptop, engineering workstation, shop-floor terminal

OFFICE_MODELS = (["Lenovo ThinkPad T14 Gen 4", "Lenovo ThinkPad T14 Gen 5",
                  "HP EliteBook 840 G10", "Dell Latitude 5440"], [.35, .25, .25, .15])
ENG_MODELS = (["Dell Precision 3660", "HP Z2 G9", "Lenovo ThinkStation P360"], [.4, .3, .3])
SF_MODELS = ["Lenovo ThinkCentre M70q Gen 3", "HP Elite Mini 800 G9", "Advantech UTC-520"]
SF_MIX = {"ROS": [.75, .10, .15], "GQB": [.15, .70, .15], "DBN": [.15, .40, .45]}
LTSC_SHARE = {"ROS": .70, "GQB": .20, "DBN": .40}   # shop-floor terminals on Windows 10 LTSC
LENOVO_MTM = {"Lenovo ThinkPad T14 Gen 4": "21HD", "Lenovo ThinkPad T14 Gen 5": "21ML",
              "Lenovo ThinkCentre M70q Gen 3": "11T3", "Lenovo ThinkStation P360": "30FM"}

BUILD_24H2 = "10.0.26100.6584"
BUILD_25H2 = "10.0.26200.6901"
BUILD_LTSC = "10.0.19044.6332"
NEVER = 10 ** 6

n_real = N_DEVICES
plant = rng.choice(PLANTS, n_real, p=[.45, .35, .20])
cls = rng.choice(CLASSES, n_real, p=[.45, .15, .40])
model = np.empty(n_real, dtype=object)
for c, (names, p) in (("OF", OFFICE_MODELS), ("EN", ENG_MODELS)):
    m = cls == c
    model[m] = rng.choice(names, m.sum(), p=p)
ltsc = np.zeros(n_real, bool)
for pl in PLANTS:
    m = (cls == "SF") & (plant == pl)
    model[m] = rng.choice(SF_MODELS, m.sum(), p=SF_MIX[pl])
    ltsc[m] = rng.random(m.sum()) < LTSC_SHARE[pl]

# Lab devices (telemetry only) appended at the end of the arrays
lab_plant = rng.choice(["ROS", "GQB"], N_LAB, p=[.7, .3])
plant = np.concatenate([plant, lab_plant])
cls = np.concatenate([cls, np.array(["OF"] * N_LAB)])
model = np.concatenate([model, np.array(["Lenovo ThinkPad T14 Gen 4"] * N_LAB, dtype=object)])
ltsc = np.concatenate([ltsc, np.zeros(N_LAB, bool)])
lab = np.concatenate([np.zeros(n_real, bool), np.ones(N_LAB, bool)])
n = n_real + N_LAB

numbers = rng.permutation(np.arange(1000, 99000))[:n]
hostname = np.array([f"{p}-LAB-{s % 1000:03d}{i % 10}" if is_lab else f"{p}-{c}-{s:05d}"
                     for i, (p, c, s, is_lab) in enumerate(zip(plant, cls, numbers, lab))], dtype=object)
aad_id = np.array([guid() for _ in range(n)], dtype=object)
intune_id = np.array([guid() for _ in range(n)], dtype=object)
manufacturer = np.array([m.split()[0] for m in model], dtype=object)
mes_user = (cls == "SF") | ((cls == "OF") & (rng.random(n) < .30))

# Windows 11 25H2 rollout (LTSC terminals are exempt)
eligible = ~ltsc & ~lab
wave = np.zeros(n, int)                  # 0 = backlog / exempt
sched = np.full(n, -1)
pilot = eligible & (plant == "ROS") & np.isin(cls, ["OF", "EN"]) & (rng.random(n) < .12)
wave[pilot], sched[pilot] = 1, rng.integers(14, 17, pilot.sum())
rest = eligible & ~pilot
r = rng.random(n)
w2 = rest & (r < .48)
w3 = rest & (r >= .48) & (r < .90)
wave[w2], sched[w2] = 2, rng.integers(21, 34, w2.sum())
wave[w3], sched[w3] = 3, rng.integers(38, 55, w3.sum())
succeeded = rng.random(n) < .93
mig_day = np.where((wave > 0) & succeeded, sched + rng.integers(0, 3, n), NEVER)

# Telemetry agent upgrade 2.3 -> 2.4 (new schema; silently fails on Windows 10 LTSC)
agent_day = np.full(n, NEVER)
m = np.isin(cls, ["OF", "EN"]) & ~lab & (rng.random(n) < .85)
agent_day[m] = rng.integers(14, 29, m.sum())
m = (cls == "SF") & (rng.random(n) < .90)
agent_day[m] = rng.integers(28, 46, m.sum())

# Device-level performance (seconds)
boot_base = np.select([cls == "OF", cls == "EN", ltsc], [42, 50, 64], 58).astype(float)
boot_base *= np.where(model == "Advantech UTC-520", 1.25, 1.0)
logon_base = np.select([cls == "OF", cls == "EN", ltsc], [30, 38, 74], 52).astype(float)
dev_boot = boot_base * rng.lognormal(0, .2, n)
dev_logon = logon_base * rng.lognormal(0, .2, n)
autologon = ltsc & (rng.random(n) < .15)       # kiosks with auto-logon have no logon time

# --------------------------------------------------------------------------
# 2. Telemetry events
# --------------------------------------------------------------------------
APP_VERSIONS = {"ms-teams.exe": "25212.2204.3818.1950", "OUTLOOK.EXE": "16.0.19127.20154",
                "EXCEL.EXE": "16.0.19127.20154", "saplogon.exe": "8.00.0.12",
                "ugraf.exe": "2406.4000.0.0"}
APP_RATE = {   # crashes per device per day
    "ms-teams.exe": np.select([cls == "OF", cls == "EN"], [.020, .015], 0),
    "OUTLOOK.EXE": np.where(cls == "OF", .012, 0),
    "EXCEL.EXE": np.select([cls == "OF", cls == "EN"], [.008, .006], 0),
    "saplogon.exe": np.select([cls == "OF", cls == "SF"], [.006, .004], 0),
    "ugraf.exe": np.where(cls == "EN", .030, 0),
    "KestrelMES.exe": np.where(mes_user, .012, 0),
}
EXC = ["0xc0000005", "0xc0000409", "0xe0434352", "0xc0000374"]
MODULES = ["ntdll.dll", "KERNELBASE.dll", "ucrtbase.dll", "combase.dll"]
BASE_BUGCHECKS = [("0x0000007E", "SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", "ntoskrnl.exe"),
                  ("0x0000003B", "SYSTEM_SERVICE_EXCEPTION", "win32kfull.sys"),
                  ("0x00000050", "PAGE_FAULT_IN_NONPAGED_AREA", "ntfs.sys"),
                  ("0x0000009F", "DRIVER_POWER_STATE_FAILURE", "pci.sys"),
                  ("0x000000EF", "CRITICAL_PROCESS_DIED", "ntoskrnl.exe")]
TDR = ("0x00000116", "VIDEO_TDR_FAILURE", "igdkmd64.sys")


def mes_version(d):
    return "4.1.3" if d < 35 else ("4.2.0" if d <= 41 else "4.2.1")


def local_ts(d, hours):
    return (datetime.combine(day(d), time(0), SAST) + timedelta(hours=float(hours))).astimezone(UTC)


def serialise(i, etype, ts, payload, new_agent):
    """Shape one event the way agent 2.3 or 2.4 would send it."""
    build = payload.pop("build")
    if not new_agent:
        if rng.random() < .05:   # one relay forwards local time with an offset
            ts_out = ts.astimezone(SAST).isoformat(timespec="seconds")
        else:
            ts_out = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {"os_build": build}
        if etype == "startup":
            body["boot_duration_ms"] = payload["boot"]
            body["logon_duration_ms"] = payload["logon"]
        elif etype == "app_crash":
            body.update(app_name=payload["app"], app_version=payload["ver"],
                        exception_code=payload["exc"], faulting_module=payload["mod"])
        else:
            body.update(bugcheck_code=payload["code"], bugcheck_name=payload["name"],
                        driver=payload["driver"])
        return {"event_id": guid(), "event_type": etype, "agent_version": "2.3.7",
                "device": {"deviceId": aad_id[i].lower(), "computer_name": hostname[i]},
                "ts": ts_out, "payload": body}
    body = {"osBuild": build.replace("10.0.", "")}
    if etype == "startup":
        body["bootDurationSec"] = payload["boot"]
        if payload["logon"] is not None:
            body["logonDurationSec"] = payload["logon"]
    elif etype == "app_crash":
        body.update(appName=payload["app"], appVersion=payload["ver"],
                    exceptionCode=payload["exc"], faultingModule=payload["mod"])
    else:
        body.update(bugCheckCode=payload["code"], driverName=payload["driver"])
    return {"eventId": guid(),
            "eventType": {"startup": "Startup", "app_crash": "AppCrash", "bugcheck": "BugCheck"}[etype],
            "agentVersion": "2.4.1", "deviceId": "{" + aad_id[i].upper() + "}",
            "fqdn": hostname[i].lower() + ".kestrel.local",
            "timestamp": int(ts.timestamp() * 1000), "data": body}


os.makedirs(os.path.join(RAW, "telemetry"), exist_ok=True)
malformed_days = set(rng.choice(N_DAYS, 15, replace=False).tolist())
DBN_GAP = range(35, 40)            # Mon 5 - Fri 9 Oct: DBN events never arrive
RESEND_DAY = 28                    # Mon 28 Sep: truncated upload, re-sent later

for d in range(N_DAYS):
    weekday = day(d).weekday() < 5
    active = ~(ltsc & (d >= agent_day))
    if d in DBN_GAP:
        active &= plant != "DBN"
    migrated = d >= mig_day
    new_agent = d >= agent_day
    build = np.where(ltsc, BUILD_LTSC, np.where(migrated, BUILD_25H2, BUILD_24H2))
    events = []

    # startups (boot + logon)
    rate = np.where(cls == "SF", .30, .62 if weekday else .08)
    k = np.minimum(rng.poisson(rate), 2) * active
    idx = np.repeat(np.arange(n), k)
    boot = dev_boot[idx] * np.where(migrated[idx], .88, 1) * rng.lognormal(0, .25, len(idx))
    logon = dev_logon[idx] * np.where(migrated[idx], .93, 1) * rng.lognormal(0, .25, len(idx))
    hours = np.where(cls[idx] == "SF", rng.uniform(0, 24, len(idx)),
                     np.clip(rng.normal(7.3, .9, len(idx)), 5.5, 11))
    for j, i in enumerate(idx):
        na = bool(new_agent[i])
        b, l = float(boot[j]), (None if autologon[i] else float(logon[j]))
        if rng.random() < .0025:                       # broken readings
            bad = pick([-1, 0, 99999])
            b = float(bad)
        if na:
            b = round(b, 1)
            l = None if l is None else round(l, 1)
        else:
            b = int(b * 1000) if b >= 0 else int(b * 1000)
            l = None if l is None else int(l * 1000)
        events.append(serialise(i, "startup", local_ts(d, hours[j]),
                                {"boot": b, "logon": l, "build": build[i]}, na))

    # application crashes
    for app, base in APP_RATE.items():
        lam = base * active
        if app == "KestrelMES.exe" and 35 <= d <= 41:
            lam = lam * 8                               # MES 4.2.0 regression
        for i in np.repeat(np.arange(n), rng.poisson(lam)):
            ver = mes_version(d) if app == "KestrelMES.exe" else APP_VERSIONS[app]
            hrs = rng.uniform(0, 24) if cls[i] == "SF" else rng.uniform(7, 18)
            events.append(serialise(i, "app_crash", local_ts(d, hrs),
                                    {"app": app, "ver": ver, "exc": pick(EXC),
                                     "mod": pick(MODULES + [app.replace(".exe", ".dll")]),
                                     "build": build[i]}, bool(new_agent[i])))

    # blue screens
    tdr_risk = (model == "HP Elite Mini 800 G9") & migrated
    lam = np.where(tdr_risk, .006, .0003) * active
    for i in np.repeat(np.arange(n), rng.poisson(lam)):
        code = TDR if (tdr_risk[i] and rng.random() < .85) else pick(BASE_BUGCHECKS)
        events.append(serialise(i, "bugcheck", local_ts(d, rng.uniform(0, 24)),
                                {"code": code[0], "name": code[1], "driver": code[2],
                                 "build": build[i]}, bool(new_agent[i])))

    lines = [json.dumps(e, separators=(",", ":")) for e in events]
    dup = rng.random(len(lines)) < .004                 # agent retries
    lines += [ln for ln, x in zip(lines, dup) if x]
    lines = [lines[i] for i in rng.permutation(len(lines))]
    if d in malformed_days:
        j = rng.integers(len(lines))
        lines[j] = lines[j][: rng.integers(20, len(lines[j]) - 5)]

    stamp = day(d).strftime("%Y%m%d")
    path = os.path.join(RAW, "telemetry", f"kestrel_reliability_{stamp}.jsonl.gz")
    if d == RESEND_DAY:
        cut = int(len(lines) * .7)
        partial = lines[:cut]
        partial[-1] = partial[-1][: len(partial[-1]) // 2]
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write("\n".join(partial) + "\n")
        path = path.replace(".jsonl.gz", "_resend.jsonl.gz")
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

# --------------------------------------------------------------------------
# 3. Intune inventory export
# --------------------------------------------------------------------------
FIRST = ["Thabo", "Lerato", "Sipho", "Ayanda", "Pieter", "Anika", "Johan", "Nomvula", "Kagiso",
         "Zanele", "Ruan", "Priya", "Yusuf", "Naledi", "Bongani", "Chantelle", "Tshepo", "Megan",
         "Lwazi", "Fatima"]
LAST = ["Mokoena", "Nkosi", "Dlamini", "van der Merwe", "Naidoo", "Botha", "Khumalo", "Pillay",
        "Mahlangu", "Smith", "Ndlovu", "Jacobs", "Pretorius", "Mthembu", "Govender", "Molefe",
        "Steyn", "Zulu", "Adams", "Sithole"]
CATEGORY = {"ROS": ["Rosslyn", "ROS", "Rosslyn Plant", "rosslyn ", "KAG-Rosslyn"],
            "GQB": ["Gqeberha", "GQB", "Port Elizabeth", "PE Plant", "gqeberha"],
            "DBN": ["Durban", "DBN", "Prospecton", "Durban Plant", "DURBAN"]}


def serial(manu):
    alnum = list("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789")
    if manu == "Lenovo":
        return "PF" + "".join(rng.choice(alnum, 6))
    if manu == "HP":
        return pick(["CZC", "5CG", "MXL"]) + "".join(rng.choice(alnum, 7))
    if manu == "Dell":
        return "".join(rng.choice(alnum, 7))
    return "AKA" + "".join(rng.choice(list("0123456789"), 7))


def fmt_dt(ts):
    r = rng.random()
    if r < .85:
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    if r < .95:
        return ts.strftime("%d/%m/%Y %H:%M")
    return ts.strftime("%m/%d/%Y %I:%M:%S %p").lstrip("0")


def null():
    return pick(["", "N/A", "null"])


def model_text(m):
    if m in LENOVO_MTM and rng.random() < .30:
        return LENOVO_MTM[m] + "".join(rng.choice(list("0123456789"), 4)) + "ZA"
    return m.replace("Lenovo ", "").replace("HP ", "").replace("Dell ", "") if rng.random() < .5 else m


def storage(c):
    gb = pick([256, 512]) if c == "OF" else pick([512, 1024])
    return pick([f"{gb} GB", f"{gb * .9313:.2f} GB", str(gb), f"{gb * .9313:.2f}"])


def upn():
    f, l = pick(FIRST), pick(LAST)
    return f"{f.lower()}.{l.lower().replace(' ', '')}{'' if rng.random() < .7 else rng.integers(1, 99)}@kestrel-ag.example"


def name_noise(h):
    r = rng.random()
    return h.lower() if r < .04 else (h + " " if r < .06 else h)


rows = []
serials = {}
reenrolled = rng.random(n) < .02
for i in range(n_real):
    s = serials.setdefault(i, serial(manufacturer[i]))
    cur_build = BUILD_LTSC if ltsc[i] else (BUILD_25H2 if mig_day[i] <= N_DAYS - 1 else BUILD_24H2)
    ago = rng.uniform(0, 3) if rng.random() < .97 else rng.uniform(8, 30)
    last = SNAPSHOT - timedelta(days=float(ago))
    enrolled = datetime(2023, 1, 1) + timedelta(days=float(rng.uniform(0, 1300)))
    if reenrolled[i]:
        enrolled = datetime(2026, 3, 1) + timedelta(days=float(rng.uniform(0, 150)))
    user = "" if cls[i] == "SF" else upn()
    compliance = pick(["Compliant", "Noncompliant", "In grace period", "Not evaluated"], [.88, .06, .03, .03])
    if rng.random() < .02:
        compliance = compliance.lower()
    os_version = cur_build if rng.random() > .02 else cur_build.replace("10.0.", "")
    row = {
        "Device ID": intune_id[i], "Device name": name_noise(hostname[i]),
        "Device category": pick(CATEGORY[plant[i]]) if rng.random() > .03 else "",
        "Managed by": "Co-managed" if cls[i] == "SF" else "Intune", "Ownership": "Corporate",
        "Compliance": compliance if rng.random() > .01 else null(), "OS": "Windows",
        "OS version": os_version, "Last check-in": fmt_dt(last), "Enrollment date": fmt_dt(enrolled),
        "Serial number": s, "Manufacturer": manufacturer[i],
        "Model": model_text(model[i]) if rng.random() > .01 else null(),
        "Primary user UPN": user, "Azure AD Device ID": aad_id[i], "Total storage": storage(cls[i]),
    }
    rows.append(row)
    if reenrolled[i]:   # stale record left behind by the old enrollment
        old = dict(row)
        old_last = enrolled - timedelta(days=float(rng.uniform(1, 20)))
        old.update({"Device ID": guid(), "Azure AD Device ID": guid(), "Last check-in": fmt_dt(old_last),
                    "Enrollment date": fmt_dt(datetime(2023, 1, 1) + timedelta(days=float(rng.uniform(0, 400)))),
                    "Compliance": "Noncompliant", "OS version": BUILD_24H2 if not ltsc[i] else BUILD_LTSC})
        rows.append(old)

for _ in range(N_RETIRED):   # retired devices nobody removed
    p, c = pick(PLANTS), pick(CLASSES, [.6, .1, .3])
    m = pick(OFFICE_MODELS[0]) if c == "OF" else (pick(ENG_MODELS[0]) if c == "EN" else pick(SF_MODELS))
    last = SNAPSHOT - timedelta(days=float(rng.uniform(100, 420)))
    rows.append({
        "Device ID": guid(), "Device name": f"{p}-{c}-{rng.integers(99000, 99999)}",
        "Device category": pick(CATEGORY[p]), "Managed by": "Intune", "Ownership": "Corporate",
        "Compliance": "Noncompliant", "OS": "Windows", "OS version": pick([BUILD_24H2, "10.0.22631.4317"]),
        "Last check-in": fmt_dt(last), "Enrollment date": fmt_dt(last - timedelta(days=float(rng.uniform(300, 900)))),
        "Serial number": serial(m.split()[0]), "Manufacturer": m.split()[0], "Model": model_text(m),
        "Primary user UPN": "" if c == "SF" else upn(), "Azure AD Device ID": guid(), "Total storage": storage(c),
    })

inv = pd.DataFrame(rows)
inv = inv.iloc[rng.permutation(len(inv))]
os.makedirs(os.path.join(RAW, "intune"), exist_ok=True)
inv.to_csv(os.path.join(RAW, "intune", "kestrel_intune_devices_20261026.csv"),
           index=False, encoding="utf-8-sig")

# --------------------------------------------------------------------------
# 4. Plant IT rollout tracker (spreadsheet export)
# --------------------------------------------------------------------------
WAVE_TEXT = {1: ["Wave 1", "W1", "wave1", "Pilot"], 2: ["Wave 2", "W2", "WAVE 2"], 3: ["Wave 3", "W3", "wave 3 "]}
PLANT_TEXT = {"ROS": ["Rosslyn"], "GQB": ["Gqeberha", "Gqeberha", "Gqeberha", "Port Elizabeth"], "DBN": ["Durban"]}
DONE = ["Done", "Completed", "done ", "Complete"]
FAILED = ["Failed", "Failed - retry", "Rollback"]
TECHS = ["TM", "SN", "JvdM", "KP", "AN"]


def fmt_date(d):
    r = rng.random()
    if r < .60:
        return d.strftime("%d/%m/%Y")
    if r < .85:
        return d.isoformat()
    if r < .95:
        return str((d - date(1899, 12, 30)).days)        # Excel serial number
    return d.strftime("%d %b").lstrip("0")


def host_typo(h):
    r = rng.random()
    if r < .003:
        return h.replace("0", "O", 1)
    if r < .006:
        return h.replace("-", "", 2)[:3] + "-" + h.replace("-", "", 2)[3:]
    return h


tracker = []
for i in range(n_real):
    if wave[i] > 0:
        sd = day(sched[i])
        if sd > TRACKER_DATE:
            status = pick(["Scheduled", "Pending"])
        elif mig_day[i] < NEVER:
            status = pick(DONE) if day(mig_day[i]) <= TRACKER_DATE else "In progress"
        else:
            status = pick(DONE) if rng.random() < .03 else pick(FAILED)   # 3% marked done but never happened
        comment = ""
        if model[i] == "HP Elite Mini 800 G9" and mig_day[i] < NEVER and rng.random() < .08:
            comment = pick(["BSOD after update??", "user says screen goes black", "blue screen x2 - logged"])
        elif rng.random() < .03:
            comment = pick(["user on leave", "machine offline", "rescheduled", "line down for maintenance"])
        if wave[i] == 3 and rng.random() < .04:      # moved from wave 2, old row left in
            tracker.append([host_typo(hostname[i]), pick(PLANT_TEXT[plant[i]]), pick(WAVE_TEXT[2]),
                            fmt_date(day(sched[i] - rng.integers(10, 18))), "Rescheduled", pick(TECHS),
                            "moved to W3"])
        tracker.append([host_typo(hostname[i]), pick(PLANT_TEXT[plant[i]]), pick(WAVE_TEXT[wave[i]]),
                        fmt_date(sd), status, pick(TECHS), comment])
    elif ltsc[i] and rng.random() < .5:
        tracker.append([hostname[i], pick(PLANT_TEXT[plant[i]]), "N/A", "", "Exempt - LTSC", pick(TECHS), ""])
    elif eligible[i] and rng.random() < .6:
        tracker.append([hostname[i], pick(PLANT_TEXT[plant[i]]), "TBC", "", "Not started", "", ""])

order = rng.permutation(len(tracker))
tracker = [tracker[i] for i in order]
os.makedirs(os.path.join(RAW, "plant_it"), exist_ok=True)
with open(os.path.join(RAW, "plant_it", "Win11_25H2_rollout_tracker.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Kestrel Assembly Group - Windows 11 25H2 Rollout Tracker"] + [""] * 6)
    w.writerow([f"Owner: Plant IT | Last updated: {TRACKER_DATE.strftime('%d/%m/%Y')}"] + [""] * 6)
    w.writerow([""] * 7)
    w.writerow(["Hostname", "Plant", "Wave", "Scheduled Date", "Status", "Updated By", "Comments"])
    w.writerows(tracker)
    w.writerow([""] * 7)
    w.writerow([""] * 7)
    w.writerow(["TOTAL", "", "", "", f"{len(tracker)} devices", "", ""])

# --------------------------------------------------------------------------
# 5. Ground truth (keep private until the pipeline is built)
# --------------------------------------------------------------------------
os.makedirs(KEY, exist_ok=True)
pd.DataFrame({
    "hostname": hostname, "aad_device_id": aad_id, "plant": plant, "device_class": cls, "model": model,
    "is_ltsc": ltsc, "is_lab_device": lab, "rollout_wave": wave,
    "scheduled_date": [day(s).isoformat() if s >= 0 else "" for s in sched],
    "migrated_date": [day(m).isoformat() if m < N_DAYS else "" for m in mig_day],
    "agent_24_date": [day(a).isoformat() if a < N_DAYS else "" for a in agent_day],
}).to_csv(os.path.join(KEY, "device_truth.csv"), index=False)

print(f"Done: {n_real:,} devices (+{N_LAB} lab), {len(inv):,} Intune rows, {len(tracker):,} tracker rows.")
