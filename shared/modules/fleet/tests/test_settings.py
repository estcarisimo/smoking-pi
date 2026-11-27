"""Fleet settings: defaults, validation, and reasons that never echo a value."""

from fleet import settings

NODE = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"


def test_defaults_with_an_id_only():
    cfg = settings.load({"FLEET_NODE_ID": NODE})
    assert cfg.has_identity
    assert cfg.node_name == "node-6f1c2a3b"
    assert cfg.site_name == "default"
    assert cfg.lead_urls == ()
    assert cfg.lead_mode == "node"
    assert cfg.problems == ()


def test_everything_set():
    cfg = settings.load({
        "FLEET_NODE_ID": NODE.upper(), "FLEET_NODE_NAME": "Kitchen Pi",
        "FLEET_SITE_NAME": "Home (Chicago)", "FLEET_LEAD_MODE": "dedicated",
        "FLEET_LEAD_URLS": " https://a.example:8443 , https://b.example,https://a.example:8443,",
    })
    assert cfg.node_id == NODE
    assert cfg.node_name == "Kitchen Pi"
    assert cfg.site_name == "Home (Chicago)"
    assert cfg.lead_urls == ("https://a.example:8443", "https://b.example")
    assert cfg.lead_mode == "dedicated"
    assert cfg.problems == ()


def test_no_id_is_a_problem_not_a_crash():
    cfg = settings.load({})
    assert not cfg.has_identity
    assert cfg.node_name == "node"
    assert any(p.startswith("FLEET_NODE_ID") for p in cfg.problems)


def test_bad_values_fall_back_and_are_named_never_echoed():
    secret_looking = "https://user:hunter2@lead.example"
    cfg = settings.load({
        "FLEET_NODE_ID": "1234", "FLEET_NODE_NAME": "x" * 65, "FLEET_SITE_NAME": "a\nb",
        "FLEET_LEAD_URLS": f"{secret_looking},https://ok.example", "FLEET_LEAD_MODE": "elected",
    })
    assert not cfg.has_identity
    assert cfg.lead_urls == ("https://ok.example",)
    assert cfg.lead_mode == "node"
    assert cfg.site_name == "default"
    names = {p.split(" ")[0].rstrip(":") for p in cfg.problems}
    assert names == {"FLEET_NODE_ID", "FLEET_NODE_NAME", "FLEET_SITE_NAME",
                     "FLEET_LEAD_URLS", "FLEET_LEAD_MODE"}
    assert "hunter2" not in " ".join(cfg.problems)


def test_elected_is_not_a_v1_mode():
    # The protocol carries it already (LeadEndpoints.lead_mode is open);
    # the setting offers only what this release implements.
    assert settings.LEAD_MODES == ("dedicated", "node")


def test_only_version_4_ids():
    assert settings.valid_node_id(NODE)
    assert not settings.valid_node_id("6f1c2a3b-4d5e-1f60-8a7b-9c0d1e2f3a4b")
    assert not settings.valid_node_id("")
