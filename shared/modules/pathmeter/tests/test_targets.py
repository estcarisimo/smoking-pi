import targets
from fakes import query_for


def test_one_destination_per_base_name_the_icmp_host_wins():
    q = query_for({"Google_h2": "https://www.google.com/x", "Google": "google.com",
                   "W_a_com_icmp": "a.com", "W_a_com_h1": "a.com"})
    found = targets.wanted(q)
    assert found == {"Google": ("custom", "google.com"), "W_a_com": ("custom", "a.com")}


def test_only_targets_measured_now():
    def query(flux):
        if "schema.tagValues" in flux:
            return [{"_value": "Live"}]
        return [{"target": "Live", "category": "c", "_value": "a.com"},
                {"target": "Gone", "category": "c", "_value": "b.com"}]
    assert targets.wanted(query) == {"Live": ("c", "a.com")}


def test_host_of():
    assert targets.host_of("https://www.x.com:443/a") == "www.x.com"
    assert targets.host_of("8.8.8.8") == "8.8.8.8"
    assert targets.host_of("2001:db8::1") == "2001:db8::1"


def test_queries_name_the_measurements():
    assert '"target_host"' in targets.hosts_flux()
    assert all(f'"{m}"' in targets.active_flux() for m in targets.MEASUREMENTS)
