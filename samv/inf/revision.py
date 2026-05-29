from __future__ import annotations

import hashlib

from samv.config import INF_API_BASE_FILE, INF_API_PROMPT_FILE, INF_SCENARIOS_FILE
from samv.inf.catalog import ensure_inf_dirs


def inf_config_revision() -> str:
    """Хеш содержимого INF-настроек (промпт API + сценарии)."""
    ensure_inf_dirs()
    h = hashlib.sha256()
    for path in (INF_API_PROMPT_FILE, INF_SCENARIOS_FILE, INF_API_BASE_FILE):
        if path.is_file():
            try:
                h.update(path.read_bytes())
            except Exception:
                h.update(str(path).encode("utf-8"))
    return h.hexdigest()[:16]
