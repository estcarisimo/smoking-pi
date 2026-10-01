"""Unit tests for measurement_budget.py -- no config-manager, no InfluxDB."""

import http.client
import io
import json
import logging
import pathlib
import sys
import urllib.error

MODULE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import measurement_budget as mb  # noqa: E402

# config-manager /budget on the shipped seed (docs/measurement-budget.md),
# two of its probes, plus one of a class with no measured cost.
SEED = {
    "available": True, "complete": True, "targets": 22,
    "samples_per_hour": 888.0, "mb_per_day": 34.0,
    "ceiling": {"mb_per_day": 1000.0, "samples_per_hour": 20000.0},
    "used": {"samples_pct": 4.4, "bandwidth_pct": 3.4},
    "over": False, "unpriced": ["MyProbe"],
    "by_probe": [
        {"probe": "CurlHTTP1", "class": "Curl", "targets": 3, "step": 300, "pings": 3,
         "samples_per_hour": 108.0, "bytes_per_sample": 12000, "mb_per_day": 31.1},
        {"probe": "FPing", "class": "FPing", "targets": 6, "step": 300, "pings": 10,
         "samples_per_hour": 720.0, "bytes_per_sample": 168, "mb_per_day": 2.9},
        {"probe": "MyProbe", "class": "MyProbe", "targets": 1, "step": 300, "pings": 5,
         "samples_per_hour": 60.0, "bytes_per_sample": None, "mb_per_day": None},
    ],
}


def lines(points):
    return [p.to_line_protocol() for p in points]


def test_the_report_becomes_a_total_and_one_point_per_probe():
    out = lines(mb.points_for(SEED, 1790874000))
    assert len(out) == 4
    total = out[0]
    assert total.startswith("measurement_budget ")
    for field in ("targets=22i", "samples_per_hour=888", "mb_per_day=34",
                  "ceiling_mb_per_day=1000", "ceiling_samples_per_hour=20000",
                  "bandwidth_pct=3.4", "samples_pct=4.4", "over=0i",
                  "complete=1i", "unpriced=1i"):
        assert field in total
    assert total.endswith(" 1790874000")
    curl = out[1]
    assert curl.startswith("measurement_budget_probe,probe=CurlHTTP1,probe_class=Curl ")
    assert "mb_per_day=31.1" in curl and "bytes_per_sample=12000i" in curl
    assert "pings=3i" in curl and "step=300i" in curl


def test_an_unpriced_probe_has_no_byte_figure_rather_than_zero():
    mine = lines(mb.points_for(SEED, 0))[3]
    assert mine.startswith("measurement_budget_probe,probe=MyProbe,probe_class=MyProbe ")
    assert "mb_per_day" not in mine and "bytes_per_sample" not in mine
    assert "samples_per_hour=60" in mine


def test_over_and_incomplete_are_flags():
    out = lines(mb.points_for({**SEED, "over": True, "complete": False}, 0))[0]
    assert "over=1i" in out and "complete=0i" in out


def test_no_budget_yet_writes_nothing():
    assert mb.points_for({"available": False, "reason": "no generated Targets file yet"}, 0) == []


def test_points_are_stamped_at_the_five_minute_boundary():
    assert mb.boundary(1790874299.9) == 1790874000
    assert mb.boundary(1790874300.0) == 1790874300


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_token_is_sent_only_when_set():
    seen = []

    def opener(req, timeout):
        seen.append(req)
        return FakeResponse(json.dumps(SEED).encode())

    assert mb.fetch_budget("http://127.0.0.1:5000/", "tok", opener)["targets"] == 22
    assert mb.fetch_budget("http://127.0.0.1:5000", "", opener) is not None
    assert seen[0].full_url == "http://127.0.0.1:5000/budget"
    assert seen[0].get_header("X-api-token") == "tok"
    assert seen[1].get_header("X-api-token") is None


def test_a_failure_logs_the_type_only(caplog):
    def refused(req, timeout):
        raise urllib.error.URLError("http://127.0.0.1:5000 token=secret-xyz")
    with caplog.at_level(logging.WARNING, logger="measurement_budget"):
        assert mb.fetch_budget("http://127.0.0.1:5000", "secret-xyz", refused) is None
    assert "URLError" in caplog.text
    assert "secret-xyz" not in caplog.text and "127.0.0.1" not in caplog.text


def test_an_answer_that_is_not_json_or_not_an_object_is_none():
    assert mb.fetch_budget("http://x", "", lambda r, timeout: FakeResponse(b"<html>")) is None
    assert mb.fetch_budget("http://x", "", lambda r, timeout: FakeResponse(b"[1]")) is None


def test_a_refusal_logs_its_status_and_nothing_else(caplog):
    def refused(req, timeout):
        raise urllib.error.HTTPError("http://127.0.0.1:5000/budget", 401,
                                     "token=secret-xyz", None, None)
    with caplog.at_level(logging.WARNING, logger="measurement_budget"):
        assert mb.fetch_budget("http://127.0.0.1:5000", "secret-xyz", refused) is None
    assert "HTTP 401" in caplog.text
    assert "secret-xyz" not in caplog.text and "127.0.0.1" not in caplog.text


def test_a_broken_response_does_not_escape():
    def broken(req, timeout):
        raise http.client.IncompleteRead(b"")
    assert mb.fetch_budget("http://x", "", broken) is None
