from __future__ import annotations

import base64
import json
import shutil
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import http.server

from samv.analyzer.runner import run_analysis
from samv.analyzer.video_worker import run_video_build
from samv.api.client import (
    api_health_status,
    effective_api_base,
    set_api_base,
    video_part_sec_from_form,
)
from samv.api.process_upload import run_process_video
from samv.config import API_PORT_HINT, FOLDERS_ROOT, INF_ROOT, PORT, RUNTIME, VIDEO_EXTS
from samv.inf.catalog import (
    defaults_for_inf_path,
    ensure_inf_dirs,
    inf_options,
    kind_to_inf_path,
    norm_inf_item,
    norm_inf_kind,
    read_inf_list,
    write_inf_list,
)
from samv.masks.core import all_prompts
from samv.reports.build import generate_report, norm_report_formats, norm_report_items
from samv.storage.folders import folder_paths, list_folders, load_folder_payload
from samv.storage.folders import processing_stats_all_folders, processing_stats_for_folder
from samv.tasks import task_get, task_set
from samv.http.multipart import field_storage_from_request
from samv.utils import json_response, safe_name


def _analysis_thread_target(folder: str, main_prompt: str, linked_prompts: list[str]) -> None:

    """Поток для запуска анализа."""
    run_analysis(folder, main_prompt, linked_prompts, load_folder_payload)


class SamvHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        """Заголовки ответа."""
        try:
            path_only = (self.path or "").split("?", 1)[0].lower()
            if path_only.endswith((".html", ".htm", ".js", ".css")):
                self.send_header("Cache-Control", "no-store, must-revalidate")
        except Exception:
            pass
        super().end_headers()

    def do_GET(self) -> None:
        """GET запросы."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/__web_samv_hint.json":
            json_response(
                self,
                {
                    "ok": True,
                    "web_port": int(RUNTIME.get("web_port", PORT)),
                    "api_port": int(RUNTIME.get("api_port", API_PORT_HINT)),
                    "lan_ipv4": list(RUNTIME.get("lan_ipv4", [])),
                },
            )
            return
        if path == "/api/folders":
            json_response(self, {"ok": True, "root": str(FOLDERS_ROOT), "items": list_folders()})
            return
        if path == "/api/status":
            st = api_health_status()
            json_response(self, {"ok": True, **st})
            return
        if path == "/api/analyzer/prompts":
            self.analyzer_prompts(parsed)
            return
        if path == "/api/analyzer/result":
            self.analyzer_result(parsed)
            return
        if path == "/api/analyzer/progress":
            self.analyzer_progress(parsed)
            return
        if path == "/api/folders/stats":
            self.folder_stats(parsed)
            return
        if path == "/api/folders/stats_all":
            self.folders_stats_all()
            return
        if path == "/api/inf/options":
            self.inf_options_route()
            return
        return super().do_GET()

    def do_POST(self) -> None:
        """POST запросы."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        try:
            if path == "/api/folders/create":
                self.create_folder()
                return
            if path == "/api/folders/delete":
                self.delete_folder()
                return
            if path == "/api/folders/upload":
                self.upload_to_folder()
                return
            if path == "/api/folders/save_processed":
                self.save_processed_result()
                return
            if path == "/api/process_video":
                self.process_video()
                return
            if path == "/api/status/set":
                self.set_status_api_base()
                return
            if path == "/api/analyzer/run":
                self.analyzer_run()
                return
            if path == "/api/analyzer/video":
                self.analyzer_video()
                return
            if path == "/api/inf/add":
                self.inf_add()
                return
            if path == "/api/inf/delete":
                self.inf_delete()
                return
            if path == "/api/report/generate":
                self.report_generate()
                return
            json_response(self, {"ok": False, "error": "Not found"}, code=404)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def read_json_body(self) -> dict:
        """Тело POST как json."""
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            parsed = json.loads(raw.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def create_folder(self) -> None:
        """Создать папку."""
        ensure_inf_dirs()
        data = self.read_json_body()
        name = safe_name(str(data.get("name", "") or ""))
        if not name:
            json_response(self, {"ok": False, "error": "Invalid folder name"}, code=400)
            return
        folder, _, _ = folder_paths(name)
        folder.mkdir(parents=True, exist_ok=True)
        json_response(self, {"ok": True, "folder": name, "items": list_folders()})

    def delete_folder(self) -> None:
        """Удалить папку."""
        ensure_inf_dirs()
        data = self.read_json_body()
        name = safe_name(str(data.get("name", "") or ""))
        if not name:
            json_response(self, {"ok": False, "error": "Invalid folder name"}, code=400)
            return
        folder, _, _ = folder_paths(name)
        if not folder.is_dir():
            json_response(self, {"ok": False, "error": "Folder not found"}, code=404)
            return
        shutil.rmtree(folder)
        json_response(self, {"ok": True, "items": list_folders()})

    def upload_to_folder(self) -> None:
        """Залить видео или json в папку."""
        ensure_inf_dirs()
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            json_response(self, {"ok": False, "error": "Expected multipart/form-data"}, code=400)
            return

        try:
            form = field_storage_from_request(self.rfile, self.headers)
        except Exception as exc:
            json_response(self, {"ok": False, "error": f"Cannot parse multipart: {exc}"}, code=400)
            return

        folder_name = safe_name(str(form.getfirst("folder", "") or ""))
        if not folder_name:
            json_response(self, {"ok": False, "error": "Need folder field"}, code=400)
            return
        folder, json_path, _ = folder_paths(folder_name)
        folder.mkdir(parents=True, exist_ok=True)

        if "video" in form and getattr(form["video"], "filename", ""):
            video_file = form["video"]
            video_name = str(video_file.filename or "")
            ext = Path(video_name).suffix.lower()
            if ext not in VIDEO_EXTS:
                json_response(self, {"ok": False, "error": "Unsupported video extension"}, code=400)
                return
            for p in folder.iterdir():
                if p.is_file() and p.stem == "video" and p.suffix.lower() in VIDEO_EXTS:
                    p.unlink(missing_ok=True)
            video_dst = folder / f"video{ext}"
            with video_dst.open("wb") as f:
                f.write(video_file.file.read())

        if "json" in form and getattr(form["json"], "filename", ""):
            json_file = form["json"]
            json_name = str(json_file.filename or "")
            if Path(json_name).suffix.lower() != ".json":
                json_response(self, {"ok": False, "error": "JSON file must have .json extension"}, code=400)
                return
            raw = json_file.file.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                json_response(self, {"ok": False, "error": f"Invalid JSON: {exc}"}, code=400)
                return
            json_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")

        json_response(self, {"ok": True, "folder": folder_name, "items": list_folders()})

    def save_processed_result(self) -> None:
        """Сохранить результат обработки с API."""
        ensure_inf_dirs()
        data = self.read_json_body()
        folder_name = safe_name(str(data.get("folder", "") or ""))
        if not folder_name:
            json_response(self, {"ok": False, "error": "Need folder"}, code=400)
            return

        folder, json_path, _ = folder_paths(folder_name)
        folder.mkdir(parents=True, exist_ok=True)

        payload = data.get("data")
        if isinstance(payload, dict):
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        video_b64 = str(data.get("video_base64", "") or "")
        if video_b64:
            ext = str(data.get("video_ext", ".mp4") or ".mp4").lower().strip()
            if not ext.startswith("."):
                ext = f".{ext}"
            if ext not in VIDEO_EXTS:
                json_response(self, {"ok": False, "error": f"Unsupported video_ext: {ext}"}, code=400)
                return
            if "," in video_b64:
                video_b64 = video_b64.split(",", 1)[1]
            try:
                raw = base64.b64decode(video_b64, validate=False)
            except Exception as exc:
                json_response(self, {"ok": False, "error": f"Invalid video_base64: {exc}"}, code=400)
                return
            for p in folder.iterdir():
                if p.is_file() and p.stem == "video" and p.suffix.lower() in VIDEO_EXTS:
                    p.unlink(missing_ok=True)
            (folder / f"video{ext}").write_bytes(raw)

        json_response(self, {"ok": True, "folder": folder_name, "items": list_folders()})

    def process_video(self) -> None:
        """Прогнать новое видео через API."""
        ensure_inf_dirs()
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            json_response(self, {"ok": False, "error": "Expected multipart/form-data"}, code=400)
            return

        try:
            form = field_storage_from_request(self.rfile, self.headers)
        except Exception as exc:
            json_response(self, {"ok": False, "error": f"Cannot parse multipart: {exc}"}, code=400)
            return

        if "video" not in form or not getattr(form["video"], "filename", ""):
            json_response(self, {"ok": False, "error": "Need video file"}, code=400)
            return

        prompt = str(form.getfirst("prompt", "") or "").strip()
        fast_x2 = str(form.getfirst("fast_x2", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
        fps_half = str(form.getfirst("fps_half", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
        raw_scale_div = str(form.getfirst("scale_div", "") or "").strip()
        raw_fps_div = str(form.getfirst("fps_div", "") or "").strip()
        try:
            scale_div = float(raw_scale_div) if raw_scale_div else (2.0 if fast_x2 else 1.0)
        except Exception:
            scale_div = 1.0
        try:
            fps_div = int(raw_fps_div) if raw_fps_div else (2 if fps_half else 1)
        except Exception:
            fps_div = 1
        if scale_div < 1.0:
            scale_div = 1.0
        if fps_div < 1:
            fps_div = 1
        video_part_sec = video_part_sec_from_form(form)
        if not prompt:
            json_response(self, {"ok": False, "error": "Need prompt"}, code=400)
            return

        vf = form["video"]
        video_name = str(vf.filename or "video.mp4")
        ext = Path(video_name).suffix.lower()
        if ext not in VIDEO_EXTS:
            json_response(self, {"ok": False, "error": "Unsupported video extension"}, code=400)
            return
        video_bytes = vf.file.read()
        if not video_bytes:
            json_response(self, {"ok": False, "error": "Video is empty"}, code=400)
            return

        try:
            out = run_process_video(
                video_name=video_name,
                video_bytes=video_bytes,
                ext=ext,
                prompt=prompt,
                scale_div=float(scale_div),
                fps_div=int(fps_div),
                video_part_sec=float(video_part_sec),
            )
        except Exception as exc:
            json_response(self, {"ok": False, "error": f"Process failed: {exc}"}, code=500)
            return

        json_response(self, {"ok": True, "items": list_folders(), **out})

    def set_status_api_base(self) -> None:
        """Поменять URL API из UI."""
        data = self.read_json_body()
        api_base = str(data.get("api_base", "") or "").strip()
        try:
            set_api_base(api_base)
        except Exception as exc:
            json_response(self, {"ok": False, "error": f"Invalid api_base: {exc}"}, code=400)
            return
        json_response(self, {"ok": True, "api_base": effective_api_base()})

    def inf_options_route(self) -> None:
        """Отдать справочники INF."""
        opts = inf_options()
        json_response(self, {"ok": True, **opts, "root": str(INF_ROOT)})

    def inf_add(self) -> None:
        """Добавить строку в справочник."""
        data = self.read_json_body()
        kind = norm_inf_kind(str(data.get("kind", "") or ""))
        value = norm_inf_item(str(data.get("value", "") or ""))
        if not value:
            json_response(self, {"ok": False, "error": "Need value"}, code=400)
            return
        try:
            path = kind_to_inf_path(kind)
            defaults = defaults_for_inf_path(path)
            current = read_inf_list(path, defaults)
            if value not in current:
                current.append(value)
                write_inf_list(path, current)
            json_response(self, {"ok": True, **inf_options()})
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)

    def inf_delete(self) -> None:
        """Удалить строку из справочника."""
        data = self.read_json_body()
        kind = norm_inf_kind(str(data.get("kind", "") or ""))
        value = norm_inf_item(str(data.get("value", "") or ""))
        if not value:
            json_response(self, {"ok": False, "error": "Need value"}, code=400)
            return
        try:
            path = kind_to_inf_path(kind)
            defaults = defaults_for_inf_path(path)
            current = read_inf_list(path, defaults)
            current = [x for x in current if x != value]
            write_inf_list(path, current)
            json_response(self, {"ok": True, **inf_options()})
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)

    def analyzer_prompts(self, parsed) -> None:
        """Промпты по папке."""
        qs = parse_qs(parsed.query or "")
        folder = safe_name((qs.get("folder") or [""])[0])
        if not folder:
            json_response(self, {"ok": False, "error": "Need folder"}, code=400)
            return
        try:
            _, payload, _ = load_folder_payload(folder)
            prompts = all_prompts(payload)
            json_response(self, {"ok": True, "folder": folder, "prompts": prompts})
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)

    def analyzer_result(self, parsed) -> None:
        """Результат анализа json."""
        qs = parse_qs(parsed.query or "")
        folder = safe_name((qs.get("folder") or [""])[0])
        if not folder:
            json_response(self, {"ok": False, "error": "Need folder"}, code=400)
            return
        try:
            folder_path, _, _ = load_folder_payload(folder)
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)
            return
        result_path = folder_path / "analysis" / "analyzer_result.json"
        if not result_path.is_file():
            json_response(self, {"ok": True, "exists": False, "folder": folder})
            return
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Invalid analyzer_result.json")
        except Exception as exc:
            json_response(self, {"ok": False, "error": f"Cannot read analyzer_result.json: {exc}"}, code=500)
            return
        preview_path = folder_path / "analysis" / "warnings_preview.mp4"
        preview_url = ""
        if preview_path.is_file():
            preview_url = f"/storage/folders/{folder}/analysis/warnings_preview.mp4?ts={int(time.time())}"
        json_response(self, {"ok": True, "exists": True, "folder": folder, "preview_video_url": preview_url, **data})

    def folder_stats(self, parsed) -> None:
        """Статистика одной папки."""
        qs = parse_qs(parsed.query or "")
        folder = safe_name((qs.get("folder") or [""])[0])
        if not folder:
            json_response(self, {"ok": False, "error": "Need folder"}, code=400)
            return
        try:
            stats = processing_stats_for_folder(folder)
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)
            return
        json_response(self, {"ok": True, **stats})

    def folders_stats_all(self) -> None:
        """Статистика всех папок."""
        try:
            rows = processing_stats_all_folders()
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=500)
            return
        json_response(self, {"ok": True, "items": rows})

    def analyzer_progress(self, parsed) -> None:
        """Прогресс анализа или видео."""
        qs = parse_qs(parsed.query or "")
        folder = safe_name((qs.get("folder") or [""])[0])
        task = str((qs.get("task") or ["analysis"])[0] or "analysis").strip().lower()
        if task not in ("analysis", "video"):
            task = "analysis"
        if not folder:
            json_response(self, {"ok": False, "error": "Need folder"}, code=400)
            return
        row = task_get(folder, task)
        if not row:
            json_response(
                self,
                {"ok": True, "folder": folder, "task": task, "status": "idle", "percent": 0, "message": ""},
            )
            return
        out = {"ok": True, "folder": folder, "task": task, **row}
        if row.get("status") == "done" and isinstance(row.get("result"), dict):
            out.update(row["result"])
        json_response(self, out)

    def analyzer_run(self) -> None:
        """Старт анализа."""
        data = self.read_json_body()
        folder = safe_name(str(data.get("folder", "") or ""))
        main_prompt = str(data.get("main_prompt", "") or "").strip()
        deps_raw = data.get("linked_prompts", [])
        if not folder or not main_prompt:
            json_response(self, {"ok": False, "error": "Need folder and main_prompt"}, code=400)
            return
        linked_prompts: list[str] = []
        if isinstance(deps_raw, list):
            for x in deps_raw:
                s = str(x or "").strip()
                if s and s != main_prompt and s not in linked_prompts:
                    linked_prompts.append(s)
        linked_prompts = linked_prompts[:2]
        cur = task_get(folder, "analysis")
        if str(cur.get("status", "")) == "running":
            json_response(self, {"ok": False, "error": "Analysis already running for this folder"}, code=409)
            return
        try:
            load_folder_payload(folder)
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)
            return
        task_set(folder, "analysis", status="running", percent=0, done=0, total=0, message="Старт анализа...")
        threading.Thread(
            target=_analysis_thread_target,
            args=(folder, main_prompt, linked_prompts),
            daemon=True,
        ).start()
        json_response(self, {"ok": True, "started": True, "folder": folder, "task": "analysis"})

    def analyzer_video(self) -> None:
        """Старт сборки видео."""
        data = self.read_json_body()
        folder = safe_name(str(data.get("folder", "") or ""))
        if not folder:
            json_response(self, {"ok": False, "error": "Need folder"}, code=400)
            return
        raw_main_id = data.get("main_id", None)
        main_id: int | None = None
        if raw_main_id is not None and str(raw_main_id).strip() != "":
            try:
                main_id = int(raw_main_id)
            except Exception:
                json_response(self, {"ok": False, "error": "main_id must be integer"}, code=400)
                return
        cur = task_get(folder, "video")
        if str(cur.get("status", "")) == "running":
            json_response(self, {"ok": False, "error": "Video build already running for this folder"}, code=409)
            return
        try:
            folder_path, _, video_path = load_folder_payload(folder)
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)
            return
        if video_path is None or not video_path.is_file():
            json_response(self, {"ok": False, "error": "video.* not found in folder"}, code=400)
            return
        result_path = folder_path / "analysis" / "analyzer_result.json"
        if not result_path.is_file():
            json_response(self, {"ok": False, "error": "Run analysis first"}, code=400)
            return
        task_set(folder, "video", status="running", percent=0, done=0, total=0, message="Старт сборки видео...")
        threading.Thread(
            target=run_video_build,
            args=(folder, main_id),
            daemon=True,
        ).start()
        json_response(self, {"ok": True, "started": True, "folder": folder, "task": "video", "main_id": main_id})

    def report_generate(self) -> None:
        """Старт генерации отчета."""
        data = self.read_json_body()
        folder = safe_name(str(data.get("folder", "") or ""))
        if not folder:
            json_response(self, {"ok": False, "error": "Need folder"}, code=400)
            return
        frame = int(data.get("frame", -1) or -1)
        image_url = str(data.get("image_url", "") or "")
        items = norm_report_items(data.get("items"))
        if not items:
            building = norm_inf_item(str(data.get("building", "") or ""))
            contractor = norm_inf_item(str(data.get("contractor", "") or ""))
            if building and contractor:
                contract = norm_inf_item(str(data.get("contract", "") or ""))
                items = [
                    {
                        "human_id": "0",
                        "building": building,
                        "contractor": contractor,
                        "contract": contract,
                        "reasons": [],
                    }
                ]
        if not items:
            json_response(self, {"ok": False, "error": "Need items with per-human selections"}, code=400)
            return
        formats = norm_report_formats(data.get("formats"))
        try:
            out = generate_report(folder, frame, image_url, items, formats=formats)
            json_response(self, out)
        except Exception as exc:
            json_response(self, {"ok": False, "error": f"Report failed: {exc}"}, code=500)

