"""docs/fleet.md's protocol reference is the one protocol.py generates."""

import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("write_reference", MODULE / "write_reference.py")
write_reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(write_reference)


def test_the_documented_protocol_is_the_coded_one():
    current = write_reference.DOC.read_text()
    assert current == write_reference.render(current), (
        "docs/fleet.md is stale: run python shared/modules/fleet/write_reference.py")


def test_every_message_and_error_code_is_in_the_reference():
    from fleet import protocol as p

    text = p.reference_markdown()
    for cls in p.REFERENCE:
        assert f"#### {cls.__name__}" in text
    for code in p.ERROR_STATUS:
        assert f"| `{code}` |" in text
