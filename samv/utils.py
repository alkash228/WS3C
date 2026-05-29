from __future__ import annotations

import json
import re
import shutil
import http.server
from pathlib import Path
from urllib.parse import quote


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


def file_download_response(
    handler: http.server.SimpleHTTPRequestHandler,
    file_path: Path,
    download_name: str,
    content_type: str = "application/octet-stream",
) -> None:
    """Отдать файл как вложение для скачивания."""
    size = file_path.stat().st_size
    safe_ascii = re.sub(r"[^A-Za-z0-9._-]+", "_", download_name).strip("._") or "download"
    encoded = quote(download_name, safe="")
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header(
        "Content-Disposition",
        f'attachment; filename="{safe_ascii}"; filename*=UTF-8\'\'{encoded}',
    )
    handler.send_header("Content-Length", str(size))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    with file_path.open("rb") as src:
        shutil.copyfileobj(src, handler.wfile)
