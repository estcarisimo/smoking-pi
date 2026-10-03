import json

from typer.testing import CliRunner

from dns_explore.cli import app

runner = CliRunner()


def test_report_from_files_without_network(tmp_path, line):
    day = 24 * 60
    lines = (
        [line("www.netflix.com", 60 * h, cnames=("x.nflxso.net",)) for h in range(3)]
        + [line("nrdp.logs.netflix.com", m) for m in range(30)]
        + [line("api.telegram.org", m) for m in range(40)]  # the Pi's own
        + [line("www.bbc.co.uk", day + 5), line("www.netflix.com", day + 6)]
        + [line("www.netflix.com", 1, qtype="PTR")]
    )
    log = tmp_path / "querylog.json"
    log.write_text("\n".join(lines) + "\n")
    out = tmp_path / "r.json"

    res = runner.invoke(
        app, ["--file", str(log), "--no-asn", "--k", "1,2", "--all-days", "--json", str(out)]
    )

    assert res.exit_code == 0, res.output
    assert "Diversity and concentration" in res.output
    assert "Behind the top-K services" in res.output
    data = json.loads(out.read_text())
    assert data["counts"]["excluded_own"] == 40
    assert data["counts"]["dropped_type_or_rcode"] == 1
    assert data["levels"] == ["fqdn", "service", "cdn"]  # no ASN levels without lookups
    top_service = data["top"]["service"][0]
    assert top_service["service"] == "netflix.com"
    assert data["churn"], "two days of log give a churn table"
    assert "Stability of the top-K" in res.output
    stab = {(r["level"], r["k"]): r for r in data["stability"] if r["score"] == "queries"}
    assert stab[("service", 1)]["days"] == 2
    assert set(stab[("service", 1)]) >= {"E1L1", "E2L3", "E3L3", "E3L5"}


def test_rules_must_be_whole_days(tmp_path, line):
    log = tmp_path / "querylog.json"
    log.write_text(line("www.netflix.com") + "\n")
    for bad in ("2", "0:3", "a:b", "2:-1"):
        res = runner.invoke(app, ["--file", str(log), "--no-asn", "--rules", bad])
        assert res.exit_code == 2, (bad, res.output)


def test_empty_after_filtering_exits_1(tmp_path, line):
    log = tmp_path / "querylog.json"
    log.write_text(line("api.telegram.org") + "\n")
    res = runner.invoke(app, ["--file", str(log), "--no-asn"])
    assert res.exit_code == 1


def test_local_midnight_stays_on_its_day(tmp_path, line):
    # Two queries 1 h apart in UTC that are on different *local* days (BST).
    lines = [
        line("www.netflix.com").replace(
            json.loads(line("www.netflix.com"))["T"], "2026-06-30T23:30:00+01:00"
        ),
        line("www.bbc.co.uk").replace(
            json.loads(line("www.bbc.co.uk"))["T"], "2026-07-01T00:30:00+01:00"
        ),
    ]
    log = tmp_path / "querylog.json"
    log.write_text("\n".join(lines) + "\n")
    out = tmp_path / "r.json"
    res = runner.invoke(app, ["--file", str(log), "--no-asn", "--k", "1", "--all-days",
                                 "--json", str(out)])
    assert res.exit_code == 0, res.output
    # In UTC both are on 30 June (one day, no churn); locally they are two days.
    assert json.loads(out.read_text())["churn"], "two local days give a churn table"


def test_own_traffic_is_counted_whatever_its_type(tmp_path, line):
    log = tmp_path / "querylog.json"
    log.write_text(
        "\n".join([line("api.telegram.org", qtype="PTR"), line("www.netflix.com")]) + "\n"
    )
    out = tmp_path / "r.json"
    runner.invoke(app, ["--file", str(log), "--no-asn", "--json", str(out)])
    counts = json.loads(out.read_text())["counts"]
    assert counts["excluded_own"] == 1 and counts["dropped_type_or_rcode"] == 0


def test_what_smokeping_measures_is_left_out(tmp_path, line, monkeypatch):
    # SmokePing re-resolves www.netflix.com all night; the house asked for
    # nrdp.logs.netflix.com once. Only the house's lookup is kept.
    lines = [line("www.netflix.com", m) for m in range(20)] + [
        line("nrdp.logs.netflix.com", 3),
        line("www.bbc.co.uk", 4),
    ]
    log = tmp_path / "querylog.json"
    log.write_text("\n".join(lines) + "\n")
    targets = tmp_path / "Targets"
    targets.write_text("++ W_netflix\nhost = www.netflix.com\n++ G\nhost = 8.8.8.8\n")
    out = tmp_path / "r.json"

    res = runner.invoke(app, ["--file", str(log), "--no-asn", "--k", "1",
                              "--targets", str(targets), "--json", str(out)])
    assert res.exit_code == 0, res.output
    counts = json.loads(out.read_text())["counts"]
    assert counts["excluded_measured"] == 20 and counts["kept"] == 2

    monkeypatch.setattr("dns_explore.cli.TARGETS_CANDIDATES", (targets,))
    res = runner.invoke(app, ["--file", str(log), "--no-asn", "--k", "1", "--json", str(out)])
    assert json.loads(out.read_text())["counts"]["excluded_measured"] == 20  # found by auto

    for args in (["--targets", "none"], ["--no-exclude-own"]):
        res = runner.invoke(app, ["--file", str(log), "--no-asn", "--k", "1",
                                  "--json", str(out), *args])
        assert json.loads(out.read_text())["counts"]["excluded_measured"] == 0, args


def test_a_missing_targets_file_is_an_error(tmp_path, line):
    log = tmp_path / "querylog.json"
    log.write_text(line("www.bbc.co.uk") + "\n")
    res = runner.invoke(app, ["--file", str(log), "--no-asn", "--targets", str(tmp_path / "nope")])
    assert res.exit_code != 0


def test_auto_follows_a_relocated_output_dir(tmp_path, line, monkeypatch):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "Targets").write_text("host = www.netflix.com\n")
    defaults = tmp_path / "smoking-pi"
    defaults.write_text(f'SMOKING_PI_EDITION=pro\nSMOKING_PI_OUTPUT_DIR="{out_dir}"\n')
    monkeypatch.setattr("dns_explore.cli.DEB_DEFAULTS", defaults)
    log = tmp_path / "querylog.json"
    log.write_text(line("www.netflix.com") + "\n" + line("www.bbc.co.uk") + "\n")
    out = tmp_path / "r.json"
    res = runner.invoke(app, ["--file", str(log), "--no-asn", "--k", "1", "--json", str(out)])
    assert res.exit_code == 0, res.output
    assert json.loads(out.read_text())["counts"]["excluded_measured"] == 1


def test_partial_days_at_the_ends_are_left_out(tmp_path, line):
    # From 10:00 on day 0 to 10:00 on day 3: days 1 and 2 are whole.
    day = 24 * 60
    lines = [line("www.netflix.com", m) for m in range(0, 3 * day + 1, 30)]
    log = tmp_path / "querylog.json"
    log.write_text("\n".join(lines) + "\n")
    out = tmp_path / "r.json"
    res = runner.invoke(app, ["--file", str(log), "--no-asn", "--k", "1", "--json", str(out)])
    assert res.exit_code == 0, res.output
    data = json.loads(out.read_text())
    assert data["partial_days_left_out"] == ["2026-09-26", "2026-09-29"]
    assert {r["days"] for r in data["stability"]} == {2}
