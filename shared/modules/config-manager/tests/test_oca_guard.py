"""Regression tests: the OCA refresh must never wipe existing targets.

An empty or failed fetch replaced the netflix_oca category with nothing
in production (the fetcher deleted first, inserted after, committing in
between). update_targets now refuses empty input, and the DB replace is
a single transaction.
"""

from unittest.mock import MagicMock, patch

from scripts.oca_fetcher import OCAFetcher


def test_empty_fetch_never_replaces_targets():
    fetcher = OCAFetcher()
    with patch.object(fetcher, "_update_targets_database") as db_update, \
         patch.object(fetcher, "_update_targets_yaml") as yaml_update, \
         patch.object(fetcher, "generate_smokeping_config") as regen:
        assert fetcher.update_targets([]) is False
        db_update.assert_not_called()
        yaml_update.assert_not_called()
        regen.assert_not_called()


def test_none_fetch_never_replaces_targets():
    fetcher = OCAFetcher()
    with patch.object(fetcher, "_update_targets_database") as db_update, \
         patch.object(fetcher, "_update_targets_yaml") as yaml_update:
        assert fetcher.update_targets(None) is False
        db_update.assert_not_called()
        yaml_update.assert_not_called()


def test_add_pending_does_not_commit():
    from models import TargetRepository

    session = MagicMock()
    repo = TargetRepository(session)
    repo.add_pending({"name": "X", "host": "x.example", "category_id": 1,
                      "probe_id": 1})
    session.add.assert_called_once()
    session.commit.assert_not_called()


# --- Stable names (#275) ------------------------------------------------------
# Netflix hands out a different appliance at the same site every day. A name
# with the appliance in it started a new series at every refresh.

def _servers(*specs):
    return {"oca_servers": [
        {"ip_address": ip, "domain": f"ipv4-{cache}-{site}001-ix.1.oca.nflxvideo.net",
         "city": "Ocean Reef Club Airport"}
        for ip, cache, site in specs]}


def test_names_are_slot_and_site_not_the_appliance():
    fetcher = OCAFetcher()
    today = fetcher.parse_oca_data(_servers(("23.246.2.205", "c534", "dfw"),
                                            ("45.57.95.134", "c072", "mci")))
    tomorrow = fetcher.parse_oca_data(_servers(("23.246.3.9", "c569", "dfw"),
                                               ("45.57.95.1", "c091", "mci")))
    assert [t["name"] for t in today] == ["Netflix_1_DFW", "Netflix_2_MCI"]
    assert [t["name"] for t in tomorrow] == [t["name"] for t in today]
    # The appliance is still recorded, where it does not split the series.
    assert today[0]["metadata"]["cache_id"] == "c534"
    assert "c534" in today[0]["title"]
    assert tomorrow[0]["host"] == "23.246.3.9"


def test_no_name_ends_in_a_digit():
    # The exporter reads a name ending in "6" as an IPv6 target (Google6).
    fetcher = OCAFetcher()
    servers = _servers(*[(f"23.246.2.{i}", f"c{i}", "dfw") for i in range(1, 11)])
    names = [t["name"] for t in fetcher.parse_oca_data(servers, max_targets=10)]
    assert len(names) == 10
    assert not [n for n in names if n[-1].isdigit()]


def _refresh_with(existing, new_targets):
    """Run _update_targets_database against mocks; returns (session, repo)."""
    from scripts import oca_fetcher

    session = MagicMock()
    target_repo = MagicMock()
    target_repo.get_all.return_value = existing
    category_repo = MagicMock()
    category_repo.get_by_name.return_value = MagicMock(id=7)
    probe_repo = MagicMock()
    probe_repo.get_by_name.return_value = MagicMock(id=3)
    with patch.object(oca_fetcher, "get_db_session", return_value=session), \
         patch.object(oca_fetcher, "TargetRepository", return_value=target_repo), \
         patch.object(oca_fetcher, "CategoryRepository", return_value=category_repo), \
         patch.object(oca_fetcher, "ProbeRepository", return_value=probe_repo):
        assert OCAFetcher()._update_targets_database(new_targets) is True
    return session, target_repo


def test_refresh_updates_slots_in_place_and_removes_only_what_is_gone():
    fetcher = OCAFetcher()
    kept = MagicMock()
    kept.name = "Netflix_1_DFW"
    kept.host = "23.246.2.205"
    gone = MagicMock()
    gone.name = "Chicago_ORD_c122_1"   # the old naming, removed on the first refresh
    new = fetcher.parse_oca_data(_servers(("23.246.3.9", "c569", "dfw"),
                                          ("45.57.95.1", "c091", "mci")))
    session, repo = _refresh_with([kept, gone], new)
    session.delete.assert_called_once_with(gone)
    # The kept row is the same row, now pointing at today's appliance.
    assert kept.host == "23.246.3.9"
    assert kept.cache_id == "c569"
    added = [c.args[0]["name"] for c in repo.add_pending.call_args_list]
    assert added == ["Netflix_2_MCI"]
    # One transaction: deletes flushed before inserts, one commit, at the end.
    session.flush.assert_called_once()
    session.commit.assert_called_once()
    repo.create.assert_not_called()
    repo.update.assert_not_called()


def test_a_slot_turned_off_stays_off_across_refreshes():
    fetcher = OCAFetcher()
    kept = MagicMock()
    kept.name = "Netflix_1_DFW"
    kept.is_active = False
    new = fetcher.parse_oca_data(_servers(("23.246.3.9", "c569", "dfw"),
                                          ("45.57.95.1", "c091", "mci")))
    _, repo = _refresh_with([kept], new)
    assert kept.is_active is False
    assert kept.host == "23.246.3.9"
    # A new slot is turned on.
    assert repo.add_pending.call_args.args[0]["is_active"] is True


def test_a_slot_that_moves_site_is_a_new_name_in_the_same_transaction():
    fetcher = OCAFetcher()
    old = MagicMock()
    old.name = "Netflix_1_DFW"
    new = fetcher.parse_oca_data(_servers(("198.38.98.1", "c348", "atl")))
    session, repo = _refresh_with([old], new)
    session.delete.assert_called_once_with(old)
    assert [c.args[0]["name"] for c in repo.add_pending.call_args_list] == ["Netflix_1_ATL"]
    # The delete is flushed before the insert, and one commit covers both.
    calls = [c[0] for c in session.mock_calls if c[0] in ("delete", "flush", "commit")]
    assert calls == ["delete", "flush", "commit"]
