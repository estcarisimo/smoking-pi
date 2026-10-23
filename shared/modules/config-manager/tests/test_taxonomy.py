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
        "web_search", "web", "ai_assistant", "cloud_saas", "software_update", "cdn",
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


def test_a_name_rule_beats_a_host_rule(raw):
    # The seed has no name rules (it labels by host); the precedence holds.
    raw["rules"].append({"match": {"name": "Google"}, "service": "Not search",
                         "categories": ["cloud_saas"], "source": "manual",
                         "confidence": "high", "since": "2026-10-23"})
    tx = taxonomy.Taxonomy(taxonomy.parse(yaml.safe_dump(raw)))
    assert tx.resolve("Google_h2", "www.google.com", "http")["rule"] == "name:Google"
    assert tx.resolve("W_google_com_h2", "www.google.com", "dns_wizard")["rule"] == (
        "host_suffix:google.com")


def test_the_seed_labels_by_host_not_by_name(tx):
    # A name rule labels the template's copy of a host and not the DNS
    # wizard's (W_amazon_com_h2 for www.amazon.com): see docs/taxonomy.md.
    assert tx.by_name == {}


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
    assert taxonomy.label_fields(resolved)["label_rule"] == "host_suffix:google.com"


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


# --- what a real install measures ------------------------------------------

# The shipped targets keep the labels they had when the seed matched them
# by name, except Amazon, NYT and Apple, which became websites ([web, cdn]).
TEMPLATE_LABELS = {
    "Google": ("Google", ["web_search"]),
    "Facebook": ("Facebook", ["social"]),
    "Amazon": ("Amazon", ["web", "cdn"]),
    "NYT": ("The New York Times", ["web", "cdn"]),
    "Apple": ("Apple", ["web", "cdn"]),
    "Cloudflare": ("Cloudflare", ["cdn"]),
    "GoogleDNS": ("Google Public DNS", ["dns"]),
    "CloudflareDNS": ("Cloudflare DNS", ["dns"]),
    "Quad9DNS": ("Quad9", ["dns"]),
    "uba_ar": ("Universidad de Buenos Aires", ["academic"]),
}


@pytest.mark.parametrize("target", list(template_targets()), ids=lambda t: t["name"])
def test_template_targets_keep_their_labels(tx, target):
    label = tx.resolve(target["name"], target["host"], target["category"],
                       target["title"])
    assert (label["service"], label["categories"]) == TEMPLATE_LABELS[
        taxonomy.base_name(target["name"])]


@pytest.mark.parametrize("seed, wizard, host", [
    ("Google_h2", "W_google_com_h2", "www.google.com"),
    ("Amazon", "W_amazon_com_h2", "www.amazon.com"),
    ("Amazon", "W_www_amazon_com_icmp", "www.amazon.com"),
    ("Apple", "W_apple_com_tcp", "apple.com"),
    ("NYT", "W_nytimes_com_h3", "nytimes.com"),
    ("Facebook_tcp443", "W_facebook_com_tcp", "www.facebook.com"),
    ("uba_ar", "W_uba_ar_icmp", "uba.ar"),
])
def test_a_wizard_copy_resolves_like_its_seed_twin(tx, seed, wizard, host):
    twin = tx.resolve(seed, host, "top_sites")
    assert tx.resolve(wizard, host, "dns_wizard") == twin
    assert twin["categories"] != ["unknown"]


# The hosts the reference Pi measured unlabeled on 2026-10-22 (DNS wizard
# targets, named W_<host>_<probe>), with the label each now gets.
PRODUCTION_HOSTS = {
    "acrobat.adobe.com": ("Adobe", ["web", "cloud_saas"]),
    "api.amazonalexa.com": ("Alexa", ["ai_assistant"]),
    "api.open-meteo.com": ("Open-Meteo", ["cloud_saas"]),
    "api2.cursor.sh": ("Cursor", ["ai_assistant"]),
    "app-analytics-services.com": ("Google Ads and Analytics", ["ads_analytics"]),
    "app.notion.com": ("Notion", ["cloud_saas"]),
    "asqzwp4gpyut7.us-east-1.prod.service.minerva.devices.a2z.com": (
        "Amazon telemetry", ["ads_analytics"]),
    "bag.itunes.apple.com": ("App Store", ["software_update"]),
    "c.go-mpulse.net": ("Akamai mPulse", ["ads_analytics"]),
    "configuration-lb.ls-apple.com.akadns.net": ("Apple device services", ["cloud_saas"]),
    "configuration.ls.v.aaplimg.com": ("Apple device services", ["cloud_saas"]),
    "consumer.cloud.gist.build": ("Customer.io in-app messaging", ["ads_analytics"]),
    "easylist-downloads.adblockplus.org": ("Adblock Plus filter lists", ["software_update"]),
    "extension.femetrics.grammarly.io": ("Grammarly", ["ads_analytics"]),
    "gateway.grammarly.com": ("Grammarly", ["ai_assistant", "cloud_saas"]),
    "grafana.com": ("Grafana Labs", ["software_update"]),
    "http-intake.logs.us5.datadoghq.com": ("Datadog", ["ads_analytics"]),
    "imap.gmail.com": ("Gmail", ["messaging", "cloud_saas"]),
    "img-resize-cdn-prod.samsungnyc.com": ("Samsung Smart TV", ["cloud_saas"]),
    "osb-v2.samsungqbe.com": ("Samsung Smart TV", ["cloud_saas"]),
    "time.samsungcloudsolution.com": ("Time service (NTP)", ["connectivity"]),
    "mask.apple-dns.net": ("iCloud Private Relay", ["connectivity"]),
    "mattermost.caida.org": ("CAIDA Mattermost", ["messaging", "academic"]),
    "mega-us-pr.eufy.com": ("eufy", ["cloud_saas"]),
    "mobile.events.data.microsoft.com": ("Microsoft telemetry", ["ads_analytics"]),
    "napps-1.com": ("NordVPN", ["connectivity"]),
    "ns1.uba.ar": ("Universidad de Buenos Aires", ["academic"]),
    "ntp.aliyun.com": ("Time service (NTP)", ["connectivity"]),
    "oauthaccountmanager.googleapis.com": ("Google APIs", ["cloud_saas"]),
    "sentry.io": ("Sentry", ["ads_analytics"]),
    "slackb.com": ("Slack", ["messaging", "cloud_saas"]),
    "ssl.gstatic.com": ("Google static content", ["cdn"]),
    "tvx.adgrx.com": ("Samsung Ads", ["ads_analytics"]),
    "unagi-na.amazon.com": ("Amazon telemetry", ["ads_analytics"]),
    "web.diagnostic.networking.aws.dev": ("AWS network diagnostics", ["connectivity"]),
    "www.piwheels.org": ("piwheels", ["software_update"]),
}

# The hosts the staging wizard had adopted and the v2.47.0-rc.1 acceptance
# found unlabeled (the seed above was fed production's list only).
STAGING_HOSTS = {
    "www.microsoft.com": ("Microsoft", ["web"]),
    "www.mozilla.org": ("Mozilla", ["web"]),
    "www.python.org": ("Python Software Foundation", ["web"]),
    "www.wikipedia.org": ("Wikipedia", ["web"]),
    "www.bbc.co.uk": ("BBC", ["web"]),
    "acm.org": ("ACM", ["academic", "web"]),
}

# Left unknown on purpose, with the reason.
STILL_UNKNOWN = {
    # No source lists it and its owner could not be confirmed (it may be a
    # bot-detection collector); a guess would be worse than unknown.
    "tzm.protechts.net",
}


def _wizard_name(host):
    return "W_" + host.replace(".", "_").replace("-", "_") + "_icmp"


@pytest.mark.parametrize("host", sorted(PRODUCTION_HOSTS))
def test_production_hosts_have_a_label(tx, host):
    label = tx.resolve(_wizard_name(host), host, "dns_wizard")
    assert (label["service"], label["categories"]) == PRODUCTION_HOSTS[host]
    assert label["rule"].startswith("host_suffix:")


@pytest.mark.parametrize("host", sorted(STAGING_HOSTS))
def test_staging_hosts_have_a_label(tx, host):
    label = tx.resolve(_wizard_name(host), host, "dns_wizard")
    assert (label["service"], label["categories"]) == STAGING_HOSTS[host]
    assert label["rule"].startswith("host_suffix:")


def test_microsoft_catch_all_yields_to_its_services(tx):
    assert tx.resolve("W_x", "teams.microsoft.com", "dns_wizard")["service"] == "Microsoft Teams"
    assert tx.resolve("W_x", "mobile.events.data.microsoft.com", "dns_wizard")["categories"] == [
        "ads_analytics"]
    assert tx.resolve("W_x", "copilot.microsoft.com", "dns_wizard")["service"] == "Microsoft Copilot"
    assert tx.resolve("W_x", "update.microsoft.com", "dns_wizard")["service"] == "Windows Update"
    assert tx.resolve("W_x", "delivery.mp.microsoft.com", "dns_wizard")["service"] == "Windows Update"


@pytest.mark.parametrize("host", sorted(STILL_UNKNOWN))
def test_hosts_left_unknown_on_purpose(tx, host):
    assert tx.resolve(_wizard_name(host), host, "dns_wizard")["categories"] == [
        "unknown"]


def test_production_counted_37_unlabeled_hosts():
    assert len(PRODUCTION_HOSTS) + len(STILL_UNKNOWN) == 37


def test_a_service_s_telemetry_outranks_its_website(tx):
    assert tx.resolve("W_x", "www.amazon.com", "dns_wizard")["categories"] == ["web", "cdn"]
    assert tx.resolve("W_x", "unagi-na.amazon.com", "dns_wizard")["categories"] == [
        "ads_analytics"]
    assert tx.resolve("W_x", "music.amazon.com", "dns_wizard")["service"] == "Amazon Music"


def test_googleapis_carves_out_video_and_fonts(tx):
    assert tx.resolve("W_x", "youtubei.googleapis.com", "dns_wizard")["service"] == "YouTube"
    assert tx.resolve("W_x", "fonts.googleapis.com", "dns_wizard")["categories"] == ["cdn"]
    assert tx.resolve("W_x", "www.googleapis.com", "dns_wizard")["categories"] == [
        "cloud_saas"]
