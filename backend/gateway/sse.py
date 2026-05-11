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
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.run import AgentRunResultEvent

from backend.core.memory import Memory, MemoryDeps

logger = logging.getLogger(__name__)


def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_HEARTBEAT_INTERVAL = 10.0  # emit tool_progress every N seconds during tool execution


async def _next_with_heartbeat(
    iterator,
    *,
    interval: float,
):
    """Yield ('event', value) for each upstream item, ('tick', None) on idle ticks.

    Uses asyncio.wait (not wait_for) so the underlying __anext__ task is not
    cancelled when the timer fires — it keeps running across heartbeat ticks
    until the upstream actually produces a value.
    """
    pending_task: asyncio.Task | None = None
    try:
        while True:
            if pending_task is None:
                pending_task = asyncio.create_task(iterator.__anext__())

            done, _ = await asyncio.wait({pending_task}, timeout=interval)
            if pending_task not in done:
                yield ("tick", None)
                continue

            try:
                value = pending_task.result()
            except StopAsyncIteration:
                pending_task = None
                return
            pending_task = None
            yield ("event", value)
    finally:
        if pending_task is not None and not pending_task.done():
            pending_task.cancel()


async def stream_agent_response(
    agent: Agent,
    message: str,
    *,
    memory: Memory,
    conversation_id: str | None = None,
    timeout: float = 60.0,
    disconnected: asyncio.Event | None = None,
) -> AsyncIterator[str]:
    """Stream an agent response over SSE.

    `timeout` is an *idle* timeout — the max gap (seconds) between activity
    signals before the stream is aborted. A tool_progress heartbeat during
    tool execution counts as activity, so legitimate long-running tools never
    trip the timeout. A hung LLM call (no events, no in-flight tool) is killed
    after `timeout` seconds. Sandbox subprocesses are bounded server-side by
    `ExecuteCommandRequest.timeout` (≤300s), so an unresponsive tool is also
    eventually surfaced as an error event.
    """
    conv_id = conversation_id or uuid.uuid4().hex
    history: list[ModelMessage] = await memory.get(conv_id) or []

    yield format_sse("message_start", {"conversation_id": conv_id})

    loop = asyncio.get_running_loop()
    in_tool_call = False
    current_tool_name: str | None = None
    current_tool_call_id: str | None = None
    tool_started_at: float | None = None
    result_event: AgentRunResultEvent | None = None

    try:
        async with asyncio.timeout(timeout) as deadline:
            events_iter = agent.run_stream_events(
                message,
                message_history=history,
                deps=MemoryDeps(memory=memory, conversation_id=conv_id),
            ).__aiter__()

            async for kind, event in _next_with_heartbeat(
                events_iter, interval=_HEARTBEAT_INTERVAL
            ):
                if disconnected and disconnected.is_set():
                    logger.info("Client disconnected, aborting stream")
                    return

                if kind == "tick":
                    # Only emit (and reset deadline) while a tool is running.
                    # Outside tool execution, ticks are silent — silent gaps
                    # in normal LLM streaming should still count toward idle.
                    if in_tool_call and tool_started_at is not None:
                        deadline.reschedule(loop.time() + timeout)
                        elapsed = loop.time() - tool_started_at
                        yield format_sse(
                            "tool_progress",
                            {
                                "tool_name": current_tool_name,
                                "tool_call_id": current_tool_call_id,
                                "elapsed": round(elapsed, 1),
                            },
                        )
                    continue

                # Real upstream event — reset idle deadline
                deadline.reschedule(loop.time() + timeout)

                # Text part start (may contain first character)
                if isinstance(event, PartStartEvent) and isinstance(
                    event.part, TextPart
                ):
                    if event.part.content:
                        yield format_sse(
                            "text_delta", {"text": event.part.content}
                        )

                # Text delta
                elif isinstance(event, PartDeltaEvent) and isinstance(
                    event.delta, TextPartDelta
                ):
                    yield format_sse(
                        "text_delta", {"text": event.delta.content_delta}
                    )

                # Tool call start — heartbeats will extend the deadline while
                # the subprocess runs (see "tick" branch above).
                elif isinstance(event, FunctionToolCallEvent):
                    in_tool_call = True
                    current_tool_name = event.part.tool_name
                    current_tool_call_id = event.tool_call_id
                    tool_started_at = loop.time()
                    yield format_sse(
                        "tool_call",
                        {
                            "tool_name": current_tool_name,
                            "tool_call_id": current_tool_call_id,
                        },
                    )

                # Tool result
                elif isinstance(event, FunctionToolResultEvent):
                    in_tool_call = False
                    tool_started_at = None
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
            "error",
            {
                "error": "IdleTimeout",
                "message": f"No stream activity for {timeout:.0f}s — aborted.",
            },
        )
    except Exception as exc:
        logger.exception("Stream error: %s", exc)
        yield format_sse(
            "error", {"error": type(exc).__name__, "message": str(exc)}
        )
