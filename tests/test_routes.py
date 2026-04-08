import json

import pytest


@pytest.mark.anyio
async def test_unknown_agent_returns_404(client):
    resp = await client.post("/chat/stream", json={"message": "hi", "agent": "nope"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "UNKNOWN_AGENT"


@pytest.mark.anyio
async def test_empty_message_returns_422(client):
    resp = await client.post("/chat/stream", json={"message": ""})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_missing_message_returns_422(client):
    resp = await client.post("/chat/stream", json={})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_chat_stream_with_test_model(client):
    """Smoke test: the test model should return a streaming response."""
    resp = await client.post("/chat/stream", json={"message": "hello"})
    # test model should at least start streaming (200) or fail gracefully
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    # Parse SSE events
    events = []
    for line in resp.text.split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line.removeprefix("data: ")))

    # Should have at least one event (text_delta, done, or error)
    assert len(events) >= 1
