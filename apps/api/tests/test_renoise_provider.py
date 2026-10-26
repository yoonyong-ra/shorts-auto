from __future__ import annotations

import json

import httpx
import pytest

from app.production.renoise_client import RenoiseClient, RenoiseError, _parse_mcp_body
from app.production.service import _resolve_model_provider


def test_resolve_model_provider_splits_prefix():
    assert _resolve_model_provider("renoise:nano-banana-pro", "higgsfield") == ("renoise", "nano-banana-pro")
    assert _resolve_model_provider("higgsfield:nano_banana_pro", "renoise") == ("higgsfield", "nano_banana_pro")
    assert _resolve_model_provider("Renoise:seedance-2.5-byteplus", "higgsfield") == ("renoise", "seedance-2.5-byteplus")


def test_resolve_model_provider_uses_default_for_plain_model():
    assert _resolve_model_provider("nano_banana_pro", "higgsfield") == ("higgsfield", "nano_banana_pro")
    assert _resolve_model_provider("nano-banana-pro", "renoise") == ("renoise", "nano-banana-pro")
    assert _resolve_model_provider("nano_banana_pro", "something-else") == ("higgsfield", "nano_banana_pro")


def test_resolve_model_provider_ignores_empty_prefix_value():
    assert _resolve_model_provider("renoise:", "higgsfield") == ("higgsfield", "renoise:")


def test_parse_mcp_body_handles_json_and_sse():
    assert _parse_mcp_body('{"result": {"ok": true}}') == {"result": {"ok": True}}
    sse = 'event: message\ndata: {"result": {"ok": true}}\n\n'
    assert _parse_mcp_body(sse) == {"result": {"ok": True}}
    with pytest.raises(RenoiseError):
        _parse_mcp_body("not json")


def test_client_requires_token():
    with pytest.raises(RenoiseError):
        RenoiseClient("https://example.invalid/mcp", "")


def _sse(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        text="event: message\ndata: " + json.dumps(payload) + "\n\n",
        headers={"content-type": "text/event-stream"},
    )


def _tool_result(rid: int, text: str) -> httpx.Response:
    return _sse({
        "jsonrpc": "2.0",
        "id": rid,
        "result": {"content": [{"type": "text", "text": text}]},
    })


def test_generate_image_full_flow(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            seen.append("download")
            return httpx.Response(200, content=b"PNGDATA")
        body = json.loads(request.content)
        method = body.get("method")
        seen.append(method if method != "tools/call" else body["params"]["name"])
        if method == "initialize":
            return _sse({"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}})
        name = body["params"]["name"]
        if name == "create_tasks":
            return _tool_result(body["id"], "Submitted 1 task(s), about 8 credits:\n#366249 image nano-banana-2: running")
        if name == "wait_tasks":
            return _tool_result(body["id"], "1/1 finished.\n#366249 image nano-banana-2: completed")
        if name == "download_results":
            return _tool_result(
                body["id"],
                "Run:\ncurl -fsSL -o renoise-366249.png 'https://cdn.example/r.png?auth=1'\nFiles: renoise-366249.png",
            )
        raise AssertionError(f"unexpected tool {name}")

    client = RenoiseClient("https://example.invalid/mcp", "token")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        data = client.generate_image("clay figurine", model="nano-banana-2")
    finally:
        client.close()

    assert data == b"PNGDATA"
    assert seen == ["initialize", "create_tasks", "wait_tasks", "download_results", "download"]


def test_failed_task_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            return _sse({"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}})
        name = body["params"]["name"]
        if name == "create_tasks":
            return _tool_result(body["id"], "Submitted 1 task(s):\n#42 video kling: running")
        if name == "wait_tasks":
            return _tool_result(body["id"], "#42 video kling: failed (not_enough_credits)")
        raise AssertionError(name)

    client = RenoiseClient("https://example.invalid/mcp", "token")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RenoiseError, match="failed"):
            client.generate_image("prompt", model="nano-banana-2", timeout_s=5)
    finally:
        client.close()


def test_upload_material_registers_after_put():
    puts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            puts.append(str(request.url))
            return httpx.Response(200)
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            return _sse({"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}})
        args = body["params"]["arguments"]
        if "files" in args:
            payload = {
                "existing": [],
                "command": "curl -fsS -X PUT --data-binary @'f.png' 'https://storage.example/put?sig=1'",
                "uploadIds": ["uid-1"],
            }
            return _sse({"jsonrpc": "2.0", "id": body["id"], "result": {
                "content": [{"type": "text", "text": "Run: curl ..."}],
                "structuredContent": payload,
            }})
        if "uploadIds" in args:
            return _sse({"jsonrpc": "2.0", "id": body["id"], "result": {
                "content": [{"type": "text", "text": "Registered"}],
                "structuredContent": {"materials": [{"materialId": 347136, "kind": "image"}]},
            }})
        raise AssertionError(args)

    client = RenoiseClient("https://example.invalid/mcp", "token")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        from pathlib import Path
        img = Path(__file__).parent / "_tmp_upload.png"
        img.write_bytes(b"\x89PNG\r\n")
        try:
            material_id = client.upload_material(img)
        finally:
            img.unlink(missing_ok=True)
    finally:
        client.close()

    assert material_id == 347136
    assert puts == ["https://storage.example/put?sig=1"]
