"""templates/taxonomy.yaml and its resolver: pure functions, no I/O but the file."""

import copy
import pathlib

import pytest
import yaml

import taxonomy

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "templates"


@pytest.fixture(scope="module")
def tx():
    return taxonomy.load(taxonomy.TAXONOMY_FILE)


@pytest.fixture()
def raw():
    return yaml.safe_load(taxonomy.TAXONOMY_FILE.read_text())


def template_targets():
    data = yaml.safe_load((TEMPLATES / "targets.yaml").read_text())
    for bucket, entries in data["active_targets"].items():
        for t in entries or []:
            yield t


def test_the_shipped_file_is_valid(raw):
    assert taxonomy.validate(raw) == []


def test_the_vocabulary_is_the_decided_one(tx):
    assert list(tx.categories) == [
        "video", "audio", "gaming", "realtime_comms", "messaging", "social",
        "web_search", "ai_assistant", "cloud_saas", "software_update", "cdn",
        "dns", "connectivity", "ads_analytics", "academic", "unknown"]
    assert "unknown" not in tx.category_ids()


@pytest.mark.parametrize("target", list(template_targets()), ids=lambda t: t["name"])
def test_every_template_target_has_a_label(tx, target):
    label = tx.resolve(target["name"], target["host"], target["category"],
                       target["title"])
    assert label["categories"] != ["unknown"]
    assert label["service"]
    assert label["source"] in taxonomy.SOURCES


def test_the_rule_count_stays_sane(tx):
    assert 50 <= len(tx.rules) <= 120


@pytest.mark.parametrize("name, base", [
    ("Google_h1", "Google"), ("Google_h3", "Google"), ("Google_tcp443", "Google"),
    ("Google_tcp", "Google"), ("W_netflix_com_icmp", "W_netflix_com"),
    ("uba_ar", "uba_ar"), ("", ""),
])
def test_base_name_strips_the_probe_suffix(name, base):
    assert taxonomy.base_name(name) == base


def test_a_name_rule_beats_a_host_rule(tx):
    # NYT by name, though its host would say nothing; Google_h2 by base name.
    assert tx.resolve("Google_h2", "www.youtube.com", "http")["rule"] == "name:Google"


def test_the_longest_host_suffix_wins(tx):
    assert tx.resolve("W_x", "meet.google.com", "dns_wizard")["service"] == "Google Meet"
    assert tx.resolve("W_x", "gemini.google.com", "dns_wizard")["categories"] == [
        "ai_assistant"]
    assert tx.resolve("W_x", "www.google.com", "dns_wizard")["rule"] == (
        "host_suffix:google.com")


def test_a_suffix_matches_on_label_boundaries_only(tx):
    assert tx.resolve("W_x", "notnetflix.com", "dns_wizard")["categories"] == ["unknown"]
    assert tx.resolve("W_x", "OCC-0.NFLXVIDEO.NET.", "dns_wizard")["service"] == "Netflix"


def test_an_address_matches_only_itself(tx):
    assert tx.resolve("W_x", "1.1.1.1", "custom")["service"] == "Cloudflare DNS"
    assert tx.resolve("W_x", "11.1.1.1", "custom")["categories"] == ["unknown"]
    assert tx.resolve("W_x", "[2606:4700:4700::1111]", "custom")["categories"] == ["dns"]


def test_the_category_bucket_is_the_last_resort(tx):
    oca = tx.resolve("OCA_ord001", "198.51.100.7", "netflix_oca")
    assert (oca["service"], oca["rule"]) == ("Netflix", "category:netflix_oca")
    resolver = tx.resolve("ISP_DNS", "192.0.2.53", "dns_resolvers", "My ISP DNS")
    assert resolver["service"] == "My ISP DNS"  # no service in the rule: the title
    assert resolver["categories"] == ["dns"]


def test_youtube_is_multi_label(tx):
    assert tx.resolve("W_x", "rr3---sn-a.googlevideo.com", "dns_wizard")[
        "categories"] == ["video", "social"]


def test_nothing_matched_is_unknown(tx):
    label = tx.resolve("Mystery", "example.org", "custom", "Mystery box")
    assert label == {"service": "Mystery box", "categories": ["unknown"],
                     "source": None, "confidence": None, "rule": None, "note": None}


def _problems(raw):
    return taxonomy.validate(raw)


def test_validation_catches_a_category_outside_the_vocabulary(raw):
    raw["rules"][0]["categories"] = ["video", "news"]
    assert any("category news is not in the vocabulary" in p for p in _problems(raw))


def test_validation_catches_a_match_with_two_keys(raw):
    raw["rules"][0]["match"] = {"name": "x", "host_suffix": "y.com"}
    assert any("exactly one key" in p for p in _problems(raw))


def test_validation_catches_an_unknown_match_key(raw):
    raw["rules"][0]["match"] = {"asn": "15169"}
    assert any("is not one of" in p for p in _problems(raw))


def test_validation_catches_a_non_integer_version(raw):
    raw["version"] = "1"
    assert "version must be a positive integer" in _problems(raw)


def test_validation_catches_a_duplicate_match(raw):
    raw["rules"].append(copy.deepcopy(raw["rules"][0]))
    assert any("already matched by rule 1" in p for p in _problems(raw))


def test_validation_catches_a_rule_assigning_unknown(raw):
    raw["rules"][0]["categories"] = ["unknown"]
    assert any("cannot assign unknown" in p for p in _problems(raw))


def test_validation_catches_bad_source_confidence_and_date(raw):
    raw["rules"][0].update(source="wikipedia", confidence="sure", since="yesterday")
    problems = _problems(raw)
    assert any("source must be one of" in p for p in problems)
    assert any("confidence must be one of" in p for p in problems)
    assert any("since must be a date" in p for p in problems)


def test_validation_needs_unknown_in_the_vocabulary(raw):
    del raw["categories"]["unknown"]
    assert "categories must include unknown" in _problems(raw)


def test_parse_raises_with_every_problem():
    with pytest.raises(taxonomy.TaxonomyError) as err:
        taxonomy.parse("version: x\ncategories: {}\nrules: 3\n")
    assert len(err.value.problems) >= 3
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy.parse("version: [1\n")


@pytest.mark.parametrize("body, reason", [
    (None, "the body must be a JSON object"),
    ({"categories": ["video"]}, "service is required"),
    ({"service": "x" * 101, "categories": ["video"]}, "service must be at most 100 characters"),
    ({"service": "S"}, "categories must be a non-empty list of category ids"),
    ({"service": "S", "categories": []}, "categories must be a non-empty list of category ids"),
    ({"service": "S", "categories": ["news"]},
     "categories must come from GET /taxonomy (and cannot be unknown)"),
    ({"service": "S", "categories": ["unknown"]},
     "categories must come from GET /taxonomy (and cannot be unknown)"),
    ({"service": "S", "categories": ["video"], "note": 3}, "note must be a string"),
    ({"service": "S", "categories": ["video"], "note": "n" * 501},
     "note must be at most 500 characters"),
    ({"service": "S", "categories": ["video"], "source": "adguard"},
     "only service, categories and note can be set"),
])
def test_an_override_is_validated_with_literal_reasons(tx, body, reason):
    cleaned, reasons = tx.validate_override(body)
    assert cleaned is None
    assert reason in reasons


def test_a_valid_override_is_cleaned(tx):
    cleaned, reasons = tx.validate_override(
        {"service": "  Plex ", "categories": ["video", "video", "cloud_saas"],
         "note": "  "})
    assert reasons == []
    assert cleaned == {"service": "Plex", "categories": ["video", "cloud_saas"],
                       "note": None}


def test_an_override_replaces_the_resolution_entirely(tx):
    resolved = tx.resolve("Google", "google.com", "top_sites")
    fields = taxonomy.label_fields(
        resolved, {"service": "Work search", "categories": ["cloud_saas"], "note": "n"})
    assert fields == {"service": "Work search", "categories": ["cloud_saas"],
                      "label_source": "manual", "label_confidence": "high",
                      "label_rule": "override", "label_note": "n"}
    assert taxonomy.label_fields(resolved)["label_rule"] == "name:Google"


# --- the in-container CLI (smoking-pi taxonomy) -----------------------------

LISTING = {"version": 1, "targets": [
    {"id": 7, "name": "Mystery", "service": "Mystery", "categories": ["unknown"],
     "label_source": None, "label_rule": None}]}


@pytest.fixture()
def calls(monkeypatch):
    seen = []

    def fake(method, path, body=None):
        seen.append((method, path, body))
        if method == "GET":
            return 200, LISTING
        return 200, {"target": {"name": "Mystery", "service": "Plex",
                                "categories": ["video"], "label_source": "manual",
                                "label_rule": "override"}}
    monkeypatch.setattr(taxonomy, "_call", fake)
    return seen


def test_cli_list_prints_a_table(calls, capsys):
    assert taxonomy.main(["list"]) == 0
    assert calls == [("GET", "/taxonomy/targets", None)]
    out = capsys.readouterr().out
    assert "TARGET" in out and "Mystery" in out and "unknown" in out


def test_cli_unknown_json_is_the_api_body(calls, capsys):
    assert taxonomy.main(["unknown", "--json"]) == 0
    assert calls[0][1] == "/taxonomy/unknown"
    assert '"Mystery"' in capsys.readouterr().out


def test_cli_set_puts_the_override(calls, capsys):
    assert taxonomy.main(["set", "Mystery", "Plex", "video, cloud_saas", "--note", "n"]) == 0
    assert calls[1] == ("PUT", "/targets/7/labels",
                        {"service": "Plex", "categories": ["video", "cloud_saas"],
                         "note": "n"})
    assert "Plex [video] (manual, override)" in capsys.readouterr().out


def test_cli_reset_deletes_the_override(calls):
    assert taxonomy.main(["reset", "Mystery"]) == 0
    assert calls[1] == ("DELETE", "/targets/7/labels", None)


def test_cli_an_unknown_target_fails(calls, capsys):
    assert taxonomy.main(["reset", "Nope"]) == 1
    assert "no target named Nope" in capsys.readouterr().err


def test_cli_a_refusal_prints_its_reasons(monkeypatch, capsys):
    monkeypatch.setattr(taxonomy, "_call", lambda *a, **k: (
        (200, LISTING) if a[0] == "GET" else
        (400, {"error": "Invalid labels", "reasons": ["service is required"]})))
    assert taxonomy.main(["set", "Mystery", "x", "news"]) == 1
    err = capsys.readouterr().err
    assert "refused (400): Invalid labels" in err and "service is required" in err


def test_an_address_matches_in_any_spelling(tx):
    assert tx.resolve("W_x", "2001:4860:4860:0:0:0:0:8888", "custom")["service"] == (
        "Google Public DNS")
    assert tx.resolve("W_x", "2606:4700:4700:0000::1111", "custom")["categories"] == ["dns"]
