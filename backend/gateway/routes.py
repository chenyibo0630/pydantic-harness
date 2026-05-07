import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.gateway.schemas import ChatError, ChatRequest
from backend.gateway.sse import stream_agent_response

router = APIRouter(prefix="/chat", tags=["chat"])

_DEFAULT_STREAM_TIMEOUT = 60.0  # idle timeout — max gap between SSE events


@router.post("/stream")
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    registry = request.app.state.agent_registry
    memory = request.app.state.memory
    try:
        agent = registry.get(body.agent)
    except KeyError as exc:
        error = ChatError(error="NotFound", code="UNKNOWN_AGENT", detail=str(exc))
        return JSONResponse(status_code=404, content=error.model_dump())

    timeout = getattr(request.app.state, "stream_timeout", _DEFAULT_STREAM_TIMEOUT)
    disconnected = asyncio.Event()

    async def watch_disconnect() -> None:
        while not await request.is_disconnected():
            await asyncio.sleep(1)
        disconnected.set()

    asyncio.create_task(watch_disconnect())

    return StreamingResponse(
        stream_agent_response(
            agent,
            body.message,
            memory=memory,
            conversation_id=body.conversation_id,
            timeout=timeout,
            disconnected=disconnected,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
