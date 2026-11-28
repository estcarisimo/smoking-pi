"""Leaving: the lead is told when it can be, credentials go, nothing else."""

import io
import json
from datetime import timedelta

import pytest
from fakelead import FLEET, LEAD, LEAD2, NODE, FakeLead, enrolled, error

from fleet import layout, leave, statefile


@pytest.fixture
def paths(tmp_path):
    return layout.node_paths(tmp_path / "state")


@pytest.fixture(autouse=True)
def node_env(monkeypatch):
    monkeypatch.setenv("FLEET_NODE_ID", NODE)


def run_leave(paths, lead, *args):
    out, err = io.StringIO(), io.StringIO()
    code = leave.main(list(args), stdout=out, stderr=err, transport=lead, paths=paths)
    return code, json.loads(out.getvalue()), err.getvalue()


def node_files(paths):
    d = paths.enrollment.parent
    return sorted(f.name for f in d.iterdir()) if d.is_dir() else []


def test_leave_tells_the_lead_and_removes_only_the_enrollment(paths):
    lead = enrolled(paths)
    # What else lives in the agent's volume (and, standing in for them, the
    # node's measurements) is not the enrollment's to remove.
    paths.status.write_text("{}")
    measurements = paths.root / "elsewhere.rrd"
    measurements.write_text("data")
    code, result, err = run_leave(paths, lead)
    assert code == 0 and result == {"left": True, "told_lead": True}
    (call,) = lead.to("/fleet/leave")
    assert call.tls.chain == paths.chain or call.tls.chain.name == "chain.pem"
    assert call.body["node_id"] == NODE and call.body["drained"] is True
    assert node_files(paths) == []
    assert paths.status.exists() and measurements.read_text() == "data"
    assert f"Left fleet {FLEET}" in err


def test_no_lead_answering_keeps_everything_unless_forced(paths):
    lead = enrolled(paths, None, LEAD, LEAD2)
    lead.down.update({LEAD, LEAD2})
    before = node_files(paths)
    code, result, err = run_leave(paths, lead)
    assert code == 1 and result["left"] is False and "--force" in err
    assert node_files(paths) == before
    assert [c.url for c in lead.calls] == [LEAD + "/fleet/leave", LEAD2 + "/fleet/leave"]
    code, result, err = run_leave(paths, lead, "--force")
    assert code == 0 and result == {"left": True, "told_lead": False}
    assert node_files(paths) == [] and "keeps listing" in err


def test_a_lead_that_refuses_is_not_told_either(paths):
    lead = enrolled(paths)
    status_code, body = error("unauthenticated")
    lead.answer("POST", LEAD + "/fleet/leave", status_code, body)
    code, result, err = run_leave(paths, lead)
    assert code == 1 and "unauthenticated" in err and node_files(paths)


def test_a_revoked_node_leaves_without_calling(paths):
    lead = enrolled(paths)
    e = statefile.read_object(paths.enrollment)
    statefile.write_object(paths.enrollment, dict(e, revoked={"at": "x", "reason": "operator"}))
    code, result, _ = run_leave(paths, lead)
    assert code == 0 and result["told_lead"] is False
    assert lead.calls == [] and node_files(paths) == []


@pytest.mark.parametrize("breakage", ["corrupt", "mismatch", "expired"])
def test_states_that_cannot_speak_to_the_lead_need_force(paths, breakage, monkeypatch):
    lead = enrolled(paths)
    if breakage == "corrupt":
        paths.chain.write_text("garbage")
    elif breakage == "mismatch":
        monkeypatch.setenv("FLEET_NODE_ID", "11111111-2222-4333-8444-555555555555")
    else:
        from fakelead import now
        real = leave.datetime

        class Later(real):
            @classmethod
            def now(cls, tz=None):
                return now() + timedelta(days=91)
        monkeypatch.setattr(leave, "datetime", Later)
    code, result, err = run_leave(paths, lead)
    assert code == 1 and "--force" in err and node_files(paths)
    code, result, err = run_leave(paths, lead, "--force")
    assert code == 0 and node_files(paths) == [] and lead.calls == []


def test_nothing_to_leave(paths):
    code, result, err = run_leave(paths, FakeLead())
    assert code == 0 and "not in a fleet" in err
