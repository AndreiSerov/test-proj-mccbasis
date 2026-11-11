import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Literal

from payments.domain.payment import PaymentStatus

FailureMode = Literal["decline", "error"]


class EmulatorError(Exception):
    """The Emulator's non-success outcome in `error` mode: a Delivery failure.

    Raised instead of returning a Decline, so the handler rolls back and the
    message is retried (ADR-0003).
    """


class Emulator:
    """Stand-in for the external payment gateway.

    Waits a random time in ``[min_delay, max_delay]``, then reports ``succeeded``
    with probability ``success_rate``. Otherwise, in ``decline`` mode it reports
    ``failed`` (a Decline); in ``error`` mode it raises EmulatorError.
    """

    def __init__(
        self,
        *,
        min_delay: float,
        max_delay: float,
        success_rate: float,
        failure_mode: FailureMode = "decline",
        rng: random.Random | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._success_rate = success_rate
        self._failure_mode = failure_mode
        self._rng = rng or random.Random()
        self._sleep = sleep

    async def run(self) -> PaymentStatus:
        await self._sleep(self._rng.uniform(self._min_delay, self._max_delay))
        # random() is in [0, 1), so rate 1.0 always succeeds and 0.0 never does.
        if self._rng.random() < self._success_rate:
            return PaymentStatus.succeeded
        if self._failure_mode == "error":
            raise EmulatorError("the Emulator reported an error")
        return PaymentStatus.failed
