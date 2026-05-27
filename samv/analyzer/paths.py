from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


def scenario_analysis_dir(folder_path: Path, scenario_id: str | None) -> Path:
    """Каталог результатов анализа (один сценарий — legacy analysis/)."""
    if scenario_id:
        return folder_path / "analysis" / "by_scenario" / str(scenario_id)
    return folder_path / "analysis"


def warnings_url_prefix(folder: str, scenario_id: str | None) -> str:
    if scenario_id:
        return f"/storage/folders/{folder}/analysis/by_scenario/{scenario_id}/warnings"
    return f"/storage/folders/{folder}/analysis/warnings"


def analyzer_result_path(folder_path: Path, scenario_id: str | None = None) -> Path:
    return scenario_analysis_dir(folder_path, scenario_id) / "analyzer_result.json"


def list_scenario_analysis_ids(folder_path: Path) -> list[str]:
    base = folder_path / "analysis" / "by_scenario"
    if not base.is_dir():
        return []
    out: list[str] = []
    for p in sorted(base.iterdir()):
        if p.is_dir() and (p / "analyzer_result.json").is_file():
            out.append(p.name)
    return out


def resolve_scenario_id(folder_path: Path, scenario_id: str | None) -> str | None:
    """Выбрать сценарий: явный id, единственный в by_scenario, или None (legacy)."""
    sid = str(scenario_id or "").strip()
    if sid:
        return sid
    ids = list_scenario_analysis_ids(folder_path)
    if len(ids) == 1:
        return ids[0]
    if (folder_path / "analysis" / "analyzer_result.json").is_file():
        return None
    if ids:
        return ids[0]
    return None


def ensure_unified_analyzer_result(folder_path: Path) -> Path | None:
    """
    Вернуть путь к общему analyzer_result.json.

    Если legacy-файл отсутствует, но есть by_scenario/*/analyzer_result.json,
    собираем общий результат (warnings объединяются без дедупа).
    """
    analysis_dir = folder_path / "analysis"
    unified_path = analysis_dir / "analyzer_result.json"
    if unified_path.is_file():
        return unified_path
    ids = list_scenario_analysis_ids(folder_path)
    if not ids:
        return None

    warnings: list[dict] = []
    frames_checked = 0
    frames_with_main = 0
    pending_count = 0
    cleared_count = 0
    pending_rows: list[dict] = []
    last_payload: dict | None = None
    for sid in ids:
        p = analysis_dir / "by_scenario" / sid / "analyzer_result.json"
        if not p.is_file():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        last_payload = raw
        rows = raw.get("warnings")
        if isinstance(rows, list):
            warnings.extend([x for x in rows if isinstance(x, dict)])
        diag = raw.get("diagnostics")
        if isinstance(diag, dict):
            p = diag.get("pending_by_main")
            if isinstance(p, list):
                pending_rows.extend([x for x in p if isinstance(x, dict)])
        try:
            frames_checked = max(frames_checked, int(raw.get("frames_checked", 0) or 0))
        except Exception:
            pass
        try:
            frames_with_main = max(frames_with_main, int(raw.get("frames_with_main", 0) or 0))
        except Exception:
            pass
        try:
            pending_count += int(raw.get("pending_count", 0) or 0)
        except Exception:
            pass
        try:
            cleared_count += int(raw.get("cleared_count", 0) or 0)
        except Exception:
            pass

    if last_payload is None:
        return None

    merged = {
        "schema": "samv_mask_analyzer_v1",
        "folder": str(folder_path.name),
        "main_prompt": "multi",
        "linked_prompts": [],
        "frames_checked": int(frames_checked),
        "frames_with_main": int(frames_with_main),
        "warnings_count": int(len(warnings)),
        "pending_count": int(pending_count),
        "cleared_count": int(cleared_count),
        "warnings": warnings,
        "violation_label": str(last_payload.get("violation_label", "") or ""),
        "scenario_title": str(last_payload.get("scenario_title", "") or ""),
        "diagnostics": {"pending_by_main": pending_rows},
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    analysis_dir.mkdir(parents=True, exist_ok=True)
    unified_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return unified_path
