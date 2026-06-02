from __future__ import annotations

import threading
import time

from samv.utils import safe_name

_TASK_LOCK = threading.Lock()
_TASKS: dict[str, dict[str, object]] = {}


def task_key(folder: str, task: str) -> str:

    """Ключ задачи в памяти."""
    return f"{safe_name(folder)}:{task}"


def task_get(folder: str, task: str) -> dict[str, object]:

    """Статус задачи анализа или видео."""
    key = task_key(folder, task)
    with _TASK_LOCK:
        row = _TASKS.get(key)
        return dict(row) if isinstance(row, dict) else {}


def task_set(folder: str, task: str, **fields: object) -> None:

    """Обновляем статус задачи."""
    key = task_key(folder, task)
    with _TASK_LOCK:
        row = _TASKS.setdefault(key, {})
        if not isinstance(row, dict):
            row = {}
            _TASKS[key] = row
        row.update(fields)
        row["folder"] = safe_name(folder)
        row["task"] = task
        row["updated_at"] = time.time()


def task_progress(
    folder: str,
    task: str,
    done: int,
    total: int,
    message: str,
    *,
    eta_seconds: float | None = None,
) -> None:

    """Процент и текст для прогресс-бара."""
    total_n = max(1, int(total))
    done_n = max(0, min(int(done), total_n))
    pct = int(round(100.0 * float(done_n) / float(total_n)))
    fields: dict[str, object] = {
        "status": "running",
        "percent": pct,
        "done": done_n,
        "total": total_n,
        "message": str(message or "").strip(),
    }
    if eta_seconds is not None and float(eta_seconds) > 0:
        fields["eta_seconds"] = float(eta_seconds)
    task_set(folder, task, **fields)
