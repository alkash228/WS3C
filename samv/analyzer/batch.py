from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from samv.analyzer.paths import (
    analyzer_result_path,
    ensure_unified_analyzer_result,
    resolve_mask_main_prompt,
)
from samv.analyzer.runner import run_analysis
from samv.inf.scenarios import get_enabled_scenarios, scenario_to_analyzer, scenario_violation_label, write_scenario_meta
from samv.tasks import task_progress, task_set


def run_all_scenarios_for_folder(
    folder: str,
    load_folder_payload,
) -> dict:
    """Прогон всех включённых сценариев INF по уже обработанной папке."""
    scenarios = get_enabled_scenarios()
    if not scenarios:
        raise RuntimeError("Нет включённых сценариев анализа в INF")

    folder_path, payload, video_path = load_folder_payload(folder)
    if video_path is None or not video_path.is_file():
        raise RuntimeError("video.* not found in folder")
    data_path = folder_path / "data.json"
    if not data_path.is_file():
        raise RuntimeError("data.json not found in folder")

    use_subdirs = len(scenarios) > 1
    merged_warnings: list[dict] = []
    merged_frames_checked = 0
    merged_frames_with_main = 0
    merged_pending_count = 0
    merged_cleared_count = 0
    merged_frame_candidates_total = 0
    merged_frame_candidates_informative = 0
    merged_pending_rows: list[dict] = []
    violation_labels: list[str] = []
    scenario_titles: list[str] = []
    runs_ok = 0

    total = len(scenarios)
    task_set(
        folder,
        "analysis",
        status="running",
        percent=0,
        done=0,
        total=total,
        message=f"Анализ сценариев 0/{total}…",
    )

    for idx, scenario in enumerate(scenarios, start=1):
        chain = str(scenario.get("prompt", "") or "").strip()
        sid = str(scenario.get("id", "") or "").strip()
        title = str(scenario.get("title", "") or "").strip()
        if not chain:
            task_progress(
                folder,
                "analysis",
                idx - 1,
                total,
                f"Пропуск «{title or sid}»: пустая цепочка",
            )
            continue
        main_prompt, linked_prompts = scenario_to_analyzer(chain)
        scenario_key = sid if use_subdirs else None
        write_scenario_meta(
            folder_path,
            scenario,
            main_prompt,
            linked_prompts,
            scenario_subdir=use_subdirs,
        )
        task_progress(
            folder,
            "analysis",
            idx - 1,
            total,
            f"Анализ ({idx}/{total}): {title or main_prompt}…",
        )
        run_analysis(
            folder,
            main_prompt,
            linked_prompts,
            load_folder_payload,
            scenario_id=scenario_key,
            publish_task_result=False,
        )
        result_path = analyzer_result_path(folder_path, scenario_key)
        if not result_path.is_file():
            continue
        parsed = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            continue
        runs_ok += 1
        rows = parsed.get("warnings")
        if isinstance(rows, list):
            merged_warnings.extend([x for x in rows if isinstance(x, dict)])
        merged_frames_checked = max(merged_frames_checked, int(parsed.get("frames_checked", 0) or 0))
        merged_frames_with_main = max(merged_frames_with_main, int(parsed.get("frames_with_main", 0) or 0))
        merged_pending_count += int(parsed.get("pending_count", 0) or 0)
        merged_cleared_count += int(parsed.get("cleared_count", 0) or 0)
        merged_frame_candidates_total += int(parsed.get("frame_candidates_total", 0) or 0)
        merged_frame_candidates_informative += int(parsed.get("frame_candidates_informative", 0) or 0)
        diag = parsed.get("diagnostics")
        if isinstance(diag, dict):
            pending_rows = diag.get("pending_by_main")
            if isinstance(pending_rows, list):
                merged_pending_rows.extend([x for x in pending_rows if isinstance(x, dict)])
        lbl = scenario_violation_label(scenario)
        if lbl and lbl not in violation_labels:
            violation_labels.append(lbl)
        if title and title not in scenario_titles:
            scenario_titles.append(title)

    if runs_ok <= 0:
        raise RuntimeError("Ни один сценарий не проанализирован")

    analysis_dir = folder_path / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    mask_main = resolve_mask_main_prompt(folder_path, None, payload)
    unified = {
        "schema": "samv_mask_analyzer_v1",
        "folder": folder,
        "main_prompt": "multi",
        "mask_main_prompt": mask_main,
        "linked_prompts": [],
        "frames_checked": int(merged_frames_checked),
        "frames_with_main": int(merged_frames_with_main),
        "warnings_count": int(len(merged_warnings)),
        "pending_count": int(merged_pending_count),
        "cleared_count": int(merged_cleared_count),
        "frame_candidates_total": int(merged_frame_candidates_total),
        "frame_candidates_informative": int(merged_frame_candidates_informative),
        "warnings": merged_warnings,
        "violation_label": violation_labels[0] if violation_labels else "",
        "violation_labels": violation_labels,
        "scenario_title": scenario_titles[0] if scenario_titles else "",
        "scenario_titles": scenario_titles,
        "scenarios_total": int(len(scenarios)),
        "scenarios_done": int(runs_ok),
        "diagnostics": {"pending_by_main": merged_pending_rows},
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    (analysis_dir / "analyzer_result.json").write_text(
        json.dumps(unified, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ensure_unified_analyzer_result(folder_path)
    task_set(
        folder,
        "analysis",
        status="done",
        percent=100,
        done=total,
        total=total,
        message="Анализ завершён",
        result=unified,
        error="",
    )
    return unified
