"""The TLS handshake the lead's server does, in memory (two ssl.MemoryBIO
pairs, no socket): the server side is gunicorn's own TLS context, built
from the very options fleet/server.py passes; the client side is what a
node does (trust the root alone, present its chain or nothing)."""

import ssl

import pytest
from cryptography.hazmat.primitives import serialization
from gunicorn import sock as gsock

from fleet import pki, server
from leadkit import NODE, enroll, make_lead


def _server_context(paths):
    app = server._Application(object(), server.options(paths, lambda _w: None))
    return gsock.ssl_context(app.cfg)


def _client_context(tmp_path, root_pem: str, key=None, chain=None):
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_verify_locations(cadata=root_pem)
    if key is not None:
        f = tmp_path / "client.pem"
        f.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()) + "".join(chain).encode())
        ctx.load_cert_chain(f)
    return ctx


def handshake(server_ctx, client_ctx, hostname="lead.example"):
    """Run both sides to completion; return the server's view of the client
    certificate (DER or None). Raises ssl.SSLError when either side refuses."""
    c_in, c_out, s_in, s_out = (ssl.MemoryBIO() for _ in range(4))
    client = client_ctx.wrap_bio(c_in, c_out, server_side=False, server_hostname=hostname)
    srv = server_ctx.wrap_bio(s_in, s_out, server_side=True)
    done = {"c": False, "s": False}
    for _ in range(50):
        for name, obj in (("c", client), ("s", srv)):
            if not done[name]:
                try:
                    obj.do_handshake()
                    done[name] = True
                except ssl.SSLWantReadError:
                    pass
        # Shuttle bytes both ways.
        data = c_out.read()
        if data:
            s_in.write(data)
        data = s_out.read()
        if data:
            c_in.write(data)
        if all(done.values()) and not c_out.pending and not s_out.pending:
            # TLS 1.3: the server learns about a refused client certificate
            # on the first read after the handshake.
            client.write(b"x")
            s_in.write(c_out.read())
            try:
                srv.read(1)
            except ssl.SSLWantReadError:
                pass
            return srv.getpeercert(binary_form=True)
    raise AssertionError("handshake did not finish")


@pytest.fixture
def lead(tmp_path):
    paths, service = make_lead(tmp_path)
    return paths, service, pki.cert_pem(service.ca.root)


def test_a_node_chain_is_accepted_and_seen_by_the_lead(tmp_path, lead):
    paths, service, root_pem = lead
    key, resp = enroll(service, paths)
    der = handshake(_server_context(paths), _client_context(tmp_path, root_pem, key,
                                                            resp.cert_chain))
    assert pki.node_id_of(pki.x509.load_der_x509_certificate(der)) == NODE
    # The lead looks the certificate up by exactly this DER.
    assert service.registry.cert(pki.der_fingerprint(der)).node_id == NODE


def test_no_client_certificate_is_accepted_for_enrollment(tmp_path, lead):
    paths, _service, root_pem = lead
    assert handshake(_server_context(paths), _client_context(tmp_path, root_pem)) is None


def test_a_foreign_ca_client_certificate_is_refused_in_the_handshake(tmp_path, lead):
    paths, _service, root_pem = lead
    other_paths, other = make_lead(tmp_path / "other")
    key, resp = enroll(other, other_paths)
    with pytest.raises(ssl.SSLError):
        handshake(_server_context(paths),
                  _client_context(tmp_path, root_pem, key, resp.cert_chain))


def test_a_leaf_without_its_intermediate_is_refused(tmp_path, lead):
    """The lead trusts the root only: a node must send the chain."""
    paths, service, root_pem = lead
    key, resp = enroll(service, paths)
    with pytest.raises(ssl.SSLError):
        handshake(_server_context(paths),
                  _client_context(tmp_path, root_pem, key, resp.cert_chain[:1]))


def test_the_node_verifies_the_lead_by_name_against_the_root(tmp_path, lead):
    paths, _service, root_pem = lead
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(_server_context(paths), _client_context(tmp_path, root_pem),
                  hostname="elsewhere.example")
