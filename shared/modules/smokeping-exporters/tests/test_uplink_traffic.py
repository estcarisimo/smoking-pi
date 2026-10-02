"""The uplink meter: counter deltas, resets and the state file. No network."""

import pathlib
import sys

import pytest

MODULE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import uplink_traffic as ut  # noqa: E402

PROC_NET_DEV = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  123456     100    0    0    0     0          0         0   123456     100    0    0    0     0       0          0
 wlan0: 9000000    8000    0    0    0     0          0         0  4000000    3000    0    0    0     0       0          0
docker0:      0       0    0    0    0     0          0         0        0       0    0    0    0     0       0          0
"""


def test_read_counters(tmp_path):
    f = tmp_path / "dev"
    f.write_text(PROC_NET_DEV)
    assert ut.read_counters("wlan0", f) == (9_000_000, 4_000_000)
    assert ut.read_counters("eth0", f) is None
    assert ut.read_counters("wlan0", tmp_path / "missing") is None


def test_first_reading_is_only_a_baseline():
    state, interval = ut.step({"intervals": []}, "wlan0", (100, 50), 1000.0)
    assert interval is None
    assert state["last"] == {"t": 1000.0, "mono": None, "interface": "wlan0",
                             "rx": 100, "tx": 50}


def test_next_reading_is_an_interval():
    state, _ = ut.step({"intervals": []}, "wlan0", (100, 50), 1000.0)
    state, interval = ut.step(state, "wlan0", (1100, 250), 1300.0)
    assert interval == {"t": 1300, "interface": "wlan0", "rx": 1000, "tx": 200, "seconds": 300.0}
    assert state["intervals"] == [interval]
    assert ut.mb_per_day(interval) == round(1200 / 300 * 86400 / 1e6, 3)


def test_a_counter_going_backwards_drops_the_interval():
    # The host rebooted: the counters start again from zero.
    state, _ = ut.step({"intervals": []}, "wlan0", (10_000, 10_000), 1000.0)
    state, interval = ut.step(state, "wlan0", (500, 500), 1300.0)
    assert interval is None
    assert state["last"]["rx"] == 500  # the new baseline


def test_a_changed_interface_or_a_long_gap_drops_the_interval():
    state, _ = ut.step({"intervals": []}, "wlan0", (100, 100), 1000.0)
    _, interval = ut.step(state, "eth0", (900, 900), 1300.0)
    assert interval is None
    _, interval = ut.step(state, "wlan0", (900, 900), 1000.0 + ut.MAX_GAP + 1)
    assert interval is None


def test_no_uplink_keeps_history_and_forgets_the_baseline():
    state, _ = ut.step({"intervals": []}, "wlan0", (100, 100), 1000.0)
    state, _ = ut.step(state, "wlan0", (200, 200), 1300.0)
    state, interval = ut.step(state, None, None, 1600.0)
    assert interval is None and state["last"] is None
    assert len(state["intervals"]) == 1


def test_intervals_older_than_a_day_are_pruned():
    old = {"t": 0, "interface": "wlan0", "rx": 1, "tx": 1, "seconds": 300}
    state, _ = ut.step({"intervals": [old]}, "wlan0", (1, 1), ut.KEEP_SECONDS + 1.0)
    assert state["intervals"] == []


def test_state_round_trip_and_a_broken_file(tmp_path):
    path = str(tmp_path / "state.json")
    assert ut.load_state(path) == {"intervals": []}
    state, _ = ut.step({"intervals": []}, "wlan0", (1, 2), 1000.0)
    ut.save_state(path, state)
    assert ut.load_state(path)["last"]["tx"] == 2
    (tmp_path / "state.json").write_text("{not json")
    assert ut.load_state(path) == {"intervals": []}


def test_point_is_stamped_at_the_interval_end():
    # Not the 5-minute slot: a short interval after a restart mid-slot
    # must not overwrite the full one already written for that slot.
    pt = ut.point_for({"t": 1301, "interface": "wlan0", "rx": 10, "tx": 20, "seconds": 300.0})
    line = pt.to_line_protocol()
    assert line.startswith("uplink_traffic,interface=wlan0 ")
    assert "rx_bytes=10i" in line and "tx_bytes=20i" in line
    assert line.endswith(" 1301")


def test_elapsed_time_comes_from_the_monotonic_clock():
    state, _ = ut.step({"intervals": []}, "wlan0", (0, 0), 1000.0, mono=50.0)
    # NTP stepped the wall clock forward ten minutes in between.
    _, interval = ut.step(state, "wlan0", (300, 0), 1900.0, mono=350.0)
    assert interval["seconds"] == 300.0 and interval["t"] == 1900


def test_rows_stamped_in_the_future_are_dropped():
    future = {"t": 10_000, "interface": "wlan0", "rx": 1, "tx": 1, "seconds": 300}
    state, _ = ut.step({"intervals": [future]}, "wlan0", (1, 1), 1000.0)
    assert state["intervals"] == []


@pytest.fixture
def chicago(monkeypatch):
    monkeypatch.setenv("TZ", "America/Chicago")
    ut.time.tzset()
    yield
    monkeypatch.undo()
    ut.time.tzset()


def test_the_ledger_adds_each_interval_to_its_local_date(chicago):
    # 2026-10-02 04:55 and 05:00 UTC: 23:55 on the 1st and midnight on the 2nd
    # in Chicago. An interval counts for the day it ends in.
    t1, t2 = 1790916900.0, 1790917200.0
    state, _ = ut.step({"intervals": []}, "wlan0", (0, 0), t1 - 300)
    state, _ = ut.step(state, "wlan0", (1000, 100), t1)
    state, _ = ut.step(state, "wlan0", (3000, 400), t2)
    assert state["days"] == {
        "2026-10-01": {"rx": 1000, "tx": 100, "seconds": 300.0, "interfaces": ["wlan0"]},
        "2026-10-02": {"rx": 2000, "tx": 300, "seconds": 300.0, "interfaces": ["wlan0"]},
    }


def test_the_ledger_survives_a_dropped_interval_and_keeps_the_newest_days():
    days = {}
    for n in range(5):
        days = ut.add_to_ledger(days, {"t": 1_790_000_000 + n * 86_400, "rx": 1, "tx": 1,
                                       "seconds": 300, "interface": "wlan0"}, keep=3)
    assert len(days) == 3 and min(days) == ut.local_day(1_790_000_000 + 2 * 86_400)
    # A reboot drops an interval, never the ledger.
    state = {"intervals": [], "days": days, "last": {"t": 1.0, "interface": "wlan0",
                                                      "rx": 10**9, "tx": 10**9}}
    state, interval = ut.step(state, "wlan0", (5, 5), 2.0)
    assert interval is None and state["days"] == days


def test_two_interfaces_in_one_day_are_both_named():
    days = ut.add_to_ledger({}, {"t": 1_790_000_000, "rx": 1, "tx": 1, "seconds": 300,
                                 "interface": "wlan0"})
    days = ut.add_to_ledger(days, {"t": 1_790_000_300, "rx": 1, "tx": 1, "seconds": 300,
                                   "interface": "eth0"})
    assert days[ut.local_day(1_790_000_000)]["interfaces"] == ["eth0", "wlan0"]


def test_a_damaged_state_file_falls_back_to_the_previous_copy(tmp_path):
    path = str(tmp_path / "uplink_traffic.json")
    good = {"intervals": [], "days": {"2026-10-01": {"rx": 1, "tx": 2, "seconds": 300.0}}}
    ut.save_state(path, good)
    ut.save_state(path, dict(good, updated=1))  # the first is now the .bak
    (tmp_path / "uplink_traffic.json").write_text('{"intervals": [')  # a torn write
    state = ut.load_state(path)
    assert state["days"] == good["days"]
    # The damaged file is kept for a look, not overwritten by the next save.
    assert (tmp_path / "uplink_traffic.json.corrupt").read_text() == '{"intervals": ['
    # A crash between save_state's two renames leaves only the .bak.
    (tmp_path / "uplink_traffic.json").unlink(missing_ok=True)
    assert ut.load_state(path)["days"] == good["days"]
    assert ut.load_state(str(tmp_path / "none.json")) == {"intervals": []}


def test_a_clock_before_2020_stays_out_of_the_ledger():
    # A Pi without a real-time clock, before NTP: the bytes are real, the date is not.
    days = ut.add_to_ledger({}, {"t": 300, "rx": 1, "tx": 1, "seconds": 300,
                                 "interface": "wlan0"})
    assert days == {}
