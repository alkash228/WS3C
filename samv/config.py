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

AN_MIN_INTERSECTION_PX = int(os.environ.get("WEB_SAMV_ANALYZER_MIN_INTERSECTION_PX", "80"))
AN_MIN_INTERSECTION_RATIO = float(os.environ.get("WEB_SAMV_ANALYZER_MIN_INTERSECTION_RATIO", "0.03"))
AN_CROP_PAD_RATIO = float(os.environ.get("WEB_SAMV_ANALYZER_CROP_PAD_RATIO", "0.12"))
WORKER_COUNT = int(os.environ.get("WEB_SAMV_WORKERS", "0"))

ROOT = Path(__file__).resolve().parent.parent
FOLDERS_ROOT = (ROOT / "storage" / "folders").resolve()
INF_ROOT = (ROOT / "INF").resolve()
INF_BUILDINGS_FILE = INF_ROOT / "buildings.json"
INF_CONTRACTORS_FILE = INF_ROOT / "contractors.json"
INF_CONTRACTS_FILE = INF_ROOT / "contracts.json"

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

RUNTIME: dict[str, object] = {"web_port": PORT, "api_port": API_PORT_HINT, "lan_ipv4": []}
