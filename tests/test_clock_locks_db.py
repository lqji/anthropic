import threading
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from clinic.clock import FixedClock, SystemClock
from clinic.config import Settings
from clinic.db import make_pool, ping
from clinic.errors import ConversationBusy
from clinic.locks import ConversationLocks


def test_fixed_clock_returns_the_same_instant_until_set() -> None:
    first = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    clock = FixedClock(first)
    assert clock.now() == first
    later = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    clock.set(later)
    assert clock.now() == later


def test_fixed_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 11, 2, 9, 0))


def test_system_clock_is_timezone_aware() -> None:
    assert SystemClock().now().tzinfo is not None


def test_lock_is_busy_while_held_and_free_afterwards() -> None:
    locks = ConversationLocks()
    with locks.hold("c1"):
        with pytest.raises(ConversationBusy):
            with locks.hold("c1"):
                pass
        with locks.hold("c2"):  # another conversation is not blocked
            pass
    with locks.hold("c1"):
        pass


def test_second_thread_gets_busy() -> None:
    locks = ConversationLocks()
    outcome: list[str] = []
    entered, release = threading.Event(), threading.Event()

    def holder() -> None:
        with locks.hold("c1"):
            entered.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    entered.wait(5)
    try:
        with locks.hold("c1"):
            outcome.append("acquired")
    except ConversationBusy:
        outcome.append("busy")
    finally:
        release.set()
        thread.join()
    assert outcome == ["busy"]


def test_ping_false_for_unreachable_database(make_settings: Callable[..., Settings]) -> None:
    pool = make_pool(make_settings(db_pool_timeout_s=1))
    pool.open(wait=False)
    try:
        assert ping(pool) is False
    finally:
        pool.close(timeout=2)


def test_ping_true_for_real_database(
    make_settings: Callable[..., Settings], database_url: str
) -> None:
    pool = make_pool(make_settings(database_url=database_url))
    pool.open(wait=False)
    try:
        assert ping(pool) is True
    finally:
        pool.close(timeout=2)
