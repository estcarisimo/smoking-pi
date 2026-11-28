"""chain.verify and chain.pinned_root against chains built to fool them."""

from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fakelead import CA, NODE, now

from fleet import chain


def name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def cert(subject, key, issuer_name, issuer_key, *, ca, start=None, days=30):
    start = start or now() - timedelta(minutes=5)
    return (x509.CertificateBuilder().subject_name(name(subject)).issuer_name(issuer_name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(start + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .sign(issuer_key, hashes.SHA256()))


def key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def ca():
    return CA()


def leaf_from(ca, issuer=None, issuer_key=None, **kw):
    k = key()
    return cert(f"node-{NODE}", k, (issuer or ca.inter).subject,
                issuer_key or ca.inter_key, ca=False, **kw), k


def pems(*certs):
    return [chain.pem(c) for c in certs]


def refused(certs, root, at=None, words=""):
    with pytest.raises(chain.ChainError) as err:
        chain.verify(pems(*certs), root, at)
    assert words in err.value.reason
    return err.value.reason


def test_a_good_chain_passes(ca):
    leaf, _ = leaf_from(ca)
    assert chain.verify(pems(leaf, ca.inter), ca.root, now())[0] == leaf
    # The root appended by the lead is tolerated and dropped.
    assert len(chain.verify(pems(leaf, ca.inter, ca.root), ca.root, now())) == 2


def test_a_node_leaf_used_as_an_intermediate(ca):
    first, first_key = leaf_from(ca)
    second = cert("node-x", key(), first.subject, first_key, ca=False)
    refused([second, first, ca.inter], ca.root, words="does not lead to the pinned root")


def test_a_ca_as_the_leaf(ca):
    refused([ca.inter], ca.root, words="is a CA")


def test_an_expired_intermediate(ca):
    inter_key = key()
    old = now() - timedelta(days=400)
    inter = cert("old intermediate", inter_key, ca.root.subject, ca.root_key, ca=True,
                 start=old, days=10)
    leaf, _ = leaf_from(ca, inter, inter_key)
    refused([leaf, inter], ca.root, now(), words="not valid at this time")
    # Without a time, the signatures alone are checked.
    chain.verify(pems(leaf, inter), ca.root, None)


def test_a_not_yet_valid_leaf(ca):
    leaf, _ = leaf_from(ca, start=now() + timedelta(days=2))
    refused([leaf, ca.inter], ca.root, now(), words="not valid at this time")


def test_a_bad_signature(ca):
    # Names the intermediate as issuer, signed by some other key.
    leaf, _ = leaf_from(ca, issuer=ca.inter, issuer_key=key())
    refused([leaf, ca.inter], ca.root, words="does not lead to the pinned root")


def test_an_intermediate_from_another_root(ca):
    other = CA("other")
    leaf, _ = leaf_from(other)
    refused([leaf, other.inter], ca.root, words="does not lead to the pinned root")


def test_an_empty_chain_and_a_chain_that_is_only_the_root(ca):
    refused([], ca.root, words="no certificate")
    refused([ca.root], ca.root, words="no certificate")


def test_garbage_is_refused_not_raised():
    with pytest.raises(chain.ChainError):
        chain.verify(["-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"],
                     CA().root, None)


def test_pinned_root_refuses_another_fingerprint(ca):
    with pytest.raises(chain.ChainError, match="does not match the fingerprint"):
        chain.pinned_root(ca.root_pem, CA().fingerprint)


def test_pinned_root_refuses_a_certificate_that_is_not_self_signed(ca):
    with pytest.raises(chain.ChainError, match="not a self-signed CA"):
        chain.pinned_root(chain.pem(ca.inter), chain.fingerprint(ca.inter))


def test_pinned_root_refuses_a_self_signed_non_ca(ca):
    k = key()
    lone = cert("lone", k, name("lone"), k, ca=False)
    with pytest.raises(chain.ChainError, match="not a self-signed CA"):
        chain.pinned_root(chain.pem(lone), chain.fingerprint(lone))


def test_node_identity():
    ca = CA()
    from fleet import nodekeys
    k = nodekeys.new_key()
    leaf = chain.load(ca.issue(nodekeys.csr_pem(k, NODE), NODE)[0])
    assert chain.node_identity_ok(leaf, NODE)
    assert not chain.node_identity_ok(leaf, "11111111-2222-4333-8444-555555555555")
