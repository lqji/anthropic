from typing import Protocol

from clinic.workflows.state import ConversationState


class Node(Protocol):
    # The parameter must be called `state`: LangGraph's add_node matches on the name.
    def __call__(self, state: ConversationState) -> dict[str, object]: ...
