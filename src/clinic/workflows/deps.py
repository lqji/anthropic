from dataclasses import dataclass

from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.config import Settings


@dataclass(frozen=True)
class WorkflowDeps:
    """What real nodes need. Each node is built once with these; its signature stays
    `def node(state)`. Later stories add fields (e.g. the LLM port)."""

    settings: Settings
    pool: ConnectionPool
    clock: Clock
