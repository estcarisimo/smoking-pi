"""classify(): KNOWN, then the Linnaeus-derived roles, then the AS name."""

from common import asn


def test_known_wins_over_the_roles_file():
    # Linnaeus labels Netflix's AS an entertainment enterprise and Akamai a
    # cloud and a CDN; KNOWN says what answers a measurement from them.
    assert asn.classify(2906, "AS-SSI", "1.2.3.4", []) == ("Netflix", asn.CACHE)
    assert asn.classify(20940, "AKAMAI-ASN1", "x", []) == ("Akamai", asn.CDN)


def test_the_roles_file_names_the_rest():
    assert asn.classify(3449, "AS3449 - Universidad Nacional de Buenos Aires, AR", "x",
                        []) == ("University of Buenos Aires", asn.ACADEMIC)
    assert asn.classify(7922, "COMCAST-7922", "x", [])[1] == asn.ACCESS
    assert asn.classify(3356, "LEVEL3", "x", [])[1] == asn.TRANSIT


def test_an_as_in_neither_is_its_own_operator():
    assert asn.classify(64500, "EXAMPLE-NET - Example Hosting, US", "x",
                        []) == ("EXAMPLE-NET", asn.OTHER)
    assert asn.classify(64501, "AS64501 - Universidad de Ejemplo, AR", "x",
                        []) == ("Universidad de Ejemplo", asn.ACADEMIC)
    assert asn.classify(None, "", "x", []) == (asn.UNKNOWN, asn.UNKNOWN)
    assert asn.classify(0, "", "x", []) == (asn.UNKNOWN, asn.UNKNOWN)


def test_amazon_is_decided_by_the_cname_chain_not_the_table():
    assert asn.classify(16509, "AMAZON-02", "d1.cloudfront.net", []) == (
        "Amazon CloudFront", asn.CDN)
    assert asn.classify(16509, "AMAZON-02", "api.example.com", []) == ("AWS", asn.CLOUD)


def test_the_roles_file_is_well_formed():
    table = asn.roles()
    assert len(table) > 500
    assert {kind for _, kind in table.values()} <= set(asn.KINDS)


def test_a_missing_or_broken_roles_file_is_an_empty_table(tmp_path):
    assert asn.roles(tmp_path / "absent.csv") == {}
    bad = tmp_path / "roles.csv"
    bad.write_text("# header\nasn,operator,kind\nnot-a-number,X,cdn\n64500,Y,made_up\n"
                   "64501,,transit\n")
    assert asn.roles(bad) == {64501: ("AS64501", asn.TRANSIT)}
