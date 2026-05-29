const DEBUG_LOG_MAX = 500;
let debugLogLines = [];

function debugLog(level, message) {
  const ts = new Date().toLocaleTimeString("ru-RU", { hour12: false });
  const line = `[${ts}] [${String(level || "info").toUpperCase()}] ${String(message || "")}`;
  debugLogLines.push(line);
  if (debugLogLines.length > DEBUG_LOG_MAX) {
    debugLogLines = debugLogLines.slice(-DEBUG_LOG_MAX);
  }
  const el = document.getElementById("debug-log");
  if (el) {
    el.textContent = debugLogLines.join("\n");
    el.scrollTop = el.scrollHeight;
  }
}

function debugLogServer(lines) {
  (Array.isArray(lines) ? lines : []).forEach((x) => debugLog("srv", x));
}

function clearDebugLog() {
  debugLogLines = [];
  const el = document.getElementById("debug-log");
  if (el) el.textContent = "";
}

async function apiGet(url) {
  debugLog("api", `GET ${url}`);
  const r = await fetch(url, { cache: "no-store" });
  const data = await r.json();
  debugLog("api", `GET ${url} → ok=${!!data.ok}${data.error ? ` err=${data.error}` : ""}`);
  return data;
}

async function apiPost(url, body, isForm = false) {
  debugLog("api", `POST ${url}${isForm ? " (multipart)" : ""}`);
  const init = { method: "POST" };
  if (isForm) {
    init.body = body;
  } else {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body || {});
  }
  const r = await fetch(url, init);
  const data = await r.json();
  debugLog("api", `POST ${url} → ok=${!!data.ok}${data.error ? ` err=${data.error}` : ""}`);
  return data;
}

function parsePromptSegments(raw) {
  const s = String(raw || "").trim();
  if (!s) return [];
  return s.split(/\s*[.,]\s*/).map((x) => x.trim()).filter(Boolean);
}

function promptToAnalyzer(prompt) {
  const segments = parsePromptSegments(prompt);
  if (!segments.length) return { main: "", linked: [] };
  const main = segments[0];
  const linked = segments.slice(1).filter((x) => x && x !== main);
  return { main, linked };
}

function esc(s) {
  return String(s || "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

let selectedFolder = "";
let lastWarnings = [];
let infOptions = { buildings: [], contractors: [], contracts: [] };
let infScenarios = { scenarios: [], enabled_ids: [], active_id: "" };
let folderAnalyzerParams = { main: "", linked: [], prompt: "", violation_label: "", api_prompt: "" };
let apiPromptText = "";
let currentMiddleWarning = null;
let middleWarningByHumanId = {};
let warningsByHumanId = {};
let analysisPrompts = { main: "", linked: [] };
let detectedViolationsByHumanId = {};
let builtWarningVideoUrl = "";
let builtWarningVideoByHumanId = {};
const WARNING_VIDEO_STATE_PREFIX = "samv.warning_video_state:";
let accessRole = "guest";
let accessClientIp = "";
let infConfigRevision = "";
let infConfigPollTimer = null;

function setTabVisible(tabId, visible) {
  const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
  const pane = document.getElementById(tabId);
  if (btn) btn.style.display = visible ? "" : "none";
  if (pane && !visible) pane.classList.add("hidden");
}

function isAdmin() {
  return accessRole === "admin";
}

function setProjectFoldersCardVisible(visible) {
  const card = document.getElementById("project-folders-card");
  if (card) card.classList.toggle("hidden", !visible);
}

function setAnalyzerFolderHint(folder) {
  const anFolder = document.getElementById("an-folder");
  if (!anFolder) return;
  if (!isAdmin()) {
    anFolder.textContent = "";
    anFolder.classList.add("hidden");
    return;
  }
  anFolder.classList.remove("hidden");
  anFolder.textContent = folder ? `Папка: ${folder}` : "Папка не выбрана";
}

function infAdminHint() {
  return isAdmin()
    ? "Настройте на вкладке «Справочник»."
    : "Обратитесь к администратору — настройки INF задаются на сервере.";
}

async function fetchInfConfigRevision() {
  const out = await apiGet("/api/inf/revision");
  if (!out.ok) return "";
  return String(out.revision || "").trim();
}

async function loadServerInfConfig() {
  await loadApiPrompt();
  await loadScenarios();
  infConfigRevision = await fetchInfConfigRevision();
}

async function pollInfConfigUpdates() {
  if (accessRole !== "user") return;
  try {
    const rev = await fetchInfConfigRevision();
    if (!rev || rev === infConfigRevision) return;
    const hadRevision = !!infConfigRevision;
    infConfigRevision = rev;
    await loadApiPrompt();
    await loadScenarios();
    await refreshApiStatus();
    if (hadRevision) {
      debugLog("inf", "Настройки INF обновлены с сервера");
      const st = document.getElementById("status");
      if (st) st.textContent = "Настройки обновлены администратором.";
    }
  } catch (e) {
    debugLog("err", `inf sync: ${e?.message || e}`);
  }
}

function onInfConfigVisibilityChange() {
  if (!document.hidden) pollInfConfigUpdates();
}

function startInfConfigAutoSync() {
  stopInfConfigAutoSync();
  if (accessRole !== "user") return;
  infConfigPollTimer = setInterval(() => {
    if (!document.hidden) pollInfConfigUpdates();
  }, 6000);
  document.addEventListener("visibilitychange", onInfConfigVisibilityChange);
}

function stopInfConfigAutoSync() {
  if (infConfigPollTimer) {
    clearInterval(infConfigPollTimer);
    infConfigPollTimer = null;
  }
  document.removeEventListener("visibilitychange", onInfConfigVisibilityChange);
}

function setAccessDeniedOverlay(visible) {
  let el = document.getElementById("access-denied-overlay");
  if (!el) {
    el = document.createElement("div");
    el.id = "access-denied-overlay";
    el.className = "access-denied-overlay hidden";
    el.innerHTML = `
      <div class="access-denied-box">
        <h2>НЕТ ДОСТУПА</h2>
        <p>Ваш IP не добавлен в список разрешённых.</p>
      </div>
    `;
    document.body.appendChild(el);
  }
  el.classList.toggle("hidden", !visible);
}

function applyAccessUi() {
  if (accessRole === "admin") {
    stopInfConfigAutoSync();
    setAccessDeniedOverlay(false);
    setProjectFoldersCardVisible(true);
    setAnalyzerFolderHint(selectedFolder);
    setTabVisible("main-tab", true);
    setTabVisible("stats-tab", true);
    setTabVisible("inf-tab", true);
    return;
  }
  if (accessRole === "user") {
    setAccessDeniedOverlay(false);
    setProjectFoldersCardVisible(false);
    setAnalyzerFolderHint(selectedFolder);
    setTabVisible("main-tab", true);
    setTabVisible("stats-tab", false);
    setTabVisible("inf-tab", false);
    activateTab("main-tab");
    return;
  }
  stopInfConfigAutoSync();
  setAccessDeniedOverlay(true);
  setProjectFoldersCardVisible(false);
  setTabVisible("main-tab", false);
  setTabVisible("stats-tab", false);
  setTabVisible("inf-tab", false);
  const st = document.getElementById("status");
  if (st) st.textContent = "Доступ запрещён: ваш IP не добавлен в admins/users.";
}

async function loadAccessMe() {
  try {
    const out = await apiGet("/api/access/me");
    if (!out.ok) return;
    accessRole = String(out.role || "guest").trim() || "guest";
    accessClientIp = String(out.client_ip || "").trim();
    debugLog("auth", `role=${accessRole}${accessClientIp ? ` ip=${accessClientIp}` : ""}`);
  } catch (e) {
    accessRole = "guest";
    debugLog("err", `access/me: ${e?.message || e}`);
  } finally {
    applyAccessUi();
  }
}

function videoStateStorageKey(folder) {
  const name = String(folder || "").trim();
  if (!name) return "";
  return `${WARNING_VIDEO_STATE_PREFIX}${name}`;
}

function writeVideoState(key, payload) {
  if (!key) return;
  const raw = JSON.stringify(payload || {});
  try {
    if (typeof sessionStorage !== "undefined") sessionStorage.setItem(key, raw);
  } catch (_e) {
    // ignore storage errors
  }
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(key, raw);
  } catch (_e) {
    // ignore storage errors
  }
}

function readVideoState(key) {
  if (!key) return "";
  try {
    if (typeof sessionStorage !== "undefined") {
      const raw = sessionStorage.getItem(key);
      if (raw) return raw;
    }
  } catch (_e) {
    // ignore storage errors
  }
  try {
    if (typeof localStorage !== "undefined") {
      const raw = localStorage.getItem(key);
      if (raw) return raw;
    }
  } catch (_e) {
    // ignore storage errors
  }
  return "";
}

function removeVideoState(key) {
  if (!key) return;
  try {
    if (typeof sessionStorage !== "undefined") sessionStorage.removeItem(key);
  } catch (_e) {
    // ignore storage errors
  }
  try {
    if (typeof localStorage !== "undefined") localStorage.removeItem(key);
  } catch (_e) {
    // ignore storage errors
  }
}

function saveBuiltWarningVideosState() {
  try {
    const key = videoStateStorageKey(selectedFolder);
    if (!key) return;
    const byHuman = {};
    Object.entries(builtWarningVideoByHumanId || {}).forEach(([hid, url]) => {
      const id = String(hid || "").trim();
      const u = String(url || "").trim();
      if (id && u) byHuman[id] = u;
    });
    writeVideoState(key, {
      global_url: String(builtWarningVideoUrl || "").trim(),
      by_human: byHuman,
    });
  } catch (_e) {
    // non-blocking: storage may be unavailable in some browser modes
  }
}

function loadBuiltWarningVideosState(folder = selectedFolder) {
  try {
    const key = videoStateStorageKey(folder);
    if (!key) return;
    const raw = readVideoState(key);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    const globalUrl = String(parsed?.global_url || "").trim();
    const byHuman = parsed?.by_human && typeof parsed.by_human === "object" ? parsed.by_human : {};
    if (globalUrl) builtWarningVideoUrl = globalUrl;
    const mergedByHuman = { ...(builtWarningVideoByHumanId || {}) };
    Object.entries(byHuman).forEach(([hid, url]) => {
      const id = String(hid || "").trim();
      const u = String(url || "").trim();
      if (id && u) mergedByHuman[id] = u;
    });
    builtWarningVideoByHumanId = mergedByHuman;
  } catch (_e) {
    // ignore malformed storage payload
  }
}

function clearBuiltWarningVideos(removeStored = false) {
  const key = videoStateStorageKey(selectedFolder);
  builtWarningVideoUrl = "";
  builtWarningVideoByHumanId = {};
  if (removeStored) removeVideoState(key);
}

function setVideoSrc(vid, url, forceReload = false) {
  if (!vid || !url) return;
  const next = String(url);
  const current = String(vid.getAttribute("src") || "");
  if (current === next) {
    if (forceReload) vid.load();
    return;
  }
  vid.src = next;
  vid.load();
}

function restoreBuiltWarningVideos() {
  const box = document.getElementById("warning-video-box");
  const globalVid = document.getElementById("warning-video");
  if (builtWarningVideoUrl && globalVid) {
    setVideoSrc(globalVid, builtWarningVideoUrl, true);
    box?.classList.remove("hidden");
  }
  Object.entries(builtWarningVideoByHumanId).forEach(([hid, url]) => {
    const q = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(hid) : hid;
    const vid = document.querySelector(`.warning-video-one[data-human-id="${q}"]`);
    const shell = document.querySelector(`[data-video-shell="${q}"]`);
    if (!vid || !url) return;
    setVideoSrc(vid, String(url), true);
    shell?.classList.remove("hidden");
  });
}

function restoreBuiltWarningVideosDeferred() {
  restoreBuiltWarningVideos();
  // Вкладки/карточки могут дорисовываться чуть позже; повторяем восстановление.
  [120, 350, 800].forEach((delayMs) => {
    setTimeout(() => restoreBuiltWarningVideos(), delayMs);
  });
}

function setBuildVideoEnabled(enabled) {
  const btn = document.getElementById("build-video-btn");
  if (btn) btn.disabled = !enabled;
}

function setRunAnalysisEnabled(enabled) {
  const btn = document.getElementById("run-analysis-btn");
  if (btn) btn.disabled = !enabled;
}

async function syncRunAnalysisButton(folder = selectedFolder) {
  if (!folder) {
    setRunAnalysisEnabled(false);
    return;
  }
  const res = await apiGet("/api/folders");
  if (!res.ok) {
    setRunAnalysisEnabled(false);
    return;
  }
  const row = (Array.isArray(res.items) ? res.items : []).find((x) => String(x.name) === String(folder));
  setRunAnalysisEnabled(!!row?.has_json);
}

async function runFolderAnalysis() {
  if (!selectedFolder) {
    alert(isAdmin() ? "Сначала выберите папку в списке." : "Сначала обработайте видео.");
    return;
  }
  if (!getEnabledScenarios().length) {
    await loadServerInfConfig();
  }
  if (!getEnabledScenarios().length) {
    alert(`Нет включённых сценариев анализатора. ${infAdminHint()}`);
    return;
  }
  const btn = document.getElementById("run-analysis-btn");
  const status = document.getElementById("analysis-status");
  debugLog("analysis", `Старт анализа папки: ${selectedFolder}`);
  if (btn) btn.disabled = true;
  hideVideoProgress();
  setProgressUi(
    "analysis-progress-wrap",
    "analysis-progress-bar",
    "analysis-progress-text",
    "analysis-progress-pct",
    true,
    0,
    "Запуск анализа…",
  );
  if (status) status.textContent = "Анализ запущен…";
  const started = await apiPost("/api/analyzer/run_all", { folder: selectedFolder });
  if (!started.ok) {
    if (status) status.textContent = `Ошибка: ${started.error || "unknown"}`;
    debugLog("err", started.error || "analysis start failed");
    await syncRunAnalysisButton();
    return;
  }
  try {
    clearBuiltWarningVideos(true);
    resetWarningVideo();
    await pollAnalysisAndShow(selectedFolder);
  } catch (err) {
    if (status) status.textContent = `Ошибка анализа: ${err?.message || err}`;
    debugLog("err", String(err?.message || err));
  } finally {
    await syncRunAnalysisButton();
  }
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
  const main = String(analysisPrompts.main || folderAnalyzerParams.main || "").trim();
  const linked = Array.isArray(analysisPrompts.linked) && analysisPrompts.linked.length
    ? analysisPrompts.linked.map((x) => String(x || "").trim()).filter(Boolean)
    : (folderAnalyzerParams.linked || []).slice();
  return { main, linked };
}


function violKey(text) {
  return encodeURIComponent(String(text || "").trim());
}

/** Текст нарушения из строки сценария INF (как scenario_violation_label на бэкенде). */
function scenarioViolationLabelFromRow(row) {
  if (!row || typeof row !== "object") return "";
  const custom = String(row.violation_label || "").trim();
  if (custom) return custom;
  const title = String(row.title || "").trim();
  if (title) return title;
  const prompt = String(row.prompt || "").trim();
  if (!prompt) return "";
  const parts = prompt.split(/\s*\.\s*/).map((x) => x.trim()).filter(Boolean);
  const linked = parts.slice(1);
  if (linked.length) {
    const last = linked[linked.length - 1];
    if (last) return `Отсутствует «${last}» (СИЗ)`;
  }
  return "Нарушение требований ТБ";
}

function scenarioViolationLabelById(scenarioId) {
  const id = String(scenarioId || "").trim();
  if (!id) return "";
  const rows = Array.isArray(infScenarios.scenarios) ? infScenarios.scenarios : [];
  const row = rows.find((s) => String(s?.id || "") === id);
  return scenarioViolationLabelFromRow(row);
}

/** Подпись нарушения для UI: из warning, иначе INF по scenario_id. */
function violationLabelForWarning(w) {
  const fromWarn = String(w?.violation_label || "").trim();
  if (fromWarn) return fromWarn;
  const fromInf = scenarioViolationLabelById(w?.scenario_id);
  if (fromInf) return fromInf;
  const reasons = Array.isArray(w?.reasons) ? w.reasons : [];
  if (reasons.length) return formatReasonDetail(reasons[0], w?.main_id ?? "");
  return "";
}

/** Техническая расшифровка reason от анализатора (без привязки к конкретным промптам). */
function formatReasonDetail(text, hid) {
  let s = String(text || "").trim();
  const escHid = String(hid || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  s = s.replace(new RegExp(`^human_id\\s*:\\s*${escHid}\\s*->\\s*`, "i"), "").trim();
  s = s.replace(new RegExp(`^human\\s*->\\s*`, "i"), "").trim();
  const absent = s.match(/отсутствует\s+(.+?)(?:\s*\(|$)/i);
  if (absent) {
    const cov = s.match(/СИЗ на (\d+)% кадров/i);
    if (cov) return `Отсутствует «${absent[1].trim()}» (на ${cov[1]}% кадров с human)`;
    return `Отсутствует «${absent[1].trim()}»`;
  }
  const weak = s.match(/^(.+?):\s*слабое пересечение с (.+?)\s+\((\d+)%/i);
  if (weak) return `Слабое пересечение «${weak[1].trim()}» с ${weak[2].trim()} (${weak[3]}% кадров)`;
  const pct = s.match(/пересечение с (.+?) в (\d+)% кадров/i);
  if (pct) return `Недостаточное пересечение с ${pct[1]} (${pct[2]}% кадров)`;
  const pairPct = s.match(/пересекаются только в (\d+)% кадров/i);
  if (pairPct) return `Слабое пересечение элементов (${pairPct[1]}% кадров)`;
  return s || text;
}

function uniqueDetectedViolations(warnings, hid) {
  const map = new Map();
  (Array.isArray(warnings) ? warnings : []).forEach((w) => {
    const label = violationLabelForWarning(w);
    const reasons = Array.isArray(w?.reasons) ? w.reasons : [];
    const detail = reasons.map((r) => formatReasonDetail(r, hid)).filter(Boolean).join(" · ");
    const mapKey = String(w?.scenario_id || label || reasons[0] || "").trim();
    if (!mapKey) return;
    if (!map.has(mapKey)) {
      map.set(mapKey, {
        text: reasons[0] || "",
        detail,
        key: violKey(mapKey),
        label,
        count: 0,
        confidenceSum: 0,
        confidenceMax: 0,
        sample: w,
      });
    }
    const row = map.get(mapKey);
    row.count += 1;
    const conf = Number(w?.confidence || 0);
    if (Number.isFinite(conf) && conf > 0) {
      row.confidenceSum += conf;
      if (conf > row.confidenceMax) {
        row.confidenceMax = conf;
        row.sample = w;
      }
    }
  });
  return Array.from(map.values());
}

function humanViolBlock(hid) {
  return humanBlockById(hid);
}

function humanViolationTexts(hid) {
  const block = humanViolBlock(hid);
  const inputs = block ? block.querySelectorAll(".human-violation-text") : [];
  return Array.from(inputs).map((inp) => String(inp.value || "").trim()).filter(Boolean);
}

function humanViolationText(hid) {
  return humanViolationTexts(hid).join("; ");
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

function defaultViolationLabel() {
  const raw = String(folderAnalyzerParams.violation_label || "").trim();
  if (!raw) return "";
  const parts = raw.split(/\s*\|\s*/).map((x) => String(x || "").trim()).filter(Boolean);
  if (parts.length <= 1) return raw;
  return parts.join("; ");
}

function violationLabelFromResult(out) {
  const arr = Array.isArray(out?.violation_labels)
    ? out.violation_labels.map((x) => String(x || "").trim()).filter(Boolean)
    : [];
  if (arr.length) return arr.join(" | ");
  return String(out?.violation_label || out?.scenario_title || "").trim();
}

function violationPresetForHuman(hid, detected, warningsForHuman) {
  const fromWarnings = (Array.isArray(warningsForHuman) ? warningsForHuman : [])
    .map((w) => violationLabelForWarning(w))
    .filter(Boolean);
  const uniqLabels = [...new Set(fromWarnings)];
  if (uniqLabels.length) return uniqLabels.join("; ");
  const list = Array.isArray(detected) ? detected : [];
  if (list.length) {
    return list.map((v) => String(v.label || "").trim()).filter(Boolean).join("; ");
  }
  return "";
}

function violationRowsHtml(hid, warningsForHuman) {
  const arr = Array.isArray(warningsForHuman) ? warningsForHuman : [];
  if (!arr.length) {
    return `
      <label class="human-violation-field full">
        Нарушение
        <input class="human-violation-text" type="text" placeholder="Например: человек без каски" />
      </label>`;
  }
  return arr.map((w, i) => {
    const label = violationLabelForWarning(w);
    const reasons = Array.isArray(w?.reasons) ? w.reasons : [];
    const detail = reasons.map((r) => formatReasonDetail(r, hid)).filter(Boolean).join(" · ");
    const conf = Number(w?.confidence || 0);
    const confLabel = conf > 0 ? ` · confidence: ${esc(conf.toFixed(2))}` : "";
    const title = arr.length > 1 ? `Нарушение ${i + 1}` : "Нарушение";
    const hint = detail
      ? `<p class="muted small viol-hint">Детали анализатора: ${esc(detail)}${confLabel}</p>`
      : "";
    return `
      <div class="human-violation-item">
        <label class="human-violation-field full">
          ${title}
          <input class="human-violation-text" type="text" value="${esc(label)}" placeholder="Например: человек без каски" />
        </label>
        ${hint}
      </div>`;
  }).join("");
}

function violationFieldHtml(hid, detected, warningsForHuman) {
  return violationRowsHtml(hid, warningsForHuman);
}

function applyViolationPresetsToBlocks() {
  document.querySelectorAll(".human-block").forEach((block) => {
    const hid = String(block.getAttribute("data-human-id") || "").trim();
    const warnings = warningsByHumanId[hid] || [];
    const inputs = block.querySelectorAll(".human-violation-text");
    if (warnings.length && inputs.length) {
      warnings.forEach((w, i) => {
        const inp = inputs[i];
        if (!inp || String(inp.value || "").trim()) return;
        const label = violationLabelForWarning(w);
        if (label) inp.value = label;
      });
      return;
    }
    const inp = block.querySelector(".human-violation-text");
    if (!inp || String(inp.value || "").trim()) return;
    const preset = violationPresetForHuman(
      hid,
      detectedViolationsByHumanId[hid] || [],
      warningsByHumanId[hid] || [],
    );
    if (preset) inp.value = preset;
  });
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
    restoreBuiltWarningVideosDeferred();
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
  const rows = Array.isArray(items)
    ? items.filter((x) => String(x?.status || "confirmed").toLowerCase() === "confirmed")
    : [];
  lastWarnings = rows;
  currentMiddleWarning = null;
  middleWarningByHumanId = {};
  warningsByHumanId = {};
  detectedViolationsByHumanId = {};
  if (repStatus) repStatus.textContent = "";
  if (repResult) repResult.innerHTML = "";
  setBuildVideoEnabled(lastWarnings.length > 0 && !!selectedFolder);
  setReportEnabled();
  resetWarningVideo();
  if (!rows.length) {
    box.innerHTML = "<p class='muted'>WARNING не найдено.</p>";
    updateReportSelectionSummary();
    updateReportPanel();
    return;
  }
  const grouped = groupWarningsByMainId(rows);
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
          <p class="muted small">Всего CONFIRMED WARNING: ${arr.length}${arr.length > 1 ? " (по одному на сценарий)" : ""}</p>
          <div class="inline-report human-block" data-human-id="${esc(hid)}">
            ${violationFieldHtml(hid, detected, arr)}
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
  box.innerHTML = `
    ${blocks}
  `;
  applyRevealAnimation();
  fillRowSelects();
  applyViolationPresetsToBlocks();
  bindHumanReportPickers();
  box.querySelectorAll(".build-one-video-btn").forEach((btn) => {
    btn.addEventListener("click", () => buildVideoForOneHuman(String(btn.getAttribute("data-human-id") || "")));
  });
  restoreBuiltWarningVideosDeferred();
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

function getEnabledScenarios() {
  const enabledSet = new Set(
    (Array.isArray(infScenarios.enabled_ids) ? infScenarios.enabled_ids : []).map((x) => String(x || "")),
  );
  const rows = Array.isArray(infScenarios.scenarios) ? infScenarios.scenarios : [];
  return rows.filter((x) => enabledSet.has(String(x.id || "")) || x.enabled || x.active);
}

function getActiveScenario() {
  const rows = getEnabledScenarios();
  return rows[0] || null;
}

function renderInfApiPromptDisplay() {
  const el = document.getElementById("inf-api-prompt-display");
  if (el) el.textContent = apiPromptText || "— не задан —";
}

async function loadApiPrompt() {
  const out = await apiGet("/api/inf/api_prompt");
  if (!out.ok) return;
  apiPromptText = String(out.prompt || "").trim();
  if (out.revision) infConfigRevision = String(out.revision).trim();
  const inp = document.getElementById("inf-api-prompt");
  if (inp) inp.value = apiPromptText;
  renderInfApiPromptDisplay();
}

async function saveApiPrompt() {
  const st = document.getElementById("inf-status");
  const inp = document.getElementById("inf-api-prompt");
  const prompt = String(inp?.value || "").trim();
  if (!prompt) {
    if (st) st.textContent = "Введите промпт SAM API.";
    return;
  }
  const out = await apiPost("/api/inf/api_prompt/set", { prompt });
  if (!out.ok) {
    if (st) st.textContent = `Ошибка: ${out.error || "unknown"}`;
    return;
  }
  apiPromptText = String(out.prompt || "").trim();
  if (out.revision) infConfigRevision = String(out.revision).trim();
  renderInfApiPromptDisplay();
  if (st) st.textContent = "Промпт SAM API сохранён.";
  debugLog("inf", `API prompt: ${apiPromptText}`);
}

function renderInfScenarioSummary() {
  const enabled = getEnabledScenarios();
  const listEl = document.getElementById("inf-enabled-scenarios-summary");
  const splitEl = document.getElementById("inf-scenario-split");
  renderInfApiPromptDisplay();
  if (!listEl) return;
  if (!enabled.length) {
    listEl.innerHTML = "<li>— нет включённых —</li>";
    if (splitEl) splitEl.textContent = "Отметьте сценарии галочками ниже.";
    return;
  }
  listEl.innerHTML = enabled.map((sc) => {
    const chain = String(sc.prompt || "").trim();
    const { main, linked } = promptToAnalyzer(chain);
    const parts = main
      ? `main=${main}${linked.length ? `, linked=[${linked.join(", ")}]` : ""}`
      : "";
    return `<li><strong>${esc(sc.title)}</strong>${chain ? `<br><span class="mono muted">Цепочка: ${esc(chain)}</span>` : ""}${parts ? `<br><span class="muted">${esc(parts)}</span>` : ""}</li>`;
  }).join("");
  if (splitEl) {
    splitEl.textContent = enabled.length > 1
      ? `После инференса — ${enabled.length} прогона анализатора в одной папке.`
      : "После инференса — один прогон анализатора.";
  }
}

function renderInfScenariosList() {
  const box = document.getElementById("inf-scenarios-list");
  const countEl = document.getElementById("inf-scenarios-count");
  const rows = Array.isArray(infScenarios.scenarios) ? infScenarios.scenarios : [];
  if (countEl) countEl.textContent = String(rows.length);
  if (!box) return;
  if (!rows.length) {
    box.innerHTML = "<p class='muted small'>Сценариев нет. Добавьте ниже.</p>";
    return;
  }
  const enabledSet = new Set(
    (Array.isArray(infScenarios.enabled_ids) ? infScenarios.enabled_ids : []).map((x) => String(x || "")),
  );
  box.innerHTML = rows.map((sc) => {
    const id = esc(sc.id);
    const checked = enabledSet.has(String(sc.id || "")) || !!sc.enabled || !!sc.active;
    return `
      <div class="scenario-row${checked ? " is-active" : ""}" data-scenario-id="${id}">
        <label class="check-field" title="Включить в обработку">
          <input type="checkbox" class="inf-scenario-enabled" value="${id}" ${checked ? "checked" : ""} />
        </label>
        <div class="scenario-row-body">
          <strong>${esc(sc.title)}</strong>
          <span class="mono small muted">Цепочка: ${esc(sc.prompt)}</span>
        </div>
        <button type="button" class="btn-ghost btn-sm" data-scenario-del="${id}">Удалить</button>
      </div>
    `;
  }).join("");
  box.querySelectorAll(".inf-scenario-enabled").forEach((inp) => {
    inp.addEventListener("change", async () => {
      const sid = String(inp.value || "").trim();
      if (!sid) return;
      await setScenarioEnabled(sid, !!inp.checked);
    });
  });
  box.querySelectorAll("[data-scenario-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const sid = btn.getAttribute("data-scenario-del");
      if (!sid) return;
      if (!confirm("Удалить сценарий?")) return;
      await deleteScenario(sid);
    });
  });
}

async function loadScenarios() {
  const out = await apiGet("/api/inf/scenarios");
  if (!out.ok) {
    debugLog("err", `Сценарии: ${out.error || "unknown"}`);
    return;
  }
  infScenarios = {
    scenarios: Array.isArray(out.scenarios) ? out.scenarios : [],
    enabled_ids: Array.isArray(out.enabled_ids) ? out.enabled_ids.map((x) => String(x || "")) : [],
    active_id: String(out.active_id || "").trim(),
  };
  if (out.revision) infConfigRevision = String(out.revision).trim();
  renderInfScenariosList();
  renderInfScenarioSummary();
}

async function setScenarioEnabled(id, enabled) {
  const st = document.getElementById("inf-status");
  const out = await apiPost("/api/inf/scenarios/set_enabled", { id, enabled: !!enabled });
  if (!out.ok) {
    if (st) st.textContent = `Ошибка сценария: ${out.error || "unknown"}`;
    return;
  }
  infScenarios = {
    scenarios: Array.isArray(out.scenarios) ? out.scenarios : [],
    enabled_ids: Array.isArray(out.enabled_ids) ? out.enabled_ids.map((x) => String(x || "")) : [],
    active_id: String(out.active_id || "").trim(),
  };
  renderInfScenariosList();
  renderInfScenarioSummary();
  if (st) st.textContent = enabled ? "Сценарий включён." : "Сценарий выключен.";
  debugLog("inf", `Сценарий ${id}: enabled=${enabled}`);
}

async function addScenario() {
  const st = document.getElementById("inf-status");
  const title = String(document.getElementById("inf-scenario-title")?.value || "").trim();
  const prompt = String(document.getElementById("inf-scenario-prompt")?.value || "").trim();
  if (!title || !prompt) {
    if (st) st.textContent = "Введите название и цепочку анализатора.";
    return;
  }
  const out = await apiPost("/api/inf/scenarios/add", { title, prompt });
  if (!out.ok) {
    if (st) st.textContent = `Ошибка: ${out.error || "unknown"}`;
    return;
  }
  infScenarios = {
    scenarios: Array.isArray(out.scenarios) ? out.scenarios : [],
    enabled_ids: Array.isArray(out.enabled_ids) ? out.enabled_ids.map((x) => String(x || "")) : [],
    active_id: String(out.active_id || "").trim(),
  };
  document.getElementById("inf-scenario-title").value = "";
  document.getElementById("inf-scenario-prompt").value = "";
  renderInfScenariosList();
  renderInfScenarioSummary();
  if (st) st.textContent = "Сценарий добавлен.";
}

async function deleteScenario(id) {
  const st = document.getElementById("inf-status");
  const out = await apiPost("/api/inf/scenarios/delete", { id });
  if (!out.ok) {
    if (st) st.textContent = `Ошибка: ${out.error || "unknown"}`;
    return;
  }
  infScenarios = {
    scenarios: Array.isArray(out.scenarios) ? out.scenarios : [],
    enabled_ids: Array.isArray(out.enabled_ids) ? out.enabled_ids.map((x) => String(x || "")) : [],
    active_id: String(out.active_id || "").trim(),
  };
  renderInfScenariosList();
  renderInfScenarioSummary();
  if (st) st.textContent = "Сценарий удалён.";
}

function updateFolderScenarioReadonly() {
  // Блок параметров анализатора на вкладке "Работа" отключен по UX-требованию.
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
  await loadScenarios();
  await loadApiPrompt();
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

async function loadFolderAnalyzerParams(folder) {
  folderAnalyzerParams = { main: "", linked: [], prompt: "", violation_label: "", api_prompt: "" };
  if (!folder) return;
  const meta = await apiGet(`/api/folders/meta?folder=${encodeURIComponent(folder)}`);
  if (meta.ok) {
    const chain = String(meta.analyzer_chain || meta.scenario_prompt || "").trim();
    const main = String(meta.analyzer_main || "").trim();
    const violationLabel = String(meta.violation_label || meta.scenario_title || "").trim();
    const apiPrompt = String(meta.api_prompt || "").trim();
    const linked = Array.isArray(meta.analyzer_linked)
      ? meta.analyzer_linked.map((x) => String(x || "").trim()).filter(Boolean)
      : [];
    if (main || chain) {
      folderAnalyzerParams = {
        main: main || promptToAnalyzer(chain).main,
        linked: linked.length ? linked : promptToAnalyzer(chain).linked,
        prompt: chain,
        violation_label: violationLabel,
        api_prompt: apiPrompt,
      };
    }
  }
}

async function loadPromptsForFolder(folder) {
  const anFolder = document.getElementById("an-folder");
  document.getElementById("report-status").textContent = "";
  document.getElementById("report-result").innerHTML = "";
  if (!folder) {
    setAnalyzerFolderHint("");
    folderAnalyzerParams = { main: "", linked: [], prompt: "", violation_label: "", api_prompt: "" };
    updateFolderScenarioReadonly();
    lastWarnings = [];
    setBuildVideoEnabled(false);
    setRunAnalysisEnabled(false);
    setReportEnabled();
    resetWarningVideo();
    clearBuiltWarningVideos();
    return;
  }
  loadBuiltWarningVideosState(folder);
  setAnalyzerFolderHint(folder);
  await loadFolderAnalyzerParams(folder);
  updateFolderScenarioReadonly();
  analysisPrompts = {
    main: folderAnalyzerParams.main,
    linked: folderAnalyzerParams.linked.slice(),
  };
  await loadSavedAnalysis(folder);
  await syncRunAnalysisButton(folder);
}

async function pollAnalysisAndShow(folder) {
  const status = document.getElementById("analysis-status");
  const out = await pollTaskUntilDone(folder, "analysis", (_st, percent, message) => {
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
    main: String(out.main_prompt || folderAnalyzerParams.main || "").trim(),
    linked: Array.isArray(out.linked_prompts)
      ? out.linked_prompts.map((x) => String(x || "").trim()).filter(Boolean)
      : folderAnalyzerParams.linked.slice(),
  };
  const violFromResult = violationLabelFromResult(out);
  folderAnalyzerParams = {
    main: analysisPrompts.main,
    linked: analysisPrompts.linked.slice(),
    prompt: folderAnalyzerParams.prompt,
    violation_label: violFromResult || folderAnalyzerParams.violation_label,
    api_prompt: folderAnalyzerParams.api_prompt,
  };
  updateFolderScenarioReadonly();
  if (status) {
    const thr = out.link_frame_thresholds || {};
    const minPct = thr.min_link_frame_ratio != null
      ? Math.round(Number(thr.min_link_frame_ratio) * 100)
      : 40;
    status.textContent = `Готово. Проверено: ${out.frames_checked}, нарушений: ${out.warnings_count} (порог пересечения по кадрам >= ${minPct}%)`;
  }
  renderWarnings(out.warnings || []);
  setProgressUi(
    "analysis-progress-wrap",
    "analysis-progress-bar",
    "analysis-progress-text",
    "analysis-progress-pct",
    true,
    100,
    "Анализ завершён",
  );
  setTimeout(hideAnalysisProgress, 1200);
  return out;
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
      box.innerHTML = "<p class='muted'>Анализ ещё не запускался. Нажмите «Запустить анализ».</p>";
      return;
    }
    const warnings = Array.isArray(out.warnings) ? out.warnings : [];
    analysisPrompts = {
      main: String(out.main_prompt || "").trim(),
      linked: Array.isArray(out.linked_prompts) ? out.linked_prompts.map((x) => String(x || "").trim()).filter(Boolean) : [],
    };
    const violFromResult = violationLabelFromResult(out);
    if (analysisPrompts.main || violFromResult) {
      folderAnalyzerParams = {
        main: analysisPrompts.main || folderAnalyzerParams.main,
        linked: analysisPrompts.linked.length ? analysisPrompts.linked.slice() : folderAnalyzerParams.linked.slice(),
        prompt: folderAnalyzerParams.prompt,
        violation_label: violFromResult || folderAnalyzerParams.violation_label,
        api_prompt: folderAnalyzerParams.api_prompt,
      };
      updateFolderScenarioReadonly();
    }
    if (out.human_video_urls && typeof out.human_video_urls === "object") {
      Object.entries(out.human_video_urls).forEach(([hid, url]) => {
        const id = String(hid || "").trim();
        const u = String(url || "").trim();
        if (id && u) builtWarningVideoByHumanId[id] = u;
      });
      saveBuiltWarningVideosState();
    }
    renderWarnings(warnings);
    const thr = out.link_frame_thresholds || {};
    const minPct = thr.min_link_frame_ratio != null
      ? Math.round(Number(thr.min_link_frame_ratio) * 100)
      : 40;
    status.textContent = `Загружен анализ. Проверено: ${Number(out.frames_checked || 0)}, нарушений: ${Number(out.warnings_count || 0)} (порог >= ${minPct}% кадров с пересечением)`;
    if (out.preview_video_url) showWarningVideo(out.preview_video_url);
  } catch (e) {
    status.textContent = `Ошибка чтения сохраненного анализа: ${e?.message || e}`;
  }
}

async function syncSelectedFolderPrefer(preferName = "") {
  const res = await apiGet("/api/folders");
  if (!res.ok) return;
  const items = Array.isArray(res.items) ? res.items : [];
  const names = items.map((x) => String(x.name || "")).filter(Boolean);
  const prefer = String(preferName || selectedFolder || "").trim();
  const next = prefer && names.includes(prefer) ? prefer : (names[0] || "");
  if (!next) {
    selectedFolder = "";
    lastWarnings = [];
    setBuildVideoEnabled(false);
    setReportEnabled();
    resetWarningVideo();
    clearBuiltWarningVideos();
    await loadPromptsForFolder("");
    return;
  }
  if (next !== selectedFolder) {
    selectedFolder = next;
    lastWarnings = [];
    setBuildVideoEnabled(false);
    setReportEnabled();
    resetWarningVideo();
    clearBuiltWarningVideos();
  }
  await loadPromptsForFolder(selectedFolder);
}

async function refreshList() {
  const res = await apiGet("/api/folders");
  if (!isAdmin()) {
    const prefer = String(selectedFolder || "").trim();
    const items = Array.isArray(res.items) ? res.items : [];
    const names = items.map((x) => String(x.name || "")).filter(Boolean);
    const next = prefer && names.includes(prefer) ? prefer : (names[0] || "");
    await syncSelectedFolderPrefer(next);
    return;
  }
  const box = document.getElementById("list");
  box.innerHTML = "<p class='muted'>Загрузка...</p>";
  if (!res.ok) {
    box.innerHTML = `<p class='err'>Ошибка: ${esc(res.error || "unknown")}</p>`;
    return;
  }
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
    const base = st.api_base || "-";
    const inp = document.getElementById("api-base-input");
    // Показываем сохраненный адрес всегда, даже когда API временно недоступен.
    if (inp && base && base !== "-") inp.value = base;
    if (!Object.prototype.hasOwnProperty.call(st, "ok")) {
      el.textContent = "API: статус недоступен";
      return;
    }
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
  const enabledScenarios = getEnabledScenarios();
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
  const analysisStatus = document.getElementById("analysis-status");
  const btn = document.getElementById("process-btn");
  if (!video) return alert("Выберите видео");
  if (!apiPromptText || !enabledScenarios.length) {
    await loadServerInfConfig();
  }
  if (!apiPromptText) {
    alert(`Промпт SAM API не задан. ${infAdminHint()}`);
    return;
  }
  if (!enabledScenarios.length) {
    alert(`Нет включённых сценариев анализатора. ${infAdminHint()}`);
    return;
  }
  debugLog(
    "proc",
    `Старт: ${enabledScenarios.length} сценар(иев) — ${enabledScenarios.map((s) => s.title).join(", ")}`,
  );
  const fd = new FormData();
  fd.append("video", video);
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
  let res;
  try {
    res = await apiPost("/api/process_video", fd, true);
  } catch (err) {
    btn.disabled = false;
    status.textContent = `Ошибка: ${err?.message || err}`;
    debugLog("err", String(err?.message || err));
    return;
  }
  btn.disabled = false;
  if (Array.isArray(res.debug)) debugLogServer(res.debug);
  if (!res.ok) {
    status.textContent = `Ошибка: ${res.error || "unknown"}`;
    debugLog("err", res.error || "unknown");
    return;
  }
  const doneTags = [];
  if (Number(res.scale_div || 1) > 1) doneTags.push(`D/${Number(res.scale_div)}`);
  if (Number(res.fps_div || 1) > 1) doneTags.push(`FPS/${Number(res.fps_div)}`);
  if (Number(res.video_part_sec || 0) > 0) {
    doneTags.push(`части/${Number(res.video_part_sec)}с×${Number(res.chunks_total || 0)}`);
  }
  const runs = Array.isArray(res.runs) ? res.runs : [];
  const folder = String(res.folder || "").trim();
  const okRuns = runs.filter((r) => r.analysis_ok);
  const analysisPart = okRuns.length ? `, анализ: ${okRuns.length}/${runs.length} сценариев` : "";
  const tagsPart = doneTags.length ? ` (${doneTags.join(" + ")})` : "";
  status.textContent = isAdmin()
    ? `Готово. Папка: ${folder || "-"}${analysisPart}${tagsPart}`
    : `Готово${analysisPart}${tagsPart}`;
  document.getElementById("video").value = "";
  await refreshList();
  if (folder) {
    selectedFolder = folder;
    await loadPromptsForFolder(folder);
    if (okRuns.length) {
      const last = okRuns[okRuns.length - 1];
      const allViolations = okRuns
        .map((r) => String(r?.violation_label || "").trim())
        .filter(Boolean)
        .filter((x, i, arr) => arr.indexOf(x) === i);
      folderAnalyzerParams = {
        main: String(last.analyzer_main || "").trim(),
        linked: Array.isArray(last.analyzer_linked) ? last.analyzer_linked.map((x) => String(x || "").trim()).filter(Boolean) : [],
        prompt: String((last.scenario && last.scenario.prompt) || "").trim(),
        violation_label: allViolations.join(" | "),
        api_prompt: String(res.api_prompt || apiPromptText || "").trim(),
      };
      await loadSavedAnalysis(folder);
    }
  }
  if (folder && res.analysis_started && !okRuns.length) {
    debugLog("proc", "Авто-анализ: ожидание (фон)…");
    setBuildVideoEnabled(false);
    resetWarningVideo();
    clearBuiltWarningVideos(true);
    hideVideoProgress();
    setProgressUi(
      "analysis-progress-wrap",
      "analysis-progress-bar",
      "analysis-progress-text",
      "analysis-progress-pct",
      true,
      0,
      "Авто-анализ…",
    );
    if (analysisStatus) analysisStatus.textContent = "Авто-анализ…";
    try {
      await pollAnalysisAndShow(folder);
    } catch (err) {
      if (analysisStatus) analysisStatus.textContent = `Ошибка авто-анализа: ${err?.message || err}`;
      debugLog("err", String(err?.message || err));
    }
  } else if (runs.length && analysisStatus) {
    const okCount = runs.filter((r) => r.analysis_ok).length;
    analysisStatus.textContent = `Обработано сценариев: ${runs.length}, анализ OK: ${okCount}.`;
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

document.getElementById("run-analysis-btn")?.addEventListener("click", () => runFolderAnalysis());
document.getElementById("build-video-btn").addEventListener("click", async () => {
  const status = document.getElementById("analysis-status");
  const btn = document.getElementById("build-video-btn");
  if (!selectedFolder) {
    status.textContent = isAdmin() ? "Сначала откройте папку." : "Сначала обработайте видео.";
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

document.getElementById("debug-clear-btn")?.addEventListener("click", clearDebugLog);
document.getElementById("debug-copy-btn")?.addEventListener("click", async () => {
  const text = debugLogLines.join("\n");
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    debugLog("ui", "Лог скопирован в буфер");
  } catch (e) {
    debugLog("err", `Копирование: ${e?.message || e}`);
  }
});
document.getElementById("inf-api-prompt-save")?.addEventListener("click", () => saveApiPrompt());
document.getElementById("inf-scenario-add")?.addEventListener("click", () => addScenario());
document.getElementById("inf-scenario-del")?.addEventListener("click", async () => {
  const enabled = getEnabledScenarios();
  const active = enabled[0] || getActiveScenario();
  if (!active) {
    const st = document.getElementById("inf-status");
    if (st) st.textContent = "Нет сценария для удаления.";
    return;
  }
  if (!confirm(`Удалить сценарий «${active.title}»?`)) return;
  await deleteScenario(String(active.id || ""));
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
  if (!document.hidden) {
    loadBuiltWarningVideosState();
    restoreBuiltWarningVideosDeferred();
  }
});

(async () => {
  debugLog("ui", "WEB samv загружен");
  await loadAccessMe();
  if (accessRole === "admin" || accessRole === "user") {
    refreshApiStatus();
  }
  if (accessRole === "admin") {
    await loadInfOptions();
    await loadApiPrompt();
    await refreshList();
  } else if (accessRole === "user") {
    await loadServerInfConfig();
    await syncSelectedFolderPrefer("");
    startInfConfigAutoSync();
  }
  applyRevealAnimation();
})();
