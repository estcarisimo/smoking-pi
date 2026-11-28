"""The lead's CA: layout, permissions, what it signs and what it refuses."""

import datetime as dt
import os
import stat

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID
from cryptography.x509.verification import PolicyBuilder, Store

from fleet import admin, layout, pki
from leadkit import LEAD_URL, NODE, NODE2, cfg, csr_pem, make_lead


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_init_lays_out_the_ca_with_tight_permissions(tmp_path):
    old = os.umask(0)  # a permissive umask must not widen a secret
    try:
        paths, _service = make_lead(tmp_path)
    finally:
        os.umask(old)
    for name in layout.LEAD_SECRETS:
        assert mode(getattr(paths, name)) == 0o600, name
    for name in ("root_cert", "intermediate_cert", "server_cert", "fleet"):
        assert mode(getattr(paths, name)) == 0o644, name
    for d in (paths.root_key.parent, paths.server_key.parent):
        assert mode(d) == 0o700
    # Nothing left from the scratch directory init builds in.
    assert [p.name for p in paths.root.iterdir()] == ["lead"]


def test_root_and_intermediate(tmp_path):
    paths, service = make_lead(tmp_path)
    ca = service.ca
    root, inter = ca.root, ca.intermediate
    assert root.issuer == root.subject and pki.verify_issued_by(root, root)
    bc = root.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca and bc.path_length == 1
    bc = inter.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca and bc.path_length == 0
    assert pki.verify_issued_by(inter, root)
    for cert in (root, inter):
        assert isinstance(cert.public_key().curve, ec.SECP256R1)
        assert cert.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign
    assert pki.not_after(inter) <= pki.not_after(root)
    import hashlib
    from cryptography.hazmat.primitives import serialization
    assert ca.root_sha256 == hashlib.sha256(
        root.public_bytes(serialization.Encoding.DER)).hexdigest()
    assert service.fleet.fleet_id in root.subject.rfc4514_string()


def _verify_client(chain_pems, root, node_id):
    leaf, *inters = [x509.load_pem_x509_certificate(p.encode()) for p in chain_pems]
    verifier = PolicyBuilder().store(Store([root])).build_client_verifier()
    return verifier.verify(leaf, inters)


def test_a_node_certificate_is_what_the_protocol_says(tmp_path):
    _paths, service = make_lead(tmp_path)
    key, pem = csr_pem()
    csr = pki.load_node_csr(pem, NODE)
    leaf = service.ca.sign_node(csr, NODE)
    life = pki.not_after(leaf) - leaf.not_valid_before_utc
    assert dt.timedelta(days=90) <= life <= dt.timedelta(days=90, minutes=6)
    assert leaf.subject.rfc4514_string().startswith(f"CN=node-{NODE}")
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [ExtendedKeyUsageOID.CLIENT_AUTH]
    assert not leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert pki.node_id_of(leaf) == NODE
    assert pki.same_public_key(leaf.public_key(), key.public_key())
    # The chain the lead hands out verifies against the root alone.
    verified = _verify_client(service.ca.chain(leaf), service.ca.root, NODE)
    assert [x509.UniformResourceIdentifier(f"urn:smoking-pi:node:{NODE}")] == list(
        verified.subjects or [])


@pytest.mark.parametrize("make_key, reason", [
    (lambda: ec.generate_private_key(ec.SECP384R1()), "csr: the key must be ECDSA P-256"),
    (lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048),
     "csr: the key must be ECDSA P-256"),
])
def test_csr_refused_for_other_keys(make_key, reason):
    _k, pem = csr_pem(make_key())
    with pytest.raises(pki.CsrRejected) as err:
        pki.load_node_csr(pem, NODE)
    assert err.value.reasons == (reason,)


def test_csr_refused_when_the_signature_does_not_verify():
    key = ec.generate_private_key(ec.SECP256R1())
    _k, pem = csr_pem(key, cn="node-x")
    # Same request, its subject changed after signing: the signature no
    # longer covers it.
    tampered = pem.replace(pem.splitlines()[3], pem.splitlines()[3][:-4] + "AAAA", 1)
    with pytest.raises(pki.CsrRejected) as err:
        pki.load_node_csr(tampered, NODE)
    assert err.value.reasons[0] in ("csr: the signature does not verify",
                                    "csr: not a PEM certificate request")


def test_csr_refused_when_it_names_another_node():
    for kw in ({"cn": f"node-{NODE2}"}, {"uri": f"urn:smoking-pi:node:{NODE2}"}):
        _k, pem = csr_pem(**kw)
        with pytest.raises(pki.CsrRejected) as err:
            pki.load_node_csr(pem, NODE)
        assert err.value.reasons == ("csr: names another node",)
    # Its own id, or no id at all, is fine: the lead writes the subject.
    for kw in ({"cn": f"node-{NODE}"}, {"uri": f"urn:smoking-pi:node:{NODE}"}, {"cn": "pi"}, {}):
        _k, pem = csr_pem(**kw)
        pki.load_node_csr(pem, NODE)


def test_csr_garbage_is_refused_with_a_literal_reason():
    with pytest.raises(pki.CsrRejected) as err:
        pki.load_node_csr("-----BEGIN CERTIFICATE REQUEST-----\nAAAA\n"
                          "-----END CERTIFICATE REQUEST-----\n", NODE)
    assert err.value.reasons == ("csr: not a PEM certificate request",)


def test_server_certificate_names_the_lead_urls_and_is_reissued_when_due(tmp_path):
    urls = (LEAD_URL, "https://192.0.2.10:8443", "https://[2001:db8::1]:8443")
    paths, service = make_lead(tmp_path, urls=urls)
    chain = paths.server_cert.read_text()
    assert chain.count("BEGIN CERTIFICATE") == 2  # leaf, then intermediate
    cert = pki.load_cert(chain)
    assert sorted(pki.server_hosts(cert)) == sorted(["lead.example", "192.0.2.10",
                                                    "2001:db8::1"])
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [ExtendedKeyUsageOID.SERVER_AUTH]
    hosts = pki.url_hosts(urls)
    lead_id = service.fleet.lead_id
    assert not pki.ensure_server_cert(paths, service.ca, lead_id, hosts)
    # A new URL, or less than 30 days left: a new certificate (and key).
    assert pki.ensure_server_cert(paths, service.ca, lead_id, hosts + ["pi.example"])
    later = pki.not_after(pki.load_cert(paths.server_cert.read_bytes())) - dt.timedelta(days=29)
    assert pki.ensure_server_cert(paths, service.ca, lead_id, hosts + ["pi.example"], now=later)
    assert mode(paths.server_key) == 0o600


def test_a_second_lead_gets_an_intermediate_from_the_same_root(tmp_path):
    """The offline-root layout: another lead's intermediate, signed by the
    root, issues certificates nodes pinned to the same root accept."""
    paths, service = make_lead(tmp_path)
    ca = pki.load(paths, with_root_key=True)  # whoever holds the root key
    key2 = pki.new_key()
    csr = pki.intermediate_csr(service.fleet.fleet_id, "lead-two", key2)
    inter2 = pki.sign_intermediate(csr, ca.root, ca.root_key)
    other = pki.CA(root=ca.root, intermediate=inter2, intermediate_key=key2)
    _k, pem = csr_pem()
    leaf = other.sign_node(pki.load_node_csr(pem, NODE), NODE)
    _verify_client(other.chain(leaf), ca.root, NODE)
    # An intermediate request for another fleet is refused.
    with pytest.raises(pki.CsrRejected):
        pki.sign_intermediate(pki.intermediate_csr("another-fleet", "x", key2),
                              ca.root, ca.root_key)


def test_load_refuses_a_ca_that_does_not_hang_together(tmp_path):
    paths, _service = make_lead(tmp_path)
    good = paths.intermediate_key.read_bytes()
    pki.write_private(paths.intermediate_key, pki.key_pem(pki.new_key()))
    with pytest.raises(pki.CAKeyMismatch) as err:
        pki.load(paths)
    assert str(err.value) == ""  # the type says what; the text is a table's
    pki.write_private(paths.intermediate_key, good)
    pki.load(paths)
    pki.write_private(paths.root_key, pki.key_pem(pki.new_key()))
    pki.load(paths)  # the lead never reads the root key...
    with pytest.raises(pki.RootKeyBroken):
        pki.load(paths, with_root_key=True)  # ...whoever does checks it
    paths.root_key.unlink()
    assert pki.load(paths, with_root_key=True).root_key is None
    paths.intermediate_cert.unlink()
    with pytest.raises(pki.CAMissing):
        pki.load(paths)


def test_the_serving_lead_never_holds_the_root_key(tmp_path):
    """Taking the root key offline deletes the file; a running lead must
    not still have it in memory."""
    _paths, service = make_lead(tmp_path)
    assert service.ca.root_key is None


def test_the_root_key_goes_offline_and_comes_back_only_as_itself(tmp_path):
    paths, service = make_lead(tmp_path)
    exported = admin.export_root_key(paths)
    with pytest.raises(admin.Refusal):
        admin.remove_root_key(paths, pki.key_pem(pki.new_key()))
    assert paths.root_key.read_bytes() == exported  # a wrong copy deletes nothing
    with pytest.raises(admin.Refusal):  # nor overwrites it
        admin.restore_root_key(paths, pki.key_pem(pki.new_key()))
    assert paths.root_key.read_bytes() == exported
    admin.remove_root_key(paths, exported)
    assert not paths.root_key.exists()
    assert admin.ca_status(paths)["root_key_online"] is False
    with pytest.raises(admin.Refusal):
        admin.export_root_key(paths)
    # The lead keeps issuing node certificates without it.
    from leadkit import enroll, rebuild
    enroll(rebuild(paths), paths)
    with pytest.raises(admin.Refusal):
        admin.restore_root_key(paths, pki.key_pem(pki.new_key()))
    assert not paths.root_key.exists()
    admin.restore_root_key(paths, exported)
    assert mode(paths.root_key) == 0o600
    assert pki.load(paths, with_root_key=True).root_key is not None


def test_init_refuses_without_urls_or_over_an_existing_fleet(tmp_path):
    paths = layout.lead_paths(tmp_path)
    with pytest.raises(admin.Refusal, match="at least one URL"):
        admin.init(paths, cfg(urls=()), "x")
    with pytest.raises(admin.Refusal, match="FLEET_LEAD_URLS"):
        admin.init(paths, cfg(urls=("http://lead.example",)), "x")
    with pytest.raises(admin.Refusal, match="fleet name"):
        admin.init(paths, cfg(), "bad\nname")
    made = admin.init(paths, cfg(), "Home")
    with pytest.raises(admin.Refusal, match="already initialized"):
        admin.init(paths, cfg(), "Home")
    paths.fleet.write_text("{broken")
    with pytest.raises(admin.Refusal, match="corrupt"):
        admin.init(paths, cfg(), "Home")
    paths.fleet.unlink()
    with pytest.raises(admin.Refusal, match="exists without fleet.json"):
        admin.init(paths, cfg(), "Home")
    assert made["root_sha256"] == pki.load(paths).root_sha256


def test_init_removes_what_an_interrupted_init_left(tmp_path, capsys):
    paths = layout.lead_paths(tmp_path)
    leftover = tmp_path / ".init-abc123" / "lead" / "ca"
    leftover.mkdir(parents=True)
    (leftover / "root-key.pem").write_text("a root key of a fleet that never existed")
    admin.init(paths, cfg(), "Home")
    assert not (tmp_path / ".init-abc123").exists()
    assert "interrupted 'fleet init'" in capsys.readouterr().err
    assert [p.name for p in tmp_path.iterdir()] == ["lead"]


def test_a_key_beside_a_certificate_it_does_not_match_is_reissued(tmp_path):
    """A power cut between two writes must not leave a pair gunicorn cannot
    load and the lead calls fresh (a crash loop). The pair is one file; and
    a mismatched one is re-issued."""
    paths, service = make_lead(tmp_path)
    hosts, lead_id = ["lead.example"], service.fleet.lead_id
    key, leaf, inter = pki.read_server_pair(paths.server_key)
    assert pki.same_public_key(key.public_key(), leaf.public_key())
    assert inter == service.ca.intermediate
    chain = "".join(service.ca.chain(leaf)).encode()
    pki.write_private(paths.server_key, pki.key_pem(pki.new_key()) + chain)
    with pytest.raises(ValueError):
        pki.read_server_pair(paths.server_key)
    assert pki.ensure_server_cert(paths, service.ca, lead_id, hosts)
    key2, leaf2, _ = pki.read_server_pair(paths.server_key)
    assert pki.same_public_key(key2.public_key(), leaf2.public_key())
    assert not pki.ensure_server_cert(paths, service.ca, lead_id, hosts)
    # A truncated file (no chain) is re-issued too.
    pki.write_private(paths.server_key, pki.key_pem(key2))
    assert pki.ensure_server_cert(paths, service.ca, lead_id, hosts)
