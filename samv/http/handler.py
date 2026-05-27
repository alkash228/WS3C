from __future__ import annotations

import base64
import json
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import http.server

from samv.analyzer.batch import run_all_scenarios_for_folder
from samv.analyzer.runner import run_analysis
from samv.analyzer.video_worker import run_video_build
from samv.api.client import (
    api_health_status,
    effective_api_base,
    new_folder_name,
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
from samv.analyzer.paths import analyzer_result_path, ensure_unified_analyzer_result
from samv.inf.api_prompt import get_api_prompt, read_api_prompt, write_api_prompt
from samv.inf.scenarios import (
    add_scenario,
    analyzer_params_for_folder,
    delete_scenario,
    get_enabled_scenarios,
    read_folder_meta,
    scenario_to_analyzer,
    scenario_violation_label,
    scenarios_list,
    set_active_scenario,
    set_scenario_enabled,
    write_run_meta,
    write_scenario_meta,
)
from samv.masks.core import all_prompts
from samv.reports.build import generate_report, norm_report_formats, norm_report_items
from samv.storage.folders import folder_paths, invalidate_folders_cache, list_folders, load_folder_payload
from samv.storage.folders import processing_stats_all_folders, processing_stats_for_folder
from samv.tasks import task_get, task_set
from samv.http.multipart import field_storage_from_request
from samv.utils import json_response, safe_name


def _analysis_thread_target(
    folder: str,
    main_prompt: str,
    linked_prompts: list[str],
    scenario_id: str | None = None,
) -> None:

    """Поток для запуска анализа."""
    run_analysis(
        folder,
        main_prompt,
        linked_prompts,
        load_folder_payload,
        scenario_id=scenario_id,
    )


def _analysis_all_thread_target(folder: str) -> None:
    """Поток: все включённые сценарии по папке с data.json."""
    try:
        run_all_scenarios_for_folder(folder, load_folder_payload)
    except Exception as exc:
        task_set(
            folder,
            "analysis",
            status="error",
            percent=0,
            message="Ошибка анализа",
            error=str(exc),
            result=None,
        )


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
        try:
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
            if path == "/api/inf/scenarios":
                self.inf_scenarios_route()
                return
            if path == "/api/inf/api_prompt":
                self.inf_api_prompt_route()
                return
            if path == "/api/folders/meta":
                self.folder_meta(parsed)
                return
            return super().do_GET()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

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
            if path == "/api/analyzer/run_all":
                self.analyzer_run_all()
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
            if path == "/api/inf/scenarios/add":
                self.inf_scenarios_add()
                return
            if path == "/api/inf/scenarios/delete":
                self.inf_scenarios_delete()
                return
            if path == "/api/inf/scenarios/set_active":
                self.inf_scenarios_set_active()
                return
            if path == "/api/inf/scenarios/set_enabled":
                self.inf_scenarios_set_enabled()
                return
            if path == "/api/inf/api_prompt/set":
                self.inf_api_prompt_set()
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
        invalidate_folders_cache()
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
        invalidate_folders_cache()
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
        invalidate_folders_cache()
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
        invalidate_folders_cache()
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
        debug_log: list[str] = []
        api_prompt = get_api_prompt()
        if not api_prompt:
            json_response(
                self,
                {"ok": False, "error": "Не задан промпт SAM API в INF (вкладка «Справочник»)."},
                code=400,
            )
            return
        scenarios = get_enabled_scenarios()
        if not scenarios:
            json_response(
                self,
                {
                    "ok": False,
                    "error": "Нет включённых сценариев анализа в INF. Отметьте на вкладке «Справочник».",
                },
                code=400,
            )
            return
        debug_log.append(f"Промпт SAM API: {api_prompt}")
        debug_log.append(f"Сценариев анализатора: {len(scenarios)}")

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

        folder_name = safe_name(new_folder_name())
        use_subdirs = len(scenarios) > 1
        runs: list[dict[str, object]] = []
        merged_warnings: list[dict[str, object]] = []
        merged_frames_checked = 0
        merged_frames_with_main = 0
        merged_pending_count = 0
        merged_cleared_count = 0
        merged_pending_rows: list[dict[str, object]] = []
        last_out: dict[str, object] = {}

        try:
            debug_log.append("Инференс SAM API (один прогон)…")
            last_out = run_process_video(
                video_name=video_name,
                video_bytes=video_bytes,
                ext=ext,
                prompt=api_prompt,
                scale_div=float(scale_div),
                fps_div=int(fps_div),
                video_part_sec=float(video_part_sec),
                folder_name=folder_name,
            )
            folder_name = safe_name(str(last_out.get("folder", "") or folder_name))
            debug_log.append(f"Инференс завершён: {folder_name}")
        except Exception as exc:
            traceback.print_exc()
            json_response(
                self,
                {"ok": False, "error": f"Process failed: {exc}", "debug": debug_log},
                code=500,
            )
            return

        try:
            folder_path, _, _ = load_folder_payload(folder_name)
            write_run_meta(folder_path, api_prompt)
            debug_log.append("meta.json (промпт API) записан")
        except Exception as exc:
            debug_log.append(f"Ошибка meta API: {exc}")

        for idx, scenario in enumerate(scenarios, start=1):
            chain = str(scenario.get("prompt", "") or "").strip()
            sid = str(scenario.get("id", "") or "").strip()
            if not chain:
                debug_log.append(f"[{idx}] Пропуск «{scenario.get('title', '')}»: пустая цепочка анализатора")
                continue
            try:
                main_prompt, linked_prompts = scenario_to_analyzer(chain)
            except Exception as exc:
                debug_log.append(f"[{idx}] Ошибка разбора цепочки: {exc}")
                continue

            scenario_key = sid if use_subdirs else None
            debug_log.append(f"[{idx}/{len(scenarios)}] Анализ «{scenario.get('title', '')}»")
            debug_log.append(f"  Цепочка: {chain}")
            debug_log.append(f"  main={main_prompt}, linked={linked_prompts}")

            analysis_ok = False
            analysis_error = ""
            try:
                folder_path, _, _ = load_folder_payload(folder_name)
                write_scenario_meta(
                    folder_path,
                    scenario,
                    main_prompt,
                    linked_prompts,
                    scenario_subdir=use_subdirs,
                )
                task_set(
                    folder_name,
                    "analysis",
                    status="running",
                    percent=0,
                    done=0,
                    total=0,
                    message=f"Анализ ({idx}/{len(scenarios)}): {scenario.get('title', '')}…",
                )
                run_analysis(
                    folder_name,
                    main_prompt,
                    linked_prompts,
                    load_folder_payload,
                    scenario_id=scenario_key,
                )
                analysis_ok = True
                debug_log.append("  Анализ завершён")
                result_path = analyzer_result_path(folder_path, scenario_key)
                if result_path.is_file():
                    try:
                        parsed = json.loads(result_path.read_text(encoding="utf-8"))
                        if isinstance(parsed, dict):
                            warn_rows = parsed.get("warnings")
                            if isinstance(warn_rows, list):
                                merged_warnings.extend([w for w in warn_rows if isinstance(w, dict)])
                            merged_frames_checked = max(
                                merged_frames_checked,
                                int(parsed.get("frames_checked", 0) or 0),
                            )
                            merged_frames_with_main = max(
                                merged_frames_with_main,
                                int(parsed.get("frames_with_main", 0) or 0),
                            )
                            merged_pending_count += int(parsed.get("pending_count", 0) or 0)
                            merged_cleared_count += int(parsed.get("cleared_count", 0) or 0)
                            diag = parsed.get("diagnostics")
                            if isinstance(diag, dict):
                                pending_rows = diag.get("pending_by_main")
                                if isinstance(pending_rows, list):
                                    merged_pending_rows.extend([x for x in pending_rows if isinstance(x, dict)])
                    except Exception as exc:
                        debug_log.append(f"  Ошибка чтения результата сценария: {exc}")
            except Exception as exc:
                analysis_error = str(exc)
                debug_log.append(f"  Ошибка анализа: {exc}")

            viol_label = scenario_violation_label(scenario)
            run_row: dict[str, object] = {
                "folder": folder_name,
                "scenario": scenario,
                "scenario_id": sid,
                "analyzer_main": main_prompt,
                "analyzer_linked": linked_prompts,
                "violation_label": viol_label,
                "analysis_ok": analysis_ok,
                "ok": True,
            }
            if analysis_error:
                run_row["analysis_error"] = analysis_error
            runs.append(run_row)

        if not runs:
            json_response(
                self,
                {"ok": False, "error": "Ни один сценарий не обработан", "debug": debug_log},
                code=500,
            )
            return

        last_run = runs[-1]
        last_scenario = last_run.get("scenario") if isinstance(last_run.get("scenario"), dict) else {}
        scenario_titles: list[str] = []
        violation_labels: list[str] = []
        for r in runs:
            sc = r.get("scenario")
            if isinstance(sc, dict):
                title = str(sc.get("title", "") or "").strip()
                if title and title not in scenario_titles:
                    scenario_titles.append(title)
            lbl = str(r.get("violation_label", "") or "").strip()
            if lbl and lbl not in violation_labels:
                violation_labels.append(lbl)
        try:
            folder_path, _, _ = load_folder_payload(folder_name)
            analysis_dir = folder_path / "analysis"
            analysis_dir.mkdir(parents=True, exist_ok=True)
            unified = {
                "schema": "samv_mask_analyzer_v1",
                "folder": folder_name,
                "main_prompt": "multi",
                "linked_prompts": [],
                "frames_checked": int(merged_frames_checked),
                "frames_with_main": int(merged_frames_with_main),
                "warnings_count": int(len(merged_warnings)),
                "pending_count": int(merged_pending_count),
                "cleared_count": int(merged_cleared_count),
                "warnings": merged_warnings,
                "diagnostics": {"pending_by_main": merged_pending_rows},
                "violation_label": str(last_run.get("violation_label", "") or ""),
                "violation_labels": violation_labels,
                "scenario_title": str((last_scenario or {}).get("title", "") or ""),
                "scenario_titles": scenario_titles,
                "scenarios_total": int(len(scenarios)),
                "scenarios_done": int(len(runs)),
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            (analysis_dir / "analyzer_result.json").write_text(
                json.dumps(unified, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            debug_log.append("Единый analyzer_result.json собран")
        except Exception as exc:
            debug_log.append(f"Ошибка сборки единого результата: {exc}")
        invalidate_folders_cache()
        json_response(
            self,
            {
                "ok": True,
                "items": list_folders(),
                "debug": debug_log,
                "batch": use_subdirs,
                "api_prompt": api_prompt,
                "runs": runs,
                "scenarios_total": len(scenarios),
                "scenarios_done": len(runs),
                "scenario": last_scenario,
                "analyzer_main": "multi",
                "analyzer_linked": [],
                "violation_label": last_run.get("violation_label", ""),
                "analysis_started": any(bool(r.get("analysis_ok")) for r in runs),
                "folder": folder_name,
                **last_out,
            },
        )

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

    def inf_scenarios_route(self) -> None:
        """Список сценариев INF."""
        json_response(self, {"ok": True, **scenarios_list()})

    def inf_scenarios_add(self) -> None:
        data = self.read_json_body()
        title = str(data.get("title", "") or "")
        prompt = str(data.get("prompt", "") or "")
        try:
            out = add_scenario(title, prompt)
            json_response(self, {"ok": True, **out})
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)

    def inf_scenarios_delete(self) -> None:
        data = self.read_json_body()
        sid = str(data.get("id", "") or "")
        try:
            out = delete_scenario(sid)
            json_response(self, {"ok": True, **out})
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)

    def inf_scenarios_set_active(self) -> None:
        data = self.read_json_body()
        sid = str(data.get("id", "") or "")
        try:
            out = set_active_scenario(sid)
            json_response(self, {"ok": True, **out})
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)

    def inf_scenarios_set_enabled(self) -> None:
        data = self.read_json_body()
        sid = str(data.get("id", "") or "")
        enabled = data.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
        else:
            enabled = bool(enabled)
        try:
            out = set_scenario_enabled(sid, enabled)
            json_response(self, {"ok": True, **out})
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)

    def inf_api_prompt_route(self) -> None:
        json_response(self, {"ok": True, **read_api_prompt()})

    def inf_api_prompt_set(self) -> None:
        data = self.read_json_body()
        prompt = str(data.get("prompt", "") or "")
        try:
            out = write_api_prompt(prompt)
            json_response(self, {"ok": True, **out})
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)

    def folder_meta(self, parsed) -> None:
        qs = parse_qs(parsed.query or "")
        folder = safe_name((qs.get("folder") or [""])[0])
        if not folder:
            json_response(self, {"ok": False, "error": "Need folder"}, code=400)
            return
        try:
            folder_path, payload, _ = load_folder_payload(folder)
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)
            return
        meta = read_folder_meta(folder_path)
        out: dict[str, object] = {
            "ok": True,
            "folder": folder,
            "exists": meta is not None,
            "api_prompt": str((meta or {}).get("api_prompt", "") or ""),
        }
        if meta:
            out.update(meta)
        try:
            main, linked = analyzer_params_for_folder(folder_path, payload, scenario_id=None)
            out["analyzer_main"] = main
            out["analyzer_linked"] = linked
            out["scenario_prompt"] = str((meta or {}).get("analyzer_chain", "") or (meta or {}).get("scenario_prompt", "") or "")
            out["violation_label"] = str((meta or {}).get("violation_label", "") or "")
            out["scenario_title"] = str((meta or {}).get("scenario_title", "") or "")
        except Exception:
            pass
        json_response(self, out)

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
        result_path = ensure_unified_analyzer_result(folder_path)
        if result_path is None:
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
        analysis_dir = folder_path / "analysis"
        preview_path = analysis_dir / "warnings_preview.mp4"
        preview_url = ""
        human_video_urls: dict[str, str] = {}
        ts = time.time_ns()
        if preview_path.is_file():
            preview_url = f"/storage/folders/{folder}/analysis/warnings_preview.mp4?ts={ts}"
        if analysis_dir.is_dir():
            for p in analysis_dir.glob("warnings_preview_human_*.mp4"):
                stem = str(p.stem or "")
                if not stem.startswith("warnings_preview_human_"):
                    continue
                hid = stem.replace("warnings_preview_human_", "", 1).strip()
                if not hid:
                    continue
                human_video_urls[hid] = f"/storage/folders/{folder}/analysis/{p.name}?ts={ts}"
        json_response(
            self,
            {
                "ok": True,
                "exists": True,
                "folder": folder,
                "preview_video_url": preview_url,
                "human_video_urls": human_video_urls,
                **data,
            },
        )

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
        if not folder:
            json_response(self, {"ok": False, "error": "Need folder"}, code=400)
            return
        main_prompt = str(data.get("main_prompt", "") or "").strip()
        deps_raw = data.get("linked_prompts", [])
        linked_prompts: list[str] = []
        if isinstance(deps_raw, list):
            for x in deps_raw:
                s = str(x or "").strip()
                if s and s != main_prompt and s not in linked_prompts:
                    linked_prompts.append(s)
        try:
            folder_path, payload, _ = load_folder_payload(folder)
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)
            return
        scenario_id = None
        if not main_prompt:
            try:
                main_prompt, linked_prompts = analyzer_params_for_folder(
                    folder_path, payload, scenario_id=scenario_id
                )
            except Exception as exc:
                json_response(self, {"ok": False, "error": str(exc)}, code=400)
                return
        elif not linked_prompts:
            try:
                _, linked_prompts = analyzer_params_for_folder(
                    folder_path, payload, scenario_id=scenario_id
                )
            except Exception:
                pass
        if not main_prompt:
            json_response(self, {"ok": False, "error": "Need main_prompt or meta.json"}, code=400)
            return
        cur = task_get(folder, "analysis")
        if str(cur.get("status", "")) == "running":
            json_response(self, {"ok": False, "error": "Analysis already running for this folder"}, code=409)
            return
        task_set(folder, "analysis", status="running", percent=0, done=0, total=0, message="Старт анализа...")
        threading.Thread(
            target=_analysis_thread_target,
            args=(folder, main_prompt, linked_prompts, scenario_id),
            daemon=True,
        ).start()
        json_response(self, {"ok": True, "started": True, "folder": folder, "task": "analysis"})

    def analyzer_run_all(self) -> None:
        """Повторный анализ всех включённых сценариев по готовой папке."""
        data = self.read_json_body()
        folder = safe_name(str(data.get("folder", "") or ""))
        if not folder:
            json_response(self, {"ok": False, "error": "Need folder"}, code=400)
            return
        try:
            folder_path, _, video_path = load_folder_payload(folder)
        except Exception as exc:
            json_response(self, {"ok": False, "error": str(exc)}, code=400)
            return
        if video_path is None or not video_path.is_file():
            json_response(self, {"ok": False, "error": "video.* not found in folder"}, code=400)
            return
        if not (folder_path / "data.json").is_file():
            json_response(self, {"ok": False, "error": "data.json not found — сначала обработайте видео"}, code=400)
            return
        if not get_enabled_scenarios():
            json_response(
                self,
                {"ok": False, "error": "Нет включённых сценариев в INF"},
                code=400,
            )
            return
        cur = task_get(folder, "analysis")
        if str(cur.get("status", "")) == "running":
            json_response(self, {"ok": False, "error": "Analysis already running for this folder"}, code=409)
            return
        task_set(folder, "analysis", status="running", percent=0, done=0, total=0, message="Старт анализа...")
        threading.Thread(target=_analysis_all_thread_target, args=(folder,), daemon=True).start()
        json_response(self, {"ok": True, "started": True, "folder": folder, "task": "analysis"})

    def analyzer_video(self) -> None:
        """Старт сборки видео."""
        data = self.read_json_body()
        folder = safe_name(str(data.get("folder", "") or ""))
        if not folder:
            json_response(self, {"ok": False, "error": "Need folder"}, code=400)
            return
        colorful_masks = bool(data.get("colorful_masks", False))
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
        result_path = ensure_unified_analyzer_result(folder_path)
        if result_path is None:
            result_path = folder_path / "analysis" / "analyzer_result.json"
        if not result_path.is_file():
            json_response(self, {"ok": False, "error": "Run analysis first"}, code=400)
            return
        task_set(folder, "video", status="running", percent=0, done=0, total=0, message="Старт сборки видео...")
        threading.Thread(
            target=run_video_build,
            args=(folder, main_id, colorful_masks),
            daemon=True,
        ).start()
        json_response(
            self,
            {
                "ok": True,
                "started": True,
                "folder": folder,
                "task": "video",
                "main_id": main_id,
                "colorful_masks": colorful_masks,
            },
        )

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

