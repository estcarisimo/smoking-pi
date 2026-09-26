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
        + [line("a.example.org", 1, qtype="PTR")]
    )
    log = tmp_path / "querylog.json"
    log.write_text("\n".join(lines) + "\n")
    out = tmp_path / "r.json"

    res = runner.invoke(app, ["--file", str(log), "--no-asn", "--k", "1,2", "--json", str(out)])

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


def test_empty_after_filtering_exits_1(tmp_path, line):
    log = tmp_path / "querylog.json"
    log.write_text(line("api.telegram.org") + "\n")
    res = runner.invoke(app, ["--file", str(log), "--no-asn"])
    assert res.exit_code == 1
