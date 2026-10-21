import status


def test_healthy_within_two_and_a_half_intervals():
    assert status.healthy({"at": 1000, "interval": 3600}, now=1000 + 8999)
    assert not status.healthy({"at": 1000, "interval": 3600}, now=1000 + 9001)
    assert status.healthy({"idle": "x"}, now=10**9)
    assert not status.healthy({}, now=10)


def test_write_and_read():
    status.write({"at": 5})
    assert status.read() == {"at": 5}
