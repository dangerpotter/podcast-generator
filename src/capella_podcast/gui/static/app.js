/* Capella Course Podcast Generator — GUI logic (vanilla JS, polls the local API). */

"use strict";

const state = {
  courses: [],
  selectedDir: null,
  course: null,        // detailed course state
  view: "course",      // course | settings
  busy: false,
  jobId: null,
  jobStatus: null,
  logCursor: 0,
  config: null,
  consoleOpen: false,
};

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body ? "POST" : "GET"), headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

function toast(message, kind = "ok", ms = 6000) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

function fmtWhen(tsOrIso) {
  if (!tsOrIso) return "";
  const d = typeof tsOrIso === "number" ? new Date(tsOrIso * 1000) : new Date(tsOrIso);
  if (isNaN(d)) return "";
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/* ---------- top-level state ---------- */

async function refreshState() {
  const s = await api("/api/state");
  state.courses = s.courses;
  state.busy = s.busy;
  $("model-badge").textContent = s.model;
  setStatusPill(s.busy, s.job);
  if (s.job) adoptJob(s.job);
  if (!state.selectedDir && s.courses.length) state.selectedDir = s.courses[0].dir;
  if (state.selectedDir && !s.courses.some((c) => c.dir === state.selectedDir)) {
    state.selectedDir = s.courses.length ? s.courses[0].dir : null;
    state.course = null;
  }
  renderSidebar();
  if (state.view === "course") {
    if (!state.selectedDir) showView("empty");
    else if (!state.course || state.course.dir !== state.selectedDir) await loadCourse(state.selectedDir);
  }
}

function setStatusPill(busy, job) {
  const pill = $("status-pill");
  pill.classList.toggle("busy", busy);
  $("status-text").textContent = busy
    ? (job && job.progress ? job.progress.label : "Working…")
    : "Idle";
}

async function loadCourse(dir) {
  try {
    state.course = await api(`/api/course?dir=${encodeURIComponent(dir)}`);
    renderCourse();
    showView("course");
  } catch (e) {
    toast(`Could not load course: ${e.message}`, "err");
  }
}

function showView(name) {
  $("view-empty").hidden = name !== "empty";
  $("view-course").hidden = name !== "course";
  $("view-settings").hidden = name !== "settings";
  state.view = name === "settings" ? "settings" : "course";
}

/* ---------- sidebar ---------- */

function renderSidebar() {
  const nav = $("course-list");
  if (!state.courses.length) {
    nav.innerHTML = `<div style="color:#8db3c7;font-size:12.5px;padding:6px 8px;">Nothing ingested yet.</div>`;
    return;
  }
  nav.innerHTML = state.courses.map((c) => {
    const total = c.modules * 3;
    const done = (c.counts.summary || 0) + (c.counts.script || 0) + (c.counts.podcast || 0);
    return `
      <button class="course-item ${c.dir === state.selectedDir && state.view === "course" ? "active" : ""}"
              data-dir="${esc(c.dir)}">
        <span class="ci-top">
          <span class="ci-number">${esc(c.number || c.dir)}</span>
          <span class="badge badge-type">${esc(c.type || "?")}</span>
          <span class="ci-progress">${done}/${total}</span>
        </span>
        <span class="ci-name">${esc(c.name || "")}</span>
      </button>`;
  }).join("");
  nav.querySelectorAll(".course-item").forEach((el) => {
    el.addEventListener("click", async () => {
      state.selectedDir = el.dataset.dir;
      await loadCourse(state.selectedDir);
      renderSidebar();
    });
  });
}

/* ---------- course view ---------- */

const KIND_LABEL = { summary: "Summary", script: "Script", podcast: "Podcast" };
const KIND_DESC = { summary: "Report DOCX", script: "Two-host DOCX", podcast: "MP3" };

function artDot(a) {
  if (!a.exists) return `<span class="art-dot miss" title="Not generated yet"><svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="3" fill="currentColor"/></svg></span>`;
  if (a.stale || a.edited) return `<span class="art-dot warn" title="${a.stale ? "Out of date" : "Edited after generation"}"><svg viewBox="0 0 16 16"><path d="M8 3v6M8 11.5v1.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></span>`;
  return `<span class="art-dot ok" title="Generated"><svg viewBox="0 0 16 16"><path d="M3 8.5 6.5 12 13 4.5" stroke="currentColor" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg></span>`;
}

function artState(a) {
  if (!a.exists) return `<span>Not generated</span>`;
  const bits = [`<span>${fmtWhen(a.mtime)} · ${fmtSize(a.size)}</span>`];
  if (a.edited) bits.push(`<span class="tag edited" title="The file was modified after it was generated — regenerate downstream artifacts to pick up your edits.">edited</span>`);
  if (a.stale) bits.push(`<span class="tag stale" title="An upstream file is newer than this one.">needs update</span>`);
  if (a.warnings && a.warnings.length) bits.push(`<span class="tag stale" title="${esc(a.warnings.join("\n"))}">${a.warnings.length} warning${a.warnings.length > 1 ? "s" : ""}</span>`);
  return bits.join("");
}

function btn(label, attrs = "", cls = "btn btn-small") {
  return `<button class="${cls}${state.busy ? " disabled" : ""}" ${attrs}>${label}</button>`;
}

function artifactRow(m, kind) {
  const a = m.artifacts[kind];
  const acts = [];
  const upstream = kind === "script" ? m.artifacts.summary : kind === "podcast" ? m.artifacts.script : null;
  const stageAction = { summary: "summaries", script: "scripts", podcast: "podcasts" }[kind];

  if (kind === "podcast" && a.exists) {
    acts.push(btn("▶ Play", `data-act="play" data-dir="${esc(m.dir)}" data-module="${m.number}"`, "btn btn-small btn-primary"));
  }
  if (a.exists) {
    if (a.stale) {
      const src = kind === "script" ? "summary" : "script";
      acts.push(btn(`Update from ${src}`, `data-act="run" data-action="${stageAction}" data-module="${m.number}"`, "btn btn-small btn-warn"));
    } else {
      acts.push(btn("Regenerate", `data-act="run" data-action="${stageAction}" data-module="${m.number}" data-confirm-edited="${kind}"`));
    }
    if (kind !== "podcast") acts.push(btn("Open", `data-act="open-file" data-dir="${esc(m.dir)}" data-kind="${kind}" title="Open in Word"`));
  } else {
    const blocked = upstream && !upstream.exists;
    const tip = blocked ? `title="Generate the ${kind === "script" ? "summary" : "script"} first"` : "";
    acts.push(`<button class="btn btn-small btn-primary${state.busy || blocked ? " disabled" : ""}"
      data-act="run" data-action="${stageAction}" data-module="${m.number}" ${tip}>Generate</button>`);
  }
  return `
    <div class="artifact-row">
      <span class="art-name">${KIND_LABEL[kind]} <span class="sub" style="color:var(--muted);font-weight:400;font-size:11.5px;">${KIND_DESC[kind]}</span></span>
      ${artDot(a)}
      <span class="art-state">${artState(a)}</span>
      <span class="art-actions">${acts.join("")}</span>
    </div>`;
}

function moduleCard(m, label) {
  const notes = m.notes && m.notes.length
    ? `<span class="module-meta" title="${esc(m.notes.join("\n"))}">⚠ ${m.notes.length} note${m.notes.length > 1 ? "s" : ""}</span>` : "";
  return `
    <div class="module-card" data-module="${m.number}">
      <div class="module-head">
        <span class="module-num">${esc(label)} ${m.number}</span>
        <span class="module-title" title="${esc(m.title)}">${esc(m.title)}</span>
        <span class="module-meta">${m.activities} activities · ${m.resources} resources</span>
        ${notes}
        <span class="module-actions">
          ${btn("Run all stages", `data-act="run" data-action="pipeline" data-module="${m.number}"`)}
          <span class="menu-wrap">
            <button class="icon-btn" data-act="menu" title="More actions">⋯</button>
            <div class="menu" hidden>
              <button data-act="run" data-action="regen-summary" data-module="${m.number}">Regen script + podcast from edited summary</button>
              <button data-act="run" data-action="regen-script" data-module="${m.number}">Regen podcast from edited script</button>
              <div class="menu-sep"></div>
              <button data-act="open-module" data-dir="${esc(m.dir)}">Open folder in Explorer</button>
            </div>
          </span>
        </span>
      </div>
      ${artifactRow(m, "summary")}
      ${artifactRow(m, "script")}
      ${artifactRow(m, "podcast")}
    </div>`;
}

function renderCourse() {
  const c = state.course;
  if (!c) return;
  const course = c.course;
  const typeBadge = course.type === "GP"
    ? `<span class="badge badge-gp">Guided Path</span>`
    : `<span class="badge badge-fpx">FlexPath</span>`;
  const warnings = c.warnings && c.warnings.length ? `
    <details class="ch-warnings">
      <summary>${c.warnings.length} ingest warning${c.warnings.length > 1 ? "s" : ""} (skipped or missing data)</summary>
      <ul>${c.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
    </details>` : "";

  $("view-course").innerHTML = `
    <div class="course-head">
      <div class="ch-top">
        <div>
          <h2>${esc(course.number || c.dir)} — ${esc(course.name || "Untitled course")}</h2>
          <div class="ch-badges">
            ${typeBadge}
            ${course.credits ? `<span class="badge badge-gp">${esc(course.credits)} credits</span>` : ""}
            <span class="badge badge-gp">${c.modules.length} ${esc(course.module_label || "module").toLowerCase()}${c.modules.length !== 1 ? "s" : ""}</span>
          </div>
        </div>
        <div class="ch-actions">
          ${btn("Generate remaining", `data-act="run" data-action="pipeline" data-only-missing="1" title="Run every stage that has not produced its file yet"`, "btn btn-primary")}
          <span class="menu-wrap">
            <button class="icon-btn" data-act="menu" title="More actions" style="font-size:17px;">⋯</button>
            <div class="menu" hidden>
              <button data-act="run" data-action="pipeline" data-confirm="This regenerates EVERY summary, script, and podcast, overwriting any edits you made. Continue?">Regenerate everything</button>
              <button data-act="reingest">Re-ingest source JSON</button>
              <div class="menu-sep"></div>
              <button data-act="open-course">Open course folder in Explorer</button>
            </div>
          </span>
        </div>
      </div>
      <div class="ch-meta">
        Source: ${esc(c.source || "unknown")}${c.ingested_at ? ` · ingested ${fmtWhen(c.ingested_at)}` : ""}
      </div>
      ${warnings}
    </div>
    ${c.modules.map((m) => moduleCard(m, course.module_label)).join("")}
  `;
  bindCourseActions();
}

function bindCourseActions() {
  const root = $("view-course");
  root.querySelectorAll("[data-act]").forEach((el) => {
    const act = el.dataset.act;
    if (act === "menu") {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const menu = el.parentElement.querySelector(".menu");
        const wasHidden = menu.hidden;
        closeMenus();
        menu.hidden = !wasHidden;
      });
    } else if (act === "run") {
      el.addEventListener("click", () => {
        if (el.dataset.confirm && !confirm(el.dataset.confirm)) return;
        if (el.dataset.confirmEdited) {
          const m = state.course.modules.find((x) => x.number === Number(el.dataset.module));
          const a = m && m.artifacts[el.dataset.confirmEdited];
          if (a && a.edited && !confirm(
            `This ${el.dataset.confirmEdited} was edited after it was generated. Regenerating overwrites your edits. Continue?`)) return;
        }
        runAction(el.dataset.action, el.dataset.module ? Number(el.dataset.module) : null,
                  el.dataset.onlyMissing === "1");
      });
    } else if (act === "play") {
      el.addEventListener("click", () => playPodcast(el.dataset.dir, Number(el.dataset.module)));
    } else if (act === "open-file") {
      el.addEventListener("click", () => openTarget({ target: "file", course: state.selectedDir, dir: el.dataset.dir, kind: el.dataset.kind }));
    } else if (act === "open-module") {
      el.addEventListener("click", () => openTarget({ target: "module", course: state.selectedDir, dir: el.dataset.dir }));
    } else if (act === "open-course") {
      el.addEventListener("click", () => openTarget({ target: "course", course: state.selectedDir }));
    } else if (act === "reingest") {
      el.addEventListener("click", () => {
        const src = state.course && state.course.source;
        openIngestModal(src || "");
      });
    }
  });
}

function closeMenus() {
  document.querySelectorAll(".menu").forEach((m) => { m.hidden = true; });
}
document.addEventListener("click", closeMenus);

/* ---------- actions ---------- */

async function runAction(action, module, onlyMissing = false) {
  if (state.busy) { toast("A job is already running — wait for it to finish.", "warn"); return; }
  try {
    const res = await api("/api/run", {
      body: { action, course: state.selectedDir, module, only_missing: onlyMissing },
    });
    adoptJob(res.job);
    state.busy = true;
    openConsole(true);
    renderCourse();
    setStatusPill(true, res.job);
  } catch (e) {
    toast(e.message, "err");
  }
}

async function openTarget(body) {
  try { await api("/api/open", { body }); }
  catch (e) { toast(e.message, "err"); }
}

function playPodcast(dir, moduleNumber) {
  const label = state.course ? state.course.course.module_label : "Module";
  $("player-title").textContent = `${state.selectedDir} · ${label} ${moduleNumber}`;
  const audio = $("player-audio");
  audio.src = `/api/file?course=${encodeURIComponent(state.selectedDir)}&dir=${encodeURIComponent(dir)}&kind=podcast&t=${Date.now()}`;
  $("player").hidden = false;
  audio.play().catch(() => {});
}

/* ---------- job console ---------- */

function adoptJob(job) {
  if (state.jobId !== job.id) {
    state.jobId = job.id;
    state.jobStatus = job.status;
    state.logCursor = 0;
    $("console-log").textContent = "";
  }
  updateConsole(job);
}

function openConsole(open) {
  state.consoleOpen = open;
  $("console").classList.toggle("collapsed", !open);
  $("console-toggle").textContent = open ? "▾" : "▴";
}

function updateConsole(job) {
  $("console-title").textContent = job.title;
  $("console-spinner").hidden = job.status !== "running";
  $("btn-cancel").hidden = !(job.status === "running" || job.status === "queued");
  const p = job.progress;
  if (job.status === "running" && p && p.total) {
    $("console-progress").textContent = `${p.done}/${p.total} · ${p.label}`;
    $("console-bar-fill").style.width = `${Math.round((p.done / p.total) * 100)}%`;
  } else if (job.status === "done") {
    $("console-progress").textContent = "finished";
    $("console-bar-fill").style.width = "100%";
  } else if (job.status === "running") {
    $("console-progress").textContent = "starting…";
    $("console-bar-fill").style.width = "4%";
  } else {
    $("console-progress").textContent = job.status === "queued" ? "queued" : job.status;
  }
}

async function pollJob() {
  if (state.jobId === null) return;
  try {
    const res = await api(`/api/job?id=${state.jobId}&since=${state.logCursor}`);
    if (!res.job) return;
    const job = res.job;
    if (res.lines.length) {
      const log = $("console-log");
      const pinned = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
      log.textContent += res.lines.join("\n") + "\n";
      if (pinned) log.scrollTop = log.scrollHeight;
      state.logCursor = res.cursor;
    }
    updateConsole(job);
    const wasRunning = state.jobStatus === "running" || state.jobStatus === "queued";
    const isTerminal = ["done", "error", "cancelled"].includes(job.status);
    state.jobStatus = job.status;
    if (wasRunning && isTerminal) {
      state.busy = false;
      if (job.status === "done") toast(`Finished: ${job.title}`, "ok");
      else if (job.status === "cancelled") toast(`Stopped: ${job.title}`, "warn");
      else toast(`Failed: ${job.title} — ${job.error || "see log"}`, "err", 10000);
      await refreshState();
      if (state.selectedDir && state.view === "course") await loadCourse(state.selectedDir);
    }
    setStatusPill(job.status === "running" || job.status === "queued", job);
  } catch { /* server briefly busy; retry next tick */ }
}

/* ---------- settings ---------- */

const MODEL_INFO = {
  "12b": { title: "Gemma 4 12B (recommended)", desc: "Best quality. ~6.3 GB download, wants ~12 GB free RAM." },
  "e4b": { title: "Gemma 4 E4B (light)", desc: "Faster on weak hardware. ~4.8 GB download, lower quality." },
};

async function showSettings() {
  try { state.config = await api("/api/config"); }
  catch (e) { toast(e.message, "err"); return; }
  renderSettings();
  showView("settings");
  renderSidebar();
}

function voiceSelect(id, current, options) {
  const known = options.some((o) => o.id === current);
  const opts = options.map((o) =>
    `<option value="${esc(o.id)}" ${o.id === current ? "selected" : ""}>${esc(o.label)}</option>`);
  if (!known && current) opts.unshift(`<option value="${esc(current)}" selected>${esc(current)} (custom)</option>`);
  return `<select id="${id}">${opts.join("")}</select>`;
}

function renderSettings() {
  const cfg = state.config;
  const v = cfg.values;
  const pinned = !!cfg.model_override;
  $("view-settings").innerHTML = `
    <div class="settings-card">
      <h3>Language model</h3>
      <p class="hint">Runs fully locally through embedded llama.cpp. Switching presets downloads the other model once (~5–7 GB), then everything is offline.</p>
      ${pinned ? `<p class="hint" style="color:var(--warn);">Model preset is pinned to “${esc(cfg.model_override)}” by the --model launch flag; change it by relaunching without the flag.</p>` : ""}
      <div class="model-options">
        ${cfg.presets.map((p) => `
          <label class="model-option ${v["llm.model"] === p ? "selected" : ""}">
            <input type="radio" name="model" value="${esc(p)}" ${v["llm.model"] === p ? "checked" : ""} ${pinned ? "disabled" : ""}>
            <span class="mo-title">${esc((MODEL_INFO[p] || { title: p }).title)}</span>
            <div class="mo-desc">${esc((MODEL_INFO[p] || { desc: "" }).desc)}</div>
          </label>`).join("")}
      </div>
      <div class="field-grid" style="margin-top:16px;">
        <div class="field">
          <label>Context length <span class="sub">tokens per call</span></label>
          <input type="number" id="set-context" min="1024" max="262144" step="1024" value="${v["llm.context_length"]}">
        </div>
        <div class="field">
          <label>Temperature</label>
          <input type="number" id="set-temp" min="0" max="2" step="0.05" value="${v["llm.sampling.temperature"]}">
        </div>
        <div class="field">
          <label>Top-p</label>
          <input type="number" id="set-topp" min="0" max="1" step="0.01" value="${v["llm.sampling.top_p"]}">
        </div>
        <div class="field">
          <label>Top-k</label>
          <input type="number" id="set-topk" min="1" max="500" step="1" value="${v["llm.sampling.top_k"]}">
        </div>
      </div>
      <div style="margin-top:14px;">
        <label class="switch"><input type="checkbox" id="set-thinking" ${v["llm.thinking_mode"] ? "checked" : ""}>
          Thinking mode <span class="sub" style="color:var(--muted);">(slower, off by default)</span></label>
      </div>
    </div>

    <div class="settings-card">
      <h3>Voices &amp; audio</h3>
      <p class="hint">Kokoro reads the script with one voice per host. Preview voices by regenerating a single module's podcast.</p>
      <div class="field-grid">
        <div class="field"><label>Host A voice</label>${voiceSelect("set-voice-a", v["tts.voices"][0] || "", cfg.voice_options)}</div>
        <div class="field"><label>Host B voice</label>${voiceSelect("set-voice-b", v["tts.voices"][1] || "", cfg.voice_options)}</div>
        <div class="field">
          <label>Speech speed</label>
          <input type="number" id="set-speed" min="0.5" max="2" step="0.05" value="${v["tts.speed"]}">
        </div>
        <div class="field">
          <label>MP3 bitrate</label>
          <select id="set-bitrate">
            ${["64k", "96k", "128k", "192k"].map((b) =>
              `<option ${v["tts.mp3_bitrate"] === b ? "selected" : ""}>${b}</option>`).join("")}
          </select>
        </div>
      </div>
    </div>

    <div class="settings-card">
      <h3>Podcast length</h3>
      <p class="hint">Target spoken length the script generator aims for.</p>
      <div class="field-grid">
        <div class="field">
          <label>Minimum minutes</label>
          <input type="number" id="set-min" min="1" max="30" value="${v["podcast.target_minutes_min"]}">
        </div>
        <div class="field">
          <label>Maximum minutes</label>
          <input type="number" id="set-max" min="1" max="60" value="${v["podcast.target_minutes_max"]}">
        </div>
      </div>
    </div>

    <div class="settings-card">
      <div class="settings-actions">
        <button class="btn btn-primary" id="btn-save-settings">Save settings</button>
        <button class="btn" id="btn-open-config">Open config.yaml</button>
        <span class="settings-note">Saved to ${esc(cfg.config_path)} — comments are preserved. Advanced options (model repo, cache dir, branding) live in the file itself.</span>
      </div>
    </div>
  `;

  $("view-settings").querySelectorAll('input[name="model"]').forEach((r) => {
    r.addEventListener("change", () => {
      $("view-settings").querySelectorAll(".model-option").forEach((el) =>
        el.classList.toggle("selected", el.querySelector("input").checked));
    });
  });
  $("btn-open-config").addEventListener("click", () => openTarget({ target: "config" }));
  $("btn-save-settings").addEventListener("click", saveSettings);
}

async function saveSettings() {
  const v = state.config.values;
  const updates = {};
  const num = (id) => Number($(id).value);
  const set = (key, val) => { if (JSON.stringify(val) !== JSON.stringify(v[key])) updates[key] = val; };

  const modelRadio = document.querySelector('input[name="model"]:checked');
  if (modelRadio && !state.config.model_override) set("llm.model", modelRadio.value);
  set("llm.context_length", num("set-context"));
  set("llm.sampling.temperature", num("set-temp"));
  set("llm.sampling.top_p", num("set-topp"));
  set("llm.sampling.top_k", num("set-topk"));
  set("llm.thinking_mode", $("set-thinking").checked);
  set("tts.voices", [$("set-voice-a").value, $("set-voice-b").value]);
  set("tts.speed", num("set-speed"));
  set("tts.mp3_bitrate", $("set-bitrate").value);
  set("podcast.target_minutes_min", num("set-min"));
  set("podcast.target_minutes_max", num("set-max"));

  if (num("set-min") > num("set-max")) { toast("Minimum minutes cannot exceed maximum.", "err"); return; }
  if (!Object.keys(updates).length) { toast("No changes to save.", "warn"); return; }
  try {
    state.config = await api("/api/config", { body: { updates } });
    toast("Settings saved. They apply to the next generation run.", "ok");
    renderSettings();
    await refreshState();
  } catch (e) {
    toast(`Could not save: ${e.message}`, "err", 9000);
  }
}

/* ---------- ingest modal ---------- */

function openIngestModal(prefill = "") {
  $("ingest-path").value = prefill;
  $("ingest-course-type").value = "";
  $("ingest-error").hidden = true;
  $("ingest-modal").showModal();
  if (!prefill) $("ingest-path").focus();
}

async function doBrowse() {
  try {
    const res = await api("/api/browse", { body: {} });
    if (res.unsupported) { toast("Native file picker unavailable — paste the path instead.", "warn"); return; }
    if (res.path) $("ingest-path").value = res.path;
  } catch (e) {
    toast(e.message, "err");
  }
}

async function doIngest() {
  const path = $("ingest-path").value.trim();
  const course_type = $("ingest-course-type").value;
  const errEl = $("ingest-error");
  if (!path) { errEl.textContent = "Enter or browse to the course export file (.json or .txt)."; errEl.hidden = false; return; }
  const goBtn = $("ingest-go");
  goBtn.disabled = true;
  goBtn.textContent = "Ingesting…";
  try {
    const res = await api("/api/ingest", { body: { path, course_type } });
    $("ingest-modal").close();
    const wn = res.warnings.length ? ` (${res.warnings.length} warning${res.warnings.length > 1 ? "s" : ""} — see course page)` : "";
    toast(`Ingested ${res.course.number}: ${res.modules} ${res.course.module_label.toLowerCase()}s${wn}`, res.warnings.length ? "warn" : "ok");
    state.selectedDir = res.dir;
    state.course = null;
    await refreshState();
    await loadCourse(res.dir);
    renderSidebar();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.hidden = false;
  } finally {
    goBtn.disabled = false;
    goBtn.textContent = "Ingest";
  }
}

/* ---------- wiring ---------- */

$("btn-ingest").addEventListener("click", () => openIngestModal());
$("btn-ingest-empty").addEventListener("click", () => openIngestModal());
$("btn-browse").addEventListener("click", doBrowse);
$("ingest-go").addEventListener("click", doIngest);
$("ingest-cancel").addEventListener("click", () => $("ingest-modal").close());
$("ingest-path").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doIngest(); } });
$("nav-settings").addEventListener("click", showSettings);
$("console-toggle").addEventListener("click", () => openConsole(!state.consoleOpen));
$("console-head").addEventListener("dblclick", () => openConsole(!state.consoleOpen));
$("btn-cancel").addEventListener("click", async () => {
  try { await api("/api/job/cancel", { body: { id: state.jobId } }); toast("Will stop after the current item.", "warn"); }
  catch (e) { toast(e.message, "err"); }
});
$("player-close").addEventListener("click", () => {
  const audio = $("player-audio");
  audio.pause();
  audio.removeAttribute("src");
  $("player").hidden = true;
});

(async function init() {
  try { await refreshState(); }
  catch (e) { toast(`Could not reach the local server: ${e.message}`, "err", 12000); }
  setInterval(pollJob, 1000);
  setInterval(() => { refreshState().catch(() => {}); }, 6000);
})();
