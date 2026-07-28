"""Tests for atomic writes and the shared config lock."""

import file_ops
from file_ops import atomic_write_text, atomic_write_yaml, get_config_lock


def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / "out.txt"
    atomic_write_text(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_replaces_existing(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old content")
    atomic_write_text(target, "new content")
    assert target.read_text() == "new content"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "out.txt"
    atomic_write_text(target, "data")
    leftovers = [p for p in tmp_path.iterdir() if p.name != "out.txt"]
    assert leftovers == []


def test_atomic_write_creates_parent_dirs(tmp_path):
    target = tmp_path / "a" / "b" / "out.txt"
    atomic_write_text(target, "nested")
    assert target.read_text() == "nested"


def test_atomic_write_yaml_roundtrip(tmp_path):
    import yaml

    target = tmp_path / "data.yaml"
    data = {"probes": {"FPing": {"step": 300}}, "list": [1, 2]}
    atomic_write_yaml(target, data)
    assert yaml.safe_load(target.read_text()) == data


def test_config_lock_is_singleton_and_reentrant():
    lock1 = get_config_lock()
    lock2 = get_config_lock()
    # Same object per process, so nested acquisition is re-entrant
    assert lock1 is lock2
    with lock1:
        with lock2:
            assert lock1.is_locked
    assert not lock1.is_locked
    # Lock file lives in the config dir
    assert str(file_ops.CONFIG_DIR) in lock1.lock_file


def test_atomic_write_preserves_mode(tmp_path):
    from file_ops import atomic_write_text

    target = tmp_path / "Targets"
    target.write_text("old")
    target.chmod(0o644)
    atomic_write_text(target, "new")
    assert target.read_text() == "new"
    assert (target.stat().st_mode & 0o777) == 0o644


def test_atomic_write_new_file_gets_readable_mode(tmp_path):
    from file_ops import atomic_write_text

    target = tmp_path / "fresh"
    atomic_write_text(target, "content")
    assert (target.stat().st_mode & 0o777) == 0o644
