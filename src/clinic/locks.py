import threading
from collections.abc import Iterator
from contextlib import contextmanager

from clinic.errors import ConversationBusy


class ConversationLocks:
    """In-process, non-blocking lock per conversation. Holds no database connection.

    Works because the service runs a single uvicorn worker.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    @contextmanager
    def hold(self, conversation_id: str) -> Iterator[None]:
        """Take the lock for a conversation or raise ConversationBusy at once."""
        with self._guard:
            lock = self._locks.setdefault(conversation_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ConversationBusy()
        try:
            yield
        finally:
            lock.release()
