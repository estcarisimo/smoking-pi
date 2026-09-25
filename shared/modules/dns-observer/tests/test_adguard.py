import bcrypt
import yaml

import adguard
from config import Config


def test_new_file_turns_filtering_off_and_sets_managed_keys(env):
    cfg = Config.from_env(env)
    doc = adguard.render(cfg, None)
    assert doc["schema_version"] == adguard.SCHEMA_VERSION
    assert doc["http"]["address"] == "127.0.0.1:3053"
    dns = doc["dns"]
    assert dns["ratelimit"] == 0
    assert dns["refuse_any"] is True
    assert dns["cache_optimistic"] is True
    assert dns["use_private_ptr_resolvers"] is False
    assert dns["fallback_dns"] == ["1.1.1.1", "8.8.8.8"]
    assert dns["upstream_timeout"] == "2000ms"
    assert doc["filters"] == []
    assert doc["filtering"]["protection_enabled"] is False
    assert doc["querylog"]["interval"] == "168h"
    assert doc["clients"]["runtime_sources"]["rdns"] is False
    assert bcrypt.checkpw(b"pw", doc["users"][0]["password"].encode())


def test_existing_file_keeps_the_users_choices(env):
    cfg = Config.from_env(env)
    existing = adguard.render(cfg, None)
    # The user turned blocking on in the UI and changed the upstreams there.
    existing["filters"] = [{"enabled": True, "url": "https://x/list.txt", "id": 1}]
    existing["filtering"]["protection_enabled"] = True
    existing["dns"]["upstream_dns"] = ["https://9.9.9.9/dns-query"]
    existing["dns"]["ratelimit"] = 20
    existing["theme"] = "dark"
    doc = adguard.render(cfg, existing)
    assert doc["filters"][0]["url"] == "https://x/list.txt"
    assert doc["filtering"]["protection_enabled"] is True
    assert doc["theme"] == "dark"
    # ... but what the environment owns is put back.
    assert doc["dns"]["upstream_dns"] == cfg.upstreams
    assert doc["dns"]["ratelimit"] == 0


def test_password_hash_is_stable_until_the_password_changes(env):
    cfg = Config.from_env(env)
    first = adguard.render(cfg, None)
    again = adguard.render(cfg, first)
    assert again["users"][0]["password"] == first["users"][0]["password"]
    env["DNS_ADMIN_PASSWORD"] = "new"
    changed = adguard.render(Config.from_env(env), first)
    assert changed["users"][0]["password"] != first["users"][0]["password"]


def test_write_config_merges_on_disk(env):
    cfg = Config.from_env(env)
    assert adguard.write_config(cfg) is True
    with open(cfg.adguard_conf) as fh:
        doc = yaml.safe_load(fh)
    doc["language"] = "es"
    with open(cfg.adguard_conf, "w") as fh:
        yaml.safe_dump(doc, fh)
    assert adguard.write_config(cfg) is False
    with open(cfg.adguard_conf) as fh:
        assert yaml.safe_load(fh)["language"] == "es"
