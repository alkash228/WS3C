from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from samv.config import INF_DEFAULT_SCENARIOS, INF_ROOT, INF_SCENARIOS_FILE
from samv.inf.catalog import ensure_inf_dirs
from samv.masks.core import label_segments, label_slug


def _norm_title(raw: str) -> str:
    s = re.sub(r"\s+", " ", str(raw or "").strip())
    return s[:120].strip()


def _norm_prompt(raw: str) -> str:
    s = re.sub(r"\s+", " ", str(raw or "").strip())
    return s[:500].strip()


def _row_enabled(row: dict) -> bool:
    if "enabled" in row:
        return bool(row.get("enabled"))
    return bool(row.get("active", False))


def _default_payload() -> dict:
    return {
        "schema": "web_samv_scenarios_v1",
        "scenarios": [dict(x) for x in INF_DEFAULT_SCENARIOS],
    }


def _ensure_file() -> None:
    ensure_inf_dirs()
    if INF_SCENARIOS_FILE.is_file():
        return
    INF_SCENARIOS_FILE.write_text(
        json.dumps(_default_payload(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalize_row(row: dict) -> dict | None:
    sid = str(row.get("id", "") or "").strip() or uuid.uuid4().hex
    title = _norm_title(str(row.get("title", "") or ""))
    prompt = _norm_prompt(str(row.get("prompt", "") or ""))
    if not title or not prompt:
        return None
    return {
        "id": sid,
        "title": title,
        "prompt": prompt,
        "enabled": _row_enabled(row),
    }


def _read_raw() -> dict:
    _ensure_file()
    try:
        raw = json.loads(INF_SCENARIOS_FILE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    scenarios_raw = raw.get("scenarios")
    if not isinstance(scenarios_raw, list) or not scenarios_raw:
        return _default_payload()
    out_rows: list[dict] = []
    seen_ids: set[str] = set()
    for row in scenarios_raw:
        if not isinstance(row, dict):
            continue
        norm = _normalize_row(row)
        if norm is None:
            continue
        sid = str(norm["id"])
        if sid in seen_ids:
            norm["id"] = uuid.uuid4().hex
            sid = str(norm["id"])
        seen_ids.add(sid)
        out_rows.append(norm)
    if not out_rows:
        return _default_payload()
    if not any(bool(x.get("enabled")) for x in out_rows):
        out_rows[0]["enabled"] = True
    return {"schema": "web_samv_scenarios_v1", "scenarios": out_rows}


def _write_raw(rows: list[dict]) -> None:
    _ensure_file()
    norm_rows: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        norm = _normalize_row(row)
        if norm is not None:
            norm_rows.append(norm)
    if not norm_rows:
        norm_rows = [_normalize_row(dict(x)) for x in INF_DEFAULT_SCENARIOS]
        norm_rows = [x for x in norm_rows if x is not None]
    if norm_rows and not any(bool(x.get("enabled")) for x in norm_rows):
        norm_rows[0]["enabled"] = True
    INF_SCENARIOS_FILE.write_text(
        json.dumps({"schema": "web_samv_scenarios_v1", "scenarios": norm_rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def scenarios_list() -> dict:
    """Список сценариев для API."""
    raw = _read_raw()
    rows = raw.get("scenarios")
    if not isinstance(rows, list):
        rows = []
    enabled_ids = [str(x.get("id", "")) for x in rows if isinstance(x, dict) and x.get("enabled")]
    active_id = enabled_ids[0] if len(enabled_ids) == 1 else ""
    return {
        "schema": "web_samv_scenarios_v1",
        "scenarios": rows,
        "enabled_ids": enabled_ids,
        "active_id": active_id,
        "root": str(INF_ROOT),
    }


def scenario_violation_label(scenario: dict) -> str:
    """Текст нарушения для отчёта (из сценария)."""
    custom = _norm_title(str(scenario.get("violation_label", "") or ""))
    if custom:
        return custom
    title = _norm_title(str(scenario.get("title", "") or ""))
    if title:
        return title
    prompt = _norm_prompt(str(scenario.get("prompt", "") or ""))
    if not prompt:
        return "Нарушение требований ТБ"
    try:
        _main, linked = scenario_to_analyzer(prompt)
    except Exception:
        linked = []
    if linked:
        last = str(linked[-1] or "").strip()
        if last:
            return f"Отсутствует «{last}» (СИЗ)"
    return "Нарушение требований ТБ"


def scenario_to_analyzer(prompt: str) -> tuple[str, list[str]]:
    """Цепочка промпта -> main + linked для анализатора."""
    segments = label_segments(prompt)
    if not segments:
        raise ValueError("Пустой промпт сценария")
    main = segments[0]
    linked = [x for x in segments[1:] if x and x != main]
    return main, linked


def get_enabled_scenarios() -> list[dict]:
    """Включённые сценарии в порядке списка INF."""
    data = scenarios_list()
    enabled_ids = set(str(x) for x in (data.get("enabled_ids") or []))
    out: list[dict] = []
    for row in data.get("scenarios") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "")) in enabled_ids:
            out.append(dict(row))
    return out


def get_active_scenario() -> dict | None:
    """Первый включённый сценарий (совместимость)."""
    rows = get_enabled_scenarios()
    return rows[0] if rows else None


def scenario_folder_suffix(scenario: dict) -> str:
    slug = label_slug(str(scenario.get("title", "") or ""))
    if slug:
        return slug[:40]
    sid = str(scenario.get("id", "") or "").strip()
    return sid[:12] if sid else "scenario"


def add_scenario(title: str, prompt: str) -> dict:
    title = _norm_title(title)
    prompt = _norm_prompt(prompt)
    if not title or not prompt:
        raise ValueError("Need title and prompt")
    scenario_to_analyzer(prompt)
    raw = _read_raw()
    rows = list(raw.get("scenarios") or [])
    sid = uuid.uuid4().hex
    enabled = not any(bool(x.get("enabled")) for x in rows if isinstance(x, dict))
    rows.append({"id": sid, "title": title, "prompt": prompt, "enabled": enabled})
    _write_raw(rows)
    return scenarios_list()


def delete_scenario(scenario_id: str) -> dict:
    sid = str(scenario_id or "").strip()
    if not sid:
        raise ValueError("Need id")
    raw = _read_raw()
    rows = [x for x in (raw.get("scenarios") or []) if isinstance(x, dict) and str(x.get("id", "")) != sid]
    if not rows:
        _write_raw([dict(x) for x in INF_DEFAULT_SCENARIOS])
        return scenarios_list()
    _write_raw(rows)
    return scenarios_list()


def set_scenario_enabled(scenario_id: str, enabled: bool) -> dict:
    sid = str(scenario_id or "").strip()
    if not sid:
        raise ValueError("Need id")
    raw = _read_raw()
    rows = raw.get("scenarios") or []
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "")) == sid:
            row["enabled"] = bool(enabled)
            found = True
    if not found:
        raise ValueError("Scenario not found")
    if enabled and not any(bool(x.get("enabled")) for x in rows if isinstance(x, dict)):
        pass
    elif not any(bool(x.get("enabled")) for x in rows if isinstance(x, dict)):
        for row in rows:
            if isinstance(row, dict):
                row["enabled"] = True
                break
    _write_raw(rows)
    return scenarios_list()


def set_active_scenario(scenario_id: str) -> dict:
    """Включить один сценарий, остальные выключить (устаревший API)."""
    sid = str(scenario_id or "").strip()
    if not sid:
        raise ValueError("Need id")
    raw = _read_raw()
    rows = raw.get("scenarios") or []
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        row["enabled"] = str(row.get("id", "")) == sid
        if row["enabled"]:
            found = True
    if not found:
        raise ValueError("Scenario not found")
    _write_raw(rows)
    return scenarios_list()


def write_run_meta(folder_path: Path, api_prompt: str) -> None:
    """Корневой meta.json: промпт SAM API."""
    meta = {
        "schema": "web_samv_folder_meta_v1",
        "api_prompt": _norm_prompt(api_prompt),
    }
    (folder_path / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def write_scenario_meta(
    folder_path: Path,
    scenario: dict,
    main: str,
    linked: list[str],
    *,
    scenario_subdir: bool,
) -> None:
    """Мета сценария анализатора (корень или analysis/by_scenario/id/)."""
    sid = str(scenario.get("id", "") or "").strip()
    chain = _norm_prompt(str(scenario.get("prompt", "") or ""))
    meta = {
        "schema": "web_samv_scenario_meta_v1",
        "scenario_id": sid,
        "scenario_title": str(scenario.get("title", "") or ""),
        "analyzer_chain": chain,
        "scenario_prompt": chain,
        "violation_label": scenario_violation_label(scenario),
        "analyzer_main": main,
        "analyzer_linked": linked,
    }
    if scenario_subdir and sid:
        dest = folder_path / "analysis" / "by_scenario" / sid
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "meta.json"
    else:
        root = folder_path / "meta.json"
        if root.is_file():
            try:
                base = json.loads(root.read_text(encoding="utf-8"))
                if isinstance(base, dict):
                    meta = {**base, **meta}
            except Exception:
                pass
        path = root
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def read_folder_meta(folder_path: Path) -> dict | None:
    path = folder_path / "meta.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def read_scenario_meta(folder_path: Path, scenario_id: str | None) -> dict | None:
    sid = str(scenario_id or "").strip()
    if sid:
        path = folder_path / "analysis" / "by_scenario" / sid / "meta.json"
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return raw if isinstance(raw, dict) else None
            except Exception:
                return None
    return read_folder_meta(folder_path)


def analyzer_params_for_folder(
    folder_path: Path,
    payload: dict | None = None,
    scenario_id: str | None = None,
) -> tuple[str, list[str]]:
    """main + linked из meta сценария."""
    meta = read_scenario_meta(folder_path, scenario_id)
    if meta:
        main = str(meta.get("analyzer_main", "") or "").strip()
        linked_raw = meta.get("analyzer_linked", [])
        linked: list[str] = []
        if isinstance(linked_raw, list):
            for x in linked_raw:
                s = str(x or "").strip()
                if s and s != main and s not in linked:
                    linked.append(s)
        if main:
            return main, linked
    chain = ""
    if meta:
        chain = str(meta.get("analyzer_chain", "") or meta.get("scenario_prompt", "") or "").strip()
    if chain:
        return scenario_to_analyzer(chain)
    raise ValueError("Не задан сценарий анализатора: нет meta.json с analyzer_chain")
