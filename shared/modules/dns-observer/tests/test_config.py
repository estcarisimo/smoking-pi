import pytest

from config import Config, ConfigError


def test_defaults_are_public_and_lan_only(env):
    cfg = Config.from_env(env)
    assert cfg.upstreams == ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"]
    assert cfg.fallback == ["1.1.1.1", "8.8.8.8"]
    assert cfg.port == 53 and cfg.bind_hosts == ["0.0.0.0"]
    assert "192.168.0.0/16" in cfg.allow_clients
    assert cfg.admin_address == "127.0.0.1:3053"
    assert cfg.retention_hours == 168
    assert cfg.canary_via == "auto" and cfg.canary_interval == 300


@pytest.mark.parametrize(
    "name,value",
    [
        ("DNS_UPSTREAMS", "192.168.86.1"),
        ("DNS_UPSTREAMS", "https://192.168.86.1/dns-query"),
        ("DNS_FALLBACK", "10.0.0.1"),
        ("DNS_BOOTSTRAP", "192.168.1.1:53"),
    ],
)
def test_router_as_upstream_is_a_loop(env, name, value):
    env[name] = value
    with pytest.raises(ConfigError, match="loops back"):
        Config.from_env(env)


def test_private_upstream_allowed_on_request(env):
    env.update(DNS_UPSTREAMS="192.168.86.2", DNS_ALLOW_PRIVATE_UPSTREAM="1")
    assert Config.from_env(env).upstreams == ["192.168.86.2"]


def test_fallback_must_be_an_ip(env):
    env["DNS_FALLBACK"] = "dns.google"
    with pytest.raises(ConfigError, match="IP address"):
        Config.from_env(env)


def test_upstream_names_resolve_through_bootstrap(env):
    env["DNS_UPSTREAMS"] = "quic://dns.adguard-dns.com,https://dns.google/dns-query"
    assert len(Config.from_env(env).upstreams) == 2
    env["DNS_BOOTSTRAP"] = "none"
    with pytest.raises(ConfigError, match="BOOTSTRAP"):
        Config.from_env(env)


def test_password_required(env):
    env["DNS_ADMIN_PASSWORD"] = ""
    with pytest.raises(ConfigError, match="PASSWORD"):
        Config.from_env(env)


@pytest.mark.parametrize("via", ["router.lan", "yes"])
def test_canary_via_must_be_an_address(env, via):
    env["DNS_CANARY_VIA"] = via
    with pytest.raises(ConfigError, match="CANARY_VIA"):
        Config.from_env(env)


def test_selftest_host_follows_bind(env):
    assert Config.from_env(env).selftest_host == "127.0.0.1"
    env["DNS_BIND"] = "::"
    assert Config.from_env(env).selftest_host == "::1"


def test_blank_values_mean_the_defaults(env):
    # What Compose passes for keys the env file leaves unset.
    for key in ("DNS_ALLOW_CLIENTS", "DNS_UPSTREAMS", "DNS_FALLBACK", "DNS_BIND",
                "DNS_CANARY_VIA", "DNS_RETENTION_DAYS", "DNS_ALLOW_PRIVATE_UPSTREAM"):
        env[key] = ""
    cfg = Config.from_env(env)
    assert "192.168.0.0/16" in cfg.allow_clients
    assert cfg.fallback == ["1.1.1.1", "8.8.8.8"]


def test_no_fallback_on_purpose(env):
    env["DNS_FALLBACK"] = "none"
    assert Config.from_env(env).fallback == []


def test_empty_client_list_is_refused(env):
    env["DNS_ALLOW_CLIENTS"] = "none"
    with pytest.raises(ConfigError, match="anyone"):
        Config.from_env(env)


@pytest.mark.parametrize(
    "address,url",
    [
        ("127.0.0.1:3053", "http://127.0.0.1:3053"),
        ("0.0.0.0:3053", "http://127.0.0.1:3053"),
        ("[::]:3053", "http://[::1]:3053"),
        ("192.168.1.10:3053", "http://192.168.1.10:3053"),
    ],
)
def test_admin_url_never_connects_to_a_wildcard(env, address, url):
    env["DNS_ADMIN_ADDRESS"] = address
    assert Config.from_env(env).admin_url == url
