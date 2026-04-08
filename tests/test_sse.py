import json

from backend.gateway.sse import format_sse


def test_format_sse():
    result = format_sse("text_delta", {"text": "hello"})
    assert result == 'event: text_delta\ndata: {"text": "hello"}\n\n'


def test_format_sse_unicode():
    result = format_sse("text_delta", {"text": "你好"})
    data_line = result.split("\n")[1]
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["text"] == "你好"
