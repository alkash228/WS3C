from __future__ import annotations

from samv.masks.core import instance_group_by_label, mask_intersects, mask_overlap_ok


def warnings_for_frame(
    fr: dict,
    main_prompt: str,
    linked_prompts: list[str],
    h: int,
    w: int,
) -> tuple[int, list[tuple[int, list[str]]]] | None:
    """Один кадр: есть ли нарушения по маскам."""
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

    hits: list[tuple[int, list[str]]] = []
    for main_id, main_mask in main_items:
        local_reasons: list[str] = []
        dep_hits: list[list[tuple[int, object]]] = []

        for dep in linked_prompts:
            dep_items = instance_group_by_label(inst2, dep, h, w)
            if not dep_items:
                local_reasons.append(f"{main_prompt}_id:{main_id} -> нет {dep} на кадре")
                dep_hits.append([])
                continue
            inter = [(dep_id, dep_mask) for dep_id, dep_mask in dep_items if mask_intersects(main_mask, dep_mask)]
            dep_hits.append(inter)
            if not inter:
                local_reasons.append(f"{main_prompt}_id:{main_id} -> {dep} не пересекается с основным")

        if len(linked_prompts) == 2 and len(dep_hits) >= 2 and dep_hits[0] and dep_hits[1]:
            ok_pair = False
            for _, d1 in dep_hits[0]:
                for _, d2 in dep_hits[1]:
                    if mask_overlap_ok(d1, d2):
                        ok_pair = True
                        break
                if ok_pair:
                    break
            if not ok_pair:
                local_reasons.append(
                    f"{main_prompt}_id:{main_id} -> {linked_prompts[0]} и {linked_prompts[1]} не пересекаются"
                )

        if local_reasons:
            hits.append((int(main_id), local_reasons))

    if not hits:
        return None
    return fidx, hits


def scan_frame_job(args: tuple) -> dict | None:
    """Воркер: проверка одного кадра из json."""
    fr, main_prompt, linked, h, w = args
    if not isinstance(fr, dict):
        return None
    row = warnings_for_frame(fr, main_prompt, linked, h, w)
    if row is None:
        return None
    fidx, hits = row
    return {"frame": fidx, "hits": hits}
