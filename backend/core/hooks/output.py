"""output_validator hooks — validate or transform model output.

Usage in agent.py:
    from backend.core.hooks import log_output

    agent = Agent(...)
    agent.output_validator(log_output)
"""

import logging

from pydantic_ai.tools import RunContext

logger = logging.getLogger(__name__)


def log_output(ctx: RunContext, output: str) -> str:
    """Log model output for auditing. Returns output unchanged."""
    logger.info("Agent output (%d chars): %.200s", len(output), output)
    return output
