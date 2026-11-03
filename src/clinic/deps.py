from dataclasses import dataclass

from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.config import Settings
from clinic.locks import ConversationLocks
from clinic.services.approval import ApprovalResumer
from clinic.services.conversation import ConversationWorkflow


@dataclass(frozen=True)
class Deps:
    """Everything the app needs, built once in the lifespan. Later stories add fields here."""

    settings: Settings
    pool: ConnectionPool
    clock: Clock
    locks: ConversationLocks
    workflow: ConversationWorkflow
    approval_resumer: ApprovalResumer
