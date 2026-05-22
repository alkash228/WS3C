"""Точка входа WEB_samv. Совместимость: import serve as samv (WEB_S_MVP)."""
from __future__ import annotations

from samv.api.client import (
    api_health_status,
    discovered_lan_ipv4,
    effective_api_base,
    fetch_job_result,
    http_bytes_get,
    http_json_get,
    merge_chunk_payloads,
    new_folder_name,
    save_processed_outputs,
    set_api_base,
    split_video_chunks,
    start_video_job,
    transform_video_bytes_ffmpeg,
    upscale_result_payload_inplace,
    video_meta_from_bytes,
    video_part_sec_from_form,
    wait_job_done,
)
from samv.config import (
    API_BASE,
    API_HOST_HINT,
    API_PORT_HINT,
    FOLDERS_ROOT,
    INF_ROOT,
    PORT,
    REPORT_FORMATS,
    ROOT,
    RUNTIME,
    VIDEO_EXTS,
)
from samv.http.handler import SamvHandler
from samv.http.server import ReusableTCPServer, main
from samv.inf.catalog import ensure_inf_dirs
from samv.masks.core import (
    all_prompts,
    draw_danger_frame,
    instance_group_by_label,
    mask_intersects,
    mask_overlap_ok,
)
from samv.storage.folders import folder_paths, list_folders, load_folder_payload
from samv.utils import json_response, safe_name

Handler = SamvHandler
_ReusableTCPServer = ReusableTCPServer

_ensure_dirs = ensure_inf_dirs
_json_response = json_response
_safe_name = safe_name
_set_api_base = set_api_base
_load_folder_payload = load_folder_payload
_instance_group_by_label = instance_group_by_label
_mask_intersects = mask_intersects
_mask_overlap_ok = mask_overlap_ok
_draw_danger_crop = draw_danger_frame
_video_meta_from_bytes = video_meta_from_bytes
_new_folder_name = new_folder_name
_folder_paths = folder_paths
_transform_video_bytes_ffmpeg = transform_video_bytes_ffmpeg
_start_video_job = start_video_job
_wait_job_done = wait_job_done
_http_json_get = http_json_get
_save_processed_outputs = save_processed_outputs
_http_bytes_get = http_bytes_get
_upscale_result_payload_inplace = upscale_result_payload_inplace
_discovered_lan_ipv4 = discovered_lan_ipv4

REPORT_ROOT = ROOT / "report"

__all__ = ["main", "Handler", "SamvHandler"]

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
