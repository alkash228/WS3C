from __future__ import annotations

import json

from samv.analyzer.paths import ensure_unified_analyzer_result, resolve_mask_main_prompt
from samv.storage.folders import load_folder_payload
from samv.tasks import task_progress, task_set
from samv.video.builder import build_warning_video


def run_video_build(folder: str, main_id: int | None, colorful_masks: bool = False) -> None:

    """Собираем preview mp4 по warnings."""
    try:
        folder_path, data_payload, video_path = load_folder_payload(folder)
        if video_path is None or not video_path.is_file():
            raise RuntimeError("video.* not found in folder")
        result_path = ensure_unified_analyzer_result(folder_path)
        if result_path is None:
            result_path = folder_path / "analysis" / "analyzer_result.json"
        if not result_path.is_file():
            raise RuntimeError("Run analysis first")
        analysis_payload = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(analysis_payload, dict):
            raise ValueError("Invalid analyzer_result.json")
        warnings = analysis_payload.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
        main_prompt = resolve_mask_main_prompt(folder_path, analysis_payload, data_payload)
        if not main_prompt:
            raise RuntimeError("mask main prompt not resolved (expected human or meta)")

        clip_count = 0
        seen: set[tuple[int, int]] = set()
        for witem in warnings:
            if not isinstance(witem, dict):
                continue
            if main_id is not None:
                try:
                    if int(witem.get("main_id")) != int(main_id):
                        continue
                except Exception:
                    continue
            try:
                key = (int(witem.get("frame", -1)), int(witem.get("main_id")))
            except Exception:
                continue
            if key[0] < 0 or key in seen:
                continue
            seen.add(key)
            clip_count += 1
        total = max(1, clip_count)

        def progress(done: int, tot: int, msg: str) -> None:
            """Прогресс сборки видео."""
            task_progress(folder, "video", done, tot, msg)

        built = build_warning_video(
            folder,
            folder_path,
            video_path,
            warnings,
            data_payload,
            main_prompt,
            main_id=main_id,
            colorful_masks=colorful_masks,
            progress_cb=progress,
        )
        task_set(
            folder,
            "video",
            status="done",
            percent=100,
            done=total,
            total=total,
            message="Видео готово",
            result=built,
            error="",
        )
    except Exception as exc:
        task_set(
            folder,
            "video",
            status="error",
            percent=0,
            message="Ошибка сборки видео",
            error=str(exc),
            result=None,
        )
