"""confine(): the one place request-named files are allowed to touch disk."""


import pytest

from app.services import crux, cloudflare
from app.services.safe_path import UnsafePath, confine


def test_plain_name_stays_inside(tmp_path):
    assert confine(tmp_path, 'crux_ar.csv') == tmp_path / 'crux_ar.csv'


@pytest.mark.parametrize('name', [
    '../etc/passwd',
    '..',
    '.',
    '',
    'sub/file.csv',
    '/etc/passwd',
    '..\\..\\windows',
    'crux_../../x.csv',
])
def test_escapes_are_refused(tmp_path, name):
    with pytest.raises(UnsafePath):
        confine(tmp_path, name)


def test_sibling_with_shared_prefix_is_refused(tmp_path):
    """/tmp/cache_evil must not pass as inside /tmp/cache."""
    base = tmp_path / 'cache'
    with pytest.raises(UnsafePath):
        confine(base, '../cache_evil/x')


def test_crux_country_file_is_confined(tmp_path, monkeypatch):
    """REINTRODUCTION TEST: build the cache path from the country string
    without confine() and a traversal country escapes the cache dir."""
    service = crux.CruxService()
    monkeypatch.setattr(service, 'cache_dir', tmp_path)
    # The regex rejects this first; confine() is the second line and is what
    # this asserts on directly.
    with pytest.raises(UnsafePath):
        confine(service.cache_dir, 'crux_../../etc/passwd.csv')
    assert service._get_country_sites('../../etc', 10, 0) == []
    assert not (tmp_path.parent / 'etc').exists()


def test_cloudflare_country_file_is_confined(tmp_path, monkeypatch):
    service = cloudflare.CloudflareService()
    monkeypatch.setattr(service, 'cache_dir', tmp_path)
    monkeypatch.setattr(service, '_fetch_from_api', lambda *a, **k: [])
    # Invalid codes fall back to global rather than reaching the filesystem.
    assert service.get_top_sites(10, '../x', 0, api_token='t') == []
    assert sorted(p.name for p in tmp_path.iterdir()) in ([], ['cloudflare_global.json'])
