"""shared/scripts/build-taxonomy.py, the maintainer's refresh tool.

Every source is a fixture written here, a few rows in each format (not
copied from the projects); nothing is fetched. The rules are a small
taxonomy of the same shape as templates/taxonomy.yaml.
"""

import datetime as dt
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "shared" / "scripts" / "build-taxonomy.py"
TAXONOMY = ROOT / "shared" / "modules" / "config-manager" / "templates" / "taxonomy.yaml"
TODAY = dt.date(2026, 10, 22)


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("build_taxonomy", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up there
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


ADGUARD = json.dumps({
    "blocked_services": [
        {"id": "examplevideo", "name": "ExampleVideo", "group": "streaming",
         "rules": ["||examplevideo.test^", "||evcdn.test^", "||bad rule^", "@@||allow.test^"]},
        {"id": "examplechat", "name": "ExampleChat", "group": "messenger",
         "rules": ["||examplechat.test^"]},
        {"id": "exampleshop", "name": "ExampleShop", "group": "shopping",
         "rules": ["||exampleshop.test^"]},
    ],
    "groups": [{"id": "streaming"}, {"id": "messenger"}, {"id": "shopping"}],
})

NDPI = """
/* not the table */
static ndpi_protocol_match other_match[] = {
  { "ignored.test", "Ignored", NDPI_PROTOCOL_X, NDPI_PROTOCOL_CATEGORY_GAME, NDPI_PROTOCOL_FUN, 0 },
};
static ndpi_protocol_match host_match[] =
  {
   { "examplegame.test",   "ExampleGame",  NDPI_PROTOCOL_EXAMPLEGAME, NDPI_PROTOCOL_CATEGORY_GAME, NDPI_PROTOCOL_FUN, NDPI_PROTOCOL_DEFAULT_LEVEL },
   { ".examplechat.test",  "ExampleChat",  NDPI_PROTOCOL_EXAMPLECHAT, NDPI_PROTOCOL_CATEGORY_VOIP, NDPI_PROTOCOL_ACCEPTABLE, NDPI_PROTOCOL_DEFAULT_LEVEL },
   { "-pattern-",          "Pattern",      NDPI_PROTOCOL_PATTERN, NDPI_PROTOCOL_CATEGORY_WEB, NDPI_PROTOCOL_FUN, NDPI_PROTOCOL_DEFAULT_LEVEL },
   { NULL, NULL, 0, 0, 0, 0 }
  };
"""

RADAR = {"success": True, "result": {"details_0": {
    "rank": 1234, "categories": [{"id": 184, "name": "Artificial Intelligence",
                                  "superCategoryId": 7}]}}}

RULES = {
    "version": 1,
    "categories": {"video": {}, "audio": {}, "messaging": {}, "realtime_comms": {},
                   "gaming": {}, "ai_assistant": {}, "unknown": {}},
    "rules": [
        {"match": {"name": "Google"}, "service": "Google", "categories": ["video"],
         "source": "manual", "confidence": "high", "since": dt.date(2026, 10, 1)},
        # Agrees with AdGuard: streaming covers audio too.
        {"match": {"host_suffix": ["examplevideo.test"]}, "service": "ExampleVideo",
         "categories": ["audio"], "source": "adguard", "confidence": "high",
         "since": dt.date(2026, 10, 1)},
        # Its own source puts it elsewhere.
        {"match": {"host_suffix": ["examplechat.test"]}, "service": "ExampleChat",
         "categories": ["realtime_comms"], "source": "adguard", "confidence": "high",
         "since": dt.date(2026, 10, 1)},
        # Its source lists none of its hosts any more.
        {"match": {"host_suffix": ["gone.test", "alsogone.test"]}, "service": "Gone",
         "categories": ["gaming"], "source": "ndpi", "confidence": "medium",
         "since": dt.date(2026, 10, 1)},
        # Old.
        {"match": {"host_suffix": ["examplegame.test"]}, "service": "ExampleGame",
         "categories": ["gaming"], "source": "ndpi", "confidence": "high",
         "since": dt.date(2025, 1, 1)},
    ],
}

TARGETS = {"version": 1, "targets": [
    {"name": "Labeled", "host": "examplevideo.test", "is_active": True,
     "categories": ["audio"]},
    {"name": "cdn_edge", "host": "https://edge.evcdn.test/x", "is_active": True,
     "categories": ["unknown"]},
    {"name": "assistant", "host": "chat.example-ai.test", "is_active": True,
     "categories": ["unknown"]},
    {"name": "home_nas", "host": "192.168.1.10", "is_active": True, "categories": ["unknown"]},
    {"name": "Off", "host": "examplegame.test", "is_active": False, "categories": ["unknown"]},
]}


@pytest.fixture
def files(tmp_path):
    (tmp_path / "taxonomy.yaml").write_text(yaml.safe_dump(RULES))
    (tmp_path / "services.json").write_text(ADGUARD)
    (tmp_path / "ndpi.inc").write_text(NDPI)
    (tmp_path / "targets.json").write_text(json.dumps(TARGETS))
    return tmp_path


def run(tool, argv, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = tool.main(argv, today=TODAY, **kw)
    return code, buf.getvalue()


def test_the_parsers_read_only_what_they_should(tool):
    adguard = tool.parse_adguard(ADGUARD)
    assert {s.domain for s in adguard} == {"examplevideo.test", "evcdn.test",
                                           "examplechat.test", "exampleshop.test"}
    video = next(s for s in adguard if s.domain == "evcdn.test")
    assert (video.category, video.accepts) == ("video", ("video", "audio"))
    assert next(s for s in adguard if s.domain == "exampleshop.test").category == ""
    ndpi = tool.parse_ndpi(NDPI)
    assert [(s.domain, s.service, s.category) for s in ndpi] == [
        ("examplegame.test", "ExampleGame", "gaming"),
        ("examplechat.test", "ExampleChat", "realtime_comms")]
    radar = tool.parse_radar("example-ai.test", RADAR)
    assert [(s.category, s.raw) for s in radar] == [("ai_assistant",
                                                     "184 Artificial Intelligence")]
    assert tool.parse_radar("x.test", {"success": False}) == []


def test_every_mapping_lands_in_the_shipped_vocabulary(tool):
    vocabulary = set(yaml.safe_load(TAXONOMY.read_text())["categories"])
    mapped = set()
    for table in (tool.ADGUARD_GROUPS, tool.NDPI_CATEGORIES, tool.RADAR_CATEGORIES):
        for value in table.values():
            mapped.update((value,) if isinstance(value, str) else value)
    assert mapped <= vocabulary - {"unknown"}


def test_unlabeled_targets_get_paste_ready_rules(tool, files):
    code, out = run(tool, ["--taxonomy", str(files / "taxonomy.yaml"),
                           "--adguard", str(files / "services.json"),
                           "--ndpi", str(files / "ndpi.inc"),
                           "--targets", str(files / "targets.json"),
                           "--out", str(files / "report.md")])
    assert code == 0  # no --check: findings never fail a plain run
    assert (files / "report.md").read_text() == out
    section = out.split("## (a)")[1].split("## (b)")[0]
    assert section.count("### cdn_edge (edge.evcdn.test)") == 1
    assert "### home_nas (192.168.1.10)" in section and "No source knows this host." in section
    assert "Labeled" not in section and "Off" not in section
    snippet = section.split("```yaml")[1].split("```")[0]
    rule = yaml.safe_load(snippet)[0]
    assert rule == {"match": {"host_suffix": ["evcdn.test"]}, "service": "ExampleVideo",
                    "categories": ["video"], "source": "adguard", "confidence": "low",
                    "since": TODAY,
                    "note": "Proposed for edge.evcdn.test from adguard (streaming); check it."}


def test_disagreements_and_stale_rules_fail_check(tool, files):
    code, out = run(tool, ["--taxonomy", str(files / "taxonomy.yaml"),
                           "--adguard", str(files / "services.json"),
                           "--ndpi", str(files / "ndpi.inc"), "--check"])
    assert code == 1
    disagreements = out.split("## (b)")[1].split("## (c)")[0]
    assert ("adguard puts examplechat.test in messaging (messenger); "
            "the rule says realtime_comms") in disagreements
    assert "its source, ndpi, lists none of its hosts" in disagreements
    assert disagreements.split("Other sources")[0].count("examplevideo.test") == 0
    # nDPI agrees with the rule, but its view of a rule sourced from
    # AdGuard is information, not a failure.
    stale = out.split("## (c)")[1]
    assert "rule 5 (['examplegame.test']): since 2025-01-01, older than 180 days" in stale
    assert "rule 1" not in stale


def test_check_passes_when_the_sources_agree(tool, files):
    rules = dict(RULES, rules=[RULES["rules"][1]])
    (files / "taxonomy.yaml").write_text(yaml.safe_dump(rules))
    code, out = run(tool, ["--taxonomy", str(files / "taxonomy.yaml"),
                           "--adguard", str(files / "services.json"), "--check"])
    assert code == 0
    assert "## (b) Disagreements\n\nNone." in out
    assert "No --targets file given" in out


def test_a_source_not_consulted_says_nothing(tool, files):
    # Without --ndpi, the ndpi-sourced rule whose hosts are gone is no finding.
    code, out = run(tool, ["--taxonomy", str(files / "taxonomy.yaml"),
                           "--adguard", str(files / "services.json"), "--max-age", "9999"])
    assert "lists none of its hosts" not in out
    assert "Sources consulted: adguard." in out


def test_radar_needs_a_token_and_is_asked_politely(tool, files, monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    code, _ = run(tool, ["--taxonomy", str(files / "taxonomy.yaml"), "--radar"])
    assert code == 2

    asked, slept = [], []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        asked.append((req.full_url, req.get_header("Authorization")))
        return Resp(json.dumps(RADAR).encode())

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t0ken")
    cache = tmp_path / "cache"
    code, out = run(tool, ["--taxonomy", str(files / "taxonomy.yaml"), "--radar",
                           "--radar-delay", "0.5", "--radar-max", "2",
                           "--targets", str(files / "targets.json"),
                           "--cache-dir", str(cache)],
                    opener=opener, sleep=slept.append)
    assert len(asked) == 2 and slept == [0.5, 0.5]
    assert asked[0] == ("https://api.cloudflare.com/client/v4/radar/ranking/domain/"
                        "evcdn.test", "Bearer t0ken")
    assert "t0ken" not in out
    assert "categories: [ai_assistant]" in out  # chat.example-ai.test, from Radar
    # Cached: a second run asks nothing.
    run(tool, ["--taxonomy", str(files / "taxonomy.yaml"), "--radar", "--radar-max", "2",
               "--targets", str(files / "targets.json"), "--cache-dir", str(cache)],
        opener=opener, sleep=slept.append)
    assert len(asked) == 2


def test_fetch_is_https_only_and_cached(tool, tmp_path):
    with pytest.raises(ValueError):
        tool.fetch("http://example.test/x", tmp_path, "x")
    (tmp_path / "y").write_text("cached")
    assert tool.fetch("https://example.test/y", tmp_path, "y",
                      opener=lambda *a, **k: pytest.fail("fetched")) == "cached"


def test_help_says_it_is_a_maintainer_tool(tool, capsys):
    with pytest.raises(SystemExit):
        tool.main(["--help"])
    assert "not a smoking-pi command" in capsys.readouterr().out


def test_the_shipped_taxonomy_loads_and_reports(tool):
    report = tool.build_report(tool.load_rules(TAXONOMY), [], tool.Index(), TODAY, 180)
    assert report.stale == []  # every shipped rule is recent
