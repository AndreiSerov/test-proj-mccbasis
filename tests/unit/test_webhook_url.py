import pytest

from payments.domain.webhook_url import is_blocked_address, mask_url, rejected_literal


@pytest.mark.parametrize(
    ("url", "masked"),
    [
        ("https://client.example/hooks/p", "https://client.example/hooks/p"),
        ("http://user:secret@client.example/h", "http://client.example/h"),
        ("http://tok@client.example:8080/h", "http://client.example:8080/h"),
        ("http://client.example/h?token=x&a=1", "http://client.example/h?…"),
        ("http://u:secret@[::1]:9/h?token=x", "http://[::1]:9/h?…"),
        ("http://client.example/h?", "http://client.example/h?…"),
    ],
)
def test_mask_url_drops_userinfo_and_query(url: str, masked: str) -> None:
    assert mask_url(url) == masked


def test_mask_url_of_garbage_does_not_leak_it() -> None:
    assert "secret" not in mask_url("http://u:secret@[bad/h?token=x")


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "100.64.0.1",
        "fd00::1",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
    ],
)
def test_internal_addresses_are_blocked(address: str) -> None:
    assert is_blocked_address(address)


@pytest.mark.parametrize(
    "address", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111", "::ffff:8.8.8.8"]
)
def test_public_addresses_are_not_blocked(address: str) -> None:
    assert not is_blocked_address(address)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://[::1]/x",
        "http://169.254.169.254/latest",
        "http://10.0.0.1/x",
        "http://localhost/x",
        "http://LOCALHOST./x",
        "http://sub.localhost/x",
    ],
)
def test_literal_blocked_hosts_are_rejected_up_front(url: str) -> None:
    assert rejected_literal(url, allowed_hosts=())


def test_literal_check_lets_names_and_allowlisted_hosts_through() -> None:
    assert not rejected_literal("https://client.example/x", allowed_hosts=())
    assert not rejected_literal("http://127.0.0.1:9/x", allowed_hosts=("127.0.0.1",))
    assert not rejected_literal("http://LocalHost/x", allowed_hosts=("localhost",))
