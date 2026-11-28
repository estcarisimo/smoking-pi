"""The TLS client: the pinned root is the only trust anchor, hostnames are
checked, and the one unverified context is the join's root fetch.

The handshakes run between two SSLObjects over in-memory BIOs: no socket."""

import ast
import ssl
from pathlib import Path

import pytest
from fakelead import CA

from fleet import chain, transport
from fleet.transport import Tls, TransportError

FLEET_SRC = Path(__file__).resolve().parents[1] / "fleet"


@pytest.fixture
def ca():
    return CA()


@pytest.fixture
def root_file(tmp_path, ca):
    p = tmp_path / "root.pem"
    p.write_text(ca.root_pem)
    return p


def test_a_verified_context_trusts_the_pinned_root_alone(root_file, ca):
    ctx = transport.context(Tls(root=root_file))
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert ctx.get_ca_certs(binary_form=True) == [chain.der(ca.root)]


def test_a_missing_root_raises_and_never_falls_back(tmp_path):
    with pytest.raises(OSError):
        transport.context(Tls(root=tmp_path / "missing.pem"))
    with pytest.raises(TransportError) as err:
        transport.HttpsTransport().request("POST", "https://lead.example/fleet/heartbeat",
                                           b"{}", Tls(root=tmp_path / "missing.pem"))
    assert err.value.reason == "local_credentials"


def test_root_none_is_the_one_unverified_context():
    ctx = transport.context(Tls(root=None))
    assert ctx.verify_mode == ssl.CERT_NONE and ctx.check_hostname is False


def test_only_the_joins_root_fetch_asks_for_an_unverified_context():
    """Every `Tls(...)` in the package names a root, except inside
    join.fetch_root."""
    unverified = []
    for path in sorted(FLEET_SRC.glob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Tls"):
                    root = next((k.value for k in node.keywords if k.arg == "root"),
                                node.args[0] if node.args else None)
                    if isinstance(root, ast.Constant) and root.value is None:
                        unverified.append(f"{path.stem}.{fn.name}")
    assert unverified == ["join.fetch_root"]


def handshake(client_ctx, server_ctx, server_hostname):
    """Run a TLS handshake between two in-memory ends; raise what the
    client raises."""
    c_in, c_out, s_in, s_out = (ssl.MemoryBIO() for _ in range(4))
    client = client_ctx.wrap_bio(c_in, c_out, server_hostname=server_hostname)
    server = server_ctx.wrap_bio(s_in, s_out, server_side=True)
    client_done = server_done = False
    for _ in range(50):
        for end, done_attr in ((client, "c"), (server, "s")):
            if (done_attr == "c" and client_done) or (done_attr == "s" and server_done):
                continue
            try:
                end.do_handshake()
                if done_attr == "c":
                    client_done = True
                else:
                    server_done = True
            except ssl.SSLWantReadError:
                pass
            except ssl.SSLError:
                if end is client:
                    raise
                server_done = True  # the client's alert ends the server's side
        if c_out.pending:
            s_in.write(c_out.read())
        if s_out.pending:
            c_in.write(s_out.read())
        if client_done and server_done:
            return
    raise AssertionError("handshake did not finish")


def server_context(tmp_path, issuer, **names):
    chain_pem, key_pem = issuer.issue_server(**names)
    (tmp_path / "srv.pem").write_text(chain_pem)
    (tmp_path / "srv.key").write_bytes(key_pem)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "srv.pem", tmp_path / "srv.key")
    return ctx


def test_a_lead_certificate_naming_the_host_is_accepted(tmp_path, ca, root_file):
    server = server_context(tmp_path, ca, dns=("lead.example",))
    handshake(transport.context(Tls(root=root_file)), server, "lead.example")


def test_an_ip_literal_with_an_ip_san_is_accepted(tmp_path, ca, root_file):
    server = server_context(tmp_path, ca, ips=("192.0.2.10",))
    handshake(transport.context(Tls(root=root_file)), server, "192.0.2.10")


def test_a_certificate_for_another_name_is_refused_as_hostname(tmp_path, ca, root_file):
    server = server_context(tmp_path, ca, dns=("other.example",))
    with pytest.raises(ssl.SSLCertVerificationError) as err:
        handshake(transport.context(Tls(root=root_file)), server, "lead.example")
    assert transport.failure_kind(err.value) == "hostname"


def test_a_certificate_from_another_ca_is_refused_as_certificate(tmp_path, root_file):
    server = server_context(tmp_path, CA("impostor"), dns=("lead.example",))
    with pytest.raises(ssl.SSLCertVerificationError) as err:
        handshake(transport.context(Tls(root=root_file)), server, "lead.example")
    assert transport.failure_kind(err.value) == "certificate"


def test_the_system_store_is_not_consulted(tmp_path, root_file, monkeypatch):
    """Even a CA the system trusts would not do: only the pinned file."""
    other = CA("system")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "sys.pem"))
    (tmp_path / "sys.pem").write_text(other.root_pem)
    server = server_context(tmp_path, other, dns=("lead.example",))
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(transport.context(Tls(root=root_file)), server, "lead.example")


@pytest.mark.parametrize("err,kind", [
    (ConnectionRefusedError(), "refused"), (TimeoutError(), "timeout"),
    (ssl.SSLError(), "tls"), (OSError(), "network"),
])
def test_failure_kinds_are_literal(err, kind):
    assert transport.failure_kind(err) == kind
