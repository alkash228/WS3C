from __future__ import annotations

import os
from pathlib import Path

PORT = int(os.environ.get("WEB_SAMV_PORT", "8090"))
MAX_PORT_TRIES = int(os.environ.get("WEB_SAMV_PORT_TRIES", "32"))
API_PORT_HINT = int(os.environ.get("WEB_SAMV_API_PORT", "8000"))
API_HOST_HINT = os.environ.get("WEB_SAMV_API_HOST", "localhost").strip() or "localhost"
API_BASE = os.environ.get("WEB_SAMV_API_BASE", "").strip().rstrip("/")
JOB_TIMEOUT_SEC = int(os.environ.get("WEB_SAMV_JOB_TIMEOUT_SEC", "3600"))
POLL_INTERVAL_SEC = float(os.environ.get("WEB_SAMV_POLL_INTERVAL_SEC", "1.2"))

AN_MIN_INTERSECTION_PX = int(os.environ.get("WEB_SAMV_ANALYZER_MIN_INTERSECTION_PX", "40"))
AN_MIN_INTERSECTION_RATIO = float(os.environ.get("WEB_SAMV_ANALYZER_MIN_INTERSECTION_RATIO", "0.015"))
AN_MIN_MAIN_COVERAGE_RATIO = float(os.environ.get("WEB_SAMV_ANALYZER_MIN_MAIN_COVERAGE_RATIO", "0.008"))
AN_MIN_BBOX_IOU = float(os.environ.get("WEB_SAMV_ANALYZER_MIN_BBOX_IOU", "0.08"))
AN_MIN_INFORMATIVE_FRAMES = int(os.environ.get("WEB_SAMV_ANALYZER_MIN_INFORMATIVE_FRAMES", "8"))
AN_MIN_LINK_FRAME_RATIO = float(os.environ.get("WEB_SAMV_ANALYZER_MIN_LINK_FRAME_RATIO", "0.35"))
AN_MIN_DEP_PRESENT_FRAMES = int(os.environ.get("WEB_SAMV_ANALYZER_MIN_DEP_PRESENT_FRAMES", "3"))
AN_CROP_PAD_RATIO = float(os.environ.get("WEB_SAMV_ANALYZER_CROP_PAD_RATIO", "0.12"))
AN_CONFIDENCE_LOW = float(os.environ.get("WEB_SAMV_ANALYZER_CONFIDENCE_LOW", "0.45"))
AN_CONFIDENCE_HIGH = float(os.environ.get("WEB_SAMV_ANALYZER_CONFIDENCE_HIGH", "0.72"))
AN_MIN_VISIBLE_AREA_PX = int(os.environ.get("WEB_SAMV_ANALYZER_MIN_VISIBLE_AREA_PX", "2200"))
AN_EDGE_MARGIN_RATIO = float(os.environ.get("WEB_SAMV_ANALYZER_EDGE_MARGIN_RATIO", "0.03"))
AN_MIN_VISIBILITY_CONFIDENCE = float(os.environ.get("WEB_SAMV_ANALYZER_MIN_VISIBILITY_CONFIDENCE", "0.55"))
AN_TEMPORAL_CONFIRM_K = int(os.environ.get("WEB_SAMV_ANALYZER_TEMPORAL_CONFIRM_K", "3"))
AN_TEMPORAL_CONFIRM_M = int(os.environ.get("WEB_SAMV_ANALYZER_TEMPORAL_CONFIRM_M", "5"))
AN_TEMPORAL_CLEAR_K = int(os.environ.get("WEB_SAMV_ANALYZER_TEMPORAL_CLEAR_K", "2"))
AN_TEMPORAL_CLEAR_M = int(os.environ.get("WEB_SAMV_ANALYZER_TEMPORAL_CLEAR_M", "3"))
AN_CONFIDENCE_W_VISIBILITY = float(os.environ.get("WEB_SAMV_ANALYZER_CONFIDENCE_W_VISIBILITY", "0.35"))
AN_CONFIDENCE_W_ABSENCE = float(os.environ.get("WEB_SAMV_ANALYZER_CONFIDENCE_W_ABSENCE", "0.35"))
AN_CONFIDENCE_W_TEMPORAL = float(os.environ.get("WEB_SAMV_ANALYZER_CONFIDENCE_W_TEMPORAL", "0.30"))
WORKER_COUNT = int(os.environ.get("WEB_SAMV_WORKERS", "0"))

ROOT = Path(__file__).resolve().parent.parent
FOLDERS_ROOT = (ROOT / "storage" / "folders").resolve()
INF_ROOT = (ROOT / "INF").resolve()
INF_BUILDINGS_FILE = INF_ROOT / "buildings.json"
INF_CONTRACTORS_FILE = INF_ROOT / "contractors.json"
INF_CONTRACTS_FILE = INF_ROOT / "contracts.json"
INF_SCENARIOS_FILE = INF_ROOT / "scenarios.json"
INF_API_PROMPT_FILE = INF_ROOT / "api_prompt.json"

INF_DEFAULT_API_PROMPT = "human . person . head . helmet . vest"

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
REPORT_FORMATS = {"json", "txt", "html", "docx"}

INF_DEFAULTS = {
    "buildings": ["Корпус 1", "Корпус 2"],
    "contractors": ["ПАО Газпром", "ООО Рога и Копыта", "ООО БНАЛ"],
    "contracts": [
        "Договор подряда №1 от 01.01.2026",
        "Договор подряда №2 от 15.02.2026",
    ],
}

INF_DEFAULT_SCENARIOS = [
    {
        "id": "default-vest",
        "title": "Человек без жилета",
        "prompt": "human . vest",
        "enabled": True,
    },
    {
        "id": "default-helmet",
        "title": "Человек без каски",
        "prompt": "human . person . head . helmet",
        "enabled": False,
    },
]

RUNTIME: dict[str, object] = {"web_port": PORT, "api_port": API_PORT_HINT, "lan_ipv4": []}
