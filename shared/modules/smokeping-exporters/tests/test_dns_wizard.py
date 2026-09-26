import json

import dns_wizard

SNAP = {
    "generated": 1790380800,
    "queries_24h": 4832,
    "queries_1h": 120,
    "own_share_24h": 0.22,
    "hours_with_data": 2,
    "diversity": {
        "service": {"richness": 200, "effective_hhi": 179.4, "effective_shannon": 188.8,
                    "cov5": 0.03, "cov10": 0.07, "cov20": 0.14, "hhi": 0.0056},
        "asn": {"richness": 25, "effective_hhi": 7.4, "effective_shannon": 11.4,
                "cov5": 0.7, "cov10": 0.88, "cov20": None},
    },
    "behind_top10": {"cdn": 10, "asn": 6},
    "churn": {"top10_jaccard_vs_yesterday": None},
    "top": [
        {"rank": 1, "service": "netflix.com", "presence_h": 2, "queries_24h": 249,
         "queries_1h": 3, "host": "www.netflix.com", "cdn": "amazonaws.com",
         "asn": "AS16509", "org": "AMAZON-02"},
    ],
}


def fields(point):
    return point._fields


def tags(point):
    return point._tags


def test_names_are_opt_in():
    assert not dns_wizard.names_enabled({})
    assert not dns_wizard.names_enabled({"DNS_EXPORT_NAMES": "0"})
    assert dns_wizard.names_enabled({"DNS_EXPORT_NAMES": "1"})
    assert dns_wizard.names_enabled({"DNS_EXPORT_NAMES": "true"})


def test_without_names_only_the_house_point_and_no_name_in_it():
    points = dns_wizard.points_for(SNAP, names=False)
    assert len(points) == 1
    line = points[0].to_line_protocol()
    assert line.startswith("dns_wizard ")
    assert "netflix" not in line and "amazonaws" not in line


def test_house_point_fields_skip_missing_values():
    f = fields(dns_wizard.house_point(SNAP, SNAP["generated"]))
    assert f["service_effective_hhi"] == 179.4
    assert f["asn_richness"] == 25.0
    assert f["top10_distinct_asn"] == 6.0
    assert "asn_cov20" not in f  # None is not written
    assert "top10_jaccard" not in f  # no yesterday yet
    assert "service_hhi" not in f  # only the listed measures


def test_with_names_one_point_per_service_at_the_snapshot_time():
    points = dns_wizard.points_for(SNAP, names=True)
    svc = [p for p in points if p.to_line_protocol().startswith("dns_wizard_service")]
    assert len(svc) == 1
    assert tags(svc[0]) == {"service": "netflix.com", "cdn": "amazonaws.com",
                            "asn": "AS16509", "org": "AMAZON-02"}
    assert fields(svc[0])["presence_h"] == 2
    assert fields(svc[0])["host"] == "www.netflix.com"
    # The same timestamp on every point: the panel shows the latest snapshot only.
    stamps = {p.to_line_protocol().rsplit(" ", 1)[1] for p in points}
    assert stamps == {str(SNAP["generated"])}


def test_missing_or_broken_snapshot(tmp_path):
    assert dns_wizard.read_snapshot(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert dns_wizard.read_snapshot(bad) is None
    good = tmp_path / "wizard.json"
    good.write_text(json.dumps(SNAP))
    assert dns_wizard.read_snapshot(good)["queries_24h"] == 4832
