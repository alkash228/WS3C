from __future__ import annotations

import json
from pathlib import Path

import cv2

from samv.masks.core import draw_danger_frame, frame_instances, mask_for_main_id


def render_warning_jpeg_job(args: tuple) -> dict | None:
    """Воркер: jpeg превью кадра с нарушением (кадр уже вырезан без seek)."""
    (
        src_jpg_path,
        data_json_path,
        folder,
        fidx,
        main_id,
        reasons,
        warn_dir,
        warn_url_base,
        main_prompt,
        h,
        w,
    ) = args
    src_path = Path(str(src_jpg_path))
    if not src_path.is_file():
        return None
    frame = cv2.imread(str(src_path), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    payload = json.loads(Path(data_json_path).read_text(encoding="utf-8"))
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
        "image_url": f"{warn_url_base}/{out_name}",
    }
