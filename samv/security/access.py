from __future__ import annotations

import json
from typing import Any

from samv.config import INF_ACCESS_ROLES_FILE
from samv.inf.catalog import ensure_inf_dirs

LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost"}


def _norm_ip(ip: str) -> str:
    return str(ip or "").strip().lower()


def _norm_list(raw: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for row in raw:
        ip = _norm_ip(str(row or ""))
        if ip and ip not in out:
            out.append(ip)
    return out


def _default_rules() -> dict[str, Any]:
    return {
        "schema": "web_samv_access_roles_v1",
        "admins": [],
        "users": [],
    }


def read_access_roles() -> dict[str, Any]:
    ensure_inf_dirs()
    if not INF_ACCESS_ROLES_FILE.is_file():
        payload = _default_rules()
        INF_ACCESS_ROLES_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload
    try:
        raw = json.loads(INF_ACCESS_ROLES_FILE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "schema": str(raw.get("schema", "web_samv_access_roles_v1") or "web_samv_access_roles_v1"),
        "admins": _norm_list(raw.get("admins", [])),
        "users": _norm_list(raw.get("users", [])),
    }


def resolve_role_by_ip(client_ip: str) -> str:
    ip = _norm_ip(client_ip)
    if ip in LOCALHOST_IPS:
        return "admin"
    rules = read_access_roles()
    admins = set(_norm_list(rules.get("admins", [])))
    users = set(_norm_list(rules.get("users", [])))
    if ip in admins:
        return "admin"
    if ip in users:
        return "user"
    return "guest"

