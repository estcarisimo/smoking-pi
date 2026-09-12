"""confine(): the one place request-named files are allowed to touch disk."""


from pathlib import Path

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


def test_root_base_still_admits_children(tmp_path):
    """A base that already ends in the separator must not grow a second one."""
    assert confine('/', 'tmp') == Path('/tmp')
    with pytest.raises(UnsafePath):
        confine('/', '..')


def test_a_symlink_out_of_the_directory_is_refused(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret').write_text('x')
    base = tmp_path / 'base'
    base.mkdir()
    (base / 'latest.md').symlink_to(outside / 'secret')
    (base / 'ok.md').write_text('y')
    assert confine(base, 'ok.md') == base / 'ok.md'
    with pytest.raises(UnsafePath):
        confine(base, 'latest.md')


def test_crux_country_file_goes_through_confine(tmp_path, monkeypatch):
    """REINTRODUCTION TEST: build the cache path from the country string
    without confine() and this spy never fires. A VALID country is used so
    the call reaches the path construction; the cache is pre-warmed so no
    network is touched."""
    service = crux.CruxService()
    monkeypatch.setattr(service, 'cache_dir', tmp_path)
    (tmp_path / 'crux_us.csv').write_text('origin,rank\nhttps://a.com,1\n')
    monkeypatch.setattr(service, '_is_cache_valid', lambda path: True)
    seen = []

    def spy(base, name):
        seen.append((base, name))
        return confine(base, name)

    monkeypatch.setattr(crux, 'confine', spy)
    assert service._get_country_sites('us', 10, 0) == ['a.com']
    assert seen == [(tmp_path, 'crux_us.csv')]
    # And the regex still stops the obvious case before any path exists.
    assert service._get_country_sites('../../etc', 10, 0) == []
    assert not (tmp_path.parent / 'etc').exists()


def test_cloudflare_country_file_goes_through_confine(tmp_path, monkeypatch):
    service = cloudflare.CloudflareService()
    monkeypatch.setattr(service, 'cache_dir', tmp_path)
    monkeypatch.setattr(service, '_fetch_from_api', lambda *a, **k: ['b.com'])
    seen = []

    def spy(base, name):
        seen.append((base, name))
        return confine(base, name)

    monkeypatch.setattr(cloudflare, 'confine', spy)
    assert service.get_top_sites(10, 'ar', 0, api_token='t') == ['b.com']
    assert seen == [(tmp_path, 'cloudflare_AR.json')]
    # Invalid codes fall back to global rather than reaching the filesystem.
    seen.clear()
    service.get_top_sites(10, '../x', 0, api_token='t')
    assert seen == [(tmp_path, 'cloudflare_global.json')]
