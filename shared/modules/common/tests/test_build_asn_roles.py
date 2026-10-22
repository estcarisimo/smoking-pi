"""shared/scripts/build-asn-roles.py: Linnaeus roles -> one kind per AS.
The fixture rows are written here, not copied from the release."""

import importlib.util
from pathlib import Path

from common import asn

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "build-asn-roles.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_asn_roles", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURE = """asn,as_name,top_level_category,sub_category
64500,Example Cable,Access,LargeISP
64500,Example Cable,Transit,Domestic
64500,Example Cable,ContentProvider,CDN
64501,Example Edge,ContentProvider,CDN
64501,Example Edge,ContentProvider,Cloud
64502,Example Host,ContentProvider,Hosting
64503,"Example Carrier, Inc.",Transit,Global
64504,Example Registry,DNS,ccTLD
64505,Example University,EducationalResearch,University
64505,Example University,Government,National
64506,Example Bank,Finance,Bank
64507,Example Mobile,Mobile,
64508,Example Content,ContentProvider,
"""


def test_each_role_maps_to_one_of_our_kinds(tmp_path):
    build = _load()
    labels = tmp_path / "hierarchical.csv"
    labels.write_text(FIXTURE)
    out = tmp_path / "asn_roles.csv"
    assert build.main([str(labels), "--release", "test", "--out", str(out)]) == 0
    table = asn.roles.__wrapped__(out)
    assert table == {
        64500: ("Example Cable", asn.ACCESS),     # access wins over CDN and transit
        64501: ("Example Edge", asn.CDN),         # CDN wins over cloud
        64502: ("Example Host", asn.CLOUD),
        64503: ("Example Carrier, Inc.", asn.TRANSIT),
        64504: ("Example Registry", asn.DNS_INFRA),
        64505: ("Example University", asn.ACADEMIC),
        64507: ("Example Mobile", asn.ACCESS),
        64508: ("Example Content", asn.CONTENT),
    }  # 64506, a bank, is an owner, not a delivery role: left out
    assert out.read_text().startswith("# AS delivery roles")
    assert "MIT License" in out.read_text()


def test_check_reports_a_stale_file(tmp_path):
    build = _load()
    labels = tmp_path / "hierarchical.csv"
    labels.write_text(FIXTURE)
    out = tmp_path / "asn_roles.csv"
    out.write_text("old\n")
    assert build.main([str(labels), "--release", "test", "--out", str(out), "--check"]) == 1
    build.main([str(labels), "--release", "test", "--out", str(out)])
    assert build.main([str(labels), "--release", "test", "--out", str(out), "--check"]) == 0


def test_every_kind_the_script_writes_is_one_classify_knows():
    build = _load()
    assert {kind for _, _, kind in build.PRECEDENCE} <= set(asn.KINDS)
