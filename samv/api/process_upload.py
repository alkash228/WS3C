"""Обработка загруженного видео через SAM API."""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path

from samv.api.client import (
    fetch_job_result,
    http_bytes_get,
    merge_chunk_payloads,
    new_folder_name,
    save_processed_outputs,
    split_video_chunks,
    start_video_job,
    transform_video_bytes_ffmpeg,
    upscale_result_payload_inplace,
    video_meta_from_bytes,
    wait_job_done,
)
from samv.config import VIDEO_EXTS
from samv.storage.folders import folder_paths


def run_process_video(
    *,
    video_name: str,
    video_bytes: bytes,
    ext: str,
    prompt: str,
    scale_div: float,
    fps_div: int,
    video_part_sec: float,
    folder_name: str | None = None,
    on_sam_progress: Callable[[int, int, float, int, int], None] | None = None,
) -> dict[str, object]:
    """Прогон видео через API; возвращает поля для JSON-ответа."""
    meta = video_meta_from_bytes(video_bytes, suffix=ext)
    original_wh = (int(meta[0]), int(meta[1])) if meta is not None else None
    original_fps = float(meta[2]) if meta is not None else 0.0

    folder_name = str(folder_name or "").strip() or new_folder_name()
    for _ in range(20):
        folder, _, _ = folder_paths(folder_name)
        if not folder.exists():
            break
        folder_name = f"{new_folder_name()}_{uuid.uuid4().hex[:4]}"

    chunks = split_video_chunks(video_bytes, ext or ".mp4", video_part_sec)
    payloads: list[dict] = []
    job_ids: list[str] = []
    last_send_meta: tuple[int, int, float] | None = None
    chunks_total = len(chunks)

    def _report_sam(prog: dict[str, object], chunk_idx: int) -> None:
        if on_sam_progress is None:
            return
        cur = int(prog.get("current") or 0)
        tot = int(prog.get("total") or 0)
        eta = float(prog.get("eta_seconds") or 0.0)
        on_sam_progress(cur, tot, eta, chunk_idx, chunks_total)

    for ci, chunk_bytes in enumerate(chunks):
        send_video_name = video_name
        send_video_bytes = chunk_bytes
        if len(chunks) > 1:
            send_video_name = f"{Path(video_name).stem}_part{ci + 1:03d}{ext or '.mp4'}"
        if scale_div > 1.0 or fps_div > 1:
            send_video_name = f"{Path(send_video_name).stem}.fast.mp4"
            send_video_bytes = transform_video_bytes_ffmpeg(
                chunk_bytes,
                suffix=ext or ".mp4",
                scale_div=float(scale_div),
                fps_div=int(fps_div),
                src_fps=original_fps,
            )
        last_send_meta = video_meta_from_bytes(send_video_bytes, suffix=".mp4")

        job_id = start_video_job(send_video_name, send_video_bytes, prompt)
        job_ids.append(job_id)
        wait_job_done(job_id, on_progress=lambda p: _report_sam(p, ci))
        result = fetch_job_result(job_id)

        if len(chunks) == 1:
            folder, _, _ = folder_paths(folder_name)
            folder.mkdir(parents=True, exist_ok=True)
            if scale_div > 1.0 or fps_div > 1:
                (folder / "video_sam.mp4").write_bytes(send_video_bytes)
            save_processed_outputs(
                folder_name,
                result,
                fallback_video=video_bytes,
                upscale_to_wh=original_wh if scale_div > 1.0 else None,
                processing_meta={
                    "scale_div": float(scale_div),
                    "fps_div": int(fps_div),
                    "video_part_sec": float(video_part_sec),
                    "chunks_total": 1,
                    "source_fps": float(original_fps),
                    "processed_fps": float(last_send_meta[2]) if last_send_meta is not None else float(original_fps),
                    "source_width": int(original_wh[0]) if original_wh is not None else 0,
                    "source_height": int(original_wh[1]) if original_wh is not None else 0,
                    "processed_width": int(last_send_meta[0]) if last_send_meta is not None else int(original_wh[0]) if original_wh is not None else 0,
                    "processed_height": int(last_send_meta[1]) if last_send_meta is not None else int(original_wh[1]) if original_wh is not None else 0,
                },
            )
            break

        data_url = str(result.get("download_data_url") or result.get("download_data_stable_url") or "")
        if data_url:
            raw = http_bytes_get(data_url)
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict):
                if original_wh and scale_div > 1.0:
                    upscale_result_payload_inplace(parsed, int(original_wh[0]), int(original_wh[1]))
                payloads.append(parsed)

    if len(chunks) > 1:
        merged = merge_chunk_payloads(payloads)
        merged["prompt"] = prompt
        if original_wh:
            merged.setdefault("width", int(original_wh[0]))
            merged.setdefault("height", int(original_wh[1]))
        merged["web_samv_processing"] = {
            "scale_div": float(scale_div),
            "fps_div": int(fps_div),
            "video_part_sec": float(video_part_sec),
            "chunks_total": len(chunks),
            "source_fps": float(original_fps),
            "processed_fps": float(last_send_meta[2]) if last_send_meta is not None else float(original_fps),
            "source_width": int(original_wh[0]) if original_wh is not None else 0,
            "source_height": int(original_wh[1]) if original_wh is not None else 0,
            "processed_width": int(last_send_meta[0]) if last_send_meta is not None else int(original_wh[0]) if original_wh is not None else 0,
            "processed_height": int(last_send_meta[1]) if last_send_meta is not None else int(original_wh[1]) if original_wh is not None else 0,
        }
        merged["fast_scale_div"] = float(scale_div)
        merged["fps_div"] = int(fps_div)
        folder, json_path, _ = folder_paths(folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "video.mp4").write_bytes(video_bytes)

    return {
        "folder": folder_name,
        "job_id": job_ids[-1] if job_ids else "",
        "job_ids": job_ids,
        "chunks_total": len(chunks),
        "video_part_sec": float(video_part_sec),
        "fast_x2": bool(scale_div == 2.0),
        "fps_half": bool(fps_div == 2),
        "scale_div": float(scale_div),
        "fps_div": int(fps_div),
    }
