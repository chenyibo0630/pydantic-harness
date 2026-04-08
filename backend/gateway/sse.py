import asyncio
import json
import logging
from collections.abc import AsyncIterator

from pydantic_ai import Agent

logger = logging.getLogger(__name__)


def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_agent_response(
    agent: Agent,
    message: str,
    *,
    timeout: float = 120.0,
    disconnected: asyncio.Event | None = None,
) -> AsyncIterator[str]:
    try:
        async with asyncio.timeout(timeout):
            async with agent.run_stream(message) as stream:
                async for delta in stream.stream_text(delta=True):
                    if disconnected and disconnected.is_set():
                        logger.info("Client disconnected, aborting stream")
                        return
                    yield format_sse("text_delta", {"text": delta})

                usage = stream.usage()
                yield format_sse("done", {
                    "usage": {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "total_tokens": usage.total_tokens,
                    },
                })
    except TimeoutError:
        yield format_sse("error", {"error": "Timeout", "message": "Stream timed out"})
    except Exception as exc:
        logger.exception("Stream error: %s", exc)
        yield format_sse("error", {
            "error": type(exc).__name__,
            "message": str(exc),
        })
