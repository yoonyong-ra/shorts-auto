"""Renoise provider: MCP client over streamable HTTP plus image/video generation.

Renoise's public MCP endpoint speaks JSON-RPC (`initialize` + `tools/call`) and
answers with either JSON or an `text/event-stream` frame; the tools themselves
return human-readable text plus optional `structuredContent`.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

# Cloudflare rejects unknown/suspicious agents outright (error 1010), so always send a UA.
USER_AGENT = "shorts-auto/0.1 (+https://github.com/yoonyong-ra/shorts-auto)"
PROTOCOL_VERSION = "2025-06-18"
DEFAULT_MCP_URL = "https://renoise.ai/api/public/mcp"
POLL_SECONDS = 40
TASK_ID_RE = re.compile(r"#(\d+)\s+[a-z]+")
DOWNLOAD_RE = re.compile(r"-o\s+(\S+)\s+'(https?://[^']+)'")
PRESIGNED_URL_RE = re.compile(r"'(https?://[^']+)'")
CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
}


class RenoiseError(RuntimeError):
    """Raised when the Renoise MCP server rejects or fails a request."""


def _content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


class RenoiseClient:
    def __init__(self, base_url: str = DEFAULT_MCP_URL, token: str = "", timeout: float = 120.0) -> None:
        if not token:
            raise RenoiseError(
                "RENOISE_TOKEN이 설정되지 않았습니다. .env에 Renoise MCP 토큰을 추가해주세요."
            )
        self.base_url = base_url or DEFAULT_MCP_URL
        self.token = token
        self.timeout = timeout
        self._req_id = 0
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
                "mcp-protocol-version": PROTOCOL_VERSION,
                "User-Agent": USER_AGENT,
            },
        )
        self._initialized = False

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "RenoiseClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ protocol
    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._http.post(self.base_url, json=payload)
        except httpx.HTTPError as err:  # network/timeout
            raise RenoiseError(f"Renoise MCP 연결 실패: {err}") from err
        if resp.status_code != 200:
            raise RenoiseError(f"Renoise MCP HTTP {resp.status_code}: {resp.text[:300]}")
        return _parse_mcp_body(resp.text)

    def initialize(self) -> None:
        if self._initialized:
            return
        data = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "shorts-auto", "version": "0.1.0"},
            },
        })
        if "error" in data:
            raise RenoiseError(f"Renoise initialize 실패: {data['error']}")
        self._initialized = True

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> tuple[str, dict[str, Any] | None]:
        """Run one MCP tool; returns (text, structuredContent)."""
        self.initialize()
        data = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        if "error" in data:
            raise RenoiseError(f"Renoise {name} 실패: {data['error']}")
        result = data.get("result") or {}
        content = result.get("content") or []
        text = content[0].get("text", "") if content else ""
        structured = result.get("structuredContent")
        if result.get("isError"):
            raise RenoiseError(f"Renoise {name} 오류: {text[:500] or '알 수 없는 오류'}")
        return text, structured

    # -------------------------------------------------------------------- upload
    def upload_material(self, path: Path) -> int:
        """Upload a local file as a Renoise material and return its material id."""
        if not path.exists():
            raise RenoiseError(f"업로드할 파일이 없습니다: {path}")
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)

        _text, structured = self.call_tool("upload_files", {
            "files": [{
                "path": str(path),
                "contentType": _content_type(path),
                "size": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }],
        })
        structured = structured or {}
        existing = structured.get("existing") or []
        if existing:
            return int(existing[0]["materialId"] if isinstance(existing[0], dict) else existing[0])

        command = structured.get("command") or ""
        match = PRESIGNED_URL_RE.search(command)
        if not match:
            raise RenoiseError(f"Renoise 업로드 명령을 해석할 수 없습니다: {command[:200]}")
        put = self._http.put(
            match.group(1),
            content=path.read_bytes(),
            headers={"Content-Type": _content_type(path)},
            timeout=300.0,
        )
        if put.status_code not in (200, 201):
            raise RenoiseError(f"Renoise 업로드 실패 (HTTP {put.status_code})")

        upload_ids = structured.get("uploadIds") or []
        if not upload_ids:
            raise RenoiseError("Renoise 업로드 ID를 받지 못했습니다.")
        _text, registered = self.call_tool("upload_files", {"uploadIds": upload_ids})
        materials = (registered or {}).get("materials") or []
        if not materials:
            raise RenoiseError("Renoise 자료 등록 결과가 비어 있습니다.")
        return int(materials[0]["materialId"])

    # ---------------------------------------------------------------- generation
    def _submit(self, task: dict[str, Any]) -> int:
        payload = dict(task)
        payload.setdefault("idempotencyKey", uuid.uuid4().hex)
        text, structured = self.call_tool("create_tasks", {"tasks": [payload]})
        ids = [int(item["id"]) for item in ((structured or {}).get("tasks") or []) if "id" in item]
        if not ids:
            ids = [int(m) for m in TASK_ID_RE.findall(text)]
        if not ids:
            raise RenoiseError(f"Renoise 작업 ID를 찾지 못했습니다: {text[:300]}")
        return ids[0]

    def _wait(self, task_id: int, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while True:
            text, _structured = self.call_tool("wait_tasks", {"taskIds": [task_id], "waitSeconds": POLL_SECONDS})
            lowered = text.lower()
            if re.search(rf"#{task_id}\b.*failed", lowered):
                raise RenoiseError(f"Renoise 작업 #{task_id} 실패: {text[:400]}")
            if re.search(rf"#{task_id}\b.*(completed|succeeded|done)", lowered):
                return
            if time.monotonic() >= deadline:
                raise RenoiseError(f"Renoise 작업 #{task_id} 시간 초과 ({int(timeout_s)}초): {text[:300]}")

    def _download(self, task_id: int) -> bytes:
        text, structured = self.call_tool("download_results", {"taskIds": [task_id]})
        files = ((structured or {}).get("files") or []) if isinstance(structured, dict) else []
        urls: list[str] = [
            str(f["url"]) for f in files if isinstance(f, dict) and f.get("url")
        ]
        if not urls:
            urls = [m.group(2) for m in DOWNLOAD_RE.finditer(text)]
        if not urls:
            raise RenoiseError(f"Renoise 결과 링크를 찾지 못했습니다: {text[:300]}")
        resp = self._http.get(urls[0], timeout=300.0)
        if resp.status_code != 200:
            raise RenoiseError(f"Renoise 결과 다운로드 실패 (HTTP {resp.status_code})")
        return resp.content

    def generate_image(
        self,
        prompt: str,
        *,
        model: str,
        aspect_ratio: str = "9:16",
        resolution: str = "1k",
        timeout_s: float = 600.0,
        label: str | None = None,
    ) -> bytes:
        task: dict[str, Any] = {
            "kind": "image",
            "model": model,
            "prompt": prompt,
            "ratio": aspect_ratio,
            "resolution": resolution,
        }
        if label:
            task["label"] = label[:48]
        task_id = self._submit(task)
        self._wait(task_id, timeout_s)
        return self._download(task_id)

    def estimate(self, task: dict[str, Any]) -> str:
        text, _structured = self.call_tool("estimate_cost", {"tasks": [task]})
        return text

    def generate_video(
        self,
        prompt: str,
        *,
        model: str,
        start_image: Path | None = None,
        duration: int = 5,
        aspect_ratio: str = "9:16",
        resolution: str = "720p",
        audio: bool = False,
        timeout_s: float = 1800.0,
        label: str | None = None,
    ) -> bytes:
        task: dict[str, Any] = {
            "kind": "video",
            "model": model,
            "prompt": prompt,
            "duration": int(duration),
            "resolution": resolution,
            "ratio": aspect_ratio,
            "audioGeneration": bool(audio),
        }
        if label:
            task["label"] = label[:48]
        if start_image is not None and start_image.exists():
            material_id = self.upload_material(start_image)
            task["references"] = [{"ref": f"material:{material_id}", "role": "reference_image"}]
            # A reference the prompt never names is only attached as a bare token; name it.
            task["prompt"] = f"{prompt} Use @material:{material_id} as the exact first frame."
        task_id = self._submit(task)
        self._wait(task_id, timeout_s)
        return self._download(task_id)


def _parse_mcp_body(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                return json.loads(payload)
    raise RenoiseError(f"Renoise MCP 응답을 해석할 수 없습니다: {raw[:200]}")
