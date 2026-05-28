from __future__ import annotations

import json

from samv.config import API_BASE, INF_API_BASE_FILE, INF_ROOT
from samv.inf.catalog import ensure_inf_dirs


def _norm_base(value: str) -> str:
    base = str(value or "").strip().rstrip("/")
    if not base:
        return ""
    if not (base.startswith("http://") or base.startswith("https://")):
        raise ValueError("api_base must start with http:// or https://")
    return base


def read_api_base() -> dict:
    ensure_inf_dirs()
    default = _norm_base(API_BASE)
    if not INF_API_BASE_FILE.is_file():
        return {"api_base": default, "root": str(INF_ROOT)}
    try:
        raw = json.loads(INF_API_BASE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"api_base": default, "root": str(INF_ROOT)}
    if not isinstance(raw, dict):
        return {"api_base": default, "root": str(INF_ROOT)}
    try:
        base = _norm_base(str(raw.get("api_base", "") or default))
    except Exception:
        base = default
    return {"api_base": base, "root": str(INF_ROOT)}


def write_api_base(value: str) -> dict:
    ensure_inf_dirs()
    base = _norm_base(value)
    payload = {"schema": "web_samv_api_base_v1", "api_base": base}
    INF_API_BASE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"api_base": base, "root": str(INF_ROOT)}
