from __future__ import annotations

from pathlib import Path

import cv2

from samv.config import VIDEO_EXTS


def find_video(folder: Path) -> Path | None:

    """Файл video.* в папке."""
    if not folder.is_dir():
        return None
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS and p.stem == "video":
            return p
    return None


def safe_video_fps(video_path: Path) -> float:

    """Fps исходника, с запасным значением."""
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            return 25.0
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0.1 or fps > 240.0:
            return 25.0
        return fps
    finally:
        cap.release()
