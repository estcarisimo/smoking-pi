"""cpe_discovery reloads SmokePing only when the CPE targets changed (#272).

It ran `killall -HUP smokeping` every hour whatever it found; a reload
restarts the probe cycles. subprocess.run is mocked: nothing is signaled.
"""

import pathlib
import sys

import pytest

MODULE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import cpe_discovery  # noqa: E402


@pytest.fixture()
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(cpe_discovery, "SMOKEPING_CONFIG_DIR", str(tmp_path))
    signals = []
    monkeypatch.setattr(cpe_discovery.subprocess, "run",
                        lambda args, **kw: signals.append(args))
    return tmp_path, signals


def _path(tmp_path):
    return tmp_path / cpe_discovery.CPE_TARGETS_FILE


def test_the_first_discovery_writes_and_reloads(config):
    tmp_path, signals = config
    cpe_discovery.ensure_cpe_file_exists()  # the placeholder SmokePing starts with
    cpe_discovery.update_smokeping_targets("192.0.2.1", None)
    assert "192.0.2.1" in _path(tmp_path).read_text()
    assert signals == [["killall", "-HUP", "smokeping"]]


def test_an_unchanged_discovery_neither_writes_nor_reloads(config):
    tmp_path, signals = config
    cpe_discovery.update_smokeping_targets("192.0.2.1", None)
    before = _path(tmp_path).stat().st_mtime_ns
    cpe_discovery.update_smokeping_targets("192.0.2.1", None)
    assert len(signals) == 1
    assert _path(tmp_path).stat().st_mtime_ns == before


def test_a_new_cpe_address_reloads(config):
    tmp_path, signals = config
    cpe_discovery.update_smokeping_targets("192.0.2.1", None)
    cpe_discovery.update_smokeping_targets("192.0.2.9", "2001:db8::1")
    assert len(signals) == 2
    assert "2001:db8::1" in _path(tmp_path).read_text()


def test_nothing_discovered_touches_nothing(config):
    tmp_path, signals = config
    cpe_discovery.update_smokeping_targets(None, None)
    assert signals == [] and not _path(tmp_path).exists()
