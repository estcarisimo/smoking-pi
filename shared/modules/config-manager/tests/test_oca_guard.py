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
