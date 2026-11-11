import random
from typing import Literal

import pytest

from payments.domain.emulator import Emulator, EmulatorError
from payments.domain.payment import PaymentStatus


class FakeSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def make(
    sleep: FakeSleep,
    *,
    success_rate: float = 0.9,
    seed: int = 1,
    failure_mode: Literal["decline", "error"] = "decline",
) -> Emulator:
    return Emulator(
        failure_mode=failure_mode,
        min_delay=2,
        max_delay=5,
        success_rate=success_rate,
        rng=random.Random(seed),
        sleep=sleep,
    )


async def test_delay_stays_within_bounds() -> None:
    sleep = FakeSleep()
    emulator = make(sleep)

    for _ in range(200):
        await emulator.run()

    assert len(sleep.delays) == 200
    assert all(2 <= d <= 5 for d in sleep.delays)
    assert max(sleep.delays) - min(sleep.delays) > 1  # actually random, not constant


async def test_success_rate_is_respected() -> None:
    sleep = FakeSleep()
    emulator = make(sleep, success_rate=0.9)

    results = [await emulator.run() for _ in range(2000)]

    succeeded = results.count(PaymentStatus.succeeded)
    assert set(results) == {PaymentStatus.succeeded, PaymentStatus.failed}
    assert 0.86 <= succeeded / 2000 <= 0.94


async def test_rate_extremes_are_deterministic() -> None:
    sleep = FakeSleep()

    always = make(sleep, success_rate=1.0)
    never = make(sleep, success_rate=0.0)

    assert {await always.run() for _ in range(100)} == {PaymentStatus.succeeded}
    assert {await never.run() for _ in range(100)} == {PaymentStatus.failed}


async def test_error_mode_raises_an_emulator_error_instead_of_declining() -> None:
    sleep = FakeSleep()
    never = make(sleep, success_rate=0.0, failure_mode="error")
    always = make(sleep, success_rate=1.0, failure_mode="error")

    with pytest.raises(EmulatorError):
        await never.run()
    assert await always.run() == PaymentStatus.succeeded
