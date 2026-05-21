from __future__ import annotations

import json
from pathlib import Path

import cv2

from samv.masks.core import draw_danger_frame, frame_instances, mask_for_main_id


def render_warning_jpeg_job(args: tuple) -> dict | None:
    """Воркер: jpeg превью кадра с нарушением."""
    (
        video_path,
        data_json_path,
        folder,
        fidx,
        main_id,
        reasons,
        warn_dir,
        main_prompt,
        h,
        w,
    ) = args
    payload = json.loads(Path(data_json_path).read_text(encoding="utf-8"))
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, float(fidx))
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        if frame.shape[0] != h or frame.shape[1] != w:
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR)
        inst = frame_instances(payload, fidx)
        main_mask = mask_for_main_id(inst, main_prompt, int(main_id), h, w)
        if main_mask is not None:
            frame = draw_danger_frame(frame, main_mask, list(reasons))
        out_name = f"warn_{int(fidx):06d}_human_{int(main_id)}.jpg"
        out_path = Path(warn_dir) / out_name
        if not cv2.imwrite(str(out_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92]):
            return None
        return {
            "frame": int(fidx),
            "main_id": int(main_id),
            "reasons": list(reasons),
            "image_url": f"/storage/folders/{folder}/analysis/warnings/{out_name}",
        }
    finally:
        cap.release()
