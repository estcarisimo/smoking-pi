"""The diagram cannot drift from the Compose files, the exporters the SmokePing
container starts, or its own committed drawings."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("architecture", HERE.parent / "architecture.py")
arch = importlib.util.module_from_spec(spec)
sys.modules["architecture"] = arch  # dataclasses look their module up
spec.loader.exec_module(arch)
ROOT = arch.ROOT


class ComposeLoader(yaml.SafeLoader):
    """Compose's !override / !reset tags, read as plain values."""


ComposeLoader.add_multi_constructor(
    "!", lambda loader, suffix, node: (
        loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
        else loader.construct_mapping(node) if isinstance(node, yaml.MappingNode)
        else loader.construct_scalar(node)))


def compose_services() -> dict[str, set[str]]:
    """Every service of every edition's base Compose file -> its profiles."""
    out: dict[str, set[str]] = {}
    for path in sorted(ROOT.glob("editions/*/docker-compose.yml")):
        data = yaml.load(path.read_text(), Loader=ComposeLoader) or {}
        for name, svc in (data.get("services") or {}).items():
            out.setdefault(name, set()).update((svc or {}).get("profiles") or [])
    return out


def drawn() -> dict[str, arch.Node]:
    return {s: n for n in arch.NODES for s in n.services}


def test_every_compose_service_is_drawn():
    missing = sorted(set(compose_services()) - set(drawn()))
    assert not missing, f"add these services to NODES in architecture.py: {missing}"


def test_nothing_drawn_that_compose_does_not_run():
    assert set(drawn()) <= set(compose_services())


def test_profiles_match_compose():
    for name, profiles in compose_services().items():
        n = drawn()[name]
        expected = next(iter(profiles)) if profiles else None
        assert n.profile == expected, f"{name}: Compose profile {profiles or 'none'}"
        if expected is None:
            assert n.kind != "optional", f"{name} is always on"
        elif n.kind != "store":
            assert n.kind == "optional", f"{name} is behind the {expected} profile"


def test_every_exporter_the_container_starts_is_listed():
    script = (ROOT / "editions/pro/custom-cont-init.d/99-run-exporter.sh").read_text()
    started = set(re.findall(r"/exporters/(\w+)\.py", script))
    listed = set(arch.node("smokeping").exporters)
    assert started == listed, f"started {sorted(started)}, listed {sorted(listed)}"
    text = "\n".join(arch.node("smokeping").lines)
    assert all(x in text for x in listed)


def test_edges_join_drawn_boxes():
    ids = {n.id for n in arch.NODES}
    for e in arch.EDGES:
        assert e.src in ids and e.dst in ids, e


def test_excalidraw_references_resolve():
    els = arch.excalidraw()["elements"]
    ids = {e["id"] for e in els}
    assert len(ids) == len(els), "duplicate ids"
    for e in els:
        if e["type"] == "text" and e["containerId"]:
            assert e["containerId"] in ids
        for b in e["boundElements"] or []:
            assert b["id"] in ids
        for key in ("startBinding", "endBinding"):
            if e.get(key):
                assert e[key]["elementId"] in ids


def test_committed_drawings_are_current():
    for path, text in arch.outputs().items():
        assert path.read_text() == text, (
            f"{path.relative_to(ROOT)} is stale: run python tools/architecture/architecture.py")
    json.loads(arch.EXCALIDRAW_OUT.read_text())
