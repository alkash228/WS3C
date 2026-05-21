from __future__ import annotations

import json
import re
import http.server


def safe_name(raw: str) -> str:

    """Безопасное имя папки без мусора."""
    value = (raw or "").strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^A-Za-z0-9_-]", "", value)
    return value.strip("._-")


def json_response(handler: http.server.SimpleHTTPRequestHandler, payload: dict, code: int = 200) -> None:

    """Ответ API в JSON."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
