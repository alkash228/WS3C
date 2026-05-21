from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO


@dataclass
class MultipartItem:
    name: str
    filename: str = ""
    value: bytes = b""

    @property
    def file(self) -> BinaryIO:
        """Байты поля как файл."""
        return BytesIO(self.value)


class MultipartForm:
    def __init__(self, items: dict[str, MultipartItem]) -> None:
        """Форма из полей multipart."""
        self._items = items

    def __contains__(self, key: object) -> bool:
        """Есть ли такое поле."""
        return str(key) in self._items

    def __getitem__(self, key: str) -> MultipartItem:
        """Поле по имени."""
        return self._items[key]

    def getfirst(self, key: str, default: str | None = None) -> str | None:
        """Текстовое значение поля."""
        item = self._items.get(key)
        if item is None:
            return default
        if item.filename:
            return default
        if not item.value:
            return default
        return item.value.decode("utf-8", errors="replace")


def _parse_content_disposition(header: str) -> tuple[str, str]:

    """Достаем name и filename из заголовка."""
    name = ""
    filename = ""
    m_name = re.search(r'name="([^"]*)"', header, re.I)
    if m_name:
        name = m_name.group(1)
    m_fn = re.search(r'filename="([^"]*)"', header, re.I)
    if m_fn:
        filename = m_fn.group(1)
    return name, filename


def parse_multipart(body: bytes, content_type: str) -> MultipartForm:

    """Разбираем multipart тело."""
    m = re.search(r'boundary=(?:"([^"]+)"|([^\s;]+))', content_type, re.I)
    if not m:
        raise ValueError("multipart boundary not found")
    boundary = (m.group(1) or m.group(2) or "").encode("latin-1")
    delimiter = b"--" + boundary
    items: dict[str, MultipartItem] = {}

    for raw in body.split(delimiter):
        chunk = raw.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        if chunk.endswith(b"--"):
            chunk = chunk[:-2].rstrip(b"\r\n")
        if not chunk:
            continue
        header_blob, _, content = chunk.partition(b"\r\n\r\n")
        if not header_blob:
            continue
        headers = header_blob.decode("utf-8", errors="replace")
        cd = ""
        for line in headers.splitlines():
            if line.lower().startswith("content-disposition:"):
                cd = line.split(":", 1)[1].strip()
                break
        if not cd:
            continue
        name, filename = _parse_content_disposition(cd)
        if not name:
            continue
        value = content
        if value.endswith(b"\r\n"):
            value = value[:-2]
        items[name] = MultipartItem(name=name, filename=filename, value=value)
    return MultipartForm(items)


def field_storage_from_request(rfile, headers) -> MultipartForm:

    """Форма из POST запроса."""
    length = int(headers.get("Content-Length", 0) or 0)
    body = rfile.read(length) if length > 0 else rfile.read()
    ctype = str(headers.get("Content-Type", "") or "")
    return parse_multipart(body, ctype)
