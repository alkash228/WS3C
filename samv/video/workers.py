from __future__ import annotations

import json
from pathlib import Path

import cv2

from samv.masks.core import draw_danger_frame, frame_instances, mask_for_main_id


def render_clip_frame_job(args: tuple) -> tuple[int, str] | None:
    """Воркер: рисуем один кадр в jpg для склейки mp4."""
    (
        seq,
        video_path,
        data_json_path,
        fidx,
        main_id,
        reasons,
        main_prompt,
        h,
        w,
        h_enc,
        w_enc,
        tmp_dir,
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
        if frame.shape[0] != h_enc or frame.shape[1] != w_enc:
            frame = cv2.resize(frame, (w_enc, h_enc), interpolation=cv2.INTER_LINEAR)
        out_jpg = Path(tmp_dir) / f"frame_{int(seq):06d}.jpg"
        if not cv2.imwrite(str(out_jpg), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 94]):
            return None
        return int(seq), str(out_jpg)
    finally:
        cap.release()
