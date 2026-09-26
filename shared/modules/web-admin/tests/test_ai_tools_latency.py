"""The web assistant's get_latency_stats covers every per-target measurement.

It read only ICMP and DNS, so the HTTP (*_h1/_h2/_h3) and TCP (*_tcp443)
targets were invisible to the in-UI assistant, as they were to the MCP tool.
"""

from app.services import ai_tools


def test_latency_stats_reads_http_and_tcp(monkeypatch):
    seen = []

    def query(flux):
        seen.append(flux)
        if '_field == "median"' in flux:
            return [{"target": "Google_h2", "_measurement": "http_latency",
                     "_value": 0.1}]
        return []

    monkeypatch.setattr(ai_tools, "query_influx", query)
    result = ai_tools._get_latency_stats({"hours": 6})
    for m in ("latency", "dns_latency", "http_latency", "tcp_latency"):
        assert all(f'r._measurement == "{m}"' in q for q in seen)
    assert result["stats"][0]["target"] == "Google_h2"
    assert result["stats"][0]["median_ms"] == 100.0


def test_assistant_queries_leave_the_dns_wizard_out():
    from app.services import ai_tools as tools
    q = tools._base_flux(["latency", "dns_latency"], 24)
    assert 'not exists r.category or r.category != "dns_wizard"' in q
