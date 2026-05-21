from __future__ import annotations

import re

import cv2
import numpy as np

from samv import config as cfg


def mask_from_rle_row_major(enc: dict, h: int, w: int) -> np.ndarray | None:

    """RLE в numpy маску."""
    if not isinstance(enc, dict):
        return None
    if str(enc.get("format", "")) != "rle_row_major":
        return None
    if str(enc.get("order", "C")) != "C":
        return None
    eh = int(enc.get("height", 0) or 0)
    ew = int(enc.get("width", 0) or 0)
    if eh != h or ew != w or h <= 0 or w <= 0:
        return None
    counts = enc.get("counts")
    if not isinstance(counts, list):
        return None
    flat = np.zeros((h * w,), dtype=np.uint8)
    pos = 0
    val = 0
    for x in counts:
        n = int(x or 0)
        if n > 0:
            end = min(flat.size, pos + n)
            if val == 1:
                flat[pos:end] = 1
            pos = end
        val = 1 - val
        if pos >= flat.size:
            break
    return flat.reshape((h, w)).astype(bool)


def label_slug(label: str) -> str:

    """Метка в вид для имени файла."""
    return re.sub(r"[^a-z0-9]+", "_", str(label or "").strip().lower()).strip("_")


def label_segments(raw: str) -> list[str]:

    """Разбиваем промпт на части."""
    s = str(raw or "").strip()
    if not s:
        return []
    parts = [str(x or "").strip() for x in re.split(r"[,\n;]+|\s\.\s|\.", s) if str(x or "").strip()]
    if not parts:
        return [s]
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        k = p.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def label_matches(instance_label: str, wanted_label: str) -> bool:

    """Подходит ли метка инстанса."""
    il = str(instance_label or "").strip()
    wl = str(wanted_label or "").strip()
    if not il or not wl:
        return False
    if il == wl:
        return True
    wanted_cf = wl.casefold()
    for seg in label_segments(il):
        if seg.casefold() == wanted_cf:
            return True
    return False


def instance_group_by_label(instances: list[dict], label: str, h: int, w: int) -> list[tuple[int, np.ndarray]]:

    """Группируем маски по метке."""
    out: list[tuple[int, np.ndarray]] = []
    slug = label_slug(label)
    key = f"{slug}_id" if slug else ""
    for x in instances:
        lbl = str(x.get("prompt_label", "") or "").strip()
        if not label_matches(lbl, label):
            continue
        m = mask_from_rle_row_major(x.get("mask", {}), h, w)
        if m is None or not m.any():
            continue
        obj_id = None
        if "object_id" in x:
            try:
                obj_id = int(x.get("object_id"))
            except Exception:
                obj_id = None
        if obj_id is None and key and key in x:
            try:
                obj_id = int(x.get(key))
            except Exception:
                obj_id = None
        if obj_id is None:
            try:
                obj_id = int(x.get("prompt_id"))
            except Exception:
                obj_id = None
        if obj_id is None:
            try:
                obj_id = int(x.get("object_id"))
            except Exception:
                obj_id = 0
        out.append((int(obj_id), m))
    return out


def bbox_from_mask(mask: np.ndarray) -> tuple[int, int, int, int] | None:

    """Прямоугольник вокруг маски."""
    ys, xs = np.where(mask)
    if ys.size == 0 or xs.size == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def mask_overlap_ok(a: np.ndarray, b: np.ndarray) -> bool:

    """Достаточно ли пересечения двух масок."""
    inter = np.logical_and(a, b)
    inter_px = int(inter.sum())
    if inter_px <= 0:
        return False
    if inter_px < int(cfg.AN_MIN_INTERSECTION_PX):
        return False
    b_area = int(b.sum())
    if b_area <= 0:
        return False
    ratio = float(inter_px) / float(b_area)
    return ratio >= float(cfg.AN_MIN_INTERSECTION_RATIO)


def mask_intersects(a: np.ndarray, b: np.ndarray) -> bool:

    """Есть ли хоть одно пересечение."""
    return bool(np.logical_and(a, b).any())


def reason_for_overlay(reason: str) -> str:

    """Текст причины для картинки."""
    s = str(reason or "").strip()
    if "->" in s and "нет " in s and " на кадре" in s:
        left, right = s.split("->", 1)
        dep = right.replace("нет ", "").replace(" на кадре", "").strip()
        return f"{left.strip()} -> no {dep} in frame"
    if "->" in s and "не пересекается с основным" in s:
        left, right = s.split("->", 1)
        dep = right.replace("не пересекается с основным", "").strip()
        return f"{left.strip()} -> no overlap with main ({dep})"
    if "->" in s and " и " in s and "не пересекаются" in s:
        left, right = s.split("->", 1)
        tail = right.replace("не пересекаются", "").strip()
        parts = [x.strip() for x in tail.split(" и ", 1)]
        if len(parts) == 2:
            return f"{left.strip()} -> no overlap: {parts[0]} & {parts[1]}"
    out = re.sub(r"[^ -~]", " ", s)
    out = out.replace("?", " ")
    out = re.sub(r"\s+", " ", out).strip()
    return out or "warning condition failed"


def draw_danger_frame(frame_bgr: np.ndarray, main_mask: np.ndarray, reasons: list[str]) -> np.ndarray:

    """Рисуем кадр с красной зоной."""
    out = frame_bgr.copy()
    bb = bbox_from_mask(main_mask)
    if bb is not None:
        x, y, bw, bh = bb
        cv2.rectangle(out, (x, y), (x + bw - 1, y + bh - 1), (30, 220, 255), 2, lineType=cv2.LINE_AA)
    bar_h = min(56, max(34, int(out.shape[0] * 0.08)))
    overlay = out.copy()
    cv2.rectangle(overlay, (0, 0), (out.shape[1] - 1, bar_h), (0, 0, 180), thickness=-1)
    cv2.addWeighted(overlay, 0.55, out, 0.45, 0.0, dst=out)
    cv2.putText(
        out, "DANGER", (10, int(bar_h * 0.7)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, lineType=cv2.LINE_AA,
    )
    if reasons:
        txt = "; ".join(reason_for_overlay(x) for x in reasons if str(x or "").strip())
        max_chars = max(20, int(out.shape[1] / 9))
        if len(txt) > max_chars:
            txt = txt[: max_chars - 3] + "..."
        cv2.putText(
            out, txt, (10, min(out.shape[0] - 10, bar_h + 24)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (245, 245, 245), 1, lineType=cv2.LINE_AA,
        )
    return out


def frame_instances(payload: dict, fidx: int) -> list[dict]:

    """Инстансы на кадре из json."""
    frames = payload.get("frames")
    if not isinstance(frames, list):
        return []
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        try:
            if int(fr.get("frame", -1)) != int(fidx):
                continue
        except Exception:
            continue
        inst = fr.get("instances")
        return [x for x in inst if isinstance(x, dict)] if isinstance(inst, list) else []
    return []


def mask_for_main_id(instances: list[dict], main_prompt: str, main_id: int, h: int, w: int) -> np.ndarray | None:

    """Маска человека по main_id."""
    for oid, mask in instance_group_by_label(instances, main_prompt, h, w):
        if int(oid) == int(main_id):
            return mask
    return None


def mask_bool_to_rle_row_major(mask_bool: np.ndarray) -> dict[str, object]:

    """Маска обратно в RLE."""
    h, w = int(mask_bool.shape[0]), int(mask_bool.shape[1])
    flat = mask_bool.reshape(-1).astype(np.uint8, copy=False)
    n = int(flat.size)
    if n <= 0:
        return {"height": h, "width": w, "format": "rle_row_major", "order": "C", "counts": []}
    counts: list[int] = []
    cur = int(flat[0])
    run = 1
    for i in range(1, n):
        v = int(flat[i])
        if v == cur:
            run += 1
        else:
            counts.append(run)
            cur = v
            run = 1
    counts.append(run)
    if int(flat[0]) == 1:
        counts.insert(0, 0)
    return {"height": h, "width": w, "format": "rle_row_major", "order": "C", "counts": counts}


def upscale_result_payload_inplace(payload: dict, target_w: int, target_h: int) -> None:

    """Подгоняем координаты под большой размер."""
    src_w = int(payload.get("width", 0) or 0)
    src_h = int(payload.get("height", 0) or 0)
    if src_w <= 0 or src_h <= 0 or target_w <= 0 or target_h <= 0:
        return
    sx = float(target_w) / float(src_w)
    sy = float(target_h) / float(src_h)
    payload["width"] = int(target_w)
    payload["height"] = int(target_h)
    payload["fast_mode"] = "scaled"
    payload["fast_scale"] = float(sx if sx == sy else min(sx, sy))
    frames = payload.get("frames")
    if not isinstance(frames, list):
        return
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        inst = fr.get("instances")
        if not isinstance(inst, list):
            continue
        for x in inst:
            if not isinstance(x, dict):
                continue
            bb = x.get("bbox_xywh")
            if isinstance(bb, list) and len(bb) >= 4:
                try:
                    bx, by, bw, bh = [float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])]
                    x["bbox_xywh"] = [
                        int(round(bx * sx)),
                        int(round(by * sy)),
                        int(round(bw * sx)),
                        int(round(bh * sy)),
                    ]
                except Exception:
                    pass
            cc = x.get("center_xy")
            if isinstance(cc, list) and len(cc) >= 2:
                try:
                    cx, cy = float(cc[0]), float(cc[1])
                    x["center_xy"] = [int(round(cx * sx)), int(round(cy * sy))]
                except Exception:
                    pass
            if "area_px" in x:
                try:
                    x["area_px"] = int(round(float(x.get("area_px", 0)) * sx * sy))
                except Exception:
                    pass
            enc = x.get("mask")
            if isinstance(enc, dict):
                mh = int(enc.get("height", 0) or 0)
                mw = int(enc.get("width", 0) or 0)
                m = mask_from_rle_row_major(enc, mh, mw)
                if m is not None:
                    up = cv2.resize(m.astype(np.uint8), (target_w, target_h), interpolation=cv2.INTER_NEAREST).astype(bool)
                    x["mask"] = mask_bool_to_rle_row_major(up)


def all_prompts(payload: dict) -> list[dict[str, object]]:

    """Список промптов из data.json."""
    seen: dict[str, int] = {}
    declared: list[str] = []
    raw_prompt = str(payload.get("prompt", "") or "").strip()
    if raw_prompt:
        for p in re.split(r"[,\n;]+|\s\.\s|\.", raw_prompt):
            s = str(p or "").strip()
            if s and s not in declared:
                declared.append(s)
    frames = payload.get("frames")
    if not isinstance(frames, list):
        return [{"label": x, "prompt_id": i + 1} for i, x in enumerate(declared)]
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        inst = fr.get("instances")
        if not isinstance(inst, list):
            continue
        for x in inst:
            if not isinstance(x, dict):
                continue
            lbl = str(x.get("prompt_label", "") or "").strip()
            if not lbl:
                continue
            if lbl not in seen:
                try:
                    seen[lbl] = int(x.get("prompt_id"))
                except Exception:
                    seen[lbl] = 0
    for i, lbl in enumerate(declared, start=1):
        if lbl not in seen:
            seen[lbl] = i
    out = [{"label": k, "prompt_id": int(v)} for k, v in seen.items()]
    out.sort(key=lambda r: str(r.get("label", "")))
    return out
