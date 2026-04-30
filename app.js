async function apiGet(url) {
  const r = await fetch(url, { cache: "no-store" });
  return r.json();
}

async function apiPost(url, body, isForm = false) {
  const init = { method: "POST" };
  if (isForm) {
    init.body = body;
  } else {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body || {});
  }
  const r = await fetch(url, init);
  return r.json();
}

function esc(s) {
  return String(s || "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

let selectedFolder = "";
let lastWarnings = [];
let infOptions = { buildings: [], contractors: [] };
let currentMiddleWarning = null;
let middleWarningByHumanId = {};

function setBuildVideoEnabled(enabled) {
  const btn = document.getElementById("build-video-btn");
  if (btn) btn.disabled = !enabled;
}

function setReportEnabled() {
  const btn = document.getElementById("gen-inline-report-btn");
  if (!btn) return;
  const rows = Array.from(document.querySelectorAll(".human-row"));
  if (!selectedFolder || !rows.length) {
    btn.disabled = true;
    return;
  }
  const ok = rows.every((row) => {
    const b = String(row.querySelector(".human-building")?.value || "").trim();
    const c = String(row.querySelector(".human-contractor")?.value || "").trim();
    return !!(b && c);
  });
  btn.disabled = !ok;
}

function setSelectOptions(selectEl, labels, includeEmpty = true) {
  if (!selectEl) return;
  const prev = String(selectEl.value || "");
  selectEl.innerHTML = "";
  if (includeEmpty) {
    const o0 = document.createElement("option");
    o0.value = "";
    o0.textContent = "— не выбрано —";
    selectEl.appendChild(o0);
  }
  labels.forEach((lbl) => {
    const o = document.createElement("option");
    o.value = lbl;
    o.textContent = lbl;
    selectEl.appendChild(o);
  });
  if (prev && labels.includes(prev)) selectEl.value = prev;
}

function resetWarningVideo() {
  const box = document.getElementById("warning-video-box");
  const vid = document.getElementById("warning-video");
  if (vid) {
    vid.pause();
    vid.removeAttribute("src");
    vid.load();
  }
  if (box) box.classList.add("hidden");
}

function showWarningVideo(url) {
  const box = document.getElementById("warning-video-box");
  const vid = document.getElementById("warning-video");
  if (!box || !vid || !url) return;
  vid.src = String(url);
  vid.load();
  box.classList.remove("hidden");
}

function groupWarningsByMainId(items) {
  const grouped = {};
  (Array.isArray(items) ? items : []).forEach((w) => {
    const key = (w && w.main_id !== undefined && w.main_id !== null)
      ? String(w.main_id)
      : (() => {
          const ids = extractHumanIdsFromWarning(w);
          return ids.length ? String(ids[0]) : "0";
        })();
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(w);
  });
  return grouped;
}

function extractHumanIdsFromWarning(warn) {
  const ids = [];
  const directId = warn?.main_id;
  if (directId !== undefined && directId !== null && String(directId).trim() !== "") {
    ids.push(String(directId));
    return ids;
  }
  const reasons = Array.isArray(warn?.reasons) ? warn.reasons : [];
  reasons.forEach((txt) => {
    const s = String(txt || "");
    const m = s.match(/human_id:(-?\d+)/g);
    if (!m) return;
    m.forEach((token) => {
      const id = token.split(":")[1];
      if (id != null && !ids.includes(id)) ids.push(id);
    });
  });
  if (!ids.length) ids.push("0");
  return ids;
}

function fillRowSelects() {
  document.querySelectorAll(".human-row").forEach((row) => {
    const bSel = row.querySelector(".human-building");
    const cSel = row.querySelector(".human-contractor");
    if (bSel) {
      const prev = String(bSel.value || "");
      setSelectOptions(bSel, infOptions.buildings || [], true);
      if (!bSel.value && infOptions.buildings?.length) bSel.value = infOptions.buildings[0];
      if (prev && Array.isArray(infOptions.buildings) && infOptions.buildings.includes(prev)) bSel.value = prev;
      bSel.addEventListener("change", setReportEnabled);
    }
    if (cSel) {
      const prev = String(cSel.value || "");
      setSelectOptions(cSel, infOptions.contractors || [], true);
      if (!cSel.value && infOptions.contractors?.length) cSel.value = infOptions.contractors[0];
      if (prev && Array.isArray(infOptions.contractors) && infOptions.contractors.includes(prev)) cSel.value = prev;
      cSel.addEventListener("change", setReportEnabled);
    }
  });
  setReportEnabled();
}

function activateTab(tabId) {
  document.querySelectorAll(".tab-pane").forEach((el) => {
    el.classList.toggle("hidden", el.id !== tabId);
  });
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-tab") === tabId);
  });
  if (tabId === "stats-tab") {
    loadStatsTable();
  }
}

function fmtNum(v, digits = 2) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  return digits <= 0 ? String(Math.round(n)) : n.toFixed(digits);
}

function renderStatsTable(items) {
  const box = document.getElementById("stats-table-box");
  if (!box) return;
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) {
    box.innerHTML = "<p class='muted'>Нет обработанных видео с data.json.</p>";
    return;
  }
  const rows = arr.map((s) => {
    const srcWh = `${Number(s?.source_width || 0)}x${Number(s?.source_height || 0)}`;
    const procWh = `${Number(s?.processed_width || 0)}x${Number(s?.processed_height || 0)}`;
    const fpsPair = `${fmtNum(s?.source_fps || 0, 2)} -> ${fmtNum(s?.processed_fps || 0, 2)}`;
    const folder = String(s?.folder || "");
    const isSelected = selectedFolder && folder === selectedFolder;
    return `<tr${isSelected ? " class=\"stats-row-selected\"" : ""}>
      <td>${esc(folder || "-")}</td>
      <td>${esc(String(s?.video_name || "-"))}</td>
      <td>${esc(`D/${fmtNum(s?.scale_div || 1, 2)} | FPS/${fmtNum(s?.fps_div || 1, 0)}`)}</td>
      <td>${esc(`${srcWh} -> ${procWh}`)}</td>
      <td>${esc(fpsPair)}</td>
      <td>${esc(fmtNum(s?.frames_total || 0, 0))}</td>
      <td>${esc(fmtNum(s?.frames_with_instances || 0, 0))}</td>
      <td>${esc(fmtNum(s?.instances_total || 0, 0))}</td>
      <td>${esc(fmtNum(s?.labels_count || 0, 0))}</td>
      <td>${esc(fmtNum(s?.elapsed_sec || 0, 2))}</td>
    </tr>`;
  }).join("");
  box.innerHTML = `<table class="stats-table">
    <thead>
      <tr>
        <th>Папка</th>
        <th>Видео</th>
        <th>Downscale/FPS</th>
        <th>Размер (src -> proc)</th>
        <th>FPS (src -> proc)</th>
        <th>Кадры</th>
        <th>Кадры с инстансами</th>
        <th>Инстансы</th>
        <th>Метки</th>
        <th>Время, сек</th>
      </tr>
    </thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function loadStatsTable() {
  const hint = document.getElementById("stats-folder-hint");
  const st = document.getElementById("stats-status");
  const box = document.getElementById("stats-table-box");
  if (hint) hint.textContent = selectedFolder ? `Выбрана папка: ${selectedFolder}` : "Папка не выбрана";
  if (st) st.textContent = "Загрузка статистики...";
  const out = await apiGet("/api/folders/stats_all");
  if (!out.ok) {
    if (st) st.textContent = `Ошибка статистики: ${out.error || "unknown"}`;
    if (box) box.innerHTML = "";
    return;
  }
  renderStatsTable(out.items || []);
  if (st) st.textContent = "Статистика обновлена.";
}

function renderWarnings(items) {
  const box = document.getElementById("warnings-list");
  const repStatus = document.getElementById("report-status");
  const repResult = document.getElementById("report-result");
  lastWarnings = Array.isArray(items) ? items : [];
  currentMiddleWarning = null;
  middleWarningByHumanId = {};
  if (repStatus) repStatus.textContent = "";
  if (repResult) repResult.innerHTML = "";
  setBuildVideoEnabled(lastWarnings.length > 0 && !!selectedFolder);
  setReportEnabled();
  resetWarningVideo();
  if (!Array.isArray(items) || !items.length) {
    box.innerHTML = "<p class='muted'>WARNING не найдено.</p>";
    return;
  }
  const grouped = groupWarningsByMainId(items);
  const ids = Object.keys(grouped).sort((a, b) => Number(a) - Number(b));
  ids.forEach((hid) => {
    const arr = grouped[hid] || [];
    middleWarningByHumanId[hid] = arr[Math.floor(arr.length / 2)] || null;
  });
  currentMiddleWarning = middleWarningByHumanId[ids[0]] || null;
  const blocks = ids.map((hid) => {
    const arr = grouped[hid] || [];
    const mid = middleWarningByHumanId[hid] || {};
    return `
      <article class="warn-item warn-layout">
        <div>
          <img src="${esc(mid.image_url || "")}" alt="warning frame" />
          <div class="row">
            <button type="button" class="build-one-video-btn" data-human-id="${esc(hid)}">Сделать видео human_id:${esc(hid)}</button>
          </div>
          <video class="warning-video-one hidden" data-human-id="${esc(hid)}" controls preload="metadata"></video>
        </div>
        <div class="warn-side">
          <strong>human_id:${esc(hid)} | кадр ${Number(mid.frame || 0)}</strong>
          <div class="muted">WARNING для этого human_id: ${arr.length}</div>
          <div class="muted">${esc((mid.reasons || []).join(" | "))}</div>
          <div class="inline-report">
            <h3>Назначение для human_id:${esc(hid)}</h3>
            <div class="human-row" data-human-id="${esc(hid)}">
              <strong>human_id:${esc(hid)}</strong>
              <label>Здание
                <select class="human-building"></select>
              </label>
              <label>Подрядчик
                <select class="human-contractor"></select>
              </label>
            </div>
          </div>
        </div>
      </article>
    `;
  }).join("");
  box.innerHTML = `
    <p class="muted">Раздельно по каждому human_id. Всего WARNING: ${items.length}</p>
    ${blocks}
    <div class="row">
      <button id="gen-inline-report-btn" type="button">Сформировать общий отчет</button>
    </div>
  `;
  fillRowSelects();
  box.querySelectorAll(".build-one-video-btn").forEach((btn) => {
    btn.addEventListener("click", () => buildVideoForOneHuman(String(btn.getAttribute("data-human-id") || "")));
  });
  document.getElementById("gen-inline-report-btn")?.addEventListener("click", generateInlineReport);
  setReportEnabled();
}

function fillInfSelects() {
  setSelectOptions(document.getElementById("inf-buildings-list"), infOptions.buildings || [], false);
  setSelectOptions(document.getElementById("inf-contractors-list"), infOptions.contractors || [], false);
  fillRowSelects();
  setReportEnabled();
}

async function loadInfOptions() {
  const st = document.getElementById("inf-status");
  const hint = document.getElementById("inf-root-hint");
  const out = await apiGet("/api/inf/options");
  if (!out.ok) {
    if (st) st.textContent = `Ошибка INF: ${out.error || "unknown"}`;
    return;
  }
  infOptions = {
    buildings: Array.isArray(out.buildings) ? out.buildings.map((x) => String(x || "")).filter(Boolean) : [],
    contractors: Array.isArray(out.contractors) ? out.contractors.map((x) => String(x || "")).filter(Boolean) : [],
  };
  if (hint) hint.textContent = `INF: ${out.root || "-"}`;
  if (st) st.textContent = `Загружено: зданий ${infOptions.buildings.length}, подрядчиков ${infOptions.contractors.length}`;
  fillInfSelects();
}

async function addInf(kind, inputId) {
  const st = document.getElementById("inf-status");
  const inp = document.getElementById(inputId);
  const value = String(inp?.value || "").trim();
  if (!value) {
    if (st) st.textContent = "Введите значение для добавления.";
    return;
  }
  const out = await apiPost("/api/inf/add", { kind, value });
  if (!out.ok) {
    if (st) st.textContent = `Ошибка INF: ${out.error || "unknown"}`;
    return;
  }
  infOptions = { buildings: out.buildings || [], contractors: out.contractors || [] };
  if (inp) inp.value = "";
  fillInfSelects();
  if (st) st.textContent = "Добавлено.";
}

async function delInf(kind, selectId) {
  const st = document.getElementById("inf-status");
  const sel = document.getElementById(selectId);
  const value = String(sel?.value || "").trim();
  if (!value) {
    if (st) st.textContent = "Выберите значение для удаления.";
    return;
  }
  const out = await apiPost("/api/inf/delete", { kind, value });
  if (!out.ok) {
    if (st) st.textContent = `Ошибка INF: ${out.error || "unknown"}`;
    return;
  }
  infOptions = { buildings: out.buildings || [], contractors: out.contractors || [] };
  fillInfSelects();
  if (st) st.textContent = "Удалено.";
}

async function loadPromptsForFolder(folder) {
  const main = document.getElementById("main-prompt");
  const p1 = document.getElementById("linked-prompt-1");
  const p2 = document.getElementById("linked-prompt-2");
  const anFolder = document.getElementById("an-folder");
  document.getElementById("report-status").textContent = "";
  document.getElementById("report-result").innerHTML = "";
  if (!folder) {
    anFolder.textContent = "Папка не выбрана";
    setSelectOptions(main, [], false);
    setSelectOptions(p1, [], true);
    setSelectOptions(p2, [], true);
    lastWarnings = [];
    setBuildVideoEnabled(false);
    setReportEnabled();
    resetWarningVideo();
    return;
  }
  anFolder.textContent = `Папка: ${folder}`;
  const res = await apiGet(`/api/analyzer/prompts?folder=${encodeURIComponent(folder)}`);
  if (!res.ok) {
    setSelectOptions(main, [], false);
    setSelectOptions(p1, [], true);
    setSelectOptions(p2, [], true);
    document.getElementById("analysis-status").textContent = `Ошибка чтения промтов: ${res.error || "unknown"}`;
    return;
  }
  const labels = (Array.isArray(res.prompts) ? res.prompts : []).map((x) => String(x.label || "")).filter(Boolean);
  setSelectOptions(main, labels, false);
  setSelectOptions(p1, labels, true);
  setSelectOptions(p2, labels, true);
  document.getElementById("analysis-status").textContent = `Найдено промтов: ${labels.length}`;
  await loadSavedAnalysis(folder);
}

async function loadSavedAnalysis(folder) {
  const status = document.getElementById("analysis-status");
  const box = document.getElementById("warnings-list");
  try {
    const out = await apiGet(`/api/analyzer/result?folder=${encodeURIComponent(folder)}`);
    if (!out.ok) {
      status.textContent = `Ошибка чтения сохраненного анализа: ${out.error || "unknown"}`;
      return;
    }
    if (!out.exists) {
      lastWarnings = [];
      setBuildVideoEnabled(false);
      setReportEnabled();
      resetWarningVideo();
      box.innerHTML = "<p class='muted'>Анализ еще не запускался для этой папки.</p>";
      return;
    }
    const warnings = Array.isArray(out.warnings) ? out.warnings : [];
    renderWarnings(warnings);
    status.textContent = `Загружен сохраненный анализ. Проверено: ${Number(out.frames_checked || 0)}, WARNING: ${Number(out.warnings_count || 0)}`;
    if (out.preview_video_url) showWarningVideo(out.preview_video_url);
  } catch (e) {
    status.textContent = `Ошибка чтения сохраненного анализа: ${e?.message || e}`;
  }
}

async function refreshList() {
  const box = document.getElementById("list");
  box.innerHTML = "<p class='muted'>Загрузка...</p>";
  const res = await apiGet("/api/folders");
  if (!res.ok) {
    box.innerHTML = `<p class='err'>Ошибка: ${esc(res.error || "unknown")}</p>`;
    return;
  }
  const rootHint = document.getElementById("root-hint");
  if (rootHint) rootHint.textContent = `Папки читаются из: ${res.root || "-"}`;
  const items = Array.isArray(res.items) ? res.items : [];
  if (!items.length) {
    box.innerHTML = "<p class='muted'>Папок пока нет.</p>";
    selectedFolder = "";
    lastWarnings = [];
    setBuildVideoEnabled(false);
    setReportEnabled();
    resetWarningVideo();
    await loadPromptsForFolder("");
    return;
  }
  box.innerHTML = items.map((x) => `
    <article class="item">
      <div>
        <strong>${esc(x.name)}</strong>
        <div class="muted">
          video: ${x.has_video ? esc(x.video_name || "yes") : "нет"},
          json: ${x.has_json ? "есть" : "нет"},
          updated: ${esc(x.updated_at || "")}
        </div>
      </div>
      <div class="row">
        <button type="button" data-open="${esc(x.name)}">Открыть</button>
        <button type="button" data-del="${esc(x.name)}">Удалить</button>
      </div>
    </article>
  `).join("");
  box.querySelectorAll("button[data-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = btn.getAttribute("data-del");
      if (!name) return;
      if (!confirm(`Удалить папку ${name}?`)) return;
      const out = await apiPost("/api/folders/delete", { name });
      if (!out.ok) alert(`Ошибка: ${out.error || "unknown"}`);
      await refreshList();
    });
  });
  box.querySelectorAll("button[data-open]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = btn.getAttribute("data-open");
      if (!name) return;
      selectedFolder = name;
      lastWarnings = [];
      setBuildVideoEnabled(false);
      setReportEnabled();
      resetWarningVideo();
      await loadPromptsForFolder(selectedFolder);
      await loadStatsTable();
    });
  });
  const names = items.map((x) => String(x.name || "")).filter(Boolean);
  if (!selectedFolder || !names.includes(selectedFolder)) {
    selectedFolder = names[0] || "";
    lastWarnings = [];
    setBuildVideoEnabled(false);
    setReportEnabled();
    resetWarningVideo();
    await loadPromptsForFolder(selectedFolder);
    await loadStatsTable();
  }
}

async function refreshApiStatus() {
  const el = document.getElementById("api-status");
  if (!el) return;
  el.textContent = "API: проверка...";
  try {
    const st = await apiGet("/api/status");
    if (!st.ok) {
      el.textContent = "API: статус недоступен";
      return;
    }
    const base = st.api_base || "-";
    const inp = document.getElementById("api-base-input");
    if (inp && base && inp.value.trim() === "") inp.value = base;
    if (st.error) {
      el.textContent = `API: ${base} | нет связи (${st.error})`;
      return;
    }
    el.textContent = st.ok ? `API: ${base} | связь есть` : `API: ${base} | нет связи`;
  } catch (e) {
    el.textContent = `API: ошибка проверки (${e?.message || e})`;
  }
}

async function connectApiBase() {
  const inp = document.getElementById("api-base-input");
  const el = document.getElementById("api-status");
  const base = String(inp?.value || "").trim().replace(/\/+$/, "");
  if (!base) {
    el.textContent = "API: введите URL";
    return;
  }
  try {
    const out = await apiPost("/api/status/set", { api_base: base });
    if (!out.ok) {
      el.textContent = `API: ошибка (${out.error || "unknown"})`;
      return;
    }
    el.textContent = `API: подключен ${out.api_base}`;
    await refreshApiStatus();
  } catch (e) {
    el.textContent = `API: ошибка подключения (${e?.message || e})`;
  }
}

document.getElementById("process-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const prompt = document.getElementById("prompt").value.trim();
  const video = document.getElementById("video").files[0];
  const fastX2 = !!document.getElementById("fast-x2")?.checked;
  const fpsHalf = !!document.getElementById("fps-half")?.checked;
  const scaleDivInp = document.getElementById("scale-div");
  const fpsDivInp = document.getElementById("fps-div");
  let scaleDiv = Number(scaleDivInp?.value || "1");
  let fpsDiv = Number(fpsDivInp?.value || "1");
  if (!Number.isFinite(scaleDiv) || scaleDiv < 1) scaleDiv = 1;
  if (!Number.isFinite(fpsDiv) || fpsDiv < 1) fpsDiv = 1;
  scaleDiv = Math.round(scaleDiv * 100) / 100;
  fpsDiv = Math.max(1, Math.round(fpsDiv));
  const status = document.getElementById("status");
  const btn = document.getElementById("process-btn");
  if (!video) return alert("Выберите видео");
  if (!prompt) return alert("Введите промпт");
  const fd = new FormData();
  fd.append("video", video);
  fd.append("prompt", prompt);
  fd.append("fast_x2", fastX2 ? "true" : "false");
  fd.append("fps_half", fpsHalf ? "true" : "false");
  fd.append("scale_div", String(scaleDiv));
  fd.append("fps_div", String(fpsDiv));
  const tags = [];
  if (scaleDiv > 1) tags.push(`D/${scaleDiv}`);
  if (fpsDiv > 1) tags.push(`FPS/${fpsDiv}`);
  status.textContent = tags.length
    ? `Обработка началась (режим ${tags.join(" + ")})...`
    : "Обработка началась...";
  btn.disabled = true;
  const res = await apiPost("/api/process_video", fd, true);
  btn.disabled = false;
  if (!res.ok) {
    status.textContent = `Ошибка: ${res.error || "unknown"}`;
    return;
  }
  const doneTags = [];
  if (Number(res.scale_div || 1) > 1) doneTags.push(`D/${Number(res.scale_div)}`);
  if (Number(res.fps_div || 1) > 1) doneTags.push(`FPS/${Number(res.fps_div)}`);
  status.textContent = `Готово. Создана папка: ${res.folder || "-"}${doneTags.length ? ` (${doneTags.join(" + ")})` : ""}`;
  document.getElementById("video").value = "";
  await refreshList();
});

document.getElementById("run-analysis-btn").addEventListener("click", async () => {
  const status = document.getElementById("analysis-status");
  if (!selectedFolder) {
    status.textContent = "Сначала откройте папку.";
    return;
  }
  const mainPrompt = String(document.getElementById("main-prompt").value || "").trim();
  const p1 = String(document.getElementById("linked-prompt-1").value || "").trim();
  const p2 = String(document.getElementById("linked-prompt-2").value || "").trim();
  if (!mainPrompt) {
    status.textContent = "Выберите основной промт.";
    return;
  }
  const linked = [];
  if (p1 && p1 !== mainPrompt) linked.push(p1);
  if (p2 && p2 !== mainPrompt && p2 !== p1) linked.push(p2);
  status.textContent = "Анализ по маскам...";
  setBuildVideoEnabled(false);
  resetWarningVideo();
  const out = await apiPost("/api/analyzer/run", {
    folder: selectedFolder,
    main_prompt: mainPrompt,
    linked_prompts: linked,
  });
  if (!out.ok) {
    status.textContent = `Ошибка анализа: ${out.error || "unknown"}`;
    renderWarnings([]);
    return;
  }
  status.textContent = `Готово. Проверено: ${out.frames_checked}, WARNING: ${out.warnings_count}`;
  renderWarnings(out.warnings || []);
});

document.getElementById("build-video-btn").addEventListener("click", async () => {
  const status = document.getElementById("analysis-status");
  const btn = document.getElementById("build-video-btn");
  const progress = document.getElementById("video-progress");
  const box = document.getElementById("warning-video-box");
  const vid = document.getElementById("warning-video");
  if (!selectedFolder) {
    status.textContent = "Сначала откройте папку.";
    return;
  }
  if (!Array.isArray(lastWarnings) || !lastWarnings.length) {
    status.textContent = "Нет warning-кадров для сборки видео.";
    return;
  }
  btn.disabled = true;
  progress?.classList.remove("hidden");
  box?.classList.add("hidden");
  status.textContent = "Сборка видео...";
  try {
    const out = await apiPost("/api/analyzer/video", { folder: selectedFolder });
    if (!out.ok) {
      status.textContent = `Ошибка сборки видео: ${out.error || "unknown"}`;
      return;
    }
    if (vid) {
      vid.src = String(out.video_url || "");
      vid.load();
    }
    box?.classList.remove("hidden");
    status.textContent = `Видео готово. Кадров: ${Number(out.frames_used || 0)}, FPS: ${Number(out.fps || 0).toFixed(2)}`;
  } catch (e) {
    status.textContent = `Ошибка сборки видео: ${e?.message || e}`;
  } finally {
    progress?.classList.add("hidden");
    setBuildVideoEnabled(Array.isArray(lastWarnings) && lastWarnings.length > 0 && !!selectedFolder);
  }
});

async function generateInlineReport() {
  const st = document.getElementById("report-status");
  const outBox = document.getElementById("report-result");
  if (!selectedFolder) {
    st.textContent = "Сначала выберите папку.";
    return;
  }
  const rows = Array.from(document.querySelectorAll(".human-row"));
  if (!rows.length) {
    st.textContent = "Нет данных по людям на фото.";
    return;
  }
  const items = rows.map((row) => {
    const hid = String(row.getAttribute("data-human-id") || "").trim();
    const building = String(row.querySelector(".human-building")?.value || "").trim();
    const contractor = String(row.querySelector(".human-contractor")?.value || "").trim();
    const mid = middleWarningByHumanId[hid] || null;
    const reasons = Array.isArray(mid?.reasons) ? mid.reasons : [];
    return {
      human_id: hid,
      building,
      contractor,
      reasons,
      frame: Number(mid?.frame ?? -1),
      image_url: String(mid?.image_url || ""),
    };
  });
  if (items.some((x) => !x.building || !x.contractor)) {
    st.textContent = "Заполните здание и подрядчика для каждого human_id.";
    return;
  }
  st.textContent = "Генерация отчета...";
  const out = await apiPost("/api/report/generate", {
    folder: selectedFolder,
    frame: -1,
    image_url: "",
    items,
  });
  if (!out.ok) {
    st.textContent = `Ошибка отчета: ${out.error || "unknown"}`;
    return;
  }
  st.textContent = "Отчет готов.";
  const rep = out.report || {};
  outBox.innerHTML = `
    <div class="report-box">
      <div><strong>${esc(rep.id || "")}</strong></div>
      <div class="muted">Людей в отчете: ${Array.isArray(rep.items) ? rep.items.length : 0}</div>
      <div class="muted">WARNING: ${Number(rep.warnings_count || 0)}</div>
      <div class="row">
        <a href="${esc(out.report_json_url || "#")}" target="_blank" rel="noopener">JSON</a>
        <a href="${esc(out.report_txt_url || "#")}" target="_blank" rel="noopener">TXT</a>
        <a href="${esc(out.report_html_url || "#")}" target="_blank" rel="noopener">HTML</a>
      </div>
    </div>
  `;
}

async function buildVideoForOneHuman(humanId) {
  const st = document.getElementById("analysis-status");
  const hid = String(humanId || "").trim();
  if (!selectedFolder || !hid) return;
  const btn = document.querySelector(`.build-one-video-btn[data-human-id="${hid}"]`);
  const vid = document.querySelector(`.warning-video-one[data-human-id="${hid}"]`);
  if (btn) btn.disabled = true;
  if (st) st.textContent = `Сборка видео для human_id:${hid}...`;
  try {
    const out = await apiPost("/api/analyzer/video", { folder: selectedFolder, main_id: Number(hid) });
    if (!out.ok) {
      if (st) st.textContent = `Ошибка видео human_id:${hid}: ${out.error || "unknown"}`;
      return;
    }
    if (vid) {
      vid.src = String(out.video_url || "");
      vid.load();
      vid.classList.remove("hidden");
    }
    if (st) st.textContent = `Видео готово для human_id:${hid}. Кадров: ${Number(out.frames_used || 0)}`;
  } catch (e) {
    if (st) st.textContent = `Ошибка видео human_id:${hid}: ${e?.message || e}`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.getElementById("inf-building-add").addEventListener("click", () => addInf("buildings", "inf-building-input"));
document.getElementById("inf-building-del").addEventListener("click", () => delInf("buildings", "inf-buildings-list"));
document.getElementById("inf-contractor-add").addEventListener("click", () => addInf("contractors", "inf-contractor-input"));
document.getElementById("inf-contractor-del").addEventListener("click", () => delInf("contractors", "inf-contractors-list"));

document.getElementById("refresh-btn").addEventListener("click", refreshList);
document.getElementById("stats-refresh-btn")?.addEventListener("click", loadStatsTable);
document.getElementById("api-connect-btn").addEventListener("click", connectApiBase);
document.getElementById("api-check-btn").addEventListener("click", refreshApiStatus);
document.getElementById("fast-x2")?.addEventListener("change", (e) => {
  const inp = document.getElementById("scale-div");
  if (!inp) return;
  if (e.target?.checked && Number(inp.value || "1") <= 1) inp.value = "2";
});
document.getElementById("fps-half")?.addEventListener("change", (e) => {
  const inp = document.getElementById("fps-div");
  if (!inp) return;
  if (e.target?.checked && Number(inp.value || "1") <= 1) inp.value = "2";
});
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => activateTab(btn.getAttribute("data-tab") || "main-tab"));
});

(async () => {
  await refreshApiStatus();
  await loadInfOptions();
  await refreshList();
})();
