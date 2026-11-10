"""Reference data used to conform the raw sources.

Everything here is derived from SOURCES.md or from profiling the raw files,
never from the answer key.
"""

PLANTS = {
    "ROS": "Rosslyn",
    "GQB": "Gqeberha",
    "DBN": "Durban",
}

DEVICE_CLASSES = {
    "OF": "Office laptop",
    "EN": "Engineering workstation",
    "SF": "Shop-floor terminal",
}

# Intune "Device category" is typed by hand. Keys are lower-cased and stripped.
PLANT_ALIASES = {
    "ros": "ROS", "rosslyn": "ROS", "rosslyn plant": "ROS", "kag-rosslyn": "ROS",
    "gqb": "GQB", "gqeberha": "GQB", "port elizabeth": "GQB", "pe": "GQB",
    "pe plant": "GQB", "gqeberha plant": "GQB",
    "dbn": "DBN", "durban": "DBN", "durban plant": "DBN", "prospecton": "DBN",
}

# Lenovo exports sometimes carry the machine type (first 4 chars of the
# MTM, e.g. 21HD5852ZA) instead of the marketing name. Public Lenovo MTM
# prefixes; checked against the device class in the hostname during profiling.
LENOVO_MTM = {
    "21HD": "ThinkPad T14 Gen 4",
    "21ML": "ThinkPad T14 Gen 5",
    "11T3": "ThinkCentre M70q Gen 3",
    "30FM": "ThinkStation P360",
}

# Model names some exports prefix with the manufacturer.
MANUFACTURER_PREFIXES = ("Lenovo ", "HP ", "Dell ", "Advantech ")

# Windows build number -> release label used as the os_release dimension.
BUILD_RELEASE = {
    "19044": "Win10 LTSC 21H2",
    "22631": "Win11 23H2",
    "26100": "Win11 24H2",
    "26200": "Win11 25H2",
}
TARGET_RELEASE = "Win11 25H2"
LTSC_BUILD = "19044"

COMPLIANCE = {
    "compliant": "Compliant",
    "noncompliant": "Noncompliant",
    "not evaluated": "Not evaluated",
    "in grace period": "In grace period",
}

# Rollout tracker vocabularies. Keys are lower-cased with spaces removed.
TRACKER_WAVES = {
    "pilot": "Pilot",
    "w1": "Wave 1", "wave1": "Wave 1",
    "w2": "Wave 2", "wave2": "Wave 2",
    "w3": "Wave 3", "wave3": "Wave 3",
    "n/a": "Exempt",
    "tbc": "TBC",
}

TRACKER_STATUSES = {
    "done": "Done", "complete": "Done", "completed": "Done",
    "inprogress": "In progress",
    "failed": "Failed", "failed-retry": "Failed",
    "rollback": "Rolled back",
    "rescheduled": "Rescheduled",
    "notstarted": "Not started",
    "exempt-ltsc": "Exempt",
}

# Telemetry event types across both agent schemas.
EVENT_TYPES = {
    "startup": "startup", "Startup": "startup",
    "app_crash": "app_crash", "AppCrash": "app_crash",
    "bugcheck": "bugcheck", "BugCheck": "bugcheck",
}
