"""The web assistant's list_targets says what each target is.

The service label (docs/taxonomy.md) is config-manager's; the tool passes
it through and filters on it, as the MCP server's list_targets does.
"""

from app.services import ai_tools

ROWS = [
    {"id": 1, "name": "Netflix_1_DFW", "host": "23.246.2.205", "title": "Netflix",
     "category": "netflix_oca", "probe": "FPing", "is_active": True,
     "service": "Netflix", "categories": ["video"], "label_source": "manual"},
    {"id": 2, "name": "Old", "host": "old.example", "title": "Old",
     "category": "custom", "probe": "FPing", "is_active": True},
]


def test_list_targets_passes_the_label_through(monkeypatch):
    monkeypatch.setattr(ai_tools, "_fetch_targets", lambda: ROWS)
    result = ai_tools._list_targets({})
    assert result["total"] == 2
    first, second = result["targets"]
    assert (first["service"], first["categories"]) == ("Netflix", ["video"])
    assert "label_source" not in first
    assert "service" not in second


def test_list_targets_filters_by_service_category(monkeypatch):
    monkeypatch.setattr(ai_tools, "_fetch_targets", lambda: ROWS)
    result = ai_tools._list_targets({"service_category": "Video"})
    assert [t["name"] for t in result["targets"]] == ["Netflix_1_DFW"]
    assert result["service_category"] == "video"
    assert ai_tools._list_targets({"service_category": "gaming"})["total"] == 0


def test_the_tool_schema_offers_the_filter():
    schema = next(t for t in ai_tools.TOOLS if t["name"] == "list_targets")
    assert "service_category" in schema["input_schema"]["properties"]
