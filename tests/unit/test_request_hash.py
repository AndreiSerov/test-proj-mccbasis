from typing import Any

import pytest

from payments.domain.create_payment import CreatePayment
from payments.domain.request_hash import request_hash


def body(**overrides: Any) -> CreatePayment:
    data: dict[str, Any] = {
        "amount": "10.50",
        "currency": "USD",
        "description": "Order 42",
        "metadata": {"a": 1, "b": {"x": 1, "y": 2}},
        "webhook_url": "https://client.example/hooks",
    }
    return CreatePayment.model_validate({**data, **overrides})


def test_hash_is_a_sha256_hex_digest() -> None:
    assert len(request_hash(body())) == 64


def test_hash_ignores_metadata_key_order_at_every_level() -> None:
    reordered = body(metadata={"b": {"y": 2, "x": 1}, "a": 1})

    assert request_hash(reordered) == request_hash(body())


@pytest.mark.parametrize("amount", ["10.5", "10.50", "10.500", 10.5])
def test_hash_ignores_amount_formatting(amount: Any) -> None:
    assert request_hash(body(amount=amount)) == request_hash(body())


def test_hash_treats_10_0_and_10_00_alike() -> None:
    assert request_hash(body(amount="10.0")) == request_hash(body(amount="10.00"))


def test_hash_ignores_host_case_and_default_port_in_the_url() -> None:
    other = body(webhook_url="https://CLIENT.example:443/hooks")

    assert request_hash(other) == request_hash(body())


def test_hash_treats_an_empty_path_as_a_root_path() -> None:
    assert request_hash(body(webhook_url="https://client.example")) == request_hash(
        body(webhook_url="https://client.example/")
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": "10.51"},
        {"currency": "EUR"},
        {"description": "Order 43"},
        {"metadata": {"a": 2}},
        {"metadata": {}},
        {"webhook_url": "https://client.example/other"},
        {"webhook_url": "https://client.example/hooks?x=1"},
    ],
)
def test_hash_differs_for_different_bodies(overrides: dict[str, Any]) -> None:
    assert request_hash(body(**overrides)) != request_hash(body())
