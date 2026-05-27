from __future__ import annotations

import json
import re

from samv.config import INF_API_PROMPT_FILE, INF_DEFAULT_API_PROMPT, INF_ROOT
from samv.inf.catalog import ensure_inf_dirs


def _norm_prompt(raw: str) -> str:
    s = re.sub(r"\s+", " ", str(raw or "").strip())
    return s[:2000].strip()


def _ensure_file() -> None:
    ensure_inf_dirs()
    if INF_API_PROMPT_FILE.is_file():
        return
    INF_API_PROMPT_FILE.write_text(
        json.dumps(
            {"schema": "web_samv_api_prompt_v1", "prompt": INF_DEFAULT_API_PROMPT},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def read_api_prompt() -> dict:
    _ensure_file()
    try:
        raw = json.loads(INF_API_PROMPT_FILE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    prompt = _norm_prompt(str(raw.get("prompt", "") or ""))
    if not prompt:
        prompt = _norm_prompt(INF_DEFAULT_API_PROMPT)
    return {"schema": "web_samv_api_prompt_v1", "prompt": prompt, "root": str(INF_ROOT)}


def get_api_prompt() -> str:
    return str(read_api_prompt().get("prompt", "") or "").strip()


def write_api_prompt(prompt: str) -> dict:
    text = _norm_prompt(prompt)
    if not text:
        raise ValueError("Промпт API не может быть пустым")
    INF_API_PROMPT_FILE.write_text(
        json.dumps({"schema": "web_samv_api_prompt_v1", "prompt": text}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return read_api_prompt()
