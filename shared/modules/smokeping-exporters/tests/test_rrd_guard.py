"""rrd_guard.py: archive the RRDs SmokePing would refuse to load."""

import json
import pathlib
import sys

MODULE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import rrd_guard  # noqa: E402


def _info(step, pings):
    lines = [f"filename = \"x.rrd\"", "rrd_version = \"0003\"", f"step = {step}",
             "ds[uptime].index = 0", "ds[loss].index = 1", "ds[median].index = 2"]
    lines += [f"ds[ping{i}].index = {2 + i}" for i in range(1, pings + 1)]
    lines += [f"ds[ping{i}].type = \"GAUGE\"" for i in range(1, pings + 1)]
    return "\n".join(lines) + "\n"


def _rrds(tmp_path, specs):
    """specs: {rel: (step, pings)} -> files + a fake rrdtool info."""
    by_path = {}
    for rel, spec in specs.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("rrd")
        by_path[path.resolve()] = spec
    return lambda path: _info(*by_path[path.resolve()])


def test_parse_info_counts_the_ping_data_sources():
    assert rrd_guard.parse_info(_info(300, 10)) == {"step": 300, "pings": 10}
    assert rrd_guard.parse_info(_info(60, 5)) == {"step": 60, "pings": 5}
    assert rrd_guard.parse_info("garbage") is None


def test_a_matching_rrd_is_left_alone(tmp_path):
    info = _rrds(tmp_path, {"websites/Google.rrd": (300, 10)})
    got = rrd_guard.guard({"websites/Google.rrd": {"step": 300, "pings": 10}},
                          tmp_path, info, stamp="S")
    assert got == {"checked": 1, "archived": [], "errors": []}
    assert (tmp_path / "websites/Google.rrd").exists()


def test_a_different_step_or_ping_count_is_archived_not_deleted(tmp_path):
    info = _rrds(tmp_path, {"websites/Google.rrd": (300, 10),
                            "DNS_Resolvers/GoogleDNS.rrd": (300, 5)})
    got = rrd_guard.guard({
        "websites/Google.rrd": {"step": 60, "pings": 10},
        "DNS_Resolvers/GoogleDNS.rrd": {"step": 300, "pings": 3},
    }, tmp_path, info, stamp="S")
    assert [a["rrd"] for a in got["archived"]] == [
        "DNS_Resolvers/GoogleDNS.rrd", "websites/Google.rrd"]
    google = got["archived"][1]
    assert google["had"] == {"step": 300, "pings": 10}
    assert google["wants"] == {"step": 60, "pings": 10}
    assert google["to"] == ".archive/S/websites/Google.rrd"
    assert not (tmp_path / "websites/Google.rrd").exists()
    assert (tmp_path / ".archive/S/websites/Google.rrd").read_text() == "rrd"


def test_missing_rrds_are_for_smokeping_to_create(tmp_path):
    got = rrd_guard.guard({"websites/New.rrd": {"step": 300, "pings": 10}},
                          tmp_path, lambda p: "", stamp="S")
    assert got == {"checked": 0, "archived": [], "errors": []}


def test_an_unreadable_rrd_is_reported_and_kept(tmp_path):
    (tmp_path / "websites").mkdir()
    (tmp_path / "websites/Bad.rrd").write_text("rrd")

    def boom(path):
        raise OSError("rrdtool: not an RRD")

    got = rrd_guard.guard({"websites/Bad.rrd": {"step": 300, "pings": 10}},
                          tmp_path, boom, stamp="S")
    assert got["archived"] == [] and got["errors"][0]["rrd"] == "websites/Bad.rrd"
    assert (tmp_path / "websites/Bad.rrd").exists()


def test_paths_outside_the_datadir_or_in_the_archive_are_refused(tmp_path):
    outside = tmp_path.parent / "outside.rrd"
    outside.write_text("rrd")
    got = rrd_guard.guard({
        "../outside.rrd": {"step": 60, "pings": 1},
        ".archive/S/websites/Google.rrd": {"step": 60, "pings": 1},
        "/etc/passwd": {"step": 60, "pings": 1},
    }, tmp_path, lambda p: _info(300, 10), stamp="S")
    assert got["archived"] == [] and len(got["errors"]) == 3
    assert outside.exists()


def test_main_prints_one_json_object(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(rrd_guard, "rrd_info", lambda p: _info(300, 10))
    arg = json.dumps({"datadir": str(tmp_path), "expected": {}})
    assert rrd_guard.main(["rrd_guard.py", arg]) == 0
    assert json.loads(capsys.readouterr().out) == {"checked": 0, "archived": [], "errors": []}
    assert rrd_guard.main(["rrd_guard.py", "not json"]) == 2


def test_dry_run_reports_and_moves_nothing(tmp_path):
    info = _rrds(tmp_path, {"websites/Google.rrd": (300, 10)})
    got = rrd_guard.guard({"websites/Google.rrd": {"step": 60, "pings": 10}},
                          tmp_path, info, stamp="S", dry_run=True)
    assert got["archived"][0]["dry_run"] is True
    assert (tmp_path / "websites/Google.rrd").exists()
    assert not (tmp_path / ".archive").exists()


def test_a_malformed_entry_is_an_error_not_a_traceback(tmp_path):
    info = _rrds(tmp_path, {"websites/Google.rrd": (300, 10)})
    got = rrd_guard.guard({"websites/Google.rrd": {"step": "x"},
                           "websites/NYT.rrd": None}, tmp_path, info, stamp="S")
    assert [e["rrd"] for e in got["errors"]] == ["websites/Google.rrd", "websites/NYT.rrd"]
    assert (tmp_path / "websites/Google.rrd").exists()
