from dataclasses import dataclass

from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.config import Settings
from clinic.llm import LLMPort
from clinic.locks import ConversationLocks
from clinic.workflows.graph import ConversationGraph


@dataclass(frozen=True)
class Deps:
    """Everything the app needs, built once in the lifespan. Later stories add fields here."""

    settings: Settings
    pool: ConnectionPool
    clock: Clock
    locks: ConversationLocks
    llm: LLMPort
    graph: ConversationGraph
