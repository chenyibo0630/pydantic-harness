"""history_processors hooks — transform message history before sending to model.

Usage in agent.py:
    from backend.core.hooks import trim_history

    Agent(
        history_processors=[trim_history(max_messages=20)],
    )

Note: summarization is handled by SummarizingConversation
(backend.core.conversation.summarizing), not by history_processors, to
avoid blocking the user-facing response.
"""

from collections.abc import Callable

from pydantic_ai.messages import ModelMessage


def trim_history(*, max_messages: int = 20) -> Callable:
    """Keep only the last N messages to prevent context overflow."""

    def _trim(messages: list[ModelMessage]) -> list[ModelMessage]:
        if len(messages) <= max_messages:
            return messages
        return messages[-max_messages:]

    return _trim
