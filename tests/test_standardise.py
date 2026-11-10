from datetime import date, datetime, timedelta

import pytest

from pipeline import standardise as s


@pytest.mark.parametrize("raw, expected", [
    ("ROS-OF-12345", "ROS-OF-12345"),
    (" ros-of-12345 ", "ROS-OF-12345"),           # case and whitespace
    ("dbn-sf-17193.kestrel.local", "DBN-SF-17193"),  # agent 2.4 FQDN
    ("GQB-OF06949", "GQB-OF-06949"),              # missing hyphen
    ("ROS-SF-69O43", "ROS-SF-69043"),             # letter O typed for zero
    ("DBN-SF-O8237", "DBN-SF-08237"),
    ("ROS-LAB-7027", None),                       # outside the naming scheme
    ("XYZ-OF-12345", None),
    ("", None),
    (None, None),
])
def test_std_hostname(raw, expected):
    assert s.std_hostname(raw) == expected


def test_letter_o_only_fixed_in_number():
    # Plant and class codes never contain digits, so 'O' there is not touched.
    assert s.std_hostname("ROS-OF-1O0O1") == "ROS-OF-10001"


@pytest.mark.parametrize("raw, expected", [
    ("Rosslyn Plant", "ROS"), ("KAG-Rosslyn", "ROS"), ("rosslyn ", "ROS"),
    ("Port Elizabeth", "GQB"), ("PE Plant", "GQB"), ("gqeberha", "GQB"),
    ("Prospecton", "DBN"), ("DURBAN", "DBN"),
    ("", None), ("Head Office", None),
])
def test_plant_from_category(raw, expected):
    assert s.plant_from_category(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("2026-10-23 14:02:57", datetime(2026, 10, 23, 14, 2, 57)),
    ("10/25/2026 02:35:57 AM", datetime(2026, 10, 25, 2, 35, 57)),   # US, 12-hour
    ("9/30/2026 12:36:09 AM", datetime(2026, 9, 30, 0, 36, 9)),
    ("14/08/2023 01:34", datetime(2023, 8, 14, 1, 34)),               # SA, 24-hour
    ("01/02/2024 10:00", datetime(2024, 2, 1, 10, 0)),                # day-first, not Jan 2
    ("not a date", None), ("", None), (None, None),
])
def test_parse_intune_datetime(raw, expected):
    assert s.parse_intune_datetime(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("2026-09-28", date(2026, 9, 28)),
    ("16/10/2026", date(2026, 10, 16)),
    ("46300", date(1899, 12, 30) + timedelta(days=46300)),   # Excel serial
    ("12 Sep", date(2026, 9, 12)),
    ("3 Oct", date(2026, 10, 3)),
    ("", None), ("soon", None), (None, None),
])
def test_parse_tracker_date(raw, expected):
    assert s.parse_tracker_date(raw) == expected


def test_os_build_and_release():
    assert s.std_os_build("26200.6901") == "10.0.26200.6901"
    assert s.std_os_build("10.0.19044.6332") == "10.0.19044.6332"
    assert s.std_os_build("garbage") is None
    assert s.os_release("10.0.26200.6901") == "Win11 25H2"
    assert s.os_release("10.0.19044.6332") == "Win10 LTSC 21H2"
    assert s.os_release("10.0.99999.1") is None


@pytest.mark.parametrize("manufacturer, raw, expected", [
    ("Lenovo", "Lenovo ThinkPad T14 Gen 4", "ThinkPad T14 Gen 4"),
    ("Lenovo", "21HD5852ZA", "ThinkPad T14 Gen 4"),      # machine type code
    ("Lenovo", "11T33300ZA", "ThinkCentre M70q Gen 3"),
    ("HP", "HP Elite Mini 800 G9", "Elite Mini 800 G9"),
    ("Dell", "Precision 3660", "Precision 3660"),
    ("Advantech", "Advantech UTC-520", "UTC-520"),
    ("Dell", "null", None), ("HP", "N/A", None), ("HP", "", None),
])
def test_std_model(manufacturer, raw, expected):
    assert s.std_model(manufacturer, raw) == expected


def test_compliance_and_storage():
    assert s.std_compliance("compliant") == "Compliant"
    assert s.std_compliance("In grace period") == "In grace period"
    assert s.std_compliance("null") is None
    assert s.storage_gb("476.83 GB") == 512       # GiB reported as GB
    assert s.storage_gb("953.65") == 1024
    assert s.storage_gb("256") == 256
    assert s.storage_gb("") is None


@pytest.mark.parametrize("raw, expected", [
    ("W2", "Wave 2"), ("WAVE 2", "Wave 2"), ("wave 3 ", "Wave 3"), ("wave1", "Wave 1"),
    ("Pilot", "Pilot"), ("N/A", "Exempt"), ("TBC", "TBC"), ("", None), ("W9", None),
])
def test_std_wave(raw, expected):
    assert s.std_wave(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("done ", "Done"), ("Complete", "Done"), ("Completed", "Done"),
    ("Failed - retry", "Failed"), ("Rollback", "Rolled back"),
    ("Exempt - LTSC", "Exempt"), ("Not started", "Not started"), ("16030 devices", None),
])
def test_std_status(raw, expected):
    assert s.std_status(raw) == expected


@pytest.mark.parametrize("seconds, ok", [
    (45.2, True), (600, True), (600.1, False), (0, False), (-3, False),
    (99999, False), (None, False),
])
def test_valid_duration(seconds, ok):
    assert s.valid_duration(seconds) is ok
