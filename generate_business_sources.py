"""
Kestrel Assembly Group (fictional) - synthetic BUSINESS source generator
Ample Roots Digital portfolio | Case study 01, Phase 6

Every company, person, device and value produced here is fictional and synthetic.

Stands in for Kestrel's other systems. It reads the device sources written by
generate_sources.py (raw/telemetry and answer_key/device_truth.csv) so that tickets,
changes, line stops and remediations line up with the same devices and events.
It never modifies those files.

Writes:
  raw/itsm/servicedesk_incidents_20261026.csv     ServiceNow-style incident export
  raw/change/change_requests_20261026.csv         CAB change records
  raw/intune/group_membership_20261026.csv        Entra/Intune group membership export
  raw/mes/production_lines.csv                    MES line master
  raw/mes/line_terminals.csv                      MES station -> terminal map
  raw/mes/line_downtime_events.csv                MES line stop log
  raw/remediation/remediation_register.csv        Problem management action tracker
  answer_key/BUSINESS_ANSWER_KEY.md               planted storylines and issues
  answer_key/business_truth/*.csv                 ground truth for evaluation

Unlike the device sources, the business pipeline was written by the same author
as this generator, so it is not a blind build. Run with: python generate_business_sources.py
"""
import csv
import json
import os
import random
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

import duckdb

SEED = 20261110
START = date(2026, 8, 31)
N_DAYS = 56
END = START + timedelta(days=N_DAYS - 1)          # Sun 25 Oct 2026
SAST = timezone(timedelta(hours=2))
SNAPSHOT = datetime(2026, 10, 26, 8, 0, tzinfo=SAST)

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
KEY = os.path.join(HERE, "answer_key")
TRUTH_DIR = os.path.join(KEY, "business_truth")

rng = random.Random(SEED)


def day(i):
    return START + timedelta(days=i)


def day_index(d):
    return (d - START).days


def pick(seq, weights=None):
    return rng.choices(seq, weights=weights, k=1)[0]


def poisson(lam):
    """Knuth's method; fine for the small rates used here."""
    if lam <= 0:
        return 0
    l, k, p = pow(2.718281828459045, -lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def lognormal_minutes(median, sigma=0.45, lo=1.0, hi=600.0):
    return max(lo, min(hi, median * pow(2.718281828459045, rng.gauss(0, sigma))))


# --------------------------------------------------------------------------
# 0. Read the device world written by generate_sources.py
# --------------------------------------------------------------------------
devices = {}
with open(os.path.join(KEY, "device_truth.csv")) as f:
    for r in csv.DictReader(f):
        if r["is_lab_device"] == "True":
            continue
        r["is_ltsc"] = r["is_ltsc"] == "True"
        r["wave"] = int(r["rollout_wave"])
        for k in ("scheduled_date", "migrated_date", "agent_24_date"):
            r[k] = date.fromisoformat(r[k]) if r[k] else None
        devices[r["hostname"]] = r
by_aad = {d["aad_device_id"].lower(): h for h, d in devices.items()}
hosts = sorted(devices)
MINI = "HP Elite Mini 800 G9"
DBN_GAP = {day(i) for i in range(35, 40)}         # Mon 5 - Fri 9 Oct, no Durban telemetry


def silent(h, d):
    """True when the device's telemetry for day d never reached the raw files."""
    dv = devices[h]
    if dv["is_ltsc"] and dv["agent_24_date"] and d >= dv["agent_24_date"]:
        return True
    return dv["plant"] == "DBN" and d in DBN_GAP


def migrated(h, d):
    m = devices[h]["migrated_date"]
    return m is not None and d >= m


con = duckdb.connect()
tel = con.execute(f"""
    WITH l AS (
        SELECT raw_line::JSON AS j FROM read_csv('{RAW}/telemetry/*.jsonl.gz', columns = {{'raw_line': 'VARCHAR'}},
             delim = chr(31), quote = '', escape = '', header = false, auto_detect = false, filename = true)
        WHERE json_valid(raw_line) AND filename NOT LIKE '%20260928.jsonl.gz'
    )
    SELECT DISTINCT coalesce(j->>'event_id', j->>'eventId') AS eid,
           coalesce(j->>'event_type', j->>'eventType') AS et,
           lower(trim(coalesce(j->'device'->>'deviceId', j->>'deviceId'), '{{}} ')) AS aad,
           coalesce(j->>'ts', j->>'timestamp') AS ts,
           coalesce(j->'payload'->>'app_name', j->'data'->>'appName') AS app,
           coalesce(j->'payload'->>'app_version', j->'data'->>'appVersion') AS ver,
           coalesce(j->'payload'->>'bugcheck_code', j->'data'->>'bugCheckCode') AS code
    FROM l
    WHERE coalesce(j->>'event_type', j->>'eventType') IN ('app_crash', 'AppCrash', 'bugcheck', 'BugCheck')
""").fetchall()


def parse_ts(s):
    if s.isdigit():
        return datetime.fromtimestamp(int(s) / 1000, timezone.utc).astimezone(SAST)
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(SAST)


# Real-world incidents: what actually happened, whether or not telemetry saw it.
# kind: mes_crash | bsod_tdr | bsod_other
incidents = []
seen = set()
for eid, et, aad, ts, app, ver, code in tel:
    h = by_aad.get(aad)
    if h is None or eid in seen:
        continue
    seen.add(eid)
    when = parse_ts(ts)
    if et in ("app_crash", "AppCrash") and app == "KestrelMES.exe":
        incidents.append({"host": h, "at": when, "kind": "mes_crash", "ver": ver, "seen_in_telemetry": True})
    elif et in ("bugcheck", "BugCheck"):
        incidents.append({"host": h, "at": when, "kind": "bsod_tdr" if code == "0x00000116" else "bsod_other",
                          "ver": None, "seen_in_telemetry": True})


def mes_version(i):
    return "4.1.3" if i < 35 else ("4.2.0" if i <= 41 else "4.2.1")


# Re-create the incidents telemetry never delivered (silent LTSC terminals, Durban gap),
# using the same rates as generate_sources.py. Shop-floor terminals are all MES users.
for h in hosts:
    dv = devices[h]
    for i in range(N_DAYS):
        d = day(i)
        if not silent(h, d):
            continue
        hrs = lambda: rng.uniform(0, 24) if dv["device_class"] == "SF" else rng.uniform(7, 18)
        if dv["device_class"] == "SF":
            for _ in range(poisson(0.012 * (8 if 35 <= i <= 41 else 1))):
                incidents.append({"host": h, "at": datetime.combine(d, time(0), SAST) + timedelta(hours=hrs()),
                                  "kind": "mes_crash", "ver": mes_version(i), "seen_in_telemetry": False})
        tdr = dv["model"] == MINI and migrated(h, d)
        for _ in range(poisson(0.006 if tdr else 0.0003)):
            kind = "bsod_tdr" if tdr and rng.random() < .85 else "bsod_other"
            incidents.append({"host": h, "at": datetime.combine(d, time(0), SAST) + timedelta(hours=rng.uniform(0, 24)),
                              "kind": kind, "ver": None, "seen_in_telemetry": False})
incidents.sort(key=lambda x: (x["at"], x["host"]))

# --------------------------------------------------------------------------
# 1. MES: production lines and station terminals
# --------------------------------------------------------------------------
MES_PLANT = {"ROS": "P01", "GQB": "P02", "DBN": "P03"}
SITE_NAME = {"P01": "Rosslyn Assembly", "P02": "PE Components", "P03": "Prospecton Seating"}
FAMILIES = {"ROS": ["BIW", "PAINT", "TRIM", "FINAL"], "GQB": ["AXLE", "EXH", "MACH"], "DBN": ["SEAT", "HARN"]}
N_LINES = {"ROS": 24, "GQB": 18, "DBN": 10}
CRITICAL_SHARE = 0.25          # share of stations where a terminal fault stops the line

lines, line_rows, terminal_rows = {}, [], []
station_of = {}                 # hostname -> (line_id, station, critical)
for plant in ("ROS", "GQB", "DBN"):
    fams = FAMILIES[plant]
    ids = []
    for k in range(N_LINES[plant]):
        fam = fams[k % len(fams)]
        lid = f"{MES_PLANT[plant]}-{fam}-L{k // len(fams) + 1:02d}"
        ids.append(lid)
        lines[lid] = {"plant": plant, "family": fam, "units_per_hour": rng.choice([24, 30, 36, 42, 48, 60])}
        site_alias = {"ROS": ["Rosslyn", "KAG Rosslyn"], "GQB": ["PE", "Gqeberha"], "DBN": ["Durban - Prospecton"]}
        site = SITE_NAME[MES_PLANT[plant]] if rng.random() > .2 else pick(site_alias[plant])
        line_rows.append([lid, MES_PLANT[plant], site, fam, lines[lid]["units_per_hour"],
                          pick(["3x8", "3x8", "2x8"])])
    sf = [h for h in hosts if devices[h]["plant"] == plant and devices[h]["device_class"] == "SF"]
    rng.shuffle(sf)
    on_line = sf[: int(len(sf) * .70)]
    for j, h in enumerate(on_line):
        lid = ids[j % len(ids)]
        st = f"ST-{j // len(ids) + 1:03d}"
        crit = rng.random() < CRITICAL_SHARE
        station_of[h] = (lid, st, crit)
        name = h
        r = rng.random()
        if r < .05:
            name = h.lower()
        elif r < .07:
            name = h.lower() + ".kestrel.local"
        elif r < .08:
            name = h.replace("-", "", 1)
        terminal_rows.append([lid, st, name, "Y" if crit else "N"])

# Production calendar: three shifts Mon-Fri, Saturday day shift, Sunday idle.
SHIFTS = [("A", 6, 14), ("B", 14, 22), ("C", 22, 30)]


def shift_of(ts):
    hour = ts.hour + ts.minute / 60
    if ts.weekday() == 6:
        return None
    if ts.weekday() == 5:
        return "A" if 6 <= hour < 14 else None
    if 6 <= hour < 14:
        return "A"
    if 14 <= hour < 22:
        return "B"
    return "C"


# --------------------------------------------------------------------------
# 2. MES line downtime log
# --------------------------------------------------------------------------
NON_IT = [  # code, events per line per production day, median minutes
    ("MAT", .30, 25), ("MECH", .25, 40), ("QUAL", .08, 60), ("CHGOVR", .20, 15), ("UTIL", .02, 90)]
IT_COMMENTS_OTHER = ["screen froze", "terminal rebooted itself", "MES closed on its own", "blue screen on station PC",
                     "had to restart the PC", "MES app crashed again"]
NONIT_COMMENTS_OTHER = ["waiting for forklift", "team leader called away", "safety walk", "no reason given"]
COMMENTS = {"MAT": ["no parts at station", "supplier late", "kanban empty"],
            "MECH": ["conveyor fault", "robot fault", "torque gun failure"],
            "QUAL": ["quality hold", "rework required"], "CHGOVR": ["changeover overran"],
            "UTIL": ["air pressure low", "power dip"], "IT-APP": ["MES not responding", "MES crashed", "MES error"],
            "IT-TERM": ["station PC blue screen", "terminal restart", "PC rebooting"]}

stops, downtime_truth = [], []


def add_stop(lid, start, minutes, code, cause, host=None, station=None):
    stops.append({"line": lid, "start": start, "end": start + timedelta(minutes=minutes), "minutes": minutes,
                  "code": code, "cause": cause, "host": host, "station": station})


for inc in incidents:
    where = station_of.get(inc["host"])
    if not where or not where[2]:
        continue
    lid, st, _ = where
    if shift_of(inc["at"]) is None:
        continue
    if inc["kind"] == "mes_crash":
        add_stop(lid, inc["at"], round(lognormal_minutes(6, .4, 2, 45), 1), "IT-APP",
                 "mes_420" if inc["ver"] == "4.2.0" else "mes_baseline", inc["host"], st)
    else:
        add_stop(lid, inc["at"], round(lognormal_minutes(18, .35, 8, 90), 1), "IT-TERM",
                 "bsod_tdr" if inc["kind"] == "bsod_tdr" else "bsod_other", inc["host"], st)

for lid, ln in lines.items():
    for i in range(N_DAYS):
        d = day(i)
        if d.weekday() == 6:
            continue
        hours = 8 if d.weekday() == 5 else 24
        for code, rate, med in NON_IT:
            for _ in range(poisson(rate * hours / 24)):
                start_h = rng.uniform(6, 14) if hours == 8 else rng.uniform(6, 30)
                add_stop(lid, datetime.combine(d, time(0), SAST) + timedelta(hours=start_h),
                         round(lognormal_minutes(med, .6, 3, 480), 1), code, "non_it")

stops.sort(key=lambda s: (s["start"], s["line"]))
downtime_rows = []
for n, s in enumerate(stops, 1):
    eid = f"DT-{s['line'][:3]}-{n:06d}"
    code, comment = s["code"], pick(COMMENTS.get(s["code"], [""]))
    recoded = False
    if code.startswith("IT-") and rng.random() < .12:          # operator picked OTHER
        code, comment, recoded = "OTHER", pick(IT_COMMENTS_OTHER), True
    elif code != "OTHER" and not code.startswith("IT-") and rng.random() < .02:
        code, comment = "OTHER", pick(NONIT_COMMENTS_OTHER)
    start = s["start"]
    fmt = pick(["%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M:%S"], [.7, .2, .1])
    duration = round(s["minutes"]) if rng.random() > .01 else round(s["minutes"] / 60, 2)   # 1% keyed in hours
    line_text = s["line"] if rng.random() > .03 else s["line"].replace("-", " ", 2)[4:]
    row = [eid, s["line"][:3], line_text, s["station"] or "", start.strftime(fmt), s["end"].strftime(fmt),
           duration, code, shift_of(start) or "A", comment]
    downtime_rows.append(row)
    downtime_truth.append([eid, s["line"], s["code"], "IT" if s["code"].startswith("IT-") else "non-IT", s["cause"],
                           s["host"] or "", round(s["minutes"], 1), recoded, False, start.isoformat()])
    if rng.random() < .015:                                     # double-logged stop
        dup = list(row)
        dup[0] = f"{eid}D"
        dup[5] = (s["end"] + timedelta(minutes=rng.randint(1, 4))).strftime(fmt)
        downtime_rows.append(dup)
        downtime_truth.append([dup[0], s["line"], s["code"], "IT" if s["code"].startswith("IT-") else "non-IT",
                               s["cause"], s["host"] or "", round(s["minutes"], 1), recoded, True, start.isoformat()])

# --------------------------------------------------------------------------
# 3. Changes and group membership
# --------------------------------------------------------------------------
def at(d, hh=6, mm=0):
    return datetime.combine(d, time(hh, mm), SAST)


groups = defaultdict(set)
groups["GRP-ALL-DEVICES"] = set(hosts)
groups["GRP-ALL-SF"] = {h for h in hosts if devices[h]["device_class"] == "SF"}
groups["GRP-OF-EN"] = {h for h in hosts if devices[h]["device_class"] in ("OF", "EN")}
groups["GRP-OF"] = {h for h in hosts if devices[h]["device_class"] == "OF"}
mes_seen = {i["host"] for i in incidents if i["kind"] == "mes_crash"}
groups["GRP-MES-USERS"] = groups["GRP-ALL-SF"] | {h for h in mes_seen}
for w in (1, 2, 3):
    groups[f"GRP-W11-25H2-W{w}"] = {h for h in hosts if devices[h]["wave"] == w}
groups["GRP-SF-FU-25H2-RING"] = {h for h in hosts if devices[h]["device_class"] == "SF" and devices[h]["wave"] > 0}
groups["GRP-HPMINI-EXCL"] = {h for h in hosts if devices[h]["model"] == MINI}
groups["GRP-HPMINI-PILOT-GQB-L03"] = {h for h in groups["GRP-HPMINI-EXCL"]
                                      if station_of.get(h, ("",))[0].startswith("P02") and rng.random() < .2}
groups["SITE-DBN"] = {h for h in hosts if devices[h]["plant"] == "DBN"}

CHANGES = [
    # number, type, category, description, group, planned start, actual start, actual end, state, close, problem
    ("CHG0041001", "Standard", "Windows quality update", "September 2026 cumulative update (Patch Tuesday)",
     "GRP-ALL-DEVICES", at(date(2026, 9, 8), 20), at(date(2026, 9, 8), 20), at(date(2026, 9, 10), 20), "Closed", "Successful", ""),
    ("CHG0041007", "Normal", "Intune - Update ring", "Enable driver updates in WUfB ring SF-FU-25H2 (shop floor)",
     "GRP-SF-FU-25H2-RING", at(date(2026, 9, 15), 9), at(date(2026, 9, 15), 9, 30), at(date(2026, 9, 15), 10), "Closed", "Successful", ""),
    ("CHG0041008", "Normal", "Intune - App deployment", "Deploy reliability agent 2.4 - office and engineering",
     "GRP-OF-EN", at(date(2026, 9, 14), 8), at(date(2026, 9, 14), 8), at(date(2026, 9, 28), 17), "Closed", "Successful", ""),
    ("CHG0041010", "Normal", "Intune - Feature update", "Windows 11 25H2 feature update - Pilot ring",
     "GRP-W11-25H2-W1", at(date(2026, 9, 14), 18), at(date(2026, 9, 14), 18), at(date(2026, 9, 18), 18), "Closed", "Successful", ""),
    ("CHG0041015", "Normal", "Microsoft 365 Apps", "Microsoft 365 Apps monthly enterprise channel update",
     "GRP-OF-EN", at(date(2026, 9, 15), 20), at(date(2026, 9, 16), 20), at(date(2026, 9, 17), 20), "Closed", "Successful", ""),
    ("CHG0041021", "Normal", "Intune - Feature update", "Windows 11 25H2 feature update - Wave 2 ring",
     "GRP-W11-25H2-W2", at(date(2026, 9, 21), 18), at(date(2026, 9, 21), 18), at(date(2026, 10, 3), 18), "Closed", "Successful", ""),
    ("CHG0041024", "Standard", "Intune - Configuration profile", "BitLocker profile: require TPM+PIN on office laptops",
     "GRP-OF", at(date(2026, 9, 23), 8), "", "", "Cancelled", "", ""),
    ("CHG0041026", "Normal", "Intune - App deployment", "Deploy reliability agent 2.4 - shop floor terminals",
     "GRP-ALL-SF", at(date(2026, 9, 28), 8), at(date(2026, 9, 28), 8), at(date(2026, 10, 15), 17), "Closed", "Successful", ""),
    ("CHG0041027", "Standard", "Intune - Proactive remediation", "Teams cache clean-up script (weekly)",
     "GRP-OF", at(date(2026, 9, 28), 7), at(date(2026, 9, 28), 7), at(date(2026, 9, 28), 8), "Closed", "Successful", ""),
    ("CHG0041030", "Normal", "Network", "Durban internet breakout migration to new proxy cluster",
     "SITE-DBN", at(date(2026, 10, 5), 5, 30), at(date(2026, 10, 5), 5, 30), at(date(2026, 10, 5), 7), "Closed", "Successful", ""),
    ("CHG0041031", "Standard", "Intune - App deployment", "Deploy KestrelMES client 4.2.0",
     "GRP-MES-USERS", at(date(2026, 10, 5), 5), at(date(2026, 10, 5), 5), at(date(2026, 10, 5), 9), "Closed", "Successful", ""),
    ("CHG0041033", "Normal", "Defender", "Defender ASR rule 'Block Office child processes' audit -> block",
     "GRP-ALL-DEVICES", at(date(2026, 10, 6), 9), at(date(2026, 10, 6), 9), at(date(2026, 10, 6), 11), "Closed", "Successful", ""),
    ("CHG0041035", "Normal", "Intune - Feature update", "Windows 11 25H2 feature update - Wave 3 ring",
     "GRP-W11-25H2-W3", at(date(2026, 10, 8), 18), at(date(2026, 10, 8), 18), at(date(2026, 10, 24), 18), "Closed", "Successful", ""),
    ("CHG0041038", "Emergency", "Network", "Add telemetry ingestion endpoint to Durban proxy allow-list",
     "SITE-DBN", at(date(2026, 10, 9), 16), at(date(2026, 10, 9), 16, 40), at(date(2026, 10, 9), 17), "Closed", "Successful", "PRB0040003"),
    ("CHG0041039", "Emergency", "Intune - App deployment", "Deploy KestrelMES client 4.2.1 hotfix",
     "GRP-MES-USERS", at(date(2026, 10, 12), 5), at(date(2026, 10, 12), 5), at(date(2026, 10, 12), 8), "Closed", "Successful", "PRB0040002"),
    ("CHG0041040", "Normal", "Browser", "Google Chrome 141 update", "GRP-OF-EN",
     at(date(2026, 10, 12), 20), at(date(2026, 10, 12), 20), at(date(2026, 10, 13), 9), "Closed", "Rolled back", ""),
    ("CHG0041041", "Standard", "Windows quality update", "October 2026 cumulative update (Patch Tuesday)",
     "GRP-ALL-DEVICES", at(date(2026, 10, 13), 20), at(date(2026, 10, 13), 20), at(date(2026, 10, 15), 20), "Closed", "Successful", ""),
    ("CHG0041044", "Normal", "Intune - Update ring", "Exclude HP Elite Mini 800 G9 from Windows 11 25H2 (ring SF-W2)",
     "GRP-HPMINI-EXCL", at(date(2026, 10, 14), 10), at(date(2026, 10, 14), 10), at(date(2026, 10, 14), 11), "Closed", "Successful", "PRB0040001"),
    ("CHG0041048", "Normal", "Intune - Driver update", "Pilot Intel graphics driver 31.0.101.5590 on HP Elite Mini (GQB L03)",
     "GRP-HPMINI-PILOT-GQB-L03", at(date(2026, 10, 26), 9), "", "", "Scheduled", "", "PRB0040001"),
]


def group_text(g):
    r = rng.random()
    if r < .1:
        return g.lower() + " "
    if r < .15:
        return g.replace("-", " ")
    return g


def dt_text(v):
    if not v:
        return ""
    return v.strftime(pick(["%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"]))


change_rows = []
for c in CHANGES:
    num, typ, cat, desc, grp, ps, as_, ae, state, close, prb = c
    change_rows.append([num, typ, cat, desc, group_text(grp), pick(["Low", "Moderate", "High"], [.5, .35, .15]),
                        dt_text(ps), dt_text(ps + timedelta(hours=4)), dt_text(as_), dt_text(ae), state, close, prb,
                        pick(["TM", "SN", "JvdM", "KP", "AN"])])

retired_like = [f"{p}-{c}-{rng.randint(99000, 99998)}" for p, c in
                [(pick(["ROS", "GQB", "DBN"]), pick(["OF", "EN", "SF"])) for _ in range(60)]]
membership_rows = []
for g in sorted(groups):
    members = sorted(groups[g])
    if g in ("GRP-ALL-DEVICES", "GRP-OF-EN", "GRP-ALL-SF"):
        members += rng.sample(retired_like, 20)                 # stale members never cleaned up
    for h in members:
        name = h.lower() if rng.random() < .03 else (h + " " if rng.random() < .01 else h)
        membership_rows.append([g, name, "Device"])

# --------------------------------------------------------------------------
# 4. Service desk incidents
# --------------------------------------------------------------------------
FIRST = ["Thabo", "Lerato", "Sipho", "Ayanda", "Pieter", "Anika", "Johan", "Nomvula", "Kagiso", "Zanele",
         "Ruan", "Priya", "Yusuf", "Naledi", "Bongani", "Chantelle", "Tshepo", "Megan", "Lwazi", "Fatima"]
LAST = ["Mokoena", "Nkosi", "Dlamini", "van der Merwe", "Naidoo", "Botha", "Khumalo", "Pillay", "Mahlangu",
        "Smith", "Ndlovu", "Jacobs", "Pretorius", "Mthembu", "Govender", "Molefe", "Steyn", "Zulu", "Adams", "Sithole"]
LOCATION = {"ROS": ["Rosslyn", "KAG Rosslyn Plant", "ROS"], "GQB": ["Gqeberha", "Gqeberha / PE", "Port Elizabeth"],
            "DBN": ["Durban", "Prospecton", "DBN"]}
PRIORITY_TEXT = {1: ["1 - Critical", "P1"], 2: ["2 - High", "P2", "High"], 3: ["3 - Moderate", "P3"], 4: ["4 - Low", "P4", "Low"]}
CATEGORY_TEXT = {
    "Hardware": ["Hardware", "hardware", "HW"], "Display / blue screen": ["Hardware - Display", "Blue screen", "BSOD"],
    "MES": ["Software - MES", "MES", "KestrelMES"], "Office apps": ["Software - Office", "Office", "Teams"],
    "Performance": ["Performance", "Slow PC", "performance"], "Network": ["Network", "Network/Wi-Fi"],
    "Access": ["Access", "Password / Account"], "Printing": ["Printing", "Printer"],
    "Monitoring": ["Monitoring", "Platform - Telemetry"]}
ACK = {1: (8, .5), 2: (35, .6), 3: (150, .7), 4: (480, .7)}          # minutes median, sigma
RESOLVE = {1: (240, .5), 2: (480, .6), 3: (2400, .7), 4: (5000, .7)}
GROUPS = {"Hardware": "EUC Field Services", "Display / blue screen": "EUC Engineering", "MES": "MES Support",
          "Office apps": "Service Desk L1", "Performance": "Service Desk L1", "Network": "Network Operations",
          "Access": "Service Desk L1", "Printing": "Service Desk L1", "Monitoring": "Endpoint Analytics"}

tickets = []


def caller():
    return f"{pick(FIRST)} {pick(LAST)}"


def new_ticket(opened, host, category, priority, desc, cause, plant=None, parent="", problem="", change="",
               resolve_override=None, close_code=None, reopen=0, state=None):
    tickets.append({"opened": opened, "host": host, "plant": plant or (devices[host]["plant"] if host else None),
                    "category": category, "priority": priority, "desc": desc, "cause": cause, "parent": parent,
                    "problem": problem, "change": change, "resolve_override": resolve_override,
                    "close_code": close_code, "reopen": reopen, "state": state})


# Baseline demand, unrelated to the storylines
BASE_RATE = {"OF": .035, "EN": .030, "SF": .012}
BASE_CATS = [("Hardware", .22), ("Office apps", .25), ("Access", .2), ("Printing", .1), ("Network", .08),
             ("Performance", .15)]
for h in hosts:
    dv = devices[h]
    for w in range(8):
        for _ in range(poisson(BASE_RATE[dv["device_class"]])):
            d = day(w * 7 + rng.randrange(5))
            cat = pick([c for c, _ in BASE_CATS], [p for _, p in BASE_CATS])
            if cat == "Performance" and migrated(h, d) and dv["device_class"] == "OF" and rng.random() < .3:
                continue                                         # 25H2 eases slow-PC complaints a little
            opened = datetime.combine(d, time(0), SAST) + timedelta(hours=rng.uniform(7, 17))
            new_ticket(opened, h, cat, pick([2, 3, 4], [.1, .6, .3]), f"{cat} issue on {h}", "baseline")

# Blue screens: users and line supervisors call some of them in
PRB_BSOD_OPENED = at(date(2026, 9, 29), 11)
for inc in incidents:
    if not inc["kind"].startswith("bsod"):
        continue
    p_call = .5 if devices[inc["host"]]["device_class"] == "SF" else .35
    if rng.random() >= p_call:
        continue
    opened = inc["at"] + timedelta(minutes=rng.uniform(5, 60))
    tdr = inc["kind"] == "bsod_tdr"
    linked = "PRB0040001" if tdr and opened >= PRB_BSOD_OPENED and rng.random() < .6 else ""
    new_ticket(opened, inc["host"], "Display / blue screen", 2 if inc["host"] in station_of else 3,
               f"Blue screen on {inc['host']}", "bsod_tdr" if tdr else "bsod_other", problem=linked,
               resolve_override=lognormal_minutes(4300 if tdr else 1500, .6, 60, 30000),
               close_code="Solved (Workaround)" if tdr else "Solved (Permanently)",
               reopen=(rng.choice([1, 1, 2]) if tdr and rng.random() < .3 else 0))

# KestrelMES 4.2.0: major incident with child tickets from the lines
MAJOR = "MAJOR-MES-420"
new_ticket(at(date(2026, 10, 5), 7, 40), None, "MES", 1, "Major incident: KestrelMES client crashing across all plants",
           "mes_420", plant="ROS", problem="PRB0040002", change="CHG0041031",
           resolve_override=(at(date(2026, 10, 12), 8, 30) - at(date(2026, 10, 5), 7, 40)).total_seconds() / 60,
           close_code="Solved (Permanently)")
tickets[-1]["is_major"] = True
for inc in incidents:
    if inc["kind"] != "mes_crash":
        continue
    p = .35 if inc["ver"] == "4.2.0" and inc["host"] in station_of else (.02 if inc["ver"] == "4.2.0" else .03)
    if rng.random() >= p:
        continue
    opened = inc["at"] + timedelta(minutes=rng.uniform(3, 30))
    is_child = inc["ver"] == "4.2.0" and opened >= at(date(2026, 10, 5), 7, 40)
    new_ticket(opened, inc["host"], "MES", 2 if inc["host"] in station_of else 3, f"MES crashed on {inc['host']}",
               "mes_420" if inc["ver"] == "4.2.0" else "mes_baseline", parent=MAJOR if is_child else "",
               problem="PRB0040002" if inc["ver"] == "4.2.0" else "",
               resolve_override=((at(date(2026, 10, 12), 8, 30) - opened).total_seconds() / 60) if is_child else None,
               close_code="Duplicate" if is_child else None)

# Durban telemetry outage: only noticed by an analyst on Thursday
new_ticket(at(date(2026, 10, 8), 10, 15), None, "Monitoring", 4,
           "Fleet dashboard missing Durban telemetry since Monday", "dbn_outage", plant="DBN",
           problem="PRB0040003", change="CHG0041030",
           resolve_override=(at(date(2026, 10, 9), 17, 5) - at(date(2026, 10, 8), 10, 15)).total_seconds() / 60,
           close_code="Solved (Permanently)")
# LTSC silent failure: no user ever calls; an analyst raises it weeks later
new_ticket(at(date(2026, 10, 20), 14, 0), None, "Monitoring", 3,
           "Windows 10 LTSC terminals stopped sending telemetry after agent 2.4", "ltsc_agent", plant="ROS",
           problem="PRB0040004", change="CHG0041026", state="In Progress")

tickets.sort(key=lambda t: t["opened"])
ticket_rows, ticket_truth = [], []
major_number = None
for n, t in enumerate(tickets, 1):
    t["number"] = f"INC{40100 + n:07d}"
    if t.get("is_major"):
        major_number = t["number"]
for t in tickets:
    pr = t["priority"]
    ack = t["opened"] + timedelta(minutes=lognormal_minutes(*ACK[pr], 1, 4000))
    resolve_min = t["resolve_override"] if t["resolve_override"] is not None else lognormal_minutes(*RESOLVE[pr], 10, 40000)
    resolved = t["opened"] + timedelta(minutes=resolve_min)
    state = t["state"]
    if state is None:
        state = "Closed" if resolved < SNAPSHOT - timedelta(days=3) else ("Resolved" if resolved < SNAPSHOT else "In Progress")
    if state == "In Progress":
        resolved = None
    closed = resolved + timedelta(days=3) if state == "Closed" else None
    bad_order = state in ("Closed", "Resolved") and rng.random() < .003            # data entry error
    if bad_order:
        resolved = t["opened"] - timedelta(hours=rng.uniform(1, 30))
    host = t["host"]
    ci = ""
    if host:
        r = rng.random()
        ci = "" if r < .12 else (host.lower() + ".kestrel.local" if r < .18 else
                                 (host.replace("-", "", 1) if r < .2 else host))
    plant = t["plant"] or "ROS"
    loc = pick(LOCATION[plant]) if rng.random() > .05 else ""
    desc = t["desc"]
    if host and rng.random() < .5:
        desc += f" - reported by {caller()}"
    parent = major_number if t["parent"] == MAJOR else t["parent"]
    fmt = pick(["%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"])
    group = GROUPS[t["category"]]
    row = [t["number"], t["opened"].strftime(fmt), ack.strftime(fmt),
           resolved.strftime(fmt) if resolved else "", closed.strftime(fmt) if closed else "",
           state, pick(PRIORITY_TEXT[pr]), pick(CATEGORY_TEXT[t["category"]]), desc,
           "Service Desk (monitoring)" if not host and t["category"] == "Monitoring" else caller(),
           loc, ci, group, parent or "", t["problem"], t["change"],
           t["close_code"] or ("Solved (Permanently)" if state in ("Closed", "Resolved") else ""), t["reopen"],
           (resolved or ack).strftime(fmt)]
    ticket_rows.append(row)
    ticket_truth.append([t["number"], host or "", plant, t["category"], pr, t["cause"], bad_order, False,
                         t["opened"].isoformat(), resolved.isoformat() if resolved and not bad_order else ""])
    if host and rng.random() < .02:                     # reassigned: exported twice, older row first
        older = list(row)
        older[12] = "Service Desk L1"
        older[18] = ack.strftime(fmt)
        ticket_rows.append(older)
        ticket_truth.append([t["number"], host, plant, t["category"], pr, t["cause"], bad_order, True,
                             t["opened"].isoformat(), ""])

# --------------------------------------------------------------------------
# 5. Remediation register (Problem management actions)
# --------------------------------------------------------------------------
REMEDIATIONS = [
    # id, problem, title, change, owner, target, status, raised, due, actioned, verified, benefit, comment, true verdict
    ("RA-001", "PRB0040001", "Pause Windows 11 25H2 for HP Elite Mini 800 G9", "CHG0041044", "KP",
     "All HP Elite Mini 800 G9", "Done", date(2026, 10, 12), date(2026, 10, 14), date(2026, 10, 14), "",
     "Stop new blue screens on HP Minis", "Exclusion group added", "claimed_not_implemented"),
    ("RA-002", "PRB0040001", "Pilot Intel graphics driver update on HP Mini (GQB L03)", "CHG0041048", "AN",
     "GQB line 3 HP Minis", "Scheduled", date(2026, 10, 16), date(2026, 10, 26), None, "",
     "Fix VIDEO_TDR_FAILURE", "Pilot booked for 26 Oct", "not_yet_measurable"),
    ("RA-003", "PRB0040002", "Hotfix KestrelMES client 4.2.1", "CHG0041039", "SN", "All MES clients", "Completed",
     date(2026, 10, 6), date(2026, 10, 12), date(2026, 10, 12), "Y", "Stop MES crashes", "", "effective"),
    ("RA-004", "PRB0040002", "Require pilot plant for MES releases (CAB rule)", "", "TM", "CAB process", "done ",
     date(2026, 10, 13), date(2026, 10, 20), date(2026, 10, 19), "N/A", "Prevent repeat", "process change",
     "not_measurable_process"),
    ("RA-005", "PRB0040003", "Allow-list telemetry endpoint on Durban proxy", "CHG0041038", "JvdM",
     "Durban site", "Done", date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 9), "Y",
     "Restore Durban telemetry", "", "effective"),
    ("RA-006", "PRB0040003", "Alert when a plant stops sending telemetry", "", "AN", "Analytics platform",
     "In Progress", date(2026, 10, 9), date(2026, 10, 30), None, "", "Detect outages same day", "",
     "open"),
    ("RA-007", "PRB0040004", "Investigate reliability agent 2.4 on Windows 10 LTSC", "", "SN",
     "LTSC shop-floor terminals", "Open", date(2026, 10, 20), date(2026, 11, 6), None, "",
     "Restore LTSC telemetry", "Vendor case logged", "open"),
    ("RA-008", "", "Teams cache clean-up script to reduce Teams crashes", "CHG0041027", "KP", "Office laptops",
     "Closed", date(2026, 9, 22), date(2026, 9, 28), date(2026, 9, 28), "Y", "Fewer Teams crashes", "",
     "no_detectable_effect"),
]
remediation_rows, remediation_truth = [], []
for r in REMEDIATIONS:
    rid, prb, title, chg, owner, target, status, raised, due, actioned, verified, benefit, comment, verdict = r
    fmt = pick(["%d/%m/%Y", "%Y-%m-%d", "%d %b %Y"])
    remediation_rows.append([rid, prb, title, chg if rng.random() > .1 or not chg else "", owner, target, status,
                             raised.strftime(fmt), due.strftime(fmt), actioned.strftime(fmt) if actioned else "",
                             verified, benefit, comment])
    remediation_truth.append([rid, chg, verdict])

# --------------------------------------------------------------------------
# 6. Write files
# --------------------------------------------------------------------------
def write_csv(path, header, rows, bom=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


rng.shuffle(ticket_rows)
write_csv(os.path.join(RAW, "itsm", "servicedesk_incidents_20261026.csv"),
          ["number", "opened_at", "first_response_at", "resolved_at", "closed_at", "state", "priority", "category",
           "short_description", "caller_id", "location", "cmdb_ci", "assignment_group", "parent_incident",
           "problem_id", "caused_by_change", "close_code", "reopen_count", "sys_updated_on"], ticket_rows, bom=True)
write_csv(os.path.join(RAW, "change", "change_requests_20261026.csv"),
          ["number", "type", "category", "short_description", "assignment_target", "risk", "planned_start",
           "planned_end", "actual_start", "actual_end", "state", "close_code", "problem_id", "requested_by"],
          change_rows)
write_csv(os.path.join(RAW, "intune", "group_membership_20261026.csv"),
          ["group_name", "member_name", "member_type"], membership_rows)
write_csv(os.path.join(RAW, "mes", "production_lines.csv"),
          ["line_id", "mes_plant", "site_name", "product_family", "standard_units_per_hour", "shift_pattern"], line_rows)
write_csv(os.path.join(RAW, "mes", "line_terminals.csv"),
          ["line_id", "station", "terminal_name", "line_critical"], terminal_rows)
write_csv(os.path.join(RAW, "mes", "line_downtime_events.csv"),
          ["event_id", "mes_plant", "line", "station", "start_local", "end_local", "duration_min", "reason_code",
           "shift", "operator_comment"], downtime_rows)
write_csv(os.path.join(RAW, "remediation", "remediation_register.csv"),
          ["action_id", "problem_id", "title", "linked_change", "owner", "target", "status", "date_raised",
           "due_date", "date_actioned", "verified", "expected_benefit", "comments"], remediation_rows)

write_csv(os.path.join(TRUTH_DIR, "downtime_truth.csv"),
          ["event_id", "line_id", "true_reason_code", "it_or_non_it", "cause", "terminal", "true_minutes",
           "recoded_as_other", "is_duplicate", "start_local"], downtime_truth)
write_csv(os.path.join(TRUTH_DIR, "ticket_truth.csv"),
          ["number", "device", "plant", "category", "priority", "cause", "resolved_before_opened",
           "is_stale_duplicate_row", "opened_local", "resolved_local"], ticket_truth)
write_csv(os.path.join(TRUTH_DIR, "remediation_truth.csv"), ["action_id", "change", "true_verdict"], remediation_truth)
write_csv(os.path.join(TRUTH_DIR, "incident_truth.csv"),
          ["device", "at_local", "kind", "mes_version", "seen_in_telemetry"],
          [[i["host"], i["at"].isoformat(), i["kind"], i["ver"] or "", i["seen_in_telemetry"]] for i in incidents])

# --------------------------------------------------------------------------
# 7. Business answer key (numbers computed from what was generated)
# --------------------------------------------------------------------------
def iso_week(dt):
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


uniq = [t for t in downtime_truth if not t[8]]
it_min = Counter()
for t in uniq:
    if t[3] == "IT":
        it_min[(iso_week(datetime.fromisoformat(t[9])), t[1][:3], t[4])] += t[6]
total_min = Counter(iso_week(datetime.fromisoformat(t[9])) for t in uniq)


def it_minutes(week=None, plant=None, cause=None):
    return round(sum(v for (w, p, c), v in it_min.items()
                     if (week is None or w == week) and (plant is None or p == plant) and (cause is None or c == cause)))


all_min = Counter()
for t in uniq:
    all_min[iso_week(datetime.fromisoformat(t[9]))] += t[6]
mes420_by_plant = {p: it_minutes("2026-W41", MES_PLANT[p], "mes_420") for p in ("ROS", "GQB", "DBN")}
dbn_hidden = sum(1 for i in incidents if devices[i["host"]]["plant"] == "DBN" and i["kind"] == "mes_crash"
                 and not i["seen_in_telemetry"] and i["at"].date() in DBN_GAP)
ros_silent_w41 = sum(1 for i in incidents if devices[i["host"]]["plant"] == "ROS" and i["kind"] == "mes_crash"
                     and not i["seen_in_telemetry"] and iso_week(i["at"]) == "2026-W41")
ros_seen_w41 = sum(1 for i in incidents if devices[i["host"]]["plant"] == "ROS" and i["kind"] == "mes_crash"
                   and i["seen_in_telemetry"] and iso_week(i["at"]) == "2026-W41")
mini_after_pause = Counter(devices[h]["plant"] for h in groups["GRP-HPMINI-EXCL"]
                           if devices[h]["migrated_date"] and devices[h]["migrated_date"] >= date(2026, 10, 14))
bsod_tickets = [t for t in tickets if t["cause"] == "bsod_tdr"]
mes_children = sum(1 for t in tickets if t["parent"] == MAJOR)
ltsc_user_tickets = sum(1 for t in tickets if t["cause"] == "ltsc_agent" and t["host"])

key = f"""# Business answer key — Kestrel Assembly Group (private)

> Generated by `generate_business_sources.py` (SEED = {SEED}). Fictional and synthetic. Use it only in
> `evals/`, after the business pipeline is built. Unlike the device sources, the business pipeline was
> written by the same author as this generator, so its evaluation is a consistency check, not a blind test.

## How the business sources relate to the device sources

- Tickets, line stops and changes refer to the same devices as Intune and telemetry (hostnames, groups).
- Line stops and tickets are generated from **what really happened** on each device, including
  incidents telemetry never delivered: Windows 10 LTSC terminals after agent 2.4, and Durban
  5–9 Oct. MES and the service desk keep working when telemetry does not.
- MES identifies plants as P01 (Rosslyn), P02 (Gqeberha), P03 (Durban) and stops by line + station;
  `line_terminals.csv` maps stations to terminals. Only stations flagged `line_critical = Y` stop the line.

## Planted storylines

### F. KestrelMES 4.2.0 is the costliest IT event (extends E)
- Deployed by **CHG0041031** (Standard change, Mon 5 Oct 05:00, group GRP-MES-USERS); hotfix
  **CHG0041039** (Emergency, Mon 12 Oct) installs 4.2.1.
- IT-APP line stops in 2026-W41 caused by 4.2.0: **ROS {mes420_by_plant['ROS']:,} min, GQB {mes420_by_plant['GQB']:,} min,
  DBN {mes420_by_plant['DBN']:,} min**.
- Major incident (P1, problem PRB0040002) with **{mes_children} child tickets**.
- **Durban's hidden share:** telemetry shows no Durban MES crashes Mon–Fri of W41, but MES logged Durban
  stops all week ({dbn_hidden} Durban MES crashes happened during the outage). Only MES and tickets reveal them.
- **Rosslyn under-count:** in W41 telemetry saw {ros_seen_w41} Rosslyn MES crashes, while {ros_silent_w41} more happened on
  silent LTSC terminals; line stops include both.
- Correct finding: credit the hotfix (RA-003) as effective — IT-APP minutes fall back to the pre-4.2.0
  baseline from W42.

### G. HP Elite Mini blue screens: change chain, tickets and a remediation that did not work (extends A)
- **CHG0041007** (15 Sep) enabled driver updates in the shop-floor 25H2 update ring, ahead of the Wave 2
  ring (**CHG0041021**, 21 Sep). Minis that moved to 25H2 received the faulty Intel graphics driver.
- IT-TERM line stops (reboots, ~18 min each) concentrate on Gqeberha and Durban lines with Minis.
- {len(bsod_tickets)} VIDEO_TDR blue screen tickets; long resolution (median ~3 days) and frequent reopens;
  problem **PRB0040001** opened 29 Sep.
- **RA-001 "Pause 25H2 for HP Elite Mini"** is marked Done on 14 Oct via **CHG0041044**, but the exclusion was
  attached to ring SF-W2 only. Wave 3 Minis kept migrating: **{sum(mini_after_pause.values())} after 14 Oct**
  (GQB {mini_after_pause['GQB']}, DBN {mini_after_pause['DBN']}, ROS {mini_after_pause['ROS']}). Correct verdict: claimed, not effective.
- **RA-002** driver pilot (CHG0041048) is scheduled for 26 Oct, after the data window: not yet measurable.

### H. Durban outage was a network change, detected late (extends C)
- **CHG0041030** (Mon 5 Oct 05:30) moved Durban to a new proxy that blocked the telemetry endpoint.
  Production was unaffected (MES kept logging).
- Only one ticket: an analyst noticed on **Thu 8 Oct 10:15** (≈3 days late).
- **CHG0041038** (Fri 9 Oct, emergency) allow-listed the endpoint; RA-005 is effective (Durban reports
  normally from Sat 10 Oct). RA-006 (alerting) is still in progress.

### I. LTSC telemetry loss generates no tickets (extends B)
- Caused by **CHG0041026** (agent 2.4 to GRP-ALL-SF, 28 Sep–15 Oct), which includes LTSC terminals.
- **{ltsc_user_tickets} user tickets** about it. One analyst ticket on 20 Oct; RA-007 investigation still open.
- Correct finding: ticket volume is not a proxy for fleet health; silent failures need coverage monitoring.

### J. Red herrings
- **CHG0041033** Defender ASR rule change (Tue 6 Oct, all devices) coincides with the MES spike. Its scope is
  every device, and only the MES app (and only version 4.2.0) crashed more: no evidence it contributed.
- **RA-008** Teams cache clean-up (CHG0041027, 28 Sep) is marked Done and verified, but Teams crash rates
  do not change: no detectable effect.
- **CHG0041040** Chrome update was rolled back; **CHG0041024** BitLocker change was cancelled.

### K. 25H2 eases performance complaints slightly (extends D)
- Migrated office laptops raise ~30% fewer "Performance" tickets than before migration.

## Planted data quality issues

### Service desk export
- UTF-8 BOM; three timestamp layouts (SAST): `2026-10-05 07:40:12`, `05/10/2026 07:40:12`.
- Priority spellings: `1 - Critical`, `P1`; `2 - High`, `P2`, `High`; `3 - Moderate`, `P3`; `4 - Low`, `P4`, `Low`.
- Category spellings drift (`Hardware`/`HW`, `Blue screen`/`BSOD`/`Hardware - Display`, `MES`/`KestrelMES`…).
- `cmdb_ci` blank (~12%), FQDN lower case (~6%) or missing a hyphen (~2%); location blank (~5%) or aliased.
- ~2% of tickets appear twice (reassigned; keep the row with the latest `sys_updated_on`).
- ~0.3% resolved before opened (data entry errors).
- `caller_id` and `short_description` contain people's names and hostnames: they must never leave Silver.
- Child tickets of the major incident are closed as `Duplicate` when the parent resolves.

### Changes and groups
- Group names in `assignment_target` with case, trailing space or spaces instead of hyphens.
- Cancelled change has no actual dates; one change was rolled back; one is scheduled after the window.
- Group membership has 3% lower-case names, trailing spaces, and stale members (retired devices not in Intune).

### MES
- `site_name` vs `mes_plant` codes; `line` sometimes written `BIW L01` instead of `P01-BIW-L01`.
- ~12% of IT stops coded `OTHER` with comments like "screen froze" or "blue screen on station PC"; a few
  non-IT stops are also `OTHER`.
- ~1.5% of stops logged twice (`…D` ids, end time a few minutes later).
- ~1% of `duration_min` keyed in hours; three timestamp layouts.
- Terminal names in `line_terminals.csv` with case, FQDN or missing hyphen.

### Remediation register
- Status spellings: `Done`, `done `, `Completed`, `Closed`; `In Progress`; `Open`; `Scheduled`.
- Three date layouts; ~10% of linked changes missing.

## Ground truth files (`business_truth/`)
`downtime_truth.csv`, `ticket_truth.csv`, `remediation_truth.csv`, `incident_truth.csv`.
"""
with open(os.path.join(KEY, "BUSINESS_ANSWER_KEY.md"), "w") as f:
    f.write(key)

print(f"Done: {len(ticket_rows):,} ticket rows, {len(downtime_rows):,} line stops, {len(change_rows)} changes, "
      f"{len(membership_rows):,} group members, {len(line_rows)} lines, {len(terminal_rows):,} terminals, "
      f"{len(remediation_rows)} remediation actions.")
