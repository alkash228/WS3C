from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from samv import config as cfg
from samv.masks.core import label_matches
from samv.parallel import map_parallel, worker_count
from samv.video.io import (
    cache_json_frames_as_jpeg,
    find_video_for_masks,
    renumber_jpeg_sequence,
    resolve_mask_dimensions,
    safe_video_fps,
)
from samv.video.workers import render_clip_frame_job


def collect_clip_items(
    warnings: list[dict],
    main_id: int | None,
) -> list[tuple[int, int, list[str]]]:

    """Кадры с нарушениями для ролика."""
    by_key: dict[tuple[int, int], list[str]] = {}
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
            fidx = int(witem.get("frame", -1))
            mid = int(witem.get("main_id"))
        except Exception:
            continue
        if fidx < 0:
            continue
        key = (fidx, mid)
        reasons = witem.get("reasons")
        rows = [str(x) for x in reasons if isinstance(reasons, list) and str(x or "").strip()]
        viol = str(witem.get("violation_label", "") or "").strip()
        if viol:
            rows.insert(0, viol)
        if key not in by_key:
            by_key[key] = []
        for r in rows:
            if r not in by_key[key]:
                by_key[key].append(r)
    clip_items = [(fidx, mid, rs) for (fidx, mid), rs in sorted(by_key.items(), key=lambda x: x[0][0])]
    return clip_items


def collect_track_clip_items(
    warnings: list[dict],
    payload: dict,
    main_prompt: str,
    main_id: int | None,
) -> list[tuple[int, int, list[str]]]:
    """Все кадры трека main_id, у которого есть нарушение."""
    warning_by_mid: dict[int, list[str]] = {}
    for witem in warnings:
        if not isinstance(witem, dict):
            continue
        try:
            mid = int(witem.get("main_id"))
        except Exception:
            continue
        if main_id is not None and mid != int(main_id):
            continue
        slot = warning_by_mid.setdefault(mid, [])
        viol = str(witem.get("violation_label", "") or "").strip()
        if viol and viol not in slot:
            slot.append(viol)
        reasons = witem.get("reasons")
        if isinstance(reasons, list):
            for row in reasons:
                txt = str(row or "").strip()
                if txt and txt not in slot:
                    slot.append(txt)
    if not warning_by_mid:
        return []

    frames = payload.get("frames")
    if not isinstance(frames, list):
        return []

    out: list[tuple[int, int, list[str]]] = []
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        try:
            fidx = int(fr.get("frame", -1))
        except Exception:
            continue
        if fidx < 0:
            continue
        inst = fr.get("instances")
        if not isinstance(inst, list):
            continue
        for row in inst:
            if not isinstance(row, dict):
                continue
            try:
                oid = int(row.get("object_id", -1))
            except Exception:
                continue
            if oid not in warning_by_mid:
                continue
            label = str(row.get("prompt_label", "") or "").strip()
            # Строго берём только кадры объекта по основному промпту сценария (обычно human).
            if main_prompt and not label_matches(label, main_prompt):
                continue
            out.append((fidx, oid, list(warning_by_mid.get(oid) or [])))
    out.sort(key=lambda x: x[0])
    return out


def expand_clip_items_with_context(
    clip_items: list[tuple[int, int, list[str]]],
    context_frames: int,
) -> list[tuple[int, int, list[str]]]:
    """Расширяем список кадров окном вокруг предупреждения."""
    ctx = max(0, int(context_frames))
    if ctx <= 0:
        return clip_items
    expanded: dict[tuple[int, int], list[str]] = {}
    for fidx, mid, reasons in clip_items:
        for nf in range(max(0, int(fidx) - ctx), int(fidx) + ctx + 1):
            key = (int(nf), int(mid))
            slot = expanded.setdefault(key, [])
            for row in reasons:
                s = str(row or "").strip()
                if s and s not in slot:
                    slot.append(s)
    return [(fidx, mid, rs) for (fidx, mid), rs in sorted(expanded.items(), key=lambda x: x[0][0])]


def resolve_context_frames(fps: float) -> int:
    """Считаем ширину окна вокруг warning в кадрах."""
    by_frames = max(0, int(cfg.VIDEO_WARNING_CONTEXT_FRAMES))
    by_seconds = 0
    if float(fps) > 0:
        by_seconds = max(0, int(round(float(cfg.VIDEO_WARNING_CONTEXT_SEC) * float(fps))))
    return max(by_frames, by_seconds)


def build_warning_video(
    folder: str,
    folder_path: Path,
    video_path: Path,
    warnings: list[dict],
    payload: dict,
    main_prompt: str,
    main_id: int | None = None,
    colorful_masks: bool = False,
    progress_cb=None,
) -> dict[str, object]:

    """Склеиваем mp4 с подсветкой."""
    analysis_dir = folder_path / "analysis"
    out_name = "warnings_preview.mp4" if main_id is None else f"warnings_preview_human_{int(main_id)}.mp4"
    out_path = analysis_dir / out_name
    if out_path.is_file():
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass

    extract_path = find_video_for_masks(folder_path, payload) or video_path
    h, w = resolve_mask_dimensions(payload, extract_path)
    if h <= 0 or w <= 0:
        raise RuntimeError("Invalid width/height in data.json")

    fps = safe_video_fps(extract_path)
    context_frames = resolve_context_frames(fps)
    if main_id is not None:
        # По human_id — только кадры нарушений ± контекст (не весь трек в data.json).
        clip_items = collect_clip_items(warnings, main_id)
        clip_items = expand_clip_items_with_context(clip_items, context_frames)
    else:
        clip_items = collect_track_clip_items(warnings, payload, main_prompt, None)
        if not clip_items:
            clip_items = collect_clip_items(warnings, None)
            clip_items = expand_clip_items_with_context(clip_items, context_frames)
    if not clip_items:
        raise RuntimeError("No warning frames found. Run analysis first.")
    w_enc = max(2, w - (w % 2))
    h_enc = max(2, h - (h % 2))
    tmp_dir = analysis_dir / "warnings_video_frames_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    src_cache = tmp_dir / "src_cache"
    json_fidx_list = [int(x[0]) for x in clip_items]
    src_by_json = cache_json_frames_as_jpeg(
        extract_path,
        json_fidx_list,
        payload,
        src_cache,
        mask_h=h,
        mask_w=w,
    )
    if not src_by_json:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(
            "Не удалось прочитать кадры из видео. Проверьте video.mp4 / video_sam.mp4 и data.json."
        )

    total = len(clip_items)
    data_json_path = str(folder_path / "data.json")
    tmp_dir_str = str(tmp_dir)

    jobs = [
        (
            seq,
            src_by_json.get(int(fidx), ""),
            data_json_path,
            fidx,
            mid,
            reasons,
            main_prompt,
            h,
            w,
            h_enc,
            w_enc,
            tmp_dir_str,
            bool(colorful_masks),
        )
        for seq, (fidx, mid, reasons) in enumerate(clip_items)
        if src_by_json.get(int(fidx))
    ]

    written = 0

    def on_done(done: int, tot: int) -> None:
        """Сообщаем сколько кадров уже готово."""
        if progress_cb:
            progress_cb(done, tot, f"Кадр {done}/{tot}")

    if len(jobs) <= 1 or worker_count() <= 1:
        for i, job in enumerate(jobs):
            row = render_clip_frame_job(job)
            if row:
                written += 1
            on_done(i + 1, total)
    else:
        results = map_parallel(render_clip_frame_job, jobs, on_progress=on_done)
        written = sum(1 for r in results if r)

    if written <= 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("No valid warning frames to encode.")
    frame_count = renumber_jpeg_sequence(tmp_dir)
    if frame_count <= 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("No JPEG frames after renumber for ffmpeg.")
    if progress_cb:
        progress_cb(total, total, "Сборка MP4 (ffmpeg)...")

    cmd = [
        "ffmpeg", "-y",
        "-framerate", f"{fps:.6f}",
        "-start_number", "0",
        "-i", str(tmp_dir / "frame_%06d.jpg"),
        "-frames:v", str(frame_count),
        "-r", f"{fps:.6f}",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if proc.returncode != 0 or not out_path.is_file():
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ffmpeg failed: {err[-600:]}")

    return {
        "video_url": f"/storage/folders/{folder}/analysis/{out_name}?ts={time.time_ns()}",
        "frames_used": int(written),
        "fps": float(fps),
        "main_id": int(main_id) if main_id is not None else None,
    }
