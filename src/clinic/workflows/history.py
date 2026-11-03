from collections.abc import Sequence

from langchain_core.messages import BaseMessage, trim_messages


def history_window(messages: Sequence[BaseMessage], size: int) -> list[BaseMessage]:
    """The last `size` messages at most, starting with a human message (for LLM prompts).

    Counts messages, not tokens. The checkpoint keeps the full history; only the prompt is cut.
    """
    return trim_messages(
        messages, max_tokens=size, token_counter=len, strategy="last", start_on="human"
    )
