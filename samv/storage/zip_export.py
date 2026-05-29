from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path


def build_folder_zip_archive(folder: Path) -> Path:
    """Упаковать папку проекта в zip (временный файл на диске)."""
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(folder.rglob("*")):
                if not path.is_file():
                    continue
                arcname = path.relative_to(folder).as_posix()
                zf.write(path, arcname)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path
