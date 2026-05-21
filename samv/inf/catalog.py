from __future__ import annotations
import json
import re
from pathlib import Path
from samv.config import (
    FOLDERS_ROOT,
    INF_BUILDINGS_FILE,
    INF_CONTRACTORS_FILE,
    INF_CONTRACTS_FILE,
    INF_DEFAULTS,
    INF_ROOT,
)

def ensure_inf_dirs() -> None:
    """Создаем INF и json если нет."""
    FOLDERS_ROOT.mkdir(parents=True, exist_ok=True)
    INF_ROOT.mkdir(parents=True, exist_ok=True)
    if not INF_BUILDINGS_FILE.is_file():
        INF_BUILDINGS_FILE.write_text(
            json.dumps(INF_DEFAULTS["buildings"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if not INF_CONTRACTORS_FILE.is_file():
        INF_CONTRACTORS_FILE.write_text(
            json.dumps(INF_DEFAULTS["contractors"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if not INF_CONTRACTS_FILE.is_file():
        INF_CONTRACTS_FILE.write_text(
            json.dumps(INF_DEFAULTS["contracts"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def norm_inf_item(raw: str) -> str:

    """Чистим строку справочника."""
    s = str(raw or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:120].strip()


def norm_inf_kind(kind: str) -> str:

    """Тип справочника: здание, подрядчик и т.д."""
    key = str(kind or "").strip().lower()
    aliases = {
        "building": "buildings",
        "contractor": "contractors",
        "contract": "contracts",
        "subcontract": "contracts",
        "subcontracts": "contracts",
    }
    return aliases.get(key, key)


def kind_to_inf_path(kind: str) -> Path:

    """Файл json под тип справочника."""
    key = norm_inf_kind(kind)
    mapping = {
        "buildings": INF_BUILDINGS_FILE,
        "contractors": INF_CONTRACTORS_FILE,
        "contracts": INF_CONTRACTS_FILE,
    }
    path = mapping.get(key)
    if path is None:
        raise ValueError("kind must be 'buildings', 'contractors' or 'contracts'")
    return path


def defaults_for_inf_path(path: Path) -> list[str]:

    """Дефолтные значения если файла нет."""
    if path == INF_BUILDINGS_FILE:
        return INF_DEFAULTS["buildings"]
    if path == INF_CONTRACTORS_FILE:
        return INF_DEFAULTS["contractors"]
    return INF_DEFAULTS["contracts"]


def read_inf_list(path: Path, defaults: list[str]) -> list[str]:

    """Читаем список из json."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = []
    out: list[str] = []
    if isinstance(raw, list):
        for x in raw:
            s = norm_inf_item(str(x or ""))
            if s and s not in out:
                out.append(s)
    if not out:
        out = [norm_inf_item(x) for x in defaults if norm_inf_item(x)]
    return out


def write_inf_list(path: Path, values: list[str]) -> None:

    """Пишем список в json."""
    uniq: list[str] = []
    for x in values:
        s = norm_inf_item(x)
        if s and s not in uniq:
            uniq.append(s)
    path.write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")


def inf_options() -> dict[str, list[str]]:

    """Все справочники разом для UI."""
    ensure_inf_dirs()
    return {
        "inf_version": 2,
        "buildings": read_inf_list(INF_BUILDINGS_FILE, INF_DEFAULTS["buildings"]),
        "contractors": read_inf_list(INF_CONTRACTORS_FILE, INF_DEFAULTS["contractors"]),
        "contracts": read_inf_list(INF_CONTRACTS_FILE, INF_DEFAULTS["contracts"]),
    }
