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
let infOptions = { buildings: [], contractors: [], contracts: [] };
let currentMiddleWarning = null;
let middleWarningByHumanId = {};
let warningsByHumanId = {};
let analysisPrompts = { main: "", linked: [] };
let detectedViolationsByHumanId = {};
let builtWarningVideoUrl = "";
let builtWarningVideoByHumanId = {};
const WARNING_VIDEO_STATE_PREFIX = "samv.warning_video_state:";

function videoStateStorageKey(folder) {
  const name = String(folder || "").trim();
  if (!name) return "";
  return `${WARNING_VIDEO_STATE_PREFIX}${name}`;
}

function saveBuiltWarningVideosState() {
  try {
    const key = videoStateStorageKey(selectedFolder);
    if (!key || typeof sessionStorage === "undefined") return;
    const byHuman = {};
    Object.entries(builtWarningVideoByHumanId || {}).forEach(([hid, url]) => {
      const id = String(hid || "").trim();
      const u = String(url || "").trim();
      if (id && u) byHuman[id] = u;
    });
    sessionStorage.setItem(key, JSON.stringify({
      global_url: String(builtWarningVideoUrl || "").trim(),
      by_human: byHuman,
    }));
  } catch (_e) {
    // non-blocking: storage may be unavailable in some browser modes
  }
}

function loadBuiltWarningVideosState(folder = selectedFolder) {
  try {
    const key = videoStateStorageKey(folder);
    if (!key || typeof sessionStorage === "undefined") return;
    const raw = sessionStorage.getItem(key);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    const globalUrl = String(parsed?.global_url || "").trim();
    const byHuman = parsed?.by_human && typeof parsed.by_human === "object" ? parsed.by_human : {};
    builtWarningVideoUrl = globalUrl;
    builtWarningVideoByHumanId = {};
    Object.entries(byHuman).forEach(([hid, url]) => {
      const id = String(hid || "").trim();
      const u = String(url || "").trim();
      if (id && u) builtWarningVideoByHumanId[id] = u;
    });
  } catch (_e) {
    // ignore malformed storage payload
  }
}

function clearBuiltWarningVideos() {
  const key = videoStateStorageKey(selectedFolder);
  builtWarningVideoUrl = "";
  builtWarningVideoByHumanId = {};
  try {
    if (key && typeof sessionStorage !== "undefined") sessionStorage.removeItem(key);
  } catch (_e) {
    // ignore storage errors
  }
}

function setVideoSrc(vid, url) {
  if (!vid || !url) return;
  const next = String(url);
  const current = String(vid.getAttribute("src") || "");
  if (current === next) return;
  vid.src = next;
  vid.load();
}

function restoreBuiltWarningVideos() {
  const box = document.getElementById("warning-video-box");
  const globalVid = document.getElementById("warning-video");
  if (builtWarningVideoUrl && globalVid) {
    setVideoSrc(globalVid, builtWarningVideoUrl);
    box?.classList.remove("hidden");
  }
  Object.entries(builtWarningVideoByHumanId).forEach(([hid, url]) => {
    const q = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(hid) : hid;
    const vid = document.querySelector(`.warning-video-one[data-human-id="${q}"]`);
    const shell = document.querySelector(`[data-video-shell="${q}"]`);
    if (!vid || !url) return;
    setVideoSrc(vid, String(url));
    shell?.classList.remove("hidden");
  });
}

function setBuildVideoEnabled(enabled) {
  const btn = document.getElementById("build-video-btn");
  if (btn) btn.disabled = !enabled;
}

function setProgressUi(wrapId, barId, textId, pctId, visible, percent, message) {
  const wrap = document.getElementById(wrapId);
  const bar = document.getElementById(barId);
  const text = document.getElementById(textId);
  const pct = document.getElementById(pctId);
  const prog = wrap?.querySelector(".progress");
  const p = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
  if (wrap) {
    wrap.classList.toggle("hidden", !visible);
    wrap.setAttribute("aria-hidden", visible ? "false" : "true");
  }
  if (bar) bar.style.width = `${p}%`;
  if (pct) pct.textContent = `${p}%`;
  if (text && message) text.textContent = String(message);
  if (prog) prog.setAttribute("aria-valuenow", String(p));
}

function hideAnalysisProgress() {
  setProgressUi("analysis-progress-wrap", "analysis-progress-bar", "analysis-progress-text", "analysis-progress-pct", false, 0, "");
}

function hideVideoProgress() {
  setProgressUi("video-progress-wrap", "video-progress-bar", "video-progress-text", "video-progress-pct", false, 0, "");
}

async function pollTaskUntilDone(folder, task, onTick) {
  const deadline = Date.now() + 2 * 60 * 60 * 1000;
  while (Date.now() < deadline) {
    const st = await apiGet(`/api/analyzer/progress?folder=${encodeURIComponent(folder)}&task=${encodeURIComponent(task)}`);
    if (!st.ok) throw new Error(st.error || "progress failed");
    const status = String(st.status || "idle");
    const percent = Number(st.percent || 0);
    const message = String(st.message || "").trim();
    if (typeof onTick === "function") onTick(st, percent, message);
    if (status === "done") return st;
    if (status === "error") throw new Error(st.error || message || "task failed");
    if (status === "idle" && !st.started) {
      await new Promise((r) => setTimeout(r, 350));
      continue;
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error("Timeout waiting for task");
}

function humanContractValue(row) {
  return String(
    row.querySelector(".human-contract")?.value
    || row.querySelector(".human-contract-text")?.value
    || "",
  ).trim();
}

function humanBlockById(hid) {
  const id = String(hid || "").trim();
  if (!id) return null;
  const q = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(id) : id;
  return document.querySelector(`.human-block[data-human-id="${q}"]`);
}

function contractFieldMarkup() {
  if ((infOptions.contracts || []).length) {
    return `<label>Договор подряда
                <select class="human-contract"></select>
              </label>`;
  }
  return `<label class="full">Договор подряда
                <input class="human-contract-text" type="text" placeholder="Введите договор подряда" />
              </label>
              <p class="muted small full">Справочник договоров пуст или сервер устарел — перезапустите <code>serve.py</code> и добавьте договор во вкладке INF.</p>`;
}

function getCurrentPrompts() {
  const main = String(document.getElementById("main-prompt")?.value || "").trim();
  const p1 = String(document.getElementById("linked-prompt-1")?.value || "").trim();
  const p2 = String(document.getElementById("linked-prompt-2")?.value || "").trim();
  const linked = [];
  if (p1 && p1 !== main) linked.push(p1);
  if (p2 && p2 !== main && p2 !== p1) linked.push(p2);
  return { main, linked };
}


function violKey(text) {
  return encodeURIComponent(String(text || "").trim());
}

function formatDefectLabel(text, hid) {
  let s = String(text || "").trim();
  const escHid = String(hid || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  s = s.replace(new RegExp(`^human_id\\s*:\\s*${escHid}\\s*->\\s*`, "i"), "").trim();
  const miss = s.match(/нет\s+(.+?)\s+на кадре/i);
  if (miss) return `Отсутствует «${miss[1]}» на кадре`;
  const lap = s.match(/^(.+?)\s+и\s+(.+?)\s+не пересекаются/i);
  if (lap) return `«${lap[1]}» и «${lap[2]}» не пересекаются`;
  const lap1 = s.match(/^(.+?)\s+не пересекается с основным/i);
  if (lap1) return `«${lap1[1]}» не пересекается с основным объектом`;
  return s || text;
}

function uniqueDetectedViolations(warnings, hid) {
  const map = new Map();
  (Array.isArray(warnings) ? warnings : []).forEach((w) => {
    const list = Array.isArray(w?.reasons) ? w.reasons : [];
    list.forEach((r) => {
      const text = String(r || "").trim();
      if (!text) return;
      if (!map.has(text)) {
        map.set(text, {
          text,
          key: violKey(text),
          label: formatDefectLabel(text, hid),
          count: 0,
          sample: w,
        });
      }
      map.get(text).count += 1;
    });
  });
  return Array.from(map.values());
}

function humanViolBlock(hid) {
  return humanBlockById(hid);
}

function humanViolationText(hid) {
  const block = humanViolBlock(hid);
  return String(block?.querySelector(".human-violation-text")?.value || "").trim();
}

function violationsSummaryForHuman(hid) {
  const text = humanViolationText(hid);
  if (text) return text;
  const block = humanViolBlock(hid);
  if (block?.querySelector(".human-violation-text")) return "";
  const list = detectedViolationsByHumanId[hid] || [];
  if (list.length) return list.map((v) => v.label || v.text).join("\n");
  return "";
}

function reasonsForHuman(hid) {
  const text = humanViolationText(hid);
  if (text) return [text];
  const block = humanViolBlock(hid);
  if (block?.querySelector(".human-violation-text")) return [];
  const list = detectedViolationsByHumanId[hid] || [];
  if (list.length) return list.map((v) => v.text);
  return [];
}

function violationFieldHtml(hid, detected) {
  const top = Array.isArray(detected) && detected.length
    ? detected.reduce((a, b) => ((b.count || 0) > (a.count || 0) ? b : a), detected[0])
    : null;
  const hint = top
    ? `<p class="muted small viol-hint">Система: ${esc(top.label || formatDefectLabel(top.text, hid))}</p>`
    : "";
  return `
    <label class="human-violation-field full">
      Нарушение
      <input class="human-violation-text" type="text" placeholder="Например: отсутствует каска" />
    </label>
    ${hint}`;
}

function reportImageForHuman(hid) {
  const mid = middleWarningByHumanId[hid];
  if (mid?.image_url) return String(mid.image_url);
  const list = detectedViolationsByHumanId[hid] || [];
  return String(list[0]?.sample?.image_url || "");
}

function updateReportSelectionSummary() {
  const el = document.getElementById("report-selection-summary");
  if (!el) return;
  const total = document.querySelectorAll(".human-report-include").length;
  const picked = Array.from(document.querySelectorAll(".human-report-include:checked"))
    .map((cb) => String(cb.getAttribute("data-human-id") || "").trim())
    .filter(Boolean);
  if (!total) {
    el.textContent = "После анализа отметьте, кого включить в отчёт.";
    return;
  }
  el.textContent = picked.length
    ? `В отчёт: ${picked.map((id) => `human_id:${id}`).join(", ")}`
    : "Отметьте галочкой хотя бы одного human_id.";
}

function bindHumanReportPickers() {
  document.querySelectorAll(".human-report-include").forEach((cb) => {
    if (cb.dataset.bound) return;
    cb.dataset.bound = "1";
    cb.addEventListener("change", () => {
      setReportEnabled();
      updateReportPanel();
      updateReportSelectionSummary();
    });
  });
  document.querySelectorAll(".human-building, .human-contractor, .human-contract, .human-contract-text, .human-deadline, .human-resolved, .human-violation-text").forEach((el) => {
    if (el.dataset.boundViol) return;
    el.dataset.boundViol = "1";
    el.addEventListener("change", setReportEnabled);
    el.addEventListener("input", setReportEnabled);
  });
}

function updateReportPanel() {
  const panel = document.getElementById("report-panel");
  const blocks = document.querySelectorAll(".human-block");
  const hasChecked = document.querySelectorAll(".human-report-include:checked").length > 0;
  if (panel) panel.classList.toggle("hidden", !(selectedFolder && blocks.length));
  if (panel && blocks.length && !hasChecked) panel.classList.remove("hidden");
  setReportEnabled();
}

function getSelectedReportFormats() {
  const out = [];
  ["report-fmt-docx", "report-fmt-html", "report-fmt-json", "report-fmt-txt"].forEach((id) => {
    const el = document.getElementById(id);
    if (el?.checked) out.push(String(el.value || ""));
  });
  return out.filter(Boolean);
}

function setReportEnabled() {
  const btn = document.getElementById("gen-inline-report-btn");
  if (!btn) return;
  const formats = getSelectedReportFormats();
  const checked = document.querySelectorAll(".human-report-include:checked");
  if (!selectedFolder || !checked.length || !formats.length) {
    btn.disabled = true;
    return;
  }
  let ok = true;
  const blocksDone = new Set();
  checked.forEach((cb) => {
    const hid = String(cb.getAttribute("data-human-id") || "").trim();
    const block = humanBlockById(hid);
    if (!block) {
      ok = false;
      return;
    }
    if (blocksDone.has(hid)) return;
    blocksDone.add(hid);
    const b = String(block.querySelector(".human-building")?.value || "").trim();
    const c = String(block.querySelector(".human-contractor")?.value || "").trim();
    const k = humanContractValue(block);
    if (!b || !c || !k) ok = false;
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

function seekVideoToMiddle(vid) {
  if (!vid) return;
  const go = () => {
    const d = Number(vid.duration);
    if (d > 0 && Number.isFinite(d)) vid.currentTime = d / 2;
  };
  if (vid.readyState >= 1) go();
  else vid.addEventListener("loadedmetadata", go, { once: true });
}

function showWarningVideo(url) {
  const box = document.getElementById("warning-video-box");
  const vid = document.getElementById("warning-video");
  if (!box || !vid || !url) return;
  builtWarningVideoUrl = String(url);
  saveBuiltWarningVideosState();
  setVideoSrc(vid, builtWarningVideoUrl);
  seekVideoToMiddle(vid);
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
  document.querySelectorAll(".human-block, .human-row").forEach((row) => {
    const bSel = row.querySelector(".human-building");
    const cSel = row.querySelector(".human-contractor");
    const kSel = row.querySelector(".human-contract");
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
    if (kSel) {
      const prev = String(kSel.value || "");
      setSelectOptions(kSel, infOptions.contracts || [], true);
      if (!kSel.value && infOptions.contracts?.length) kSel.value = infOptions.contracts[0];
      if (prev && Array.isArray(infOptions.contracts) && infOptions.contracts.includes(prev)) kSel.value = prev;
      kSel.addEventListener("change", setReportEnabled);
    }
    const kTxt = row.querySelector(".human-contract-text");
    if (kTxt && !kTxt.dataset.bound) {
      kTxt.dataset.bound = "1";
      kTxt.addEventListener("input", setReportEnabled);
    }
    const dl = row.querySelector(".human-deadline");
    const rs = row.querySelector(".human-resolved");
    if (dl && !dl.dataset.bound) {
      dl.dataset.bound = "1";
      dl.addEventListener("change", setReportEnabled);
    }
    if (rs && !rs.dataset.bound) {
      rs.dataset.bound = "1";
      rs.addEventListener("change", setReportEnabled);
    }
  });
  setReportEnabled();
}

function activateTab(tabId) {
  document.querySelectorAll(".tab-pane").forEach((el) => {
    el.classList.toggle("hidden", el.id !== tabId);
    if (el.id === tabId) {
      el.classList.remove("tab-pane-enter");
      void el.offsetWidth;
      el.classList.add("tab-pane-enter");
    }
  });
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    const isActive = btn.getAttribute("data-tab") === tabId;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  if (tabId === "stats-tab") {
    loadStatsTable();
  }
  if (tabId === "main-tab") {
    loadBuiltWarningVideosState();
    restoreBuiltWarningVideos();
  }
}

function applyRevealAnimation() {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const blocks = Array.from(document.querySelectorAll(".card, .inf-card, .warn-card, .item"));
  if (!blocks.length) return;
  blocks.forEach((el, idx) => {
    el.classList.add("reveal-item");
    el.style.setProperty("--reveal-delay", String(idx % 8));
    if (reduced) el.classList.add("is-visible");
  });
  if (reduced) return;
  const io = new IntersectionObserver((entries, obs) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      obs.unobserve(entry.target);
    });
  }, { threshold: 0.14, rootMargin: "0px 0px -8% 0px" });
  blocks.forEach((el) => io.observe(el));
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
      <td>${esc(`D/${fmtNum(s?.scale_div || 1, 2)} | FPS/${fmtNum(s?.fps_div || 1, 0)}${Number(s?.video_part_sec || 0) > 0 ? ` | части ${fmtNum(s.video_part_sec, 0)}с×${fmtNum(s?.chunks_total || 0, 0)}` : ""}`)}</td>
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
  warningsByHumanId = {};
  detectedViolationsByHumanId = {};
  if (repStatus) repStatus.textContent = "";
  if (repResult) repResult.innerHTML = "";
  setBuildVideoEnabled(lastWarnings.length > 0 && !!selectedFolder);
  setReportEnabled();
  resetWarningVideo();
  if (!Array.isArray(items) || !items.length) {
    box.innerHTML = "<p class='muted'>WARNING не найдено.</p>";
    updateReportSelectionSummary();
    updateReportPanel();
    return;
  }
  const grouped = groupWarningsByMainId(items);
  const ids = Object.keys(grouped).sort((a, b) => Number(a) - Number(b));
  ids.forEach((hid) => {
    const arr = (grouped[hid] || []).slice().sort(
      (a, b) => Number(a?.frame ?? 0) - Number(b?.frame ?? 0),
    );
    warningsByHumanId[hid] = arr;
    middleWarningByHumanId[hid] = arr[Math.floor(arr.length / 2)] || null;
  });
  currentMiddleWarning = middleWarningByHumanId[ids[0]] || null;
  detectedViolationsByHumanId = {};
  ids.forEach((hid) => {
    detectedViolationsByHumanId[hid] = uniqueDetectedViolations(grouped[hid] || [], hid);
  });
  const blocks = ids.map((hid) => {
    const arr = grouped[hid] || [];
    const mid = middleWarningByHumanId[hid] || {};
    const detected = detectedViolationsByHumanId[hid] || [];
    const preview = (mid?.image_url ? mid : (detected[0]?.sample || mid));
    return `
      <article class="warn-card">
        <div class="warn-media">
          <img class="warn-preview-img" src="${esc(preview.image_url || "")}" alt="preview human_id:${esc(hid)}" />
          <button type="button" class="build-one-video-btn btn-secondary btn-sm" data-human-id="${esc(hid)}">Видео · human_id:${esc(hid)}</button>
          <div class="video-shell hidden" data-video-shell="${esc(hid)}">
            <video class="warning-video-one" data-human-id="${esc(hid)}" controls preload="metadata"></video>
          </div>
        </div>
        <div class="warn-body">
          <div class="warn-head-row">
            <span class="warn-badge">human_id:${esc(hid)}</span>
            <label class="human-report-toggle">
              <input type="checkbox" class="human-report-include" data-human-id="${esc(hid)}" />
              Включить в отчёт
            </label>
          </div>
          <p class="muted small">Всего WARNING: ${arr.length}</p>
          <div class="inline-report human-block" data-human-id="${esc(hid)}">
            ${violationFieldHtml(hid, detected)}
            <h3>Данные для отчёта</h3>
            <div class="human-row">
              <label>Здание
                <select class="human-building"></select>
              </label>
              <label>Подрядчик
                <select class="human-contractor"></select>
              </label>
              ${contractFieldMarkup()}
              <label>Срок устранения
                <input class="human-deadline" type="date" />
              </label>
              <label>Отметка об устранении
                <select class="human-resolved">
                  <option value="">— не указано —</option>
                  <option value="Не устранено">Не устранено</option>
                  <option value="Устранено">Устранено</option>
                </select>
              </label>
            </div>
          </div>
        </div>
      </article>
    `;
  }).join("");
  const mainLbl = esc(analysisPrompts.main || getCurrentPrompts().main || "—");
  box.innerHTML = `
    <p class="warnings-intro muted small">Основной промт: <strong>${mainLbl}</strong> · всего WARNING: <strong>${items.length}</strong></p>
    ${blocks}
  `;
  applyRevealAnimation();
  fillRowSelects();
  bindHumanReportPickers();
  box.querySelectorAll(".build-one-video-btn").forEach((btn) => {
    btn.addEventListener("click", () => buildVideoForOneHuman(String(btn.getAttribute("data-human-id") || "")));
  });
  restoreBuiltWarningVideos();
  updateReportSelectionSummary();
  updateReportPanel();
}

function updateInfCounts() {
  const b = (infOptions.buildings || []).length;
  const c = (infOptions.contractors || []).length;
  const k = (infOptions.contracts || []).length;
  const elB = document.getElementById("inf-buildings-count");
  const elC = document.getElementById("inf-contractors-count");
  const elK = document.getElementById("inf-contracts-count");
  if (elB) elB.textContent = String(b);
  if (elC) elC.textContent = String(c);
  if (elK) elK.textContent = String(k);
}

function fillInfSelects() {
  setSelectOptions(document.getElementById("inf-buildings-list"), infOptions.buildings || [], false);
  setSelectOptions(document.getElementById("inf-contractors-list"), infOptions.contractors || [], false);
  setSelectOptions(document.getElementById("inf-contracts-list"), infOptions.contracts || [], false);
  updateInfCounts();
  fillRowSelects();
  setReportEnabled();
  applyRevealAnimation();
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
    contracts: Array.isArray(out.contracts) ? out.contracts.map((x) => String(x || "")).filter(Boolean) : [],
  };
  if (hint) hint.textContent = `INF: ${out.root || "-"}`;
  const oldServer = Number(out.inf_version || 0) < 2 || !Object.prototype.hasOwnProperty.call(out, "contracts");
  if (st) {
    let msg = `Загружено: зданий ${infOptions.buildings.length}, подрядчиков ${infOptions.contractors.length}, договоров ${infOptions.contracts.length}`;
    if (oldServer) {
      msg += " | Перезапустите serve.py (нужна поддержка договоров подряда).";
    }
    st.textContent = msg;
  }
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
  const apiKind = kind === "contracts" ? "contracts" : kind;
  const out = await apiPost("/api/inf/add", { kind: apiKind, value });
  if (!out.ok) {
    const err = String(out.error || "unknown");
    if (st) {
      st.textContent = err.includes("buildings") && err.includes("contractors") && !err.includes("contracts")
        ? `Ошибка INF: ${err}. Перезапустите serve.py и обновите страницу (Ctrl+F5).`
        : `Ошибка INF: ${err}`;
    }
    return;
  }
  infOptions = {
    buildings: out.buildings || [],
    contractors: out.contractors || [],
    contracts: out.contracts || [],
  };
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
    const err = String(out.error || "unknown");
    if (st) {
      st.textContent = err.includes("buildings") && err.includes("contractors") && !err.includes("contracts")
        ? `Ошибка INF: ${err}. Перезапустите serve.py и обновите страницу (Ctrl+F5).`
        : `Ошибка INF: ${err}`;
    }
    return;
  }
  infOptions = {
    buildings: out.buildings || [],
    contractors: out.contractors || [],
    contracts: out.contracts || [],
  };
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
    clearBuiltWarningVideos();
    return;
  }
  loadBuiltWarningVideosState(folder);
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
    analysisPrompts = {
      main: String(out.main_prompt || "").trim(),
      linked: Array.isArray(out.linked_prompts) ? out.linked_prompts.map((x) => String(x || "").trim()).filter(Boolean) : [],
    };
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
    clearBuiltWarningVideos();
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
        <button type="button" class="btn-primary btn-sm" data-open="${esc(x.name)}">Открыть</button>
        <button type="button" class="btn-ghost btn-sm" data-del="${esc(x.name)}">Удалить</button>
      </div>
    </article>
  `).join("");
  applyRevealAnimation();
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
      clearBuiltWarningVideos();
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
    clearBuiltWarningVideos();
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
  const partSecInp = document.getElementById("video-part-sec");
  let videoPartSec = Number(partSecInp?.value || "0");
  if (!Number.isFinite(videoPartSec) || videoPartSec < 0) videoPartSec = 0;
  videoPartSec = Math.round(videoPartSec * 10) / 10;
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
  fd.append("video_part_sec", String(videoPartSec));
  const tags = [];
  if (scaleDiv > 1) tags.push(`D/${scaleDiv}`);
  if (fpsDiv > 1) tags.push(`FPS/${fpsDiv}`);
  if (videoPartSec > 0) tags.push(`части/${videoPartSec}с`);
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
  if (Number(res.video_part_sec || 0) > 0) {
    doneTags.push(`части/${Number(res.video_part_sec)}с×${Number(res.chunks_total || 0)}`);
  }
  status.textContent = `Готово. Создана папка: ${res.folder || "-"}${doneTags.length ? ` (${doneTags.join(" + ")})` : ""}`;
  document.getElementById("video").value = "";
  await refreshList();
});

document.getElementById("run-analysis-btn").addEventListener("click", async () => {
  const status = document.getElementById("analysis-status");
  const btn = document.getElementById("run-analysis-btn");
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
  clearBuiltWarningVideos();
  hideVideoProgress();
  setProgressUi("analysis-progress-wrap", "analysis-progress-bar", "analysis-progress-text", "analysis-progress-pct", true, 0, "Запуск анализа…");
  if (btn) btn.disabled = true;
  try {
    const started = await apiPost("/api/analyzer/run", {
      folder: selectedFolder,
      main_prompt: mainPrompt,
      linked_prompts: linked,
    });
    if (!started.ok) {
      status.textContent = `Ошибка анализа: ${started.error || "unknown"}`;
      renderWarnings([]);
      return;
    }
    const out = await pollTaskUntilDone(selectedFolder, "analysis", (_st, percent, message) => {
      setProgressUi(
        "analysis-progress-wrap",
        "analysis-progress-bar",
        "analysis-progress-text",
        "analysis-progress-pct",
        true,
        percent,
        message || "Анализ…",
      );
      if (status) status.textContent = message || `Анализ… ${percent}%`;
    });
    analysisPrompts = {
      main: String(out.main_prompt || mainPrompt).trim(),
      linked: Array.isArray(out.linked_prompts) ? out.linked_prompts.map((x) => String(x || "").trim()).filter(Boolean) : linked,
    };
    status.textContent = `Готово. Проверено: ${out.frames_checked}, WARNING: ${out.warnings_count}`;
    renderWarnings(out.warnings || []);
    setProgressUi("analysis-progress-wrap", "analysis-progress-bar", "analysis-progress-text", "analysis-progress-pct", true, 100, "Анализ завершён");
  } catch (e) {
    status.textContent = `Ошибка анализа: ${e?.message || e}`;
    renderWarnings([]);
  } finally {
    if (btn) btn.disabled = false;
    setTimeout(hideAnalysisProgress, 1200);
  }
});

async function runVideoBuild(mainId) {
  const status = document.getElementById("analysis-status");
  const box = document.getElementById("warning-video-box");
  const vid = document.getElementById("warning-video");
  const colorfulMasks = !!document.getElementById("video-color-masks")?.checked;
  const isGlobal = mainId === undefined || mainId === null;
  const body = { folder: selectedFolder };
  if (!isGlobal) body.main_id = Number(mainId);
  body.colorful_masks = colorfulMasks;
  hideAnalysisProgress();
  setProgressUi("video-progress-wrap", "video-progress-bar", "video-progress-text", "video-progress-pct", true, 0, "Запуск сборки видео…");
  if (isGlobal) box?.classList.add("hidden");
  const started = await apiPost("/api/analyzer/video", body);
  if (!started.ok) throw new Error(started.error || "unknown");
  const out = await pollTaskUntilDone(selectedFolder, "video", (_st, percent, message) => {
    setProgressUi(
      "video-progress-wrap",
      "video-progress-bar",
      "video-progress-text",
      "video-progress-pct",
      true,
      percent,
      message || "Сборка видео…",
    );
    if (status) status.textContent = message || `Сборка видео… ${percent}%`;
  });
  if (vid && isGlobal) {
    builtWarningVideoUrl = String(out.video_url || "");
    saveBuiltWarningVideosState();
    setVideoSrc(vid, builtWarningVideoUrl);
    box?.classList.remove("hidden");
  }
  return out;
}

document.getElementById("build-video-btn").addEventListener("click", async () => {
  const status = document.getElementById("analysis-status");
  const btn = document.getElementById("build-video-btn");
  if (!selectedFolder) {
    status.textContent = "Сначала откройте папку.";
    return;
  }
  if (!Array.isArray(lastWarnings) || !lastWarnings.length) {
    status.textContent = "Нет warning-кадров для сборки видео.";
    return;
  }
  btn.disabled = true;
  status.textContent = "Сборка видео...";
  try {
    const out = await runVideoBuild(null);
    status.textContent = `Видео готово. Кадров: ${Number(out.frames_used || 0)}, FPS: ${Number(out.fps || 0).toFixed(2)}`;
    setProgressUi("video-progress-wrap", "video-progress-bar", "video-progress-text", "video-progress-pct", true, 100, "Видео готово");
  } catch (e) {
    status.textContent = `Ошибка сборки видео: ${e?.message || e}`;
  } finally {
    btn.disabled = false;
    setBuildVideoEnabled(Array.isArray(lastWarnings) && lastWarnings.length > 0 && !!selectedFolder);
    setTimeout(hideVideoProgress, 1500);
  }
});

async function generateInlineReport() {
  const st = document.getElementById("report-status");
  const outBox = document.getElementById("report-result");
  if (!selectedFolder) {
    if (st) st.textContent = "Сначала выберите папку.";
    return;
  }
  const formats = getSelectedReportFormats();
  if (!formats.length) {
    if (st) st.textContent = "Выберите хотя бы один формат отчёта (Word, HTML, JSON или TXT).";
    return;
  }
  const checked = document.querySelectorAll(".human-report-include:checked");
  if (!checked.length) {
    if (st) st.textContent = "Отметьте галочкой хотя бы одного human_id для отчёта.";
    return;
  }
  const items = [];
  for (const cb of checked) {
    const hid = String(cb.getAttribute("data-human-id") || "").trim();
    const block = humanBlockById(hid);
    if (!block) continue;
    const violations = violationsSummaryForHuman(hid);
    if (!violations) {
      if (st) st.textContent = `Впишите нарушение для human_id:${hid}.`;
      return;
    }
    const building = String(block.querySelector(".human-building")?.value || "").trim();
    const contractor = String(block.querySelector(".human-contractor")?.value || "").trim();
    const contract = humanContractValue(block);
    const deadline = String(block.querySelector(".human-deadline")?.value || "").trim();
    const resolved = String(block.querySelector(".human-resolved")?.value || "").trim();
    items.push({
      human_id: hid,
      building,
      contractor,
      contract,
      violations,
      deadline,
      resolved,
      reasons: reasonsForHuman(hid),
      image_url: reportImageForHuman(hid),
    });
  }
  if (!items.length) {
    if (st) st.textContent = "Нет отмеченных human_id для отчёта.";
    return;
  }
  if (items.some((x) => !x.building || !x.contractor || !x.contract)) {
    if (st) st.textContent = "Заполните здание, подрядчика и договор для каждого human_id с отмеченными нарушениями.";
    return;
  }
  const btn = document.getElementById("gen-inline-report-btn");
  if (btn) btn.disabled = true;
  if (st) st.textContent = `Генерация отчёта (${formats.join(", ")})...`;
  if (outBox) outBox.innerHTML = "";
  const out = await apiPost("/api/report/generate", {
    folder: selectedFolder,
    frame: -1,
    image_url: "",
    items,
    formats,
  });
  setReportEnabled();
  if (!out.ok) {
    if (st) st.textContent = `Ошибка отчёта: ${out.error || "unknown"}`;
    return;
  }
  if (st) st.textContent = "Отчёт готов. Скачайте выбранные форматы:";
  const rep = out.report || {};
  const links = [];
  if (out.report_docx_url) links.push(`<a class="report-dl" href="${esc(out.report_docx_url)}" download>Скачать Word (.docx)</a>`);
  if (out.report_html_url) links.push(`<a class="report-dl" href="${esc(out.report_html_url)}" target="_blank" rel="noopener">Открыть HTML</a>`);
  if (out.report_json_url) links.push(`<a class="report-dl" href="${esc(out.report_json_url)}" target="_blank" rel="noopener">JSON</a>`);
  if (out.report_txt_url) links.push(`<a class="report-dl" href="${esc(out.report_txt_url)}" target="_blank" rel="noopener">TXT</a>`);
  if (outBox) {
    outBox.innerHTML = `
    <div class="report-box">
      <div><strong>${esc(rep.id || "")}</strong></div>
      <div class="muted">Нарушений в отчёте: ${Array.isArray(rep.items) ? rep.items.length : 0}</div>
      <div class="muted">WARNING: ${Number(rep.warnings_count || 0)}</div>
      <div class="row report-links">${links.join("") || "<span class='err'>Файлы не созданы</span>"}</div>
    </div>
  `;
    outBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

async function buildVideoForOneHuman(humanId) {
  const st = document.getElementById("analysis-status");
  const hid = String(humanId || "").trim();
  if (!selectedFolder || !hid) return;
  const btn = document.querySelector(`.build-one-video-btn[data-human-id="${hid}"]`);
  const q = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(hid) : hid;
  const vid = document.querySelector(`.warning-video-one[data-human-id="${q}"]`);
  const shell = document.querySelector(`[data-video-shell="${q}"]`);
  if (btn) btn.disabled = true;
  if (st) st.textContent = `Сборка видео для human_id:${hid}...`;
  try {
    const out = await runVideoBuild(Number(hid));
    if (vid) {
      builtWarningVideoByHumanId[hid] = String(out.video_url || "");
      saveBuiltWarningVideosState();
      setVideoSrc(vid, builtWarningVideoByHumanId[hid]);
      seekVideoToMiddle(vid);
      shell?.classList.remove("hidden");
    }
    if (st) st.textContent = `Видео готово для human_id:${hid}. Кадров: ${Number(out.frames_used || 0)}`;
  } catch (e) {
    if (st) st.textContent = `Ошибка видео human_id:${hid}: ${e?.message || e}`;
  } finally {
    if (btn) btn.disabled = false;
    setTimeout(hideVideoProgress, 1500);
  }
}

document.getElementById("inf-building-add").addEventListener("click", () => addInf("buildings", "inf-building-input"));
document.getElementById("inf-building-del").addEventListener("click", () => delInf("buildings", "inf-buildings-list"));
document.getElementById("inf-contractor-add").addEventListener("click", () => addInf("contractors", "inf-contractor-input"));
document.getElementById("inf-contractor-del").addEventListener("click", () => delInf("contractors", "inf-contractors-list"));
document.getElementById("inf-contract-add").addEventListener("click", () => addInf("contracts", "inf-contract-input"));
document.getElementById("inf-contract-del").addEventListener("click", () => delInf("contracts", "inf-contracts-list"));

document.getElementById("gen-inline-report-btn")?.addEventListener("click", generateInlineReport);
["report-fmt-docx", "report-fmt-html", "report-fmt-json", "report-fmt-txt"].forEach((id) => {
  document.getElementById(id)?.addEventListener("change", setReportEnabled);
});

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

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) restoreBuiltWarningVideos();
});

(async () => {
  refreshApiStatus();
  await loadInfOptions();
  await refreshList();
  applyRevealAnimation();
})();
