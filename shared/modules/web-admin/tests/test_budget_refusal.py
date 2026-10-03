"""config-manager's 409 over_budget reaches a person as the page's own
sentence, built from the numbers only (never the answer's text)."""

from conftest import login

from app.services import ai_tools
from app.services.config_api import over_budget, over_budget_message

REFUSAL = {
    "error": "Over the measurement budget", "reason": "over_budget",
    "message": "<script>config-manager's own words are never shown</script>",
    "requested": {"samples_per_hour": 120.0, "mb_per_day": 0.48},
    "now": {"samples_per_hour": 1000.0, "mb_per_day": 999.8},
    "after": {"samples_per_hour": 1120.0, "mb_per_day": 1000.28,
              "samples_pct": 5.6, "bandwidth_pct": 100.0},
    "ceiling": {"samples_per_hour": 20000.0, "mb_per_day": 1000.0},
}


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = "x"

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _refused():
    return over_budget(FakeResponse(409, REFUSAL))


def test_over_budget_keeps_numbers_only():
    refused = _refused()
    assert refused["refused"] == "over_budget" and refused["success"] is False
    assert refused["numbers"]["after_mb_per_day"] == 1000.28
    assert refused["numbers"]["ceiling_mb_per_day"] == 1000.0
    assert all(isinstance(v, float) for v in refused["numbers"].values())
    # Not a refusal: another status, another reason, not JSON, a bool.
    assert over_budget(FakeResponse(400, REFUSAL)) is None
    assert over_budget(FakeResponse(409, {"reason": "other"})) is None
    assert over_budget(FakeResponse(409, ValueError("html"))) is None
    flagged = over_budget(FakeResponse(409, {**REFUSAL, "after": {"mb_per_day": True}}))
    assert "after_mb_per_day" not in flagged["numbers"]


def test_the_message_is_the_pages_own():
    text = over_budget_message(_refused()["numbers"])
    assert text.startswith("Not saved: over the measurement budget. It adds 0.5 MB/day")
    assert "1000 of 1000 MB/day" in text
    assert "MEASUREMENT_BUDGET_MB_PER_DAY" in text
    assert "script" not in text and "samples/hour (" not in text


def _db(monkeypatch):
    from app.routes import targets as targets_module
    monkeypatch.setattr(targets_module.config_api, "is_database_available", lambda: True)
    return targets_module


def test_toggling_on_over_budget_is_a_409_with_the_sentence(client, monkeypatch):
    targets_module = _db(monkeypatch)
    monkeypatch.setattr(targets_module.config_api, "toggle_target_in_db",
                        lambda target_id: _refused())
    login(client)
    r = client.post("/targets/7/toggle")
    assert r.status_code == 409
    assert r.get_json()["error"].startswith("Not saved: over the measurement budget.")


def test_adding_over_budget_is_a_form_error(client, monkeypatch):
    targets_module = _db(monkeypatch)
    monkeypatch.setattr(targets_module.config_api, "get_categories_from_db",
                        lambda: {"categories": [{"name": "custom", "id": 9}]})
    monkeypatch.setattr(targets_module.config_api, "get_probes_from_db",
                        lambda: {"probes": [{"name": "FPing", "id": 1}]})
    monkeypatch.setattr(targets_module.config_api, "get_all_targets_from_db",
                        lambda: {"targets": []})
    monkeypatch.setattr(targets_module.config_api, "create_target_in_db",
                        lambda data: _refused())
    login(client)
    r = client.post("/targets/add",
                    data={"name": "Example", "hostname": "www.example.com",
                          "target_type": "ping"},
                    headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 400
    assert r.get_json()["errors"]["_form"].startswith("Not saved: over the measurement budget.")


def test_the_assistant_relays_the_sentence(monkeypatch):
    monkeypatch.setattr(ai_tools.gateway, "get_categories_from_db",
                        lambda: {"categories": [{"name": "custom", "id": 9}]})
    monkeypatch.setattr(ai_tools.gateway, "get_probes_from_db",
                        lambda: {"probes": [{"name": "FPing", "id": 1, "is_default": True}]})
    monkeypatch.setattr(ai_tools.gateway, "create_target_in_db", lambda data: _refused())
    out = ai_tools.execute_tool("add_target", {"name": "Example", "host": "www.example.com"})
    assert out["error"].startswith("Not saved: over the measurement budget.")
