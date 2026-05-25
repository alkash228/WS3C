from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from samv.config import FOLDERS_ROOT, VIDEO_EXTS
from samv.inf.catalog import ensure_inf_dirs
from samv.utils import safe_name
from samv.video.io import find_video


def folder_paths(folder_name: str) -> tuple[Path, Path, Path]:

    """Пути папки, data.json и video."""
    folder = FOLDERS_ROOT / folder_name
    return folder, folder / "data.json", folder / "video"


def find_video_any(folder: Path) -> Path | None:

    """Ищем video.* в папке."""
    return find_video(folder)


def load_folder_payload(folder_name: str) -> tuple[Path, dict, Path | None]:

    """Читаем data.json и находим видео."""
    name = safe_name(folder_name)
    if not name:
        raise ValueError("Invalid folder")
    folder = FOLDERS_ROOT / name
    if not folder.is_dir():
        raise FileNotFoundError("Folder not found")
    json_path = folder / "data.json"
    if not json_path.is_file():
        raise FileNotFoundError("data.json not found")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid data.json payload")
    return folder, payload, find_video_any(folder)


def folder_row(folder: Path, *, json_exists: bool | None = None, video_path: Path | None = None) -> dict[str, object]:

    """Строка для списка папок в UI."""
    json_path = folder / "data.json"
    has_json = bool(json_exists) if json_exists is not None else json_path.is_file()
    if video_path is None:
        video_path = find_video_any(folder)
    mtime = folder.stat().st_mtime
    if has_json:
        mtime = max(mtime, json_path.stat().st_mtime)
    if video_path and video_path.is_file():
        mtime = max(mtime, video_path.stat().st_mtime)

    return {
        "name": folder.name,
        "has_video": bool(video_path and video_path.is_file()),
        "video_name": video_path.name if video_path else "",
        "has_json": has_json,
        "updated_at": dt.datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
    }


def list_folders() -> list[dict[str, object]]:

    """Все папки для главной."""
    ensure_inf_dirs()
    rows: list[dict[str, object]] = []
    for p in FOLDERS_ROOT.iterdir():
        if not p.is_dir():
            continue
        json_path = p / "data.json"
        has_json = json_path.is_file()
        video_path = find_video_any(p)
        # Показываем только проектные папки, чтобы не тратить время на внутренние служебные каталоги.
        if not has_json and (video_path is None or not video_path.is_file()):
            continue
        rows.append(folder_row(p, json_exists=has_json, video_path=video_path))
    rows.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
    return rows


def processing_stats_for_folder(folder_name: str) -> dict[str, object]:

    """Цифры обработки по одной папке."""
    _folder_path, payload, video_path = load_folder_payload(folder_name)
    frames = payload.get("frames")
    frames_list = frames if isinstance(frames, list) else []
    frames_total = int(len(frames_list))
    frames_with_instances = 0
    instances_total = 0
    labels: set[str] = set()
    for fr in frames_list:
        if not isinstance(fr, dict):
            continue
        inst = fr.get("instances")
        if not isinstance(inst, list):
            continue
        valid = [x for x in inst if isinstance(x, dict)]
        if valid:
            frames_with_instances += 1
        instances_total += int(len(valid))
        for x in valid:
            lbl = str(x.get("prompt_label", "") or "").strip()
            if lbl:
                labels.add(lbl)

    proc = payload.get("web_samv_processing")
    proc_map = proc if isinstance(proc, dict) else {}
    scale_div = float(proc_map.get("scale_div", payload.get("fast_scale_div", 1.0)) or 1.0)
    fps_div = int(proc_map.get("fps_div", payload.get("fps_div", 1)) or 1)
    video_part_sec = float(proc_map.get("video_part_sec", 0.0) or 0.0)
    chunks_total = int(proc_map.get("chunks_total", 1) or 1)
    source_fps = float(proc_map.get("source_fps", payload.get("fps", 0.0)) or 0.0)
    processed_fps = float(proc_map.get("processed_fps", payload.get("fps", 0.0)) or 0.0)
    source_w = int(proc_map.get("source_width", payload.get("width", 0)) or 0)
    source_h = int(proc_map.get("source_height", payload.get("height", 0)) or 0)
    processed_w = int(proc_map.get("processed_width", payload.get("width", 0)) or 0)
    processed_h = int(proc_map.get("processed_height", payload.get("height", 0)) or 0)

    return {
        "folder": folder_name,
        "video_name": video_path.name if video_path and video_path.is_file() else "",
        "prompt": str(payload.get("prompt", "") or ""),
        "scale_div": float(scale_div),
        "fps_div": int(fps_div),
        "video_part_sec": float(video_part_sec),
        "chunks_total": int(chunks_total),
        "source_fps": float(source_fps),
        "processed_fps": float(processed_fps),
        "source_width": int(source_w),
        "source_height": int(source_h),
        "processed_width": int(processed_w),
        "processed_height": int(processed_h),
        "frames_written": int(payload.get("frames_written", 0) or 0),
        "frames_total": int(frames_total),
        "frames_with_instances": int(frames_with_instances),
        "instances_total": int(instances_total),
        "labels_count": int(len(labels)),
        "labels": sorted(labels),
        "elapsed_sec": float(payload.get("elapsed_sec", 0.0) or 0.0),
    }


def processing_stats_all_folders() -> list[dict[str, object]]:

    """Статистика по всем папкам."""
    rows: list[dict[str, object]] = []
    for item in list_folders():
        name = str(item.get("name", "") or "")
        if not name:
            continue
        try:
            rows.append(processing_stats_for_folder(name))
        except Exception:
            continue
    return rows
