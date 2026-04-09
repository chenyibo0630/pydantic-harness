"""ask_user tool — lets the agent request clarification from the user."""

import json


def ask_user(
    question: str,
    clarification_type: str = "missing_info",
    options: list[str] | None = None,
    context: str | None = None,
) -> str:
    """Ask the user a clarifying question before proceeding.

    Use this when:
    - The request is ambiguous and could be interpreted multiple ways
    - A destructive operation (delete, overwrite) needs confirmation
    - You need specific details (file path, language, scope) to proceed correctly
    - You notice an issue or improvement opportunity and want to suggest it

    Do NOT guess — ask first.
    After calling this tool, STOP and wait for user response. Do NOT call other tools.

    Args:
        question: The question to ask the user. Be specific and concise.
        clarification_type: One of: missing_info, ambiguous_requirement, approach_choice, risk_confirmation, suggestion.
        options: Optional list of choices for the user to pick from.
        context: Optional background explaining why you need this clarification.
    """
    return json.dumps(
        {
            "question": question,
            "type": clarification_type,
            "options": options,
            "context": context,
        },
        ensure_ascii=False,
    )
