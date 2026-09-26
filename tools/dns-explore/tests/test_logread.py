from dns_explore.logread import parse_line, parse_lines


def test_parses_answer_chain_and_addresses(line):
    q = parse_line(
        line("WWW.Example.org.", cnames=("d3p8.cloudfront.net",), addrs=("192.0.2.7", "192.0.2.8"))
    )
    assert q.qname == "www.example.org"
    assert q.rcode == "NOERROR"
    assert q.cnames == ("d3p8.cloudfront.net",)
    assert q.addrs == ("192.0.2.7", "192.0.2.8")
    assert q.ts.microsecond == 123456  # nanoseconds truncated, not rejected


def test_missing_or_broken_answer_is_unknown():
    q = parse_line('{"T":"2026-09-26T10:00:00Z","QH":"a.test","QT":"A","Answer":"!!"}')
    assert q.rcode == "UNKNOWN" and q.addrs == ()
    q = parse_line('{"T":"2026-09-26T10:00:00Z","QH":"a.test","QT":"A"}')
    assert q.rcode == "UNKNOWN"


def test_unreadable_lines_are_skipped(line, caplog):
    got = list(parse_lines([line("a.example.org"), "not json", '{"QH": "no time"}', ""]))
    assert [q.qname for q in got] == ["a.example.org"]
    assert "skipped 2" in caplog.text
