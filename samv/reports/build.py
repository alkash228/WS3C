from __future__ import annotations
import datetime as dt
import html
import json
from pathlib import Path
from samv.config import FOLDERS_ROOT, REPORT_FORMATS
from samv.inf.catalog import norm_inf_item
from samv.reports.generate_parallel import write_report_formats
from samv.storage.folders import load_folder_payload

def norm_report_formats(raw: object) -> set[str]:
    """Какие форматы отчета пишем."""
    if not isinstance(raw, list):
        return set(REPORT_FORMATS)
    out: set[str] = set()
    aliases = {"word": "docx", "doc": "docx", "docx": "docx"}
    for x in raw:
        key = str(x or "").strip().lower()
        key = aliases.get(key, key)
        if key in REPORT_FORMATS:
            out.add(key)
    return out or set(REPORT_FORMATS)


def analyzer_mid_warning(warnings: list[dict]) -> dict | None:

    """Первое предупреждение по main_id."""
    if not warnings:
        return None
    idx = len(warnings) // 2
    x = warnings[idx]
    return x if isinstance(x, dict) else None


def norm_report_resolved(raw: object) -> str:

    """Статус: открыто, закрыто и т.д."""
    if isinstance(raw, bool):
        return "Устранено" if raw else "Не устранено"
    s = str(raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low in {"1", "true", "yes", "да", "устранено", "resolved"}:
        return "Устранено"
    if low in {"0", "false", "no", "нет", "не устранено", "open"}:
        return "Не устранено"
    return s[:120]


def norm_report_items(items_raw: object) -> list[dict[str, object]]:

    """Пункты отчета из формы."""
    out: list[dict[str, object]] = []
    if not isinstance(items_raw, list):
        return out
    for x in items_raw:
        if not isinstance(x, dict):
            continue
        hid = str(x.get("human_id", "") or "").strip()
        building = norm_inf_item(str(x.get("building", "") or ""))
        contractor = norm_inf_item(str(x.get("contractor", "") or ""))
        contract = norm_inf_item(str(x.get("contract", "") or ""))
        frame = int(x.get("frame", -1) or -1)
        image_url = str(x.get("image_url", "") or "").strip()
        violations = str(x.get("violations", "") or "").strip()
        deadline = str(x.get("deadline", "") or "").strip()[:32]
        resolved = norm_report_resolved(x.get("resolved", ""))
        reasons_raw = x.get("reasons")
        reasons: list[str] = []
        if isinstance(reasons_raw, list):
            reasons = [str(r or "").strip() for r in reasons_raw if str(r or "").strip()]
        if not violations and reasons:
            violations = "\n".join(reasons)
        if not hid:
            continue
        if not building or not contractor or not contract:
            continue
        out.append(
            {
                "human_id": hid,
                "building": building,
                "contractor": contractor,
                "contract": contract,
                "frame": frame,
                "image_url": image_url,
                "violations": violations,
                "deadline": deadline,
                "resolved": resolved,
                "reasons": reasons,
            }
        )
    return out


def report_image_path(image_url: str) -> Path | None:

    """Путь к картинке из url."""
    s = str(image_url or "").strip().split("?", 1)[0]
    prefix = "/storage/folders/"
    if not s.startswith(prefix):
        return None
    rel = s[len(prefix) :]
    p = (FOLDERS_ROOT / rel).resolve()
    try:
        p.relative_to(FOLDERS_ROOT)
    except ValueError:
        return None
    return p if p.is_file() else None


def report_status_badge(resolved: str) -> str:

    """Цветной бейдж статуса в html."""
    s = str(resolved or "").strip()
    if not s:
        return '<span class="badge badge-muted">—</span>'
    low = s.lower()
    if low in {"устранено", "да", "yes", "resolved"}:
        cls = "badge-ok"
    elif low in {"не устранено", "нет", "no", "open"}:
        cls = "badge-warn"
    else:
        cls = "badge-info"
    return f'<span class="badge {cls}">{html.escape(s)}</span>'


def report_html(report: dict) -> str:

    """Html страница отчета."""
    items = report.get("items")
    if not isinstance(items, list):
        items = []
    rows: list[str] = []
    for n, x in enumerate(items, start=1):
        if not isinstance(x, dict):
            continue
        hid = html.escape(str(x.get("human_id", "")))
        building = html.escape(str(x.get("building", "")))
        contractor = html.escape(str(x.get("contractor", "")))
        contract = html.escape(str(x.get("contract", "")))
        row_img_url = html.escape(str(x.get("image_url", "") or ""))
        violations = html.escape(str(x.get("violations", "") or "")).replace("\n", "<br/>")
        deadline = html.escape(str(x.get("deadline", "") or "")) or "—"
        resolved = report_status_badge(str(x.get("resolved", "") or ""))
        if row_img_url:
            img_cell = (
                f'<div class="photo-wrap"><img class="photo" src="{row_img_url}" '
                f'alt="фото нарушения" /></div>'
            )
        else:
            img_cell = '<span class="muted">—</span>'
        row_cls = "row-even" if n % 2 == 0 else "row-odd"
        rows.append(
            f'<tr class="{row_cls}">'
            f'<td class="col-num">{n}</td>'
            f'<td class="col-id">{hid}</td>'
            f'<td class="col-photo">{img_cell}</td>'
            f"<td>{building}</td>"
            f"<td>{contractor}</td>"
            f"<td>{contract}</td>"
            f'<td class="col-viol">{violations or "—"}</td>'
            f'<td class="col-date">{deadline}</td>'
            f'<td class="col-status">{resolved}</td>'
            "</tr>"
        )
    table_html = "\n".join(rows) if rows else '<tr><td colspan="9" class="empty">Нет записей</td></tr>'
    gen_at = html.escape(str(report.get("generated_at", "")))
    folder = html.escape(str(report.get("folder", "")))
    rep_id = html.escape(str(report.get("id", "")))
    frames = html.escape(str(report.get("frames_checked", "")))
    warns = html.escape(str(report.get("warnings_count", "")))
    row_count = len(rows)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Отчёт {rep_id}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font: 14px/1.5 "Segoe UI", Arial, sans-serif;
      margin: 0;
      padding: 28px 20px 40px;
      background: linear-gradient(160deg, #e8eef8 0%, #f5f7fb 45%, #eef2f9 100%);
      color: #1a2332;
    }}
    .page {{ max-width: 1280px; margin: 0 auto; }}
    .header {{
      background: linear-gradient(135deg, #1e3a6e 0%, #2d5aa8 55%, #3d6fbf 100%);
      color: #fff;
      border-radius: 14px;
      padding: 22px 26px;
      margin-bottom: 20px;
      box-shadow: 0 8px 24px rgba(30, 58, 110, 0.22);
    }}
    .header h1 {{ margin: 0 0 6px; font-size: 22px; font-weight: 600; }}
    .header .sub {{ opacity: 0.9; font-size: 13px; }}
    .stats {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 16px; }}
    .stat {{
      background: rgba(255,255,255,0.14);
      border: 1px solid rgba(255,255,255,0.22);
      border-radius: 10px;
      padding: 10px 14px;
      min-width: 140px;
    }}
    .stat .lbl {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.85; }}
    .stat .val {{ font-size: 15px; font-weight: 600; margin-top: 4px; }}
    .table-card {{
      background: #fff;
      border-radius: 14px;
      border: 1px solid #d4deef;
      box-shadow: 0 4px 18px rgba(26, 35, 50, 0.07);
      overflow: hidden;
    }}
    .table-title {{
      padding: 14px 20px;
      border-bottom: 1px solid #e4ebf6;
      font-size: 16px;
      font-weight: 600;
      color: #1e3a6e;
    }}
    .table-scroll {{ overflow-x: auto; }}
    table.report {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    table.report thead th {{
      background: #eef3fb;
      color: #1e3a6e;
      font-weight: 600;
      text-align: left;
      padding: 11px 10px;
      border-bottom: 2px solid #c5d4eb;
      white-space: nowrap;
    }}
    table.report tbody td {{
      padding: 10px;
      border-bottom: 1px solid #e8edf5;
      vertical-align: top;
    }}
    table.report tbody tr.row-even {{ background: #fafbfd; }}
    table.report tbody tr:hover {{ background: #f0f5fd; }}
    .col-num {{ width: 36px; text-align: center; color: #6b7a90; font-weight: 600; }}
    .col-id {{ width: 56px; font-weight: 600; color: #2d5aa8; }}
    .col-photo {{ width: 150px; text-align: center; }}
    .col-viol {{ min-width: 200px; max-width: 320px; }}
    .col-date {{ width: 110px; white-space: nowrap; }}
    .col-status {{ width: 130px; text-align: center; }}
    .photo-wrap {{
      display: inline-block;
      padding: 4px;
      background: #f4f7fb;
      border: 1px solid #d4deef;
      border-radius: 8px;
    }}
    .photo {{
      display: block;
      max-width: 128px;
      max-height: 96px;
      border-radius: 4px;
      object-fit: contain;
    }}
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
    }}
    .badge-ok {{ background: #e6f6ec; color: #1a7a3e; border: 1px solid #b8e6c8; }}
    .badge-warn {{ background: #fff4e5; color: #9a5b00; border: 1px solid #ffd89a; }}
    .badge-info {{ background: #e8f0ff; color: #1e4a8c; border: 1px solid #c5d8f5; }}
    .badge-muted {{ background: #f0f2f5; color: #6b7a90; border: 1px solid #dde2ea; }}
    .muted {{ color: #8a96a8; }}
    td.empty {{ text-align: center; padding: 24px; color: #8a96a8; }}
    @media print {{
      body {{ background: #fff; padding: 0; }}
      .header {{ box-shadow: none; }}
      .table-card {{ box-shadow: none; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <header class="header">
      <h1>Отчёт о выявленных нарушениях</h1>
      <p class="sub">WEB_samv · {rep_id}</p>
      <div class="stats">
        <div class="stat"><div class="lbl">Сформирован</div><div class="val">{gen_at}</div></div>
        <div class="stat"><div class="lbl">Папка</div><div class="val">{folder}</div></div>
        <div class="stat"><div class="lbl">Проверено кадров</div><div class="val">{frames}</div></div>
        <div class="stat"><div class="lbl">WARNING</div><div class="val">{warns}</div></div>
        <div class="stat"><div class="lbl">Записей</div><div class="val">{row_count}</div></div>
      </div>
    </header>
    <section class="table-card">
      <div class="table-title">Реестр нарушений</div>
      <div class="table-scroll">
        <table class="report">
          <thead>
            <tr>
              <th>№</th>
              <th>ID</th>
              <th>Фото</th>
              <th>Здание</th>
              <th>Подрядчик</th>
              <th>Договор подряда</th>
              <th>Перечень нарушений</th>
              <th>Срок устранения</th>
              <th>Отметка об устранении</th>
            </tr>
          </thead>
          <tbody>
            {table_html}
          </tbody>
        </table>
      </div>
    </section>
  </div>
</body>
</html>
"""



def report_docx_embed_image(cell, img_path: Path) -> None:


    """Вставляем картинку в docx."""
    try:
        from docx.shared import Cm
    except ImportError as exc:
        raise RuntimeError("python-docx не установлен (pip install python-docx)") from exc
    cell.text = ""
    p = cell.paragraphs[0]
    try:
        p.add_run().add_picture(str(img_path), width=Cm(2.8))
    except Exception:
        try:
            from PIL import Image

            with Image.open(img_path) as im:
                im = im.convert("RGB")
                tmp = img_path.parent / f"_docx_{img_path.stem}.jpg"
                im.save(tmp, format="JPEG", quality=85)
            p.add_run().add_picture(str(tmp), width=Cm(2.8))
        except Exception:
            p.add_run("—")


def report_docx(report: dict, docx_path: Path) -> None:

    """Сохраняем docx отчет."""
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError as exc:
        raise RuntimeError("python-docx не установлен (pip install python-docx)") from exc

    doc = Document()
    doc.add_heading("Отчёт о выявленных нарушениях", level=1)
    doc.add_paragraph(f"WEB_samv · {report.get('id', '')}")
    meta = [
        ("Сформирован", str(report.get("generated_at", ""))),
        ("Папка", str(report.get("folder", ""))),
        ("WARNING (всего)", str(report.get("warnings_count", ""))),
        ("Записей в отчёте", str(len(report.get("items") or []))),
    ]
    for label, value in meta:
        p = doc.add_paragraph()
        p.add_run(f"{label}: ").bold = True
        p.add_run(value)

    items = report.get("items")
    if not isinstance(items, list):
        items = []
    doc.add_paragraph()
    h = doc.add_paragraph("Реестр нарушений")
    if h.runs:
        h.runs[0].bold = True
        h.runs[0].font.size = Pt(13)

    headers = [
        "№",
        "ID",
        "Фото",
        "Здание",
        "Подрядчик",
        "Договор подряда",
        "Перечень нарушений",
        "Срок устранения",
        "Отметка",
    ]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for i, text in enumerate(headers):
        hdr[i].text = text
        for p in hdr[i].paragraphs:
            for r in p.runs:
                r.bold = True
                r.font.size = Pt(9)

    for n, x in enumerate(items, start=1):
        if not isinstance(x, dict):
            continue
        row = table.add_row().cells
        row[0].text = str(n)
        row[1].text = str(x.get("human_id", ""))
        img_path = report_image_path(str(x.get("image_url", "") or ""))
        if img_path:
            report_docx_embed_image(row[2], img_path)
        else:
            row[2].text = "—"
        row[3].text = str(x.get("building", ""))
        row[4].text = str(x.get("contractor", ""))
        row[5].text = str(x.get("contract", ""))
        row[6].text = str(x.get("violations", "") or "—")
        row[7].text = str(x.get("deadline", "") or "—")
        row[8].text = str(x.get("resolved", "") or "—")

    doc.save(str(docx_path))


def generate_report(
    folder: str,
    frame: int,
    image_url: str,
    items: list[dict[str, object]],
    formats: set[str] | None = None,
) -> dict[str, object]:

    """Генерация отчета по папке."""
    folder_path, _, _ = load_folder_payload(folder)
    analysis_dir = folder_path / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    result_path = analysis_dir / "analyzer_result.json"
    if not result_path.is_file():
        raise FileNotFoundError("Run analysis first")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid analyzer_result.json")
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    mid = analyzer_mid_warning(warnings)
    if frame < 0 and isinstance(mid, dict):
        frame = int(mid.get("frame", -1) or -1)
    if not image_url and isinstance(mid, dict):
        image_url = str(mid.get("image_url", "") or "")
    report_id = dt.datetime.now().strftime("report_%Y%m%d_%H%M%S")
    report = {
        "schema": "samv_report_v2",
        "id": report_id,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "folder": folder,
        "frames_checked": int(payload.get("frames_checked", 0) or 0),
        "warnings_count": int(payload.get("warnings_count", 0) or 0),
        "items": items,
    }
    json_name = f"{report_id}.json"
    txt_name = f"{report_id}.txt"
    html_name = f"{report_id}.html"
    docx_name = f"{report_id}.docx"
    json_path = analysis_dir / json_name
    txt_path = analysis_dir / txt_name
    html_path = analysis_dir / html_name
    docx_path = analysis_dir / docx_name
    fmts = formats if formats else set(REPORT_FORMATS)
    txt_lines = [
        "WEB_samv report",
        f"id: {report_id}",
        f"generated_at: {report['generated_at']}",
        f"folder: {folder}",
        f"frames_checked: {report['frames_checked']}",
        f"warnings_count: {report['warnings_count']}",
    ]
    for x in items:
        txt_lines.extend(
            [
                f"--- human_id:{x.get('human_id')} ---",
                f"building: {x.get('building')}",
                f"contractor: {x.get('contractor')}",
                f"contract: {x.get('contract')}",
                f"deadline: {x.get('deadline', '')}",
                f"resolved: {x.get('resolved', '')}",
                f"image: {x.get('image_url', '')}",
                "violations:",
                str(x.get("violations", "") or ""),
            ]
        )
    return write_report_formats(
        folder,
        analysis_dir,
        report,
        report_id,
        txt_lines,
        fmts,
        write_html=report_html,
        write_docx=report_docx,
    )
