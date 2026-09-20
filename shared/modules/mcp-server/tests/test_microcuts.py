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
