from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from samv.config import VIDEO_EXTS


def find_video(folder: Path) -> Path | None:

    """Файл video.* в папке."""
    if not folder.is_dir():
        return None
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS and p.stem == "video":
            return p
    return None


def find_video_for_masks(folder: Path, payload: dict | None = None) -> Path | None:
    """
    Видео, кадры которого соответствуют индексам в data.json.
    Если при SAM был уменьшен fps/размер — используем сохранённый video_sam.mp4.
    """
    if folder.is_dir():
        sam = folder / "video_sam.mp4"
        if sam.is_file():
            return sam
    return find_video(folder)


def processing_meta(payload: dict | None) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    proc = payload.get("web_samv_processing")
    if isinstance(proc, dict):
        return proc
    return {}


def json_frame_to_source_index(json_fidx: int, payload: dict | None, *, sam_video: bool) -> int:
    """Индекс кадра в файле на диске для кадра N из data.json."""
    if sam_video:
        return max(0, int(json_fidx))
    proc = processing_meta(payload)
    fps_div = 1
    if isinstance(payload, dict):
        fps_div = max(1, int(proc.get("fps_div") or payload.get("fps_div", 1) or 1))
    return max(0, int(json_fidx)) * fps_div


def resolve_mask_dimensions(payload: dict, video_path: Path | None = None) -> tuple[int, int]:
    """Высота и ширина для масок (из json; иначе из видеофайла)."""
    w = int(payload.get("width", 0) or 0)
    h = int(payload.get("height", 0) or 0)
    if h > 0 and w > 0:
        return h, w
    if video_path is not None:
        vw, vh, _, _ = probe_video(video_path)
        if vh > 0 and vw > 0:
            return vh, vw
    return 0, 0


def probe_video(video_path: Path | str) -> tuple[int, int, float, int]:
    """width, height, fps, frame_count (оценка)."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return 0, 0, 0.0, 0
    try:
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if n <= 0:
            n = _probe_frame_count_ffprobe(Path(video_path))
        if fps <= 0.1 or fps > 240.0:
            fps = 25.0
        return max(0, w), max(0, h), fps, max(0, n)
    finally:
        cap.release()


def _probe_frame_count_ffprobe(path: Path) -> int:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_packets",
                "-show_entries",
                "stream=nb_read_packets",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return max(0, int((proc.stdout or "").strip() or 0))
    except Exception:
        pass
    return 0


def safe_video_fps(video_path: Path) -> float:

    """Fps исходника, с запасным значением."""
    _, _, fps, _ = probe_video(video_path)
    return fps if fps > 0.1 else 25.0


def read_video_frames_sequential(video_path: Path | str, indices: list[int]) -> dict[int, np.ndarray]:
    """Читает кадры последовательно (без seek) — надёжно для H.264/MP4."""
    want = {max(0, int(i)) for i in indices}
    if not want:
        return {}
    max_idx = max(want)
    out: dict[int, np.ndarray] = {}
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return out
    try:
        idx = 0
        while idx <= max_idx:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if idx in want:
                out[idx] = frame.copy()
                if len(out) >= len(want):
                    break
            idx += 1
    finally:
        cap.release()
    return out


def cache_json_frames_as_jpeg(
    video_path: Path,
    json_indices: list[int],
    payload: dict,
    cache_dir: Path,
    *,
    mask_h: int,
    mask_w: int,
) -> dict[int, str]:
    """
    Вырезает кадры под индексы data.json → src_XXXXXXXX.jpg.
    Возвращает map json_fidx → path.
    """
    sam_video = video_path.name == "video_sam.mp4"
    unique_json = sorted({int(i) for i in json_indices if int(i) >= 0})
    if not unique_json:
        return {}
    source_by_json = {
        jf: json_frame_to_source_index(jf, payload, sam_video=sam_video) for jf in unique_json
    }
    unique_source = sorted(set(source_by_json.values()))
    frames = read_video_frames_sequential(video_path, unique_source)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[int, str] = {}
    for jf in unique_json:
        sf = source_by_json[jf]
        frame = frames.get(sf)
        if frame is None:
            continue
        if mask_h > 0 and mask_w > 0 and (frame.shape[0] != mask_h or frame.shape[1] != mask_w):
            frame = cv2.resize(frame, (mask_w, mask_h), interpolation=cv2.INTER_LINEAR)
        path = cache_dir / f"src_{int(jf):08d}.jpg"
        if cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95]):
            out[int(jf)] = str(path)
    return out


def renumber_jpeg_sequence(src_dir: Path, pattern: str = "frame_*.jpg") -> int:
    """Переименовывает frame_*.jpg в подряд 000000..N-1 (без дыр для ffmpeg)."""
    files = sorted(src_dir.glob(pattern))
    if not files:
        return 0
    staging = src_dir / "_renum"
    if staging.exists():
        import shutil

        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(files):
        dest = staging / f"frame_{i:06d}.jpg"
        dest.write_bytes(p.read_bytes())
    for p in files:
        p.unlink(missing_ok=True)
    for p in sorted(staging.glob("frame_*.jpg")):
        p.rename(src_dir / p.name)
    staging.rmdir()
    return len(files)
