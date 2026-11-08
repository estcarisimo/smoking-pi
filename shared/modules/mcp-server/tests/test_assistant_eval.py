"""The assistant question bank's report: scoring and assembly from fixture
files, the way shared/scripts/assistant-eval.sh leaves them. No agent, no
Docker, no network -- the raw files are written here."""

import json
from pathlib import Path

import pytest

from evals import report

BANK = Path(__file__).resolve().parents[1] / "evals" / "questions.yaml"
ZONE = "America/Chicago"
START = 1794000000  # 2026-11-07 ~ 17:20 UTC


def _stamp(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.123456789Z")


def _line(epoch: float, body: str) -> str:
    return (f"{_stamp(epoch)} 2026-11-07 11:20:00,000 INFO mcp.tools: {body}")


def _answer(text: str, media: str | None = None) -> str:
    return json.dumps({"status": "ok", "result": {
        "payloads": [{"text": text, "mediaUrl": media}]}})


def _write(raw: Path, qid: str, answer: str, log: list[str], rc: int = 0,
           start: int = START, end: int = START + 60) -> None:
    (raw / f"{qid}.answer.json").write_text(answer)
    (raw / f"{qid}.log").write_text("\n".join(log) + "\n")
    (raw / f"{qid}.meta").write_text(f"start={start} end={end} rc={rc}\n")


def test_bank_loads_and_ids_are_unique():
    questions = report.load_bank(BANK)
    assert len(questions) == 20
    assert {q["lang"] for q in questions} == {"en", "es"}
    for q in questions:
        assert q["ask"] and q["facts"], q["id"]
        assert q.get("tools"), q["id"]
        for name in q.get("skip") or []:
            assert name in report.GLOBAL_CHECKS, (q["id"], name)


def test_parse_log_windows_by_docker_time_and_reads_args():
    log = "\n".join([
        _line(START - 30, "tool=get_updates_since args=since=1h -> verdict=nothing_new in 12ms"),
        _line(START + 5, "tool=get_chart args=hours=6 target=Google -> 2 blocks (1 image) in 900ms"),
        _line(START + 9, "tool=mute_alerts refused: read-only connector"),
        _line(START + 10, "tool=system_status args=- -> ok in 1100ms"),
        "not a docker line",
        _line(START + 200, "tool=diagnose_loss args=hours=6 -> ok in 2500ms"),
    ])
    calls = report.parse_log(log, START, START + 60)
    assert [c.tool for c in calls] == ["get_chart", "mute_alerts", "system_status"]
    assert calls[0].args == {"hours": "6", "target": "Google"}
    assert calls[1].result.startswith("refused")
    assert calls[2].args == {}


def test_parse_answer_joins_payloads_and_sees_media():
    text = json.dumps({"result": {"payloads": [
        {"text": "one", "mediaUrl": None}, {"text": "two", "mediaUrl": "file:///x.png"}]}})
    assert report.parse_answer(text) == ("one\n\ntwo", True)
    # Not JSON: what was printed is the answer (an error, a timeout).
    assert report.parse_answer("gateway closed") == ("gateway closed", False)


def test_mask_hides_ids_hosts_and_tokens():
    text = ("open https://pi.tail1234.ts.net/d/x session 08004ab6-6246-4a4c-9ff5-b17e7ed2d0a2 "
            "chat -1001234567890 token=abc123")
    masked = report.mask(text)
    assert "tail1234" not in masked and "08004ab6" not in masked
    assert "1234567890" not in masked and "abc123" not in masked
    mail = report.mask("from someone@example.com, see https://mail.example/u/?authuser=me%40example.org#x")
    assert "example.com" not in mail and "example.org" not in mail
    assert mail.count("<email>") == 2


def test_a_withheld_answer_is_scored_but_never_printed(tmp_path):
    _write(tmp_path, "netflix-now", _answer("Your doctor wrote; your rent is paid."), [])
    results = report.collect([Q_NETFLIX], tmp_path, ZONE,
                             {"netflix-now": {"withhold": "read the owner's mail"}})
    assert not results[0].passed
    assert any(f.startswith("tool-called") for f in results[0].failures)
    text = report.render(results, "Run", ZONE)
    assert "doctor" not in text and "(withheld: read the owner's mail)" in text


def test_zone_labels_for_chicago():
    labels = report.zone_labels(ZONE)
    assert {"CST", "CDT", "CT", "Chicago", "Central"} <= labels


Q_NETFLIX = {"id": "netflix-now", "lang": "en", "ask": "Is Netflix slow?",
             "tools": {"any": ["get_latency_stats", "diagnose_loss"]},
             "must": [{"name": "latency in ms", "re": r"\d+\s?ms\b"}]}


def _call(tool, **args):
    return report.ToolCall(tool=tool, args={k: str(v) for k, v in args.items()},
                           result="ok", at=START)


def test_score_a_good_answer_passes():
    answer = ("🟢 Netflix is fine: 18 ms median over the last hour, its peers too. "
              "Last blip 9:40 pm CST. [graph](https://h/d/x?from=1794000000000&var-target=Netflix_1_DFW)")
    assert report.score(Q_NETFLIX, answer, False, [_call("get_latency_stats", target="Netflix")],
                        ZONE) == []


@pytest.mark.parametrize("answer, check", [
    ("Netflix_1_DFW is at 18 ms.", "no-raw-id"),
    ("18 ms; a topsites target. top_sites peers fine, 18 ms", "no-raw-id"),
    ("18 ms since 2026-11-07T09:00.", "no-iso-time"),
    ("18 ms since 1794000000.", "no-epoch"),
    ("18 ms; last blip at 9:40 pm.", "zone-named"),
    ("18 ms; last blip at 9:40 pm UTC.", "no-other-zone"),
    ("All clear! 18 ms.", "no-all-clear"),
    ("18 ms\n```\n▁▂▃▅▇\n```", "no-drawn-chart"),
    ("La latencia de Netflix es de 18 ms, está bien y sin pérdida en la semana.", "language"),
])
def test_score_flags_each_global_anti_pattern(answer, check):
    fails = report.score(Q_NETFLIX, answer, False, [_call("diagnose_loss")], ZONE)
    assert any(f.startswith(check + ":") for f in fails), fails


def test_score_tools_and_args():
    no_tool = report.score(Q_NETFLIX, "18 ms", False, [], ZONE)
    assert any(f.startswith("tool-called") for f in no_tool)
    assert any(f.startswith("tools.any") for f in no_tool)
    chart = {"id": "chart", "lang": "en", "ask": "chart",
             "tools": {"all": ["get_chart"]},
             "args": {"get_chart": {"hours": "^6$", "target": "^Google$"}},
             "skip": ["no-image"]}
    wrong_hours = report.score(chart, "Here it is.", True, [_call("get_chart", target="Google",
                                                                    hours=24)], ZONE)
    assert any(f.startswith("args:") for f in wrong_hours)
    right = report.score(chart, "Here it is.", True, [_call("get_chart", target="Google",
                                                              hours=6)], ZONE)
    assert right == []


def test_score_image_unasked_and_chart_tool_forbidden_by_default():
    fails = report.score(Q_NETFLIX, "18 ms", True, [_call("get_latency_stats"),
                                                    _call("get_chart", target="Netflix")], ZONE)
    assert any(f.startswith("tools.none: get_chart") for f in fails)
    assert any(f.startswith("no-image") for f in fails)


def test_score_allow_no_call_and_must_not():
    q = {"id": "data", "lang": "en", "ask": "data per day?",
         "tools": {"allow_no_call": True},
         "must": [{"name": "says so", "re": "can'?not|web admin"}],
         "must_not": [{"name": "invented", "re": r"\b\d+\s?mb\b"}]}
    assert report.score(q, "I cannot see that; the web admin's Probes page has it.",
                        False, [], ZONE) == []
    fails = report.score(q, "About 40 MB a day.", False, [], ZONE)
    assert {f.split(":")[0] for f in fails} == {"must", "must_not"}


def test_collect_and_render_from_fixture_files(tmp_path):
    questions = [Q_NETFLIX, {"id": "es-wifi", "lang": "es", "ask": "¿Cómo está la señal?",
                             "tools": {"any": ["get_wifi_stats"]},
                             "must": [{"name": "dBm", "re": "dbm"}], "facts": "dBm"}]
    _write(tmp_path, "netflix-now", _answer("🟢 Netflix 18 ms over the last hour."),
           [_line(START + 3, "tool=get_latency_stats args=hours=1 target=Netflix -> 5 stats in 80ms"),
            _line(START + 90, "tool=diagnose_loss args=hours=24 -> ok in 2000ms")])
    _write(tmp_path, "es-wifi", _answer("The signal is -48 dBm, excellent."),
           [_line(START + 4, "tool=get_wifi_stats args=hours=24 -> ok in 300ms")])
    results = report.collect(questions, tmp_path, ZONE)
    assert [r.passed for r in results] == [True, False]
    # The diagnose_loss line is after the turn: not credited to it.
    assert [c.tool for c in results[0].calls] == ["get_latency_stats"]
    assert any(f.startswith("language") for f in results[1].failures)

    judged = report.collect(questions, tmp_path, ZONE,
                            {"es-wifi": {"pass": True, "note": "fixture"}})
    assert judged[1].passed and judged[1].judged == "fixture"

    text = report.render(results, "Baseline", ZONE)
    assert "**1 of 2 pass.**" in text
    assert "| 1 | `netflix-now` Is Netflix slow? | get_latency_stats(hours=1 target=Netflix) | ✅ pass |" in text
    assert "> The signal is -48 dBm, excellent." in text
    assert "## 2. `es-wifi` — FAIL" in text


def test_collect_skips_unasked_and_reports_a_failed_turn(tmp_path):
    # What `openclaw agent --json` prints when the model times out.
    failed = json.dumps({"ok": False, "error": {"type": "cli_error",
                                                "message": "codex app-server request timed out"}})
    _write(tmp_path, "netflix-now", failed, [], rc=1)
    results = report.collect([Q_NETFLIX, {"id": "never", "ask": "x"}], tmp_path, ZONE)
    assert [r.id for r in results] == ["netflix-now"]
    assert any(f.startswith("agent-failed") for f in results[0].failures)
    assert results[0].unanswered
    text = report.render(results, "Run", ZONE)
    assert "**0 of 1 pass.** 1 never answered" in text
    assert "⚠️ no answer" in text


def test_list_prints_ids_for_the_runner(capsys):
    assert report.main(["--bank", str(BANK), "--list"]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first == "last-night\tHow was my internet last night?"


def test_answered_tells_the_runner_whether_to_ask_again(tmp_path):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(_answer("🟢 18 ms."))
    bad.write_text(json.dumps({"ok": False, "error": {"message": "tick timeout"}}))
    assert report.main(["--answered", str(good)]) == 0
    assert report.main(["--answered", str(bad)]) == 1
    assert report.main(["--answered", str(tmp_path / "missing.json")]) == 1
