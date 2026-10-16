"""Telegram sends that survive a short network outage (spec step 10)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

log = logging.getLogger("send")


async def with_retry[T](call: Callable[[], Awaitable[T]], retries: int, delay_s: float,
                        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> T:
    """call(), repeated up to `retries` times on a network error (pause delay_s, 2·delay_s, …) or a flood wait
    (pause as Telegram asks). A timeout may hide a message that did arrive: a repeat can then post it twice,
    which beats losing it."""
    for attempt in range(retries + 1):
        try:
            return await call()
        except TelegramRetryAfter as e:
            if attempt == retries:
                raise
            log.warning("flood wait %s s (attempt %s)", e.retry_after, attempt + 1)
            await sleep(e.retry_after)
        except TelegramNetworkError as e:
            if attempt == retries:
                raise
            log.warning("telegram network error, retry in %s s: %s", delay_s * (attempt + 1), e)
            await sleep(delay_s * (attempt + 1))
    raise AssertionError("unreachable")
