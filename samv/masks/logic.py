from __future__ import annotations

import numpy as np

from samv.masks.core import (
    instance_group_by_label,
    mask_link_ratio,
    mask_linked_to_main_ok,
    mask_overlap_ok,
)


def _dep_link_stats_to_target(
    target_masks: list[np.ndarray],
    dep_items: list[tuple[int, object]],
) -> dict:
    if not dep_items:
        return {"present": False, "linked_ok": False, "link_ratio": 0.0}
    if not target_masks:
        return {"present": True, "linked_ok": False, "link_ratio": 0.0}
    best_ratio = 0.0
    linked = False
    for _, dep_mask in dep_items:
        for tgt in target_masks:
            ratio = float(mask_link_ratio(tgt, dep_mask))
            if ratio > best_ratio:
                best_ratio = ratio
            if mask_linked_to_main_ok(tgt, dep_mask):
                linked = True
    return {
        "present": True,
        "linked_ok": linked,
        "link_ratio": float(best_ratio),
    }


def warnings_for_frame(
    fr: dict,
    main_prompt: str,
    linked_prompts: list[str],
    h: int,
    w: int,
) -> tuple[int, list[dict]] | None:
    """Метрики одного кадра: связь СИЗ с human или с предыдущим звеном цепочки."""
    try:
        fidx = int(fr.get("frame", -1))
    except Exception:
        return None
    if fidx < 0:
        return None
    inst = fr.get("instances")
    if not isinstance(inst, list):
        return None
    inst2 = [x for x in inst if isinstance(x, dict)]
    main_items = instance_group_by_label(inst2, main_prompt, h, w)
    if not main_items:
        return None

    rows: list[dict] = []
    for main_id, main_mask in main_items:
        dep_hits: list[list[tuple[int, object]]] = []
        dep_details: list[dict] = []
        target_masks: list[np.ndarray] = [main_mask]
        link_target = main_prompt

        for dep in linked_prompts:
            dep_items = instance_group_by_label(inst2, dep, h, w)
            stats = _dep_link_stats_to_target(target_masks, dep_items)
            inter: list[tuple[int, object]] = []
            for dep_id, dep_mask in dep_items:
                for tgt in target_masks:
                    if mask_linked_to_main_ok(tgt, dep_mask):
                        inter.append((dep_id, dep_mask))
                        break
            dep_hits.append(inter)
            dep_details.append(
                {
                    "dep": dep,
                    "link_target": link_target,
                    "present": bool(stats["present"]),
                    "linked_to_main": bool(stats["linked_ok"]),
                    "linked_ok": bool(stats["linked_ok"]),
                    "link_ratio": float(stats["link_ratio"]),
                },
            )
            if dep_items:
                target_masks = [m for _, m in dep_items]
                link_target = dep
            else:
                link_target = dep

        chain_pairs: list[dict] = []
        for i in range(len(linked_prompts) - 1):
            left_hits = dep_hits[i] if i < len(dep_hits) else []
            right_hits = dep_hits[i + 1] if i + 1 < len(dep_hits) else []
            left_label = linked_prompts[i]
            right_label = linked_prompts[i + 1]
            pair_ok = False
            if left_hits and right_hits:
                for _, d1 in left_hits:
                    for _, d2 in right_hits:
                        if mask_overlap_ok(d1, d2):
                            pair_ok = True
                            break
                    if pair_ok:
                        break
            chain_pairs.append(
                {
                    "left": left_label,
                    "right": right_label,
                    "both_present": bool(left_hits and right_hits),
                    "pair_linked": pair_ok,
                },
            )

        rows.append(
            {
                "main_id": int(main_id),
                "reasons": [],
                "dep_details": dep_details,
                "chain_pairs": chain_pairs,
            },
        )

    return fidx, rows


def scan_frame_job(args: tuple) -> dict | None:
    """Воркер: проверка одного кадра из json."""
    fr, main_prompt, linked, h, w = args
    if not isinstance(fr, dict):
        return None
    row = warnings_for_frame(fr, main_prompt, linked, h, w)
    if row is None:
        return None
    fidx, rows = row
    return {"frame": fidx, "rows": rows, "hits": []}
