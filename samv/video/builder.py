from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from samv.parallel import map_parallel, worker_count
from samv.video.io import safe_video_fps
from samv.video.workers import render_clip_frame_job


def collect_clip_items(
    warnings: list[dict],
    main_id: int | None,
) -> list[tuple[int, int, list[str]]]:

    """Кадры с нарушениями для ролика."""
    clip_items: list[tuple[int, int, list[str]]] = []
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
            fidx = int(witem.get("frame", -1))
            mid = int(witem.get("main_id"))
        except Exception:
            continue
        if fidx < 0:
            continue
        key = (fidx, mid)
        if key in seen:
            continue
        seen.add(key)
        reasons = witem.get("reasons")
        clip_items.append((fidx, mid, reasons if isinstance(reasons, list) else []))
    clip_items.sort(key=lambda x: x[0])
    return clip_items


def build_warning_video(
    folder: str,
    folder_path: Path,
    video_path: Path,
    warnings: list[dict],
    payload: dict,
    main_prompt: str,
    main_id: int | None = None,
    progress_cb=None,
) -> dict[str, object]:

    """Склеиваем mp4 с подсветкой."""
    analysis_dir = folder_path / "analysis"
    out_name = "warnings_preview.mp4" if main_id is None else f"warnings_preview_human_{int(main_id)}.mp4"
    out_path = analysis_dir / out_name

    h = int(payload.get("height", 0) or 0)
    w = int(payload.get("width", 0) or 0)
    if h <= 0 or w <= 0:
        raise RuntimeError("Invalid width/height in data.json")

    clip_items = collect_clip_items(warnings, main_id)
    if not clip_items:
        raise RuntimeError("No warning frames found. Run analysis first.")

    fps = safe_video_fps(video_path)
    w_enc = max(2, w - (w % 2))
    h_enc = max(2, h - (h % 2))
    tmp_dir = analysis_dir / "warnings_video_frames_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    total = len(clip_items)
    data_json_path = str(folder_path / "data.json")
    video_path_str = str(video_path)
    tmp_dir_str = str(tmp_dir)

    jobs = [
        (
            seq,
            video_path_str,
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
        )
        for seq, (fidx, mid, reasons) in enumerate(clip_items)
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
    if progress_cb:
        progress_cb(total, total, "Сборка MP4 (ffmpeg)...")

    cmd = [
        "ffmpeg", "-y",
        "-framerate", f"{fps:.6f}",
        "-i", str(tmp_dir / "frame_%06d.jpg"),
        "-r", f"{fps:.6f}",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
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
        "video_url": f"/storage/folders/{folder}/analysis/{out_name}?ts={int(time.time())}",
        "frames_used": int(written),
        "fps": float(fps),
        "main_id": int(main_id) if main_id is not None else None,
    }
