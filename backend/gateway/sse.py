import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable

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

from backend.core.conversation import Conversation, ConversationDeps

logger = logging.getLogger(__name__)


def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_HEARTBEAT_INTERVAL = 10.0  # emit tool_progress every N seconds during tool execution
_LOG_FIELD_TRUNCATE = 120  # cap each payload string field in logs


def _compact_payload(data: dict | None) -> dict | None:
    """Truncate long string fields so a single tool_result doesn't flood logs."""
    if not data:
        return data
    out: dict = {}
    for k, v in data.items():
        if isinstance(v, str) and len(v) > _LOG_FIELD_TRUNCATE:
            out[k] = f"{v[:_LOG_FIELD_TRUNCATE]}…<{len(v)} chars>"
        else:
            out[k] = v
    return out


class _EventTracer:
    """Per-request log helper. Tracks only ``last_event`` so the
    idle-timeout WARN can name what immediately preceded the silence.
    Gap timing is left to whoever reads the log — each line already has
    a millisecond timestamp from the logging formatter."""

    def __init__(self, conv_id: str) -> None:
        self.conv_id = conv_id[:8]
        self.last_event = "start"

    def log(self, name: str, data: dict | None = None, *, level: int = logging.INFO) -> None:
        self.last_event = name
        payload = ""
        if data:
            payload = f" | {_compact_payload(data)}"
        logger.log(level, "SSE conv=%s event=%s%s", self.conv_id, name, payload)


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
    memory: Conversation,
    build_system_prompt: Callable[[], str],
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

    `build_system_prompt` is called once per **new** conversation to capture
    the current on-disk prompt; subsequent turns within the same conv_id
    reuse the locked snapshot from memory.

    Logging: landmark events (start / tool_call / tool_result / end / error
    / timeout) log at INFO; per-token ``text_delta`` and silent ``tick``
    log at DEBUG. Set ``server.log_level: DEBUG`` in config.yaml to see
    every byte / tick. Log timestamps are millisecond-precision — derive
    inter-event gaps from those if needed.
    """
    loop = asyncio.get_running_loop()
    conv_id = conversation_id or uuid.uuid4().hex
    tracer = _EventTracer(conv_id)
    history: list[ModelMessage] = await memory.get(conv_id) or []

    # Lock-or-load the per-session system prompt snapshot. First turn of a
    # new conversation reads from disk and freezes; later turns get the
    # cached copy so the system message is byte-stable.
    snapshot = await memory.get_system_prompt(conv_id)
    if snapshot is None:
        snapshot = build_system_prompt()
        await memory.put_system_prompt(conv_id, snapshot)

    tracer.log(
        "message_start",
        {
            "conversation_id": conv_id,
            "history_len": len(history),
            "user_message_len": len(message),
            "idle_timeout": timeout,
        },
    )
    yield format_sse("message_start", {"conversation_id": conv_id})

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
                deps=ConversationDeps(
                    store=memory,
                    conversation_id=conv_id,
                    system_prompt=snapshot,
                ),
            ).__aiter__()

            async for kind, event in _next_with_heartbeat(
                events_iter, interval=_HEARTBEAT_INTERVAL
            ):
                if disconnected and disconnected.is_set():
                    tracer.log("client_disconnect")
                    return

                if kind == "tick":
                    # Only emit (and reset deadline) while a tool is running.
                    # Outside tool execution, ticks are silent — silent gaps
                    # in normal LLM streaming should still count toward idle.
                    if in_tool_call and tool_started_at is not None:
                        deadline.reschedule(loop.time() + timeout)
                        elapsed = loop.time() - tool_started_at
                        progress = {
                            "tool_name": current_tool_name,
                            "tool_call_id": current_tool_call_id,
                            "elapsed": round(elapsed, 1),
                        }
                        tracer.log("tool_progress", progress, level=logging.DEBUG)
                        yield format_sse("tool_progress", progress)
                    else:
                        # No tool running — tick is silent, but log it at
                        # DEBUG so the timeline shows the idle-timer ticking.
                        logger.debug(
                            "SSE conv=%s tick (silent, no in-flight tool, "
                            "last_event=%s)",
                            tracer.conv_id, tracer.last_event,
                        )
                    continue

                # Real upstream event — reset idle deadline
                deadline.reschedule(loop.time() + timeout)

                # Text part start (may contain first character)
                if isinstance(event, PartStartEvent) and isinstance(
                    event.part, TextPart
                ):
                    if event.part.content:
                        payload = {"text": event.part.content}
                        tracer.log("text_delta", payload, level=logging.DEBUG)
                        yield format_sse("text_delta", payload)

                # Text delta
                elif isinstance(event, PartDeltaEvent) and isinstance(
                    event.delta, TextPartDelta
                ):
                    payload = {"text": event.delta.content_delta}
                    tracer.log("text_delta", payload, level=logging.DEBUG)
                    yield format_sse("text_delta", payload)

                # Tool call start — heartbeats will extend the deadline while
                # the subprocess runs (see "tick" branch above).
                elif isinstance(event, FunctionToolCallEvent):
                    in_tool_call = True
                    current_tool_name = event.part.tool_name
                    current_tool_call_id = event.tool_call_id
                    tool_started_at = loop.time()
                    payload = {
                        "tool_name": current_tool_name,
                        "tool_call_id": current_tool_call_id,
                        "args": getattr(event.part, "args", None),
                    }
                    tracer.log("tool_call", payload)
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
                    payload = {
                        "tool_name": event.result.tool_name,
                        "tool_call_id": event.tool_call_id,
                        "content": content,
                    }
                    tracer.log("tool_result", payload)
                    yield format_sse("tool_result", payload)

                # Final result
                elif isinstance(event, AgentRunResultEvent):
                    result_event = event

            # Save history and emit done
            if result_event is not None:
                await memory.set(conv_id, result_event.result.all_messages())
                usage = result_event.result.usage()
                # ``cache_read_tokens`` is a subset of ``input_tokens`` —
                # pydantic-ai's usage already includes cached tokens in the
                # input count. Surfacing it separately lets the UI show
                # how much of the input came free from Anthropic's cache.
                end_payload = {
                    "conversation_id": conv_id,
                    "usage": {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "total_tokens": usage.total_tokens,
                        "cache_read_tokens": usage.cache_read_tokens,
                    },
                }
                tracer.log("message_end", end_payload)
                yield format_sse("message_end", end_payload)

    except TimeoutError:
        logger.warning(
            "SSE conv=%s idle_timeout=%.1fs exceeded after last_event=%s "
            "in_tool_call=%s tool=%s",
            tracer.conv_id, timeout, tracer.last_event,
            in_tool_call, current_tool_name,
        )
        yield format_sse(
            "error",
            {
                "error": "IdleTimeout",
                "message": f"No stream activity for {timeout:.0f}s — aborted.",
            },
        )
    except Exception as exc:
        logger.exception(
            "SSE conv=%s stream error after last_event=%s: %s",
            tracer.conv_id, tracer.last_event, exc,
        )
        yield format_sse(
            "error", {"error": type(exc).__name__, "message": str(exc)}
        )
