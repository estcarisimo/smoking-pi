import owners


class Table:
    ready = True

    def lookup(self, ip):
        if ip == "nonsense":
            raise ValueError(ip)
        return 16591 if ip.startswith("23.") else None


def test_the_routing_table_answers_and_nothing_else_is_asked():
    o = owners.Owners(table=Table())
    assert o.of("23.9.9.9") == 16591
    assert o.of("34.0.0.1") is None and o.of("nonsense") is None and o.of(None) is None


def test_no_table_yet_means_no_as():
    class Loading(Table):
        ready = False

    assert owners.Owners(table=Loading()).of("23.0.0.1") is None
    assert owners.Owners(table=lambda: None).of("23.0.0.1") is None
