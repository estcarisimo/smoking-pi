"""The staging injection test (inject.py): the evening it generates, the
plan it makes, the checks it reads back and what it cleans. Every query,
write and delete is a fake."""

import json

import pandas as pd
import pytest

import bins
import inject
import segments
import shared

T0 = 1_790_000_100 // 900 * 900
END = T0 + 3 * 3600
PREFIX = ["192.168.86.1", "136.25.220.1", "10.248.2.13", "10.248.0.77", "216.21.171.16"]

# Six destinations through AS6939 run by four operators, six straight on run
# by six others: ten operators, four of them behind the planted network.
OPS = {
    "L0": ("A", "cdn"), "L1": ("A", "cdn"), "L2": ("B", "content_network"),
    "L3": ("C", "cdn"), "L4": ("D", "embedded_cache"), "L5": ("D", "embedded_cache"),
    "C0": ("E", "cdn"), "C1": ("F", "content_network"), "C2": ("G", "other"),
    "C3": ("H", "cloud"), "C4": ("I", "cdn"), "C5": ("J", "other"),
}
VOTERS = sorted(OPS)


def of(target):
    return OPS.get(target, ("unknown", "unknown"))


def _row(target, hops, asns, at=T0 - 600):
    path = "|".join(hops)
    return {"_time": pd.Timestamp(at, unit="s", tz="UTC"), "target": target, "path": path,
            "as_path": " ".join(str(a) for a in asns), "reached": True, "path_hash": path}


def _traces(via_he=("L0", "L1", "L2", "L3", "L4", "L5")):
    rows = []
    for i, t in enumerate(VOTERS):
        if t in via_he:
            rows.append(_row(t, PREFIX + ["*", "184.105.81.237", f"184.105.82.{i}"],
                             [19165, 16591, 6939, 64500 + i]))
        else:
            rows.append(_row(t, PREFIX + ["*", "23.255.224.64", f"23.255.225.{i}"],
                             [19165, 16591, 64600 + i]))
    return segments.paths(rows, [], T0, END)


# --- the evening -------------------------------------------------------------


def test_the_evening_ends_two_hours_before_the_last_pass_by_default():
    through = T0 + 10 * 3600 + 123
    start, end = inject.placement(through, 3)
    assert end == (through // 900 * 900) - 2 * 3600 and start == end - 3 * 3600


def test_an_evening_in_the_tail_the_next_pass_rereads_is_refused():
    through = T0 + 10 * 3600
    with pytest.raises(inject.PlanError, match="75 minutes"):
        inject.placement(through, 3, start=through - 3 * 3600)
    with pytest.raises(inject.PlanError, match="at least 1"):
        inject.placement(through, 0)


def test_levels_lead_with_the_operator_with_most_targets_and_reach_the_pooled_loss():
    lv = inject.levels(["L0", "L1", "L2", "L3"], of, n_voters=12)
    assert lv["L0"] == lv["L1"] > lv["L2"] > lv["L3"]
    assert sum(lv.values()) / 12 == pytest.approx(inject.POOLED_PCT / 100)
    # Few affected among many voters: scaled, never past MAX_LEVEL.
    assert max(inject.levels(["L0"], of, n_voters=100).values()) == inject.MAX_LEVEL


def test_the_evening_is_the_same_for_the_same_seed_and_stays_near_each_level():
    starts = list(range(T0, END, 900))
    a = inject.evening(starts, {"L0": 0.1, "L2": 0.05}, seed=3)
    assert a.equals(inject.evening(starts, {"L0": 0.1, "L2": 0.05}, seed=3))
    assert len(a) == 2 * len(starts)
    for t, level in (("L0", 0.1), ("L2", 0.05)):
        v = a[a["target"] == t]["loss"]
        assert v.between(level * (1 - inject.JITTER), level * (1 + inject.JITTER)).all()


def test_a_maintainers_csv_takes_epochs_or_iso_times_and_moves_to_the_window(tmp_path):
    path = tmp_path / "evening.csv"
    path.write_text("bin,target,loss\n2026-10-02T01:00:00Z,L0,0.08\n1790000100,L1,0.02\n"
                    .replace("1790000100", str(T0)))
    rows = inject.read_csv(str(path))
    assert set(rows["target"]) == {"L0", "L1"} and rows["loss"].max() == 0.08
    moved = inject.shift(rows, END)
    assert moved["bin"].min() == END
    assert (moved["bin"].max() - moved["bin"].min()) == (rows["bin"].max() - rows["bin"].min())


@pytest.mark.parametrize("body, why", [
    ("bin,target,loss\n1790000101,L0,0.1\n", "15-minute"),
    ("bin,target,loss\n1790000100,L0,8.0\n", "ratio"),
    ("bin,target\n1790000100,L0\n", "missing"),
])
def test_a_csv_that_is_not_bins_of_ratios_is_refused(tmp_path, body, why):
    path = tmp_path / "bad.csv"
    path.write_text(body.replace("1790000100", str(T0)).replace("1790000101", str(T0 + 1)))
    with pytest.raises(inject.PlanError, match=why):
        inject.read_csv(str(path))


def _cache(targets=("L0", "L1", "C0", "CPE"), n_bins=24):
    rows = []
    for b in range(T0 - 8 * 900, T0 - 8 * 900 + n_bins * 900, 900):
        for t in targets:
            rows.append({"bin": b, "target": t, "category": "cpe" if t == "CPE" else "topsites",
                         "loss": 0.01 if t == "C0" else 0.0, "count": 3.0})
    return pd.DataFrame(rows, columns=shared.BIN_COLUMNS)


def test_apply_writes_the_evening_clears_the_rest_and_keeps_the_counts():
    cache = _cache()
    span = (T0, T0 + 4 * 900)
    rows = pd.DataFrame([{"bin": T0, "target": "L0", "loss": 0.2},
                         {"bin": T0 + 900, "target": "NEW", "loss": 0.3}])
    out = inject.apply(cache, rows, span, clear=["C0", "CPE", "L0"])
    get = out.set_index(["bin", "target"])
    assert get.loc[(T0, "L0"), "loss"] == 0.2
    assert get.loc[(T0, "C0"), "loss"] == 0.0           # cleared in the span
    assert get.loc[(T0 - 900, "C0"), "loss"] == 0.01    # not before it
    assert get.loc[(T0 + 900, "NEW"), "count"] == 3.0   # a bin it never reported in
    assert len(out) == len(cache) + 1 and list(out.columns) == shared.BIN_COLUMNS
    assert (out["count"] == 3.0).all()


# --- the plan ---------------------------------------------------------------


def test_expectations_follow_the_qualification_rule():
    pct = {t: (10.0 if t.startswith("L") else 0.0) for t in VOTERS}
    e = inject.expectations(pct, VOTERS, of)
    assert e["scope"] == "shared" and e["lossy_operators"] == ["A", "B", "C", "D"]
    assert e["kinds"] == ["cdn", "content_network", "embedded_cache"]
    assert e["operators"] == 10 and e["pooled_pct"] == pytest.approx(5.0)
    # One operator's evening is dominated, whatever its loss.
    one = inject.expectations({"L0": 50.0, "L1": 50.0}, VOTERS, of)
    assert one["scope"] == "dominated" and one["top_operator"] == "A"


def test_an_untagged_target_is_no_operator_in_the_expectation():
    e = inject.expectations({"X": 40.0, "L0": 2.0}, VOTERS + ["X"], of)
    assert "unknown" not in e["lossy_operators"] and e["top_operator"] == "A"


def test_the_plan_plants_the_network_the_destinations_that_lose_cross():
    option = inject.choose(VOTERS, of, _traces(), (T0, END))
    assert option["planted"] == "AS6939"
    assert option["affected"] == ["L0", "L1", "L2", "L3", "L4", "L5"]
    assert option["expect"]["scope"] == "shared" and option["expect"]["top_operator"] == "A"
    h = inject.segment_expectation(option["hypothesis"])
    assert h["confidence"] == "strong" and h["segment"] == "AS6939"


def test_with_no_segment_that_qualifies_every_voter_loses():
    option = inject.choose(VOTERS, of, _traces(via_he=()), (T0, END))
    assert option["planted"] is None and option["affected"] == VOTERS
    assert option["expect"]["scope"] == "shared"
    assert option["hypothesis"]["confidence"] == "none"


def test_a_router_is_planted_only_when_allowed():
    # Through the router 184.105.81.237 but with no AS path: no network to plant.
    rows = []
    for i, t in enumerate(VOTERS):
        hops = PREFIX + ["*", "184.105.81.237" if t.startswith("L") else "23.255.224.64",
                         f"203.0.113.{i}"]
        rows.append(_row(t, hops, []))
    traces = segments.paths(rows, [], T0, END)
    assert inject.choose(VOTERS, of, traces, (T0, END))["planted"] is None
    allowed = inject.choose(VOTERS, of, traces, (T0, END), allow_router=True)
    assert allowed["planted"] == "router 184.105.81.237"


def test_no_traces_expects_no_segment():
    option = inject.choose(VOTERS, of, {}, (T0, END))
    assert option["hypothesis"] is None and inject.segment_expectation(None) is None


# --- reading the outputs back -------------------------------------------------


PLAN = {"span": [T0, END],
        "expect": {"scope": "shared", "top_operator": "A",
                   "kinds": ["cdn", "content_network", "embedded_cache"], "pooled_pct": 5.0},
        "segment": {"confidence": "strong", "segment": "AS6939", "traced": 12,
                    "affected": 6, "unaffected": 6}}


def _period(**over):
    p = {"start": T0 + 900, "end": END, "scope": "shared",
         "by_operator": "A 12.1 %, B 10.0 %, C 8.0 %",
         "kinds": "cdn,content_network,embedded_cache", "mean_loss_pct": 4.6}
    p.update(over)
    return p


def test_periods_reads_pivoted_points():
    rows = [{"result": "_result", "table": 0, "_time": pd.Timestamp(T0, unit="s", tz="UTC"),
             "_measurement": "shared_degradation", "end": END, "scope": "shared"}]
    assert inject.periods(rows) == [{"start": T0, "end": END, "scope": "shared"}]


def test_a_written_period_that_matches_the_plan_passes_every_check():
    checks, p = inject.check_period([_period()], PLAN)
    assert all(c["ok"] for c in checks), checks
    assert p["start"] == T0 + 900


@pytest.mark.parametrize("over, failing", [
    ({"by_operator": "B 12.0 %, A 10.0 %"}, "operator that lost most (by_operator)"),
    ({"scope": "dominated"}, "scope"),
    ({"kinds": "cdn"}, "kinds that lost"),
    ({"start": T0 - 3600}, "starts with the evening"),
    ({"mean_loss_pct": 1.0}, "pooled loss within half to one and a half the planned"),
])
def test_each_difference_from_the_plan_fails_its_check(over, failing):
    checks, _ = inject.check_period([_period(**over)], PLAN)
    assert [c["check"] for c in checks if not c["ok"]] == [failing]


def test_no_period_fails_and_says_so():
    checks, p = inject.check_period([_period(start=END + 3600, end=END + 7200)], PLAN)
    assert p is None and checks[0]["ok"] is False and checks[0]["saw"] == 0


def test_the_hypothesis_is_read_at_the_periods_start():
    h = {"start": T0 + 900, "end": END, "confidence": "strong", "segment": "AS6939",
         "traced": 12}
    assert all(c["ok"] for c in inject.check_segment([h], _period(), PLAN))
    wrong = inject.check_segment([dict(h, segment="AS16591")], _period(), PLAN)
    assert [c["check"] for c in wrong if not c["ok"]] == ["segment"]
    assert not inject.check_segment([dict(h, start=T0)], _period(), PLAN)[0]["ok"]
    assert not inject.check_segment([h], None, PLAN)[0]["ok"]


def test_with_no_destination_traced_no_hypothesis_is_expected():
    plan = dict(PLAN, segment=None)
    assert inject.check_segment([], _period(), plan)[0]["ok"]
    assert not inject.check_segment([{"start": T0}], _period(), plan)[0]["ok"]


def test_episode_traces_after_the_cycle_pass():
    plan = dict(PLAN, widespread={"at": T0, "seen": {"targets_affected": 20,
                                                     "targets_total": 20, "reading": None}})
    rows = [{"target": "Google", "episode": "2026-11-10T01:00:00+00:00"},
            {"target": "Apple", "episode": "2026-11-10T01:00:00+00:00"}]
    checks = inject.check_episode(rows, plan)
    assert all(c["ok"] for c in checks), checks
    assert "2 destinations" in checks[1]["saw"]


def test_no_trace_no_cycle_or_an_uplink_reading_fails():
    assert not inject.check_episode([], PLAN)[0]["ok"]
    plan = dict(PLAN, widespread={"at": T0, "seen": None})
    assert [c["ok"] for c in inject.check_episode([], plan)] == [False, False, False]
    down = dict(PLAN, widespread={"at": T0, "seen": {"reading": "uplink_down"}})
    assert not inject.check_episode([{"target": "G", "episode": "x"}], down)[0]["ok"]


def test_leader_is_the_first_name_of_the_listing():
    assert inject.leader("Fastly 9.0 %, Google 6.0 %") == "Fastly"
    assert inject.leader("Amazon CloudFront 12.5 %") == "Amazon CloudFront"
    assert inject.leader("") == "" and inject.leader(None) == ""


# --- the steps on the state volume and InfluxDB ---------------------------------


class FakeDelete:
    def __init__(self):
        self.calls = []

    def delete(self, start, stop, predicate, bucket, org):
        self.calls.append((start, stop, predicate))


class FakeClient:
    def __init__(self):
        self.api = FakeDelete()

    def delete_api(self):
        return self.api

    def close(self):
        pass


def test_inject_copies_the_cache_aside_and_cleanup_restores_it_and_deletes_the_points():
    state = bins.state_dir()
    cache = _cache()
    bins._save("loss", cache, {"from": T0 - 8 * 900, "through": T0 + 9000, "full_at": 1},
               state)
    original = (state / "loss.csv").read_bytes(), (state / "loss.json").read_bytes()
    plan = {"span": [T0, T0 + 4 * 900],
            "_rows": [{"bin": T0, "target": "L0", "loss": 0.25}], "_clear": ["C0"]}
    inject.inject(plan, now=T0 + 9100)
    table, meta = bins._load("loss", shared.BIN_COLUMNS, state, ("target", "category"))
    assert meta["full_at"] == T0 + 9100 and meta["through"] == T0 + 9000
    assert table.set_index(["bin", "target"]).loc[(T0, "L0"), "loss"] == 0.25
    with pytest.raises(inject.PlanError, match="already under way"):
        inject.inject({"span": [T0, T0 + 900], "_rows": [], "_clear": []}, now=T0 + 9200)

    manifest = json.loads((inject.workdir() / "manifest.json").read_text())
    manifest["widespread"] = {"at": T0 + 9150, "measurements": ["dns_latency", "latency"]}
    client = FakeClient()
    report = inject.cleanup(manifest, now=T0 + 9900, client=client, query=lambda flux: [])
    assert report["cache"] == "restored"
    assert ((state / "loss.csv").read_bytes(), (state / "loss.json").read_bytes()) == original
    predicates = [c[2] for c in client.api.calls]
    assert predicates == [
        '_measurement="dns_latency"', '_measurement="latency"',
        '_measurement="path_trace" AND trigger="episode"',
        '_measurement="path_hop" AND trigger="episode"',
        '_measurement="pathmeter_pass" AND trigger="episode"',
        '_measurement="shared_degradation"', '_measurement="shared_segment"']
    # The injected cycle: its one second only.
    assert client.api.calls[0][:2] == (inject._iso(T0 + 9150), inject._iso(T0 + 9151))
    assert report["left"] == {"shared_degradation": 0, "shared_segment": 0,
                              "episode traces": 0}
    assert not (inject.workdir() / "manifest.json").exists()


def test_kept_points_lose_the_injected_cycle_only_until_a_full_cleanup():
    state = bins.state_dir()
    bins._save("loss", _cache(), {"from": T0, "through": T0 + 9000, "full_at": 1}, state)
    original = (state / "loss.csv").read_bytes()
    inject.inject({"span": [T0, T0 + 900], "_rows": [{"bin": T0, "target": "L0", "loss": 0.3}],
                   "_clear": []}, now=T0 + 9100)
    manifest = json.loads((inject.workdir() / "manifest.json").read_text())
    manifest["widespread"] = {"at": T0 + 9150, "measurements": ["latency"]}
    client = FakeClient()
    report = inject.cleanup(manifest, now=T0 + 9200, client=client, keep_points=True)
    assert [c[2] for c in client.api.calls] == ['_measurement="latency"']
    assert report["cache"] == "kept with the evening"
    assert (state / "loss.csv").read_bytes() != original
    kept = json.loads((inject.workdir() / "manifest.json").read_text())
    assert kept["widespread"]["deleted"] == T0 + 9200

    later = FakeClient()
    inject.cleanup(kept, now=T0 + 9900, client=later, query=lambda flux: [])
    predicates = [c[2] for c in later.api.calls]
    assert '_measurement="latency"' not in predicates          # gone already
    assert '_measurement="shared_degradation"' in predicates
    assert (state / "loss.csv").read_bytes() == original


def test_the_widespread_cycle_copies_every_series_but_the_gateway(monkeypatch):
    from common import loss_events

    inject.workdir().mkdir(parents=True)
    rows = [{"result": "_result", "table": 0, "_measurement": "latency", "_field": "loss",
             "_value": 0.0, "target": "Google", "category": "topsites", "host": "g"},
            {"result": "_result", "table": 1, "_measurement": "latency", "_field": "loss",
             "_value": 0.0, "target": "CPE_IPv4", "category": "cpe"},
            {"result": "_result", "table": 2, "_measurement": "dns_latency", "_field": "loss",
             "_value": 0.0, "target": "GoogleDNS", "category": "dns"},
            {"result": "_result", "table": 3, "_measurement": "latency", "_field": "loss",
             "_value": 0.0, "target": "Google", "category": "topsites", "host": "g"}]

    def query(flux):
        return rows if "|> last()" in flux else []

    monkeypatch.setattr(loss_events, "answer", lambda q, hours, cadences=None: {
        "widespread": [{"targets_affected": 2, "targets_total": 2, "reading": None}]})
    written = []
    plan = {"span": [T0, END]}
    ws = inject.widespread(plan, now=T0 + 7200, query=query, write=written.extend)
    lines = sorted(p.to_line_protocol() for p in written)
    assert len(lines) == 2 and all("loss=0.6" in line for line in lines)
    assert not any("cpe" in line for line in lines)
    assert ws["measurements"] == ["dns_latency", "latency"] and ws["at"] % 60 != 0
    assert ws["seen"]["targets_affected"] == 2
    with pytest.raises(inject.PlanError, match="already injected"):
        inject.widespread(plan, now=T0 + 7300, query=query, write=written.extend)


def test_the_widespread_cycle_refuses_a_second_that_holds_points():
    inject.workdir().mkdir(parents=True)
    rows = [{"_measurement": "latency", "_field": "loss", "target": "G", "category": "x"}]
    with pytest.raises(inject.PlanError, match="already exist"):
        inject.widespread({"span": [T0, END]}, now=T0 + 7200, query=lambda flux: rows,
                          write=lambda points: None)


def test_the_command_refuses_without_staging(capsys):
    assert inject.main(["plan"]) == 2
    assert "staging Pi only" in capsys.readouterr().err


# --- the plan, end to end on fakes -------------------------------------------------


class FakeTags:
    def of(self, target, hosts):
        return of(target)


def _plan_cache(now):
    rows = []
    for b in range(now // 900 * 900 - 86400, now // 900 * 900, 900):
        for t in VOTERS:
            rows.append({"bin": b, "target": t, "category": "topsites", "loss": 0.0,
                         "count": 3.0})
    bins._save("loss", pd.DataFrame(rows, columns=shared.BIN_COLUMNS),
               {"from": rows[0]["bin"], "through": now, "full_at": now}, bins.state_dir())


def _args(**over):
    from types import SimpleNamespace

    base = {"hours": 3, "start": None, "csv": None, "seed": 0, "allow_router": False}
    base.update(over)
    return SimpleNamespace(**base)


def _query(upgrade_at=None):
    def query(flux):
        if '"upgrade"' in flux and upgrade_at is not None:
            return [{"_time": pd.Timestamp(upgrade_at, unit="s", tz="UTC"), "phase": "start"},
                    {"_time": pd.Timestamp(upgrade_at + 300, unit="s", tz="UTC"),
                     "phase": "end"}]
        return []
    return query


def test_the_plan_with_no_traces_has_every_voter_lose_and_expects_no_segment():
    now = T0 + 86400
    _plan_cache(now)
    plan = inject.make_plan(_args(), now, query=_query(), tag_store=FakeTags(), deaf=set())
    start, end = plan["span"]
    assert end == now // 900 * 900 - inject.CLEAR_OF_TAIL_S and end - start == 3 * 3600
    assert plan["pattern"] == "every voter" and plan["segment"] is None
    assert plan["voters"] == VOTERS and plan["expect"]["scope"] == "shared"
    assert len(plan["_rows"]) == len(VOTERS) * 12


def test_the_plan_refuses_an_evening_over_an_upgrade():
    now = T0 + 86400
    _plan_cache(now)
    end = now // 900 * 900 - inject.CLEAR_OF_TAIL_S
    with pytest.raises(inject.PlanError, match="monitor's"):
        inject.make_plan(_args(), now, query=_query(upgrade_at=end - 3600),
                         tag_store=FakeTags(), deaf=set())


def test_the_plan_refuses_an_evening_over_a_deaf_radio():
    now = T0 + 86400
    _plan_cache(now)
    end = now // 900 * 900 - inject.CLEAR_OF_TAIL_S
    with pytest.raises(inject.PlanError, match="monitor's"):
        inject.make_plan(_args(), now, query=_query(), tag_store=FakeTags(),
                         deaf={end - 1800})


def test_a_csv_plan_moves_the_evening_and_skips_targets_this_pi_does_not_measure(tmp_path):
    now = T0 + 86400
    _plan_cache(now)
    path = tmp_path / "evening.csv"
    lines = ["bin,target,loss"]
    for b in range(1_780_000_200 // 900 * 900, 1_780_000_200 // 900 * 900 + 4 * 900, 900):
        lines += [f"{b},{t},0.12" for t in ("L0", "L1", "L2", "L4", "C2")]
        lines.append(f"{b},Elsewhere,0.5")
    path.write_text("\n".join(lines) + "\n")
    plan = inject.make_plan(_args(csv=str(path)), now, query=_query(), tag_store=FakeTags(),
                            deaf=set())
    assert plan["pattern"] == "csv" and plan["skipped_targets"] == ["Elsewhere"]
    assert {r["target"] for r in plan["_rows"]} == {"L0", "L1", "L2", "L4", "C2"}
    assert min(r["bin"] for r in plan["_rows"]) == plan["span"][0]
    assert plan["_clear"] == []          # no gateway here; the other voters keep their bins
    assert plan["expect"]["lossy_operators"] == ["A", "B", "D", "G"]
