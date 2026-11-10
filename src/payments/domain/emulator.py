import asyncio
import random
from collections.abc import Awaitable, Callable

from payments.domain.payment import PaymentStatus


class Emulator:
    """Stand-in for the external payment gateway.

    Waits a random time in ``[min_delay, max_delay]``, then reports ``succeeded``
    with probability ``success_rate`` and ``failed`` (a Decline) otherwise.
    """

    def __init__(
        self,
        *,
        min_delay: float,
        max_delay: float,
        success_rate: float,
        rng: random.Random | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._success_rate = success_rate
        self._rng = rng or random.Random()
        self._sleep = sleep

    async def run(self) -> PaymentStatus:
        await self._sleep(self._rng.uniform(self._min_delay, self._max_delay))
        # random() is in [0, 1), so rate 1.0 always succeeds and 0.0 never does.
        if self._rng.random() < self._success_rate:
            return PaymentStatus.succeeded
        return PaymentStatus.failed
