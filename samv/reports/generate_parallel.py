from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from samv.parallel import run_parallel_threads


def write_report_formats(
    folder: str,
    analysis_dir: Path,
    report: dict,
    report_id: str,
    txt_lines: list[str],
    fmts: set[str],
    *,
    write_html: Callable[[dict], str],
    write_docx: Callable[[dict, Path], None],
) -> dict[str, object]:

    """Пишем отчет в выбранных форматах."""
    json_name = f"{report_id}.json"
    txt_name = f"{report_id}.txt"
    html_name = f"{report_id}.html"
    docx_name = f"{report_id}.docx"
    json_path = analysis_dir / json_name
    txt_path = analysis_dir / txt_name
    html_path = analysis_dir / html_name
    docx_path = analysis_dir / docx_name

    jobs: list[tuple[str, Callable[[], object]]] = []

    if "json" in fmts:
        def _json() -> str:
            """Пишем json отчет."""
            json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"/storage/folders/{folder}/analysis/{json_name}"

        jobs.append(("report_json_url", _json))

    if "txt" in fmts:
        def _txt() -> str:
            """Пишем txt отчет."""
            txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
            return f"/storage/folders/{folder}/analysis/{txt_name}"

        jobs.append(("report_txt_url", _txt))

    if "html" in fmts:
        def _html() -> str:
            """Пишем html отчет."""
            html_path.write_text(write_html(report), encoding="utf-8")
            return f"/storage/folders/{folder}/analysis/{html_name}"

        jobs.append(("report_html_url", _html))

    if "docx" in fmts:
        def _docx() -> str:
            """Пишем docx отчет."""
            write_docx(report, docx_path)
            return f"/storage/folders/{folder}/analysis/{docx_name}"

        jobs.append(("report_docx_url", _docx))

    out: dict[str, object] = {"ok": True, "report": report, "formats": sorted(fmts)}
    if len(jobs) <= 1:
        for key, fn in jobs:
            out[key] = fn()
    else:
        urls = run_parallel_threads(jobs)
        out.update(urls)
    return out
