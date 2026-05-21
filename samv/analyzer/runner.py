from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from samv.masks.core import instance_group_by_label
from samv.masks.logic import scan_frame_job
from samv.analyzer.workers import render_warning_jpeg_job
from samv.parallel import map_parallel, worker_count
from samv.tasks import task_progress, task_set


@dataclass
class WarningItem:
    frame: int
    main_id: int
    reasons: list[str]
    image_url: str


def run_analysis(folder: str, main_prompt: str, linked_prompts: list[str], load_folder_payload) -> None:

    """Полный анализ папки в фоне."""
    try:
        folder_path, payload, video_path = load_folder_payload(folder)
        if video_path is None or not video_path.is_file():
            raise RuntimeError("video.* not found in folder")
        h = int(payload.get("height", 0) or 0)
        w = int(payload.get("width", 0) or 0)
        if h <= 0 or w <= 0:
            raise RuntimeError("Invalid width/height in data.json")
        frames = payload.get("frames")
        if not isinstance(frames, list):
            raise RuntimeError("Invalid frames in data.json")

        frame_rows = [fr for fr in frames if isinstance(fr, dict) and int(fr.get("frame", -1)) >= 0]
        total = len(frame_rows)
        task_progress(folder, "analysis", 0, max(1, total), f"Скан масок ({worker_count()} ядер)...")

        analysis_dir = folder_path / "analysis"
        warn_dir = analysis_dir / "warnings"
        warn_dir.mkdir(parents=True, exist_ok=True)
        for old in warn_dir.glob("warn_*.jpg"):
            old.unlink(missing_ok=True)

        scan_args = [(fr, main_prompt, linked_prompts, h, w) for fr in frame_rows]
        scan_results: list[dict | None] = []

        def on_scan(done: int, tot: int) -> None:
            """Обновляем прогресс скана кадров."""
            task_progress(folder, "analysis", done, tot, f"Скан кадров {done}/{tot}")

        if len(scan_args) <= 1 or worker_count() <= 1:
            scan_results = [scan_frame_job(a) for a in scan_args]
            on_scan(len(scan_args), len(scan_args))
        else:
            scan_results = map_parallel(scan_frame_job, scan_args, on_progress=on_scan)

        render_jobs: list[tuple] = []
        data_json_path = str(folder_path / "data.json")
        video_path_str = str(video_path)
        warn_dir_str = str(warn_dir)
        frames_checked = len(frame_rows)
        frames_main_present = 0

        for fr, sr in zip(frame_rows, scan_results):
            if sr is None:
                continue
            inst = fr.get("instances")
            if isinstance(inst, list):
                inst2 = [x for x in inst if isinstance(x, dict)]
                if instance_group_by_label(inst2, main_prompt, h, w):
                    frames_main_present += 1
            fidx = int(sr["frame"])
            for mid, reasons in sr["hits"]:
                render_jobs.append(
                    (
                        video_path_str,
                        data_json_path,
                        folder,
                        fidx,
                        int(mid),
                        reasons,
                        warn_dir_str,
                        main_prompt,
                        h,
                        w,
                    )
                )

        warnings_out: list[WarningItem] = []
        render_total = max(1, len(render_jobs))
        task_progress(folder, "analysis", 0, render_total, f"Рендер JPEG ({worker_count()} ядер)...")

        def on_render(done: int, tot: int) -> None:
            """Обновляем прогресс рендера jpeg."""
            task_progress(folder, "analysis", done, tot, f"Рендер warning {done}/{tot}")

        if not render_jobs:
            pass
        elif len(render_jobs) <= 1 or worker_count() <= 1:
            for i, job in enumerate(render_jobs):
                row = render_warning_jpeg_job(job)
                if row:
                    warnings_out.append(WarningItem(**row))
                on_render(i + 1, render_total)
        else:
            rendered = map_parallel(render_warning_jpeg_job, render_jobs, on_progress=on_render)
            for row in rendered:
                if row:
                    warnings_out.append(WarningItem(**row))

        summary = {
            "schema": "samv_mask_analyzer_v1",
            "folder": folder,
            "main_prompt": main_prompt,
            "linked_prompts": linked_prompts,
            "frames_checked": int(frames_checked),
            "frames_with_main": int(frames_main_present),
            "warnings_count": int(len(warnings_out)),
            "warnings": [
                {"frame": w.frame, "main_id": w.main_id, "reasons": w.reasons, "image_url": w.image_url}
                for w in warnings_out
            ],
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        (analysis_dir / "analyzer_result.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        task_set(
            folder,
            "analysis",
            status="done",
            percent=100,
            done=total,
            total=total,
            message="Анализ завершён",
            result=summary,
            error="",
        )
    except Exception as exc:
        task_set(
            folder,
            "analysis",
            status="error",
            percent=0,
            message="Ошибка анализа",
            error=str(exc),
            result=None,
        )
