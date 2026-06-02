from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from samv.masks.core import draw_danger_frame, frame_instances, mask_for_main_id, mask_from_rle_row_major

_MASK_PALETTE_BGR = [
    (66, 133, 244),
    (52, 168, 83),
    (0, 188, 212),
    (255, 193, 7),
    (255, 112, 67),
    (171, 71, 188),
    (38, 198, 218),
    (126, 87, 194),
    (255, 167, 38),
    (92, 107, 192),
]


def _stable_color_idx(instance: dict) -> int:
    oid = instance.get("object_id")
    if oid is not None and str(oid).strip() != "":
        try:
            return abs(int(oid))
        except Exception:
            pass
    text = f"{instance.get('prompt_label', '')}:{instance.get('prompt_id', '')}"
    total = 0
    for i, ch in enumerate(text):
        total += (i + 1) * ord(ch)
    return abs(total)


def _apply_colorful_masks(frame: np.ndarray, instances: list[dict], h: int, w: int) -> np.ndarray:
    out = frame.copy()
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        mask = mask_from_rle_row_major(inst.get("mask", {}), h, w)
        if mask is None or not mask.any():
            continue
        color = _MASK_PALETTE_BGR[_stable_color_idx(inst) % len(_MASK_PALETTE_BGR)]
        color_arr = np.array(color, dtype=np.uint8)
        out[mask] = ((out[mask].astype(np.float32) * 0.45) + (color_arr.astype(np.float32) * 0.55)).astype(np.uint8)
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            cv2.drawContours(out, contours, -1, color, 2, lineType=cv2.LINE_AA)
    return out


def render_clip_frame_job(args: tuple) -> tuple[int, str] | None:
    """Воркер: рисуем один кадр в jpg для склейки mp4 (кадр уже вырезан без seek)."""
    (
        seq,
        src_jpg_path,
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
        colorful_masks,
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
    if colorful_masks:
        frame = _apply_colorful_masks(frame, inst, h, w)
    main_mask = mask_for_main_id(inst, main_prompt, int(main_id), h, w)
    if main_mask is not None:
        frame = draw_danger_frame(frame, main_mask, list(reasons))
    elif list(reasons):
        frame = draw_danger_frame(frame, np.zeros((h, w), dtype=bool), list(reasons))
    if frame.shape[0] != h_enc or frame.shape[1] != w_enc:
        frame = cv2.resize(frame, (w_enc, h_enc), interpolation=cv2.INTER_LINEAR)
    out_jpg = Path(tmp_dir) / f"frame_{int(seq):06d}.jpg"
    if not cv2.imwrite(str(out_jpg), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 94]):
        return None
    return int(seq), str(out_jpg)
