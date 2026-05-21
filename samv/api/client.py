from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import cv2

from samv.config import API_BASE, API_HOST_HINT, API_PORT_HINT, JOB_TIMEOUT_SEC, POLL_INTERVAL_SEC, VIDEO_EXTS
from samv.masks.core import upscale_result_payload_inplace
from samv.storage.folders import folder_paths
from samv.utils import safe_name

def new_folder_name() -> str:
    """Имя новой папки под прогон."""
    return dt.datetime.now().strftime("run_%Y%m%d_%H%M%S")


def effective_api_base() -> str:

    """Откуда дергаем API — из настроек или localhost."""
    base = str(API_BASE or "").strip().rstrip("/")
    if base:
        return base
    return f"http://{API_HOST_HINT}:{API_PORT_HINT}"


def discovered_lan_ipv4() -> list[str]:

    """Наши IP в сети, чтобы дать ссылку с телефона."""
    out: list[str] = []
    seen: set[str] = set()

    def add(ip: str) -> None:
        """Кладем ip в список если он нормальный."""
        if not ip or ip.startswith("127."):
            return
        if ip not in seen:
            seen.add(ip)
            out.append(ip)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 1))
            add(s.getsockname()[0])
    except OSError:
        pass
    try:
        hn = socket.gethostname()
        for res in socket.getaddrinfo(hn, None, socket.AF_INET, socket.SOCK_STREAM):
            add(res[4][0])
    except OSError:
        pass
    return out


def join_api(path: str) -> str:

    """Склеиваем базовый URL и путь."""
    return urljoin(effective_api_base() + "/", path.lstrip("/"))


def http_json_get(path: str) -> dict:

    """GET и парсим JSON."""
    req = Request(join_api(path), method="GET")
    with urlopen(req, timeout=120) as resp:
        raw = resp.read()
    out = json.loads(raw.decode("utf-8"))
    return out if isinstance(out, dict) else {}


def http_bytes_get(path: str) -> bytes:

    """GET и забираем байты."""
    req = Request(join_api(path), method="GET")
    with urlopen(req, timeout=300) as resp:
        return resp.read()


def api_health_status() -> dict[str, object]:

    """Жив ли бэкенд SAM3."""
    base = effective_api_base()
    try:
        j = http_json_get("/health")
        ok = str(j.get("status", "")).lower() == "ok"
        return {"ok": ok, "api_base": base, "raw": j}
    except Exception as exc:
        return {"ok": False, "api_base": base, "error": str(exc)}


def set_api_base(new_base: str) -> None:

    """Меняем адрес API в рантайме."""
    global API_BASE
    base = str(new_base or "").strip().rstrip("/")
    if not base:
        API_BASE = ""
        return
    if not (base.startswith("http://") or base.startswith("https://")):
        raise ValueError("api_base must start with http:// or https://")
    API_BASE = base


def multipart_body(fields: dict[str, str], file_field: str, file_name: str, file_bytes: bytes) -> tuple[bytes, str]:

    """Собираем тело multipart для загрузки видео."""
    boundary = f"----samv-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    crlf = b"\r\n"
    for k, v in fields.items():
        chunks.append(f"--{boundary}".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{k}"'.encode("utf-8"))
        chunks.append(b"")
        chunks.append(str(v).encode("utf-8"))
    chunks.append(f"--{boundary}".encode("utf-8"))
    chunks.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"'.encode("utf-8")
    )
    chunks.append(b"Content-Type: application/octet-stream")
    chunks.append(b"")
    chunks.append(file_bytes)
    chunks.append(f"--{boundary}--".encode("utf-8"))
    chunks.append(b"")
    body = crlf.join(chunks)
    return body, boundary


def video_meta_from_bytes(video_bytes: bytes, suffix: str = ".mp4") -> tuple[int, int, float] | None:

    """Ширина, высота и fps из куска видео."""
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = Path(tmp.name)
        cap = cv2.VideoCapture(str(tmp_path))
        try:
            if not cap.isOpened():
                return None
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            if w > 0 and h > 0:
                return w, h, fps
            return None
        finally:
            cap.release()
    except Exception:
        return None
    finally:
        try:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def video_duration_sec(video_bytes: bytes, suffix: str) -> float:

    """Длина ролика в секундах."""
    ext = suffix if suffix.startswith(".") else f".{suffix}"
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = Path(tmp.name)
        cap = cv2.VideoCapture(str(tmp_path))
        if cap.isOpened():
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            cap.release()
            if fps > 0 and frames > 0:
                return frames / fps
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return float((proc.stdout or "0").strip() or 0)
    except Exception:
        return 0.0
    finally:
        try:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    return 0.0


def ffmpeg_cut_segment(video_bytes: bytes, suffix: str, start_sec: float, dur_sec: float) -> bytes | None:

    """Вырезаем кусок ffmpeg-ом."""
    ext = suffix if suffix.startswith(".") else f".{suffix}"
    src_path: Path | None = None
    out_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as src:
            src.write(video_bytes)
            src_path = Path(src.name)
        out_path = src_path.with_suffix(".part.mp4")
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_sec:.3f}",
            "-i",
            str(src_path),
            "-t",
            f"{dur_sec:.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not out_path.is_file():
            return None
        return out_path.read_bytes()
    finally:
        for p in (src_path, out_path):
            if p is None:
                continue
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


def split_video_chunks(video_bytes: bytes, suffix: str, part_sec: float) -> list[bytes]:
    """Режем на куски; если part_sec 0 — отдаем все видео одним куском."""
    sec = float(part_sec or 0)
    if sec <= 0:
        return [video_bytes]
    duration = video_duration_sec(video_bytes, suffix)
    if duration <= 0 or duration <= sec + 0.05:
        return [video_bytes]
    out: list[bytes] = []
    start = 0.0
    while start < duration - 0.02:
        chunk = ffmpeg_cut_segment(video_bytes, suffix, start, sec)
        if chunk:
            out.append(chunk)
        start += sec
    return out or [video_bytes]


def video_part_sec_from_form(form) -> float:

    """Сколько секунд в одном куске — из формы."""
    raw = str(form.getfirst("video_part_sec", "") or form.getfirst("part_sec", "") or "0").strip()
    try:
        sec = float(raw or 0)
    except Exception:
        sec = 0.0
    return max(0.0, sec)


def merge_chunk_payloads(payloads: list[dict]) -> dict:

    """Склеиваем json после нескольких job-ов."""
    if not payloads:
        return {}
    if len(payloads) == 1:
        return dict(payloads[0])
    merged: dict[str, object] = {
        "schema": payloads[0].get("schema", ""),
        "width": payloads[0].get("width", 0),
        "height": payloads[0].get("height", 0),
        "frames": [],
    }
    offset = 0
    for p in payloads:
        frames = p.get("frames")
        if not isinstance(frames, list):
            continue
        for fr in frames:
            if not isinstance(fr, dict):
                continue
            nf = dict(fr)
            try:
                nf["frame"] = int(nf.get("frame", 0)) + offset
            except Exception:
                nf["frame"] = offset
            merged["frames"].append(nf)
        offset = len(merged["frames"])
    return merged


def transform_video_bytes_ffmpeg(
    video_bytes: bytes,
    *,
    suffix: str = ".mp4",
    scale_div: float = 1.0,
    fps_div: int = 1,
    src_fps: float = 0.0,
) -> bytes:

    """Уменьшаем и режем fps через ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src_tmp:
        src_tmp.write(video_bytes)
        src_path = Path(src_tmp.name)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as dst_tmp:
        dst_path = Path(dst_tmp.name)
    vf_filters: list[str] = []
    sd = float(scale_div) if float(scale_div) > 0 else 1.0
    fd = int(fps_div) if int(fps_div) > 0 else 1
    if sd > 1.0001:
        vf_filters.append(f"scale=trunc(iw/{sd}/2)*2:trunc(ih/{sd}/2)*2")
    if fd > 1:
        vf_filters.append(f"select='not(mod(n,{fd}))'")
        vf_filters.append("setpts=N/FRAME_RATE/TB")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src_path),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        str(dst_path),
    ]
    if vf_filters:
        cmd[4:4] = ["-vf", ",".join(vf_filters)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not dst_path.is_file():
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"ffmpeg transform failed: {err[-400:]}")
        return dst_path.read_bytes()
    finally:
        src_path.unlink(missing_ok=True)
        dst_path.unlink(missing_ok=True)


def start_video_job(video_name: str, video_bytes: bytes, prompt: str) -> str:

    """Стартуем infer/video на API."""
    fields = {
        "prompt": prompt,
        "alpha": "0.45",
        "visualize": "true",
        "enable_segmentation": "true",
        "draw_masks": "true",
        "draw_centers": "true",
        "export_stable_json": "false",
    }
    body, boundary = multipart_body(fields, "file", video_name, video_bytes)
    req = Request(join_api("/infer/video"), data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Content-Length", str(len(body)))
    with urlopen(req, timeout=300) as resp:
        raw = resp.read()
    out = json.loads(raw.decode("utf-8"))
    if not isinstance(out, dict):
        raise RuntimeError("Invalid /infer/video response")
    jid = str(out.get("job_id", "") or "")
    if not jid:
        raise RuntimeError("No job_id from /infer/video")
    return jid


def wait_job_done(job_id: str) -> dict:

    """Ждем пока job закончится."""
    started = time.time()
    while True:
        st = http_json_get(f"/jobs/{job_id}")
        status = str(st.get("status", "") or "")
        if status == "done":
            return st
        if status == "failed":
            raise RuntimeError(str(st.get("error", "job failed")))
        if time.time() - started > JOB_TIMEOUT_SEC:
            raise TimeoutError(f"Job timeout after {JOB_TIMEOUT_SEC}s")
        time.sleep(max(0.2, POLL_INTERVAL_SEC))


def save_processed_outputs(
    folder_name: str,
    result: dict,
    fallback_video: bytes,
    upscale_to_wh: tuple[int, int] | None = None,
    processing_meta: dict[str, object] | None = None,
) -> None:

    """Качаем json и видео с API в папку."""
    folder, json_path, _ = folder_paths(folder_name)
    folder.mkdir(parents=True, exist_ok=True)

    preferred_urls = [
        str(result.get("download_data_stable_url") or "").strip(),
        str(result.get("download_data_url") or "").strip(),
    ]
    data_url = next((u for u in preferred_urls if u), "")
    raw_json: bytes | None = None
    if data_url:
        last_exc: Exception | None = None
        for u in preferred_urls:
            if not u:
                continue
            try:
                raw_json = http_bytes_get(u)
                data_url = u
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                continue
        if raw_json is None and last_exc is not None:
            raise last_exc
    if raw_json is not None:
        try:
            parsed = json.loads(raw_json.decode("utf-8"))
            if isinstance(parsed, dict) and upscale_to_wh is not None:
                upscale_result_payload_inplace(parsed, int(upscale_to_wh[0]), int(upscale_to_wh[1]))
            if isinstance(parsed, dict) and isinstance(processing_meta, dict):
                parsed["web_samv_processing"] = dict(processing_meta)
                parsed["fast_scale_div"] = float(processing_meta.get("scale_div", 1.0) or 1.0)
                parsed["fps_div"] = int(processing_meta.get("fps_div", 1) or 1)
            json_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            json_path.write_bytes(raw_json)
    else:
        json_path.write_text("{}", encoding="utf-8")

    video_url = str(result.get("download_video_url") or "")
    if video_url:
        vb = http_bytes_get(video_url)
        ext = Path(urlparse(video_url).path).suffix.lower() or ".mp4"
        if ext not in VIDEO_EXTS:
            ext = ".mp4"
        (folder / f"video{ext}").write_bytes(vb)
    else:
        (folder / "video.mp4").write_bytes(fallback_video)

