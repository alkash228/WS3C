from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from samv import config as cfg
from samv.analyzer.paths import scenario_analysis_dir, warnings_url_prefix
from samv.inf.scenarios import read_scenario_meta
from samv.masks.core import instance_group_by_label
from samv.masks.logic import scan_frame_job
from samv.analyzer.workers import render_warning_jpeg_job
from samv.parallel import map_parallel, worker_count
from samv.tasks import task_progress, task_set
from samv.video.io import cache_json_frames_as_jpeg, find_video_for_masks, resolve_mask_dimensions


@dataclass
class WarningItem:
    frame: int
    main_id: int
    reasons: list[str]
    image_url: str
    confidence: float = 0.0
    frame_confidence: float = 0.0
    visibility_confidence: float = 0.0
    equipment_absence_confidence: float = 0.0
    temporal_confidence: float = 0.0
    status: str = "confirmed"
    scenario_id: str = ""
    violation_label: str = ""


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def run_analysis(
    folder: str,
    main_prompt: str,
    linked_prompts: list[str],
    load_folder_payload,
    *,
    scenario_id: str | None = None,
    publish_task_result: bool = True,
) -> None:

    """Полный анализ папки в фоне."""
    try:
        folder_path, payload, video_path = load_folder_payload(folder)
        if video_path is None or not video_path.is_file():
            raise RuntimeError("video.* not found in folder")
        extract_path = find_video_for_masks(folder_path, payload) or video_path
        h, w = resolve_mask_dimensions(payload, extract_path)
        if h <= 0 or w <= 0:
            raise RuntimeError("Invalid width/height in data.json")
        frames = payload.get("frames")
        if not isinstance(frames, list):
            raise RuntimeError("Invalid frames in data.json")

        frame_rows = [fr for fr in frames if isinstance(fr, dict) and int(fr.get("frame", -1)) >= 0]
        total = len(frame_rows)
        task_progress(folder, "analysis", 0, max(1, total), f"Скан масок ({worker_count()} ядер)...")

        analysis_dir = scenario_analysis_dir(folder_path, scenario_id)
        warn_dir = analysis_dir / "warnings"
        warn_dir.mkdir(parents=True, exist_ok=True)
        warn_url_base = warnings_url_prefix(folder, scenario_id)
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
        render_meta: dict[tuple[str, int], dict[str, float | str]] = {}
        data_json_path = str(folder_path / "data.json")
        warn_dir_str = str(warn_dir)
        src_cache_dir = warn_dir / "src_cache"
        frames_checked = len(frame_rows)
        frames_main_present = 0
        min_track_frames = max(1, int(cfg.AN_MIN_INFORMATIVE_FRAMES))
        min_link_frame_ratio = float(cfg.AN_MIN_LINK_FRAME_RATIO)
        min_dep_present_frames = max(1, int(cfg.AN_MIN_DEP_PRESENT_FRAMES))
        scenario_key = str(scenario_id or "").strip()
        confirm_rows: list[dict] = []
        tracks: dict[int, dict] = {}
        track_stats_by_main: dict[str, dict] = {}

        scan_pairs: list[tuple[dict, dict]] = [
            (fr, sr)
            for fr, sr in zip(frame_rows, scan_results)
            if isinstance(fr, dict) and isinstance(sr, dict)
        ]
        scan_pairs.sort(key=lambda x: int((x[0] or {}).get("frame", -1)))

        def get_track(main_id: int) -> dict:
            if int(main_id) not in tracks:
                tracks[int(main_id)] = {
                    "total_frames": 0,
                    "evidence": [],
                    "deps": {
                        dep: {
                            "total": 0,
                            "present_frames": 0,
                            "linked_frames": 0,
                            "link_target": main_prompt,
                        }
                        for dep in linked_prompts
                    },
                    "pairs": {},
                }
            return tracks[int(main_id)]

        for fr, sr in scan_pairs:
            inst = fr.get("instances")
            if isinstance(inst, list):
                inst2 = [x for x in inst if isinstance(x, dict)]
                if instance_group_by_label(inst2, main_prompt, h, w):
                    frames_main_present += 1
            fidx = int(sr.get("frame", -1))
            if fidx < 0:
                continue
            rows = sr.get("rows")
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mid = int(row.get("main_id", 0))
                track = get_track(mid)
                track["total_frames"] += 1
                track["evidence"].append((fidx, row))
                for dep_row in row.get("dep_details") or []:
                    if not isinstance(dep_row, dict):
                        continue
                    dep = str(dep_row.get("dep", "") or "").strip()
                    if dep not in track["deps"]:
                        continue
                    track["deps"][dep]["total"] += 1
                    if bool(dep_row.get("present", False)):
                        track["deps"][dep]["present_frames"] += 1
                    if bool(dep_row.get("linked_ok", dep_row.get("linked_to_main", False))):
                        track["deps"][dep]["linked_frames"] += 1
                    link_tgt = str(dep_row.get("link_target", "") or "").strip()
                    if link_tgt:
                        track["deps"][dep]["link_target"] = link_tgt
                for pair_row in row.get("chain_pairs") or []:
                    if not isinstance(pair_row, dict):
                        continue
                    if not bool(pair_row.get("both_present", pair_row.get("both_linked_to_main", False))):
                        continue
                    key = f"{pair_row.get('left', '')}|{pair_row.get('right', '')}"
                    slot = track["pairs"].setdefault(key, {"total": 0, "linked_frames": 0})
                    slot["total"] += 1
                    if bool(pair_row.get("pair_linked", False)):
                        slot["linked_frames"] += 1

        def pick_evidence_frame(track: dict, dep: str | None = None) -> tuple[int, dict] | None:
            evidence = track.get("evidence") or []
            if not evidence:
                return None
            if dep:
                best: tuple[int, dict] | None = None
                best_ratio = 2.0
                for fidx, row in evidence:
                    for dep_row in row.get("dep_details") or []:
                        if not isinstance(dep_row, dict):
                            continue
                        if str(dep_row.get("dep", "")) != dep:
                            continue
                        ratio = float(dep_row.get("link_ratio", 0.0) or 0.0)
                        if not bool(dep_row.get("linked_ok", dep_row.get("linked_to_main", False))):
                            ratio = 0.0
                        if best is None or ratio < best_ratio:
                            best_ratio = ratio
                            best = (int(fidx), row)
                if best is not None:
                    return best
            return evidence[len(evidence) // 2]

        for mid, track in tracks.items():
            total_frames = int(track.get("total_frames", 0) or 0)
            if total_frames < min_track_frames:
                track_stats_by_main[str(mid)] = {
                    "main_id": int(mid),
                    "total_frames": total_frames,
                    "skipped": True,
                    "reason": "мало кадров с human",
                }
                continue

            terminal_dep = linked_prompts[-1] if linked_prompts else ""
            reasons: list[str] = []
            dep_ratios: dict[str, float] = {}
            worst_dep = terminal_dep or ""
            worst_ratio = 1.0

            for dep in linked_prompts:
                slot = track["deps"].get(dep) or {}
                present_frames = int(slot.get("present_frames", 0) or 0)
                linked_frames = int(slot.get("linked_frames", 0) or 0)
                if present_frames > 0:
                    dep_ratios[dep] = float(linked_frames) / float(present_frames)
                else:
                    dep_ratios[dep] = 0.0

            check_deps = [terminal_dep] if terminal_dep else list(linked_prompts)
            for dep in check_deps:
                slot = track["deps"].get(dep) or {}
                dep_total = int(slot.get("total", 0) or 0)
                if dep_total < min_track_frames:
                    continue
                present_frames = int(slot.get("present_frames", 0) or 0)
                linked_frames = int(slot.get("linked_frames", 0) or 0)
                coverage_ratio = float(present_frames) / float(max(1, total_frames))
                link_ratio = (
                    float(linked_frames) / float(present_frames) if present_frames > 0 else 0.0
                )
                anchor = str((track["deps"].get(dep) or {}).get("link_target", "") or main_prompt)

                if present_frames == 0:
                    if 0.0 < worst_ratio:
                        worst_ratio = 0.0
                        worst_dep = dep
                    reasons.append(
                        f"{main_prompt}_id:{mid} -> отсутствует {dep} "
                        f"(не детектирован на {total_frames} кадрах с {main_prompt})",
                    )
                    continue

                if coverage_ratio < min_link_frame_ratio:
                    pct = int(round(coverage_ratio * 100))
                    need = int(round(min_link_frame_ratio * 100))
                    if coverage_ratio < worst_ratio:
                        worst_ratio = coverage_ratio
                        worst_dep = dep
                    reasons.append(
                        f"{main_prompt}_id:{mid} -> отсутствует {dep} "
                        f"(СИЗ на {pct}% кадров с {main_prompt}, нужно >= {need}%)",
                    )
                    continue

                if link_ratio < min_link_frame_ratio:
                    if link_ratio < worst_ratio:
                        worst_ratio = link_ratio
                        worst_dep = dep
                    if link_ratio <= 0.0:
                        reasons.append(
                            f"{main_prompt}_id:{mid} -> отсутствует {dep} "
                            f"(нет пересечения с {anchor} на {present_frames} кадрах с {dep})",
                        )
                    else:
                        pct = int(round(link_ratio * 100))
                        need = int(round(min_link_frame_ratio * 100))
                        reasons.append(
                            f"{main_prompt}_id:{mid} -> {dep}: слабое пересечение с {anchor} "
                            f"({pct}% кадров, {linked_frames}/{present_frames}, нужно >= {need}%)",
                        )

            track_stats_by_main[str(mid)] = {
                "main_id": int(mid),
                "total_frames": total_frames,
                "dep_link_frame_ratios": dep_ratios,
                "min_required_ratio": float(min_link_frame_ratio),
            }

            if not reasons:
                continue

            pick = pick_evidence_frame(track, worst_dep or None)
            if pick is None:
                continue
            pick_fidx, pick_row = pick
            link_ratio = dep_ratios.get(worst_dep, worst_ratio) if worst_dep else 0.0
            confirm_rows.append(
                {
                    "frame": int(pick_fidx),
                    "main_id": int(mid),
                    "reasons": reasons,
                    "scenario_id": scenario_key,
                    "confidence": float(_clip01(1.0 - link_ratio)),
                    "frame_confidence": float(link_ratio),
                    "visibility_confidence": 0.0,
                    "equipment_absence_confidence": float(_clip01(1.0 - link_ratio)),
                    "temporal_confidence": float(link_ratio),
                },
            )

        meta_early = read_scenario_meta(folder_path, scenario_id)
        viol_label_early = str((meta_early or {}).get("violation_label", "") or "").strip()

        pending_render: list[tuple[int, int, list[str], str]] = []
        seen_confirmed: set[tuple[str, int]] = set()
        for row in sorted(confirm_rows, key=lambda x: (int(x["frame"]), int(x["main_id"]))):
            fidx = int(row["frame"])
            mid = int(row["main_id"])
            sid = str(row.get("scenario_id", scenario_key) or "")
            dedup_key = (sid, mid)
            if dedup_key in seen_confirmed:
                continue
            seen_confirmed.add(dedup_key)
            reasons = list(row.get("reasons") or [])
            render_meta[dedup_key] = {
                "confidence": float(row.get("confidence", 0.0) or 0.0),
                "frame_confidence": float(row.get("frame_confidence", 0.0) or 0.0),
                "visibility_confidence": float(row.get("visibility_confidence", 0.0) or 0.0),
                "equipment_absence_confidence": float(row.get("equipment_absence_confidence", 0.0) or 0.0),
                "temporal_confidence": float(row.get("temporal_confidence", 0.0) or 0.0),
            }
            pending_render.append((fidx, int(mid), reasons, sid))

        src_by_json: dict[int, str] = {}
        if pending_render:
            src_by_json = cache_json_frames_as_jpeg(
                extract_path,
                [x[0] for x in pending_render],
                payload,
                src_cache_dir,
                mask_h=h,
                mask_w=w,
            )

        for fidx, mid, reasons, sid in pending_render:
            src_jpg = src_by_json.get(int(fidx), "")
            if not src_jpg:
                continue
            render_jobs.append(
                (
                    src_jpg,
                    data_json_path,
                    folder,
                    fidx,
                    int(mid),
                    reasons,
                    warn_dir_str,
                    warn_url_base,
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
                    sid = str(scenario_key or "")
                    mid = int(row.get("main_id", -1))
                    meta_row = render_meta.get((sid, mid), {})
                    row.update(meta_row)
                    row["status"] = "confirmed"
                    row["scenario_id"] = sid
                    row["violation_label"] = viol_label_early
                    warnings_out.append(WarningItem(**row))
                on_render(i + 1, render_total)
        else:
            rendered = map_parallel(render_warning_jpeg_job, render_jobs, on_progress=on_render)
            for row in rendered:
                if row:
                    sid = str(scenario_key or "")
                    mid = int(row.get("main_id", -1))
                    meta_row = render_meta.get((sid, mid), {})
                    row.update(meta_row)
                    row["status"] = "confirmed"
                    row["scenario_id"] = sid
                    row["violation_label"] = viol_label_early
                    warnings_out.append(WarningItem(**row))

        meta = read_scenario_meta(folder_path, scenario_id)
        violation_label = ""
        scenario_title = ""
        scenario_key = ""
        if meta:
            violation_label = str(meta.get("violation_label", "") or "").strip()
            scenario_title = str(meta.get("scenario_title", "") or "").strip()
            scenario_key = str(meta.get("scenario_id", "") or "").strip()

        summary = {
            "schema": "samv_mask_analyzer_v1",
            "folder": folder,
            "scenario_id": scenario_key or (scenario_id or ""),
            "main_prompt": main_prompt,
            "linked_prompts": linked_prompts,
            "violation_label": violation_label,
            "scenario_title": scenario_title,
            "frames_checked": int(frames_checked),
            "frames_with_main": int(frames_main_present),
            "warnings_count": int(len(warnings_out)),
            "link_frame_thresholds": {
                "min_track_frames": int(min_track_frames),
                "min_link_frame_ratio": float(min_link_frame_ratio),
                "min_dep_present_frames": int(min_dep_present_frames),
            },
            "warnings": [
                {
                    "frame": w.frame,
                    "main_id": w.main_id,
                    "reasons": w.reasons,
                    "image_url": w.image_url,
                    "status": w.status,
                    "scenario_id": w.scenario_id,
                    "violation_label": w.violation_label,
                    "confidence": w.confidence,
                    "frame_confidence": w.frame_confidence,
                    "visibility_confidence": w.visibility_confidence,
                    "equipment_absence_confidence": w.equipment_absence_confidence,
                    "temporal_confidence": w.temporal_confidence,
                }
                for w in warnings_out
            ],
            "diagnostics": {
                "tracks_by_main": list(track_stats_by_main.values()),
                "note": (
                    "Нарушение по сценарию: проверяется последний объект цепочки (СИЗ). "
                    "Отсутствие: не детектирован, реже порога по кадрам с human, "
                    "или слабое пересечение с якорем цепочки "
                    f"(порог {int(round(min_link_frame_ratio * 100))}%)."
                ),
            },
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        (analysis_dir / "analyzer_result.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if publish_task_result:
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
        if publish_task_result:
            task_set(
                folder,
                "analysis",
                status="error",
                percent=0,
                message="Ошибка анализа",
                error=str(exc),
                result=None,
            )
        else:
            raise
