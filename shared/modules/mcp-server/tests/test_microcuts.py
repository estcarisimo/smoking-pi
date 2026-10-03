"""common.microcuts: the one definition of a microcut, tested on its own."""

from datetime import datetime, timedelta, timezone

from common import microcuts

T0 = datetime(2026, 9, 19, 0, 42, 33, tzinfo=timezone.utc)


def _rows(spec, target="cpe", protocol="ipv4"):
    return [{"_time": T0 + timedelta(seconds=off), "target": target,
             "protocol": protocol, "_value": loss} for off, loss in spec]


def test_fold_cuts_folds_consecutive_windows_and_measures_them():
    cuts = microcuts.fold_cuts(_rows([(30 * i, 100.0) for i in range(6)]))
    assert len(cuts) == 1
    cut = cuts[0]
    assert cut["seconds"] == 160 and cut["windows"] == 6
    assert cut["total"] and cut["confirmed"]
    assert cut["start"] == "2026-09-19T00:42:33+00:00"


def test_fold_cuts_tolerates_one_missing_window_but_not_two():
    assert len(microcuts.fold_cuts(_rows([(0, 100.0), (60, 100.0)]))) == 1
    # Exactly GAP_S apart still folds; one second more does not.
    assert len(microcuts.fold_cuts(_rows([(0, 100.0), (microcuts.GAP_S, 100.0)]))) == 1
    assert len(microcuts.fold_cuts(_rows([(0, 100.0), (microcuts.GAP_S + 1, 100.0)]))) == 2
    assert len(microcuts.fold_cuts(_rows([(0, 100.0), (90, 100.0)]))) == 2


def test_fold_cuts_reads_a_numeric_string_value():
    rows = [{"_time": T0, "target": "cpe", "protocol": "ipv4", "_value": "100"}]
    assert microcuts.fold_cuts(rows)[0]["total"] is True


def test_fold_cuts_keeps_targets_and_protocols_apart_and_sorts_by_start():
    rows = _rows([(0, 100.0)], protocol="ipv6") + _rows([(-300, 60.0), (-270, 60.0)])
    cuts = microcuts.fold_cuts(rows)
    assert [(c["protocol"], c["confirmed"]) for c in cuts] == [("ipv4", True), ("ipv6", True)]


def test_a_single_partial_window_is_possible_and_a_total_one_is_confirmed():
    assert microcuts.fold_cuts(_rows([(0, 52.0)]))[0]["confirmed"] is False
    assert microcuts.fold_cuts(_rows([(0, 100.0)]))[0]["confirmed"] is True


def test_fold_cuts_skips_rows_without_a_time_or_value():
    rows = [{"target": "cpe", "protocol": "ipv4", "_value": 100.0},
            {"_time": T0, "target": "cpe", "protocol": "ipv4"},
            {"_time": "not a time", "target": "cpe", "protocol": "ipv4", "_value": 1}]
    assert microcuts.fold_cuts(rows) == []


def test_fold_cuts_accepts_iso_strings():
    rows = [{"_time": "2026-09-19T00:42:33Z", "target": "cpe", "protocol": "ipv4", "_value": 100.0}]
    assert microcuts.fold_cuts(rows)[0]["seconds"] == 10


def test_describe_cuts_reads_naturally():
    six = microcuts.fold_cuts(_rows([(30 * i, 100.0) for i in range(6)]))
    assert microcuts.describe_cuts(six) == "1 cut of 2 min 40 s (6 windows, all at 100%)"
    one = microcuts.fold_cuts(_rows([(0, 52.0)]))
    assert microcuts.describe_cuts(one) == "1 possible cut (single window, 52%)"
    single = microcuts.fold_cuts(_rows([(0, 100.0)]))
    assert microcuts.describe_cuts(single) == "1 cut of 10 s (1 window, all at 100%)"
    three = microcuts.fold_cuts(_rows([(0, 52.0), (600, 62.0), (1200, 54.0)]))
    assert microcuts.describe_cuts(three) == "3 possible cuts (single windows, 52-62%)"
    mixed = microcuts.fold_cuts(_rows([(0, 100.0), (30, 60.0), (900, 52.0)]))
    assert microcuts.describe_cuts(mixed) == (
        "1 cut of 40 s (2 windows, worst 100%) and 1 possible cut (single window, 52%)"
    )
    long = microcuts.fold_cuts(_rows([(30 * i, 100.0) for i in range(411)]))
    assert microcuts.describe_cuts(long).startswith("1 cut of 3 h 25 min")
    assert microcuts.describe_cuts([]) == ""


def test_threshold_follows_the_env(monkeypatch):
    monkeypatch.delenv("MICROCUT_LOSS_PCT", raising=False)
    assert microcuts.loss_pct() == 50.0
    monkeypatch.setenv("MICROCUT_LOSS_PCT", "80")
    assert microcuts.loss_pct() == 80.0
    assert "r._value > 80.0" in microcuts.cut_windows_flux("-60m")
    monkeypatch.setenv("MICROCUT_LOSS_PCT", "lots")
    assert microcuts.loss_pct() == 50.0


# --- attribution: the host's deaf radio is not the link ---------------------


def _deaf(spec, associated=1, as_string=False):
    """Deaf uplink samples (uplink_flux) at these seconds after T0."""
    def when(off):
        t = T0 + timedelta(seconds=off)
        return t.isoformat().replace("+00:00", "Z") if as_string else t
    return [{"_time": when(off), "interface": "wlan0", "rx_packets": 0,
             "associated": associated} for off in spec]


def _cut(windows=6):
    return microcuts.fold_cuts(_rows([(30 * i, 100.0) for i in range(windows)]))


def test_a_cut_the_radio_heard_nothing_through_is_this_hosts():
    # Six windows, 160 s; a deaf sample every 10 s from start to end.
    cuts = microcuts.attribute(_cut(), _deaf(range(10, 170, 10)))
    assert cuts[0]["origin"] == "this_host" and cuts[0]["deaf"] == "received nothing"
    assert microcuts.link_cuts(cuts) == [] and len(microcuts.host_cuts(cuts)) == 1


def test_not_associated_is_named_as_such():
    cuts = microcuts.attribute(_cut(), _deaf(range(10, 170, 10), associated=0))
    assert cuts[0]["deaf"] == "not associated"


def test_one_missed_sample_is_tolerated_two_are_not():
    every = list(range(10, 170, 10))
    one_gap = [t for t in every if t != 80]
    two_gap = [t for t in every if t not in (80, 90, 100)]
    assert microcuts.attribute(_cut(), _deaf(one_gap))[0]["origin"] == "this_host"
    assert microcuts.attribute(_cut(), _deaf(two_gap))[0]["origin"] == "link"


def test_deaf_samples_must_reach_the_end_of_the_cut():
    # Deaf for the first minute, then hearing (healthy samples are filtered
    # out by the query): the link was cut for the rest.
    assert microcuts.attribute(_cut(), _deaf(range(10, 70, 10)))[0]["origin"] == "link"


def test_without_evidence_every_cut_stays_the_links():
    # A wired host, or wifi_link absent: the old behavior, never a guess.
    cuts = microcuts.attribute(_cut(), [])
    assert cuts[0]["origin"] == "link" and "deaf" not in cuts[0]


def test_samples_after_the_cut_are_not_read():
    # The long hangs end in a reboot; whatever comes after must not matter.
    cuts = microcuts.attribute(_cut(), _deaf(range(10, 170, 10)) + _deaf([3600]))
    assert cuts[0]["origin"] == "this_host"


def test_attribute_reads_iso_strings_and_equal_times():
    rows = _deaf(range(10, 170, 10), as_string=True)
    cuts = microcuts.attribute(_cut(), rows + rows)  # duplicates sort without error
    assert cuts[0]["origin"] == "this_host"


def test_link_windows_drops_the_hosts_windows_only():
    windows = _rows([(30 * i, 100.0) for i in range(6)]) + _rows([(3600, 62.0)])
    cuts = microcuts.attribute(microcuts.fold_cuts(windows), _deaf(range(10, 170, 10)))
    kept = microcuts.link_windows(windows, cuts)
    assert [r["_value"] for r in kept] == [62.0]


def test_describe_deaf():
    cuts = microcuts.attribute(_cut(), _deaf(range(10, 170, 10)))
    assert microcuts.describe_deaf(cuts) == (
        "this host's Wi-Fi heard nothing for 2 min 40 s: the monitor was deaf, "
        "not the link cut")
    assert microcuts.describe_deaf(microcuts.attribute(_cut(), [])) == ""


def test_uplink_flux_reads_only_deaf_samples_of_the_uplink_interface():
    flux = microcuts.uplink_flux("-24h")
    assert 'r._field == "uplink" and r._value == 1' in flux
    assert "contains(value: r.interface, set: uplinks)" in flux
    assert 'difference(columns: ["rx_packets"])' in flux
    assert "r.rx_packets == 0 or r.associated == 0" in flux
