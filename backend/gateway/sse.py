import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.run import AgentRunResultEvent

from backend.core.memory import Memory

logger = logging.getLogger(__name__)


def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_agent_response(
    agent: Agent,
    message: str,
    *,
    memory: Memory,
    conversation_id: str | None = None,
    timeout: float = 120.0,
    disconnected: asyncio.Event | None = None,
) -> AsyncIterator[str]:
    conv_id = conversation_id or uuid.uuid4().hex
    history: list[ModelMessage] = await memory.get(conv_id) or []

    yield format_sse("message_start", {"conversation_id": conv_id})

    try:
        async with asyncio.timeout(timeout):
            result_event: AgentRunResultEvent | None = None

            async for event in agent.run_stream_events(
                message, message_history=history
            ):
                if disconnected and disconnected.is_set():
                    logger.info("Client disconnected, aborting stream")
                    return

                # Text delta
                if isinstance(event, PartDeltaEvent) and isinstance(
                    event.delta, TextPartDelta
                ):
                    yield format_sse(
                        "text_delta", {"text": event.delta.content_delta}
                    )

                # Tool call start
                elif isinstance(event, FunctionToolCallEvent):
                    yield format_sse(
                        "tool_call",
                        {
                            "tool_name": event.part.tool_name,
                            "tool_call_id": event.tool_call_id,
                        },
                    )

                # Tool result
                elif isinstance(event, FunctionToolResultEvent):
                    content = ""
                    if isinstance(event.result, ToolReturnPart):
                        content = (
                            event.result.content
                            if isinstance(event.result.content, str)
                            else str(event.result.content)
                        )
                    yield format_sse(
                        "tool_result",
                        {
                            "tool_name": event.result.tool_name,
                            "tool_call_id": event.tool_call_id,
                            "content": content,
                        },
                    )

                # Final result
                elif isinstance(event, AgentRunResultEvent):
                    result_event = event

            # Save history and emit done
            if result_event is not None:
                await memory.set(conv_id, result_event.result.all_messages())
                usage = result_event.result.usage()
                yield format_sse(
                    "message_end",
                    {
                        "conversation_id": conv_id,
                        "usage": {
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                            "total_tokens": usage.total_tokens,
                        },
                    },
                )

    except TimeoutError:
        yield format_sse(
            "error", {"error": "Timeout", "message": "Stream timed out"}
        )
    except Exception as exc:
        logger.exception("Stream error: %s", exc)
        yield format_sse(
            "error", {"error": type(exc).__name__, "message": str(exc)}
        )
