"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// Browser storage only holds conveniences (cover-letter drafts, list filters, last tab); it may be unavailable.
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

async function api(path, opts = {}) {
  const r = await fetch(path, { method: opts.method || (opts.body ? "POST" : "GET"),
    headers: { "Content-Type": "application/json" }, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

async function pollJob(jid, onLog) {
  let seen = 0;
  for (;;) {
    const j = await api(`/api/jobs/${jid}`);
    for (; seen < j.log.length; seen++) onLog(j.log[seen]);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.error);
    await sleep(700);
  }
}

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("on"), ms);
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { toast("Couldn't copy. Select the text and copy it by hand."); }
}

const isUrl = (s) => /^https?:\/\/\S+$/.test(s.trim());
const initial = (s) => esc((s || "?").trim().charAt(0).toUpperCase());
const daysAgo = (iso) => (iso ? Math.max(0, Math.floor((Date.now() - new Date(iso + "T00:00:00").getTime()) / 86400000)) : null);
const ago = (iso) => { const d = daysAgo(iso); return d === null ? "" : d === 0 ? "today" : d === 1 ? "yesterday" : `${d} days ago`; };
const pages = (n) => `${n} page${n > 1 ? "s" : ""}`;
const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
// One scale for every score in the app (NeoPOP scoremeter: excellent, average, poor).
const tone = (n) => (n >= 85 ? "tone-high" : n >= 65 ? "tone-mid" : "tone-low");
const scoreNum = (n) => `<span class="score-num num ${tone(n)}">${n}<small>%</small></span>`;
const logoTile = (id, has, name, size = "logo--sm") => `<span class="logo ${size}">${has ? `<img src="/apps/${esc(id)}/logo" alt="">` : initial(name)}</span>`;
const ICON = {
  out: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" fill="none" stroke="currentColor" stroke-width="2"/></svg>`,
  x: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2"/></svg>`,
  up: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6" fill="none" stroke="currentColor" stroke-width="2"/></svg>`,
  star: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z" fill="currentColor"/></svg>`,
  arrow: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="square"/></svg>`,
};

const S = { state: null, prefs: null, app: null, job: null, item: null, mode: "pdf", batch: [], selected: new Set(), tab: store.get("cv.tab", "changes") };

// A tab row wider than its space scrolls sideways with an ordinary (vertical) mouse wheel.
document.addEventListener("wheel", (e) => {
  const row = e.target.closest?.(".tabs:not(.tabs--wrap)");
  if (!row || row.scrollWidth <= row.clientWidth || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
  e.preventDefault();
  row.scrollLeft += e.deltaY;
}, { passive: false });

// ================= boot =================
async function boot() {
  S.state = await api("/api/state");
  S.prefs = S.state.profile.preferences;
  wireNav();
  wireCapsule();
  wireStage();
  wireDiscover();
  wireDrawer();
  wireEngineSwitch();
  $("#careerGen").onclick = generateCareer;
  renderChips();
  renderEmpty();
  previewBase();
  pollBatch();
  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
    if (e.key === "/" && !typing) { e.preventDefault(); showView("tailor"); $("#cmd").focus(); }
    if (e.key === "Escape") { closeDrawer(); closeMenus(); }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && S.job && !S.item && !S.app && $("#v-tailor").classList.contains("on") && document.activeElement !== $("#cmd")) tailorJob();
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".menu-wrap")) closeMenus(); });
}

function wireNav() {
  $("#nav").onclick = (e) => { const b = e.target.closest("button"); if (b) showView(b.dataset.view); };
}

const VIEWS = ["tailor", "discover", "pipeline", "interview", "career"];
function showView(v) {
  const from = $(".view.on")?.id.slice(2);
  $$("#nav button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  const swap = () => {
    $$(".view").forEach((s) => s.classList.toggle("on", s.id === `v-${v}`));
    window.scrollTo(0, 0);
    if (v === "discover") loadFound();
    if (v === "pipeline") loadPipeline();
    if (v === "career") loadCareer();
    if (v === "interview") loadInterview();
    renderDock();
  };
  if (from === v) return swap();
  FX.page(VIEWS.indexOf(v), $(`#nav [data-view=${v}]`).firstChild.textContent.trim(), swap);
}

const D = () => S.prefs.defaults;
async function savePrefs(patch) {
  const p = await api("/api/profile", { body: { preferences: patch } });
  S.state.profile = p;
  S.prefs = p.preferences;
}

// ================= composer: paste a link or a JD =================
function wireCapsule() {
  const cmd = $("#cmd");
  const size = () => {
    const v = cmd.value.trim();
    $("#capsule").classList.toggle("composer--long", v.length > 140 && !isUrl(v));
    cmd.style.height = "auto"; cmd.style.height = Math.min(cmd.scrollHeight, 220) + "px";
  };
  cmd.addEventListener("input", () => { size(); hintCapsule(); });
  cmd.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const v = cmd.value.trim();
    if (e.ctrlKey || e.metaKey || (!e.shiftKey && isUrl(v))) { e.preventDefault(); readInput(); }
  });
  cmd.addEventListener("paste", () => setTimeout(() => { size(); if (isUrl(cmd.value)) readInput(); else hintCapsule(); }, 0));
  $("#cmdGo").onclick = readInput;
  hintCapsule();
}

function resetComposer() {
  $("#cmd").value = ""; $("#cmd").style.height = "auto"; $("#capsule").classList.remove("composer--long"); hintCapsule();
}

function hintCapsule() {
  const v = $("#cmd").value.trim();
  const st = $("#cmdStatus");
  st.classList.remove("err");
  if (!v) st.innerHTML = `<span class="kbd">/</span> to focus. Any careers-page link, or the whole description.`;
  else if (isUrl(v)) st.innerHTML = `<span class="kbd">Enter</span> to read the posting`;
  else st.innerHTML = `<span class="num">${v.split(/\s+/).length}</span> words. <span class="kbd">Ctrl</span><span class="kbd">Enter</span> to use this description`;
}

async function readInput() {
  const v = $("#cmd").value.trim();
  const st = $("#cmdStatus");
  if (!v) { $("#cmd").focus(); return; }
  if (!isUrl(v)) {
    if (v.length < 200) { st.classList.add("err"); st.textContent = "That's too short for a job description. Paste the whole posting, or its link."; return; }
    setJob({ role: "", company: "", location: "", job_id: "", posted: "", url: "", description: v, method: "pasted", market: "auto" });
    resetComposer();
    return;
  }
  const cap = $("#capsule");
  cap.classList.add("working");
  $("#cmdGo").disabled = true;
  st.textContent = "Reading the posting";
  try {
    const { job } = await api("/api/extract", { body: { url: v } });
    const got = await pollJob(job, (l) => (st.textContent = l.msg));
    setJob(got);
    resetComposer();
  } catch (e) {
    st.classList.add("err"); st.textContent = e.message;
  } finally {
    cap.classList.remove("working");
    $("#cmdGo").disabled = false;
  }
}

function setJob(job) {
  S.job = job; S.app = null; S.item = null;
  $("#side").classList.add("has-job");
  renderJobCard();
  $("#insights").innerHTML = "";
  $("#emptyHero").innerHTML = "";
  $("#emptyState").innerHTML = "";
  previewBase();
}

// ================= job card =================
function marketOptions(cur) {
  const auto = `<option value="auto" ${cur === "auto" ? "selected" : ""}>Detect from location</option>`;
  return auto + Object.entries(S.state.markets).map(([k, m]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${esc(m.label)}</option>`).join("");
}

function renderJobCard() {
  const j = S.job;
  if (!j) { $("#jobCard").innerHTML = ""; return; }
  const item = S.item ? S.batch.find((b) => b.id === S.item) : null;
  const running = item && ["queued", "running"].includes(item.status);
  const failed = item && item.status === "error";
  const done = !!S.app;
  const market = j.market && j.market !== "auto" ? j.market : D().market;
  const words = (j.description || "").split(/\s+/).filter(Boolean).length;
  const facts = [
    j.location && `<span class="chip"><span>${esc(j.location)}</span></span>`,
    j.posted && `<span class="chip ${daysAgo(j.posted) <= 3 ? "chip--ok" : ""}"><span>Posted ${esc(ago(j.posted))}</span></span>`,
    j.job_id && `<span class="chip" title="${esc(j.job_id)}"><span>ID ${esc(j.job_id)}</span></span>`,
    j.url && `<a class="chip" href="${esc(j.url)}" target="_blank" rel="noopener" title="Open the posting: ${esc(j.url)}"><span>${esc(host(j.url) || "Open posting")}</span>${ICON.out}</a>`,
  ].filter(Boolean).join("");
  const state = running
    ? `<div><div class="progress"></div><div class="progress__text" id="jobStage">${esc(item.stage || "Working")}</div>
        <p class="type-meta" style="margin-top:4px">Usually one to three minutes. It carries on if you leave; follow it in Pipeline.</p></div>`
    : failed ? `<div class="notice notice--bad">${esc(item.stage)}</div><div class="job__foot"><span class="type-meta"></span><button class="btn btn--primary" id="jRetry">Try again</button></div>`
    : done ? (S.app.scores.after < S.app.scores.ceiling - 3
        ? `<div class="job__foot"><span class="type-meta">Your profile can now reach ${S.app.scores.ceiling}% for this job; this CV is at ${S.app.scores.after}%.</span><button class="btn btn--primary btn--sm" id="jRedo">Re-tailor</button></div>`
        : `<div class="job__foot"><span class="type-meta">Tailored from your profile only. Every number and skill was checked against it.</span><button class="btn btn--sm" id="jRedo" title="Rewrite this CV with your current profile">Re-tailor</button></div>`)
        + `<button class="btn btn--primary btn--block" id="jPrep">Prepare for the interview ${ICON.arrow}</button>`
    : `<div class="job__controls">
        <label class="field"><span class="eyebrow">Market</span><select id="jMarket" class="control--sm">${marketOptions(market)}</select></label>
        <label class="field"><span class="eyebrow">Career track</span><select id="jTrack" class="control--sm"><option value="auto" ${D().track === "auto" ? "selected" : ""}>From the job title</option>${Object.entries(S.state.tracks).map(([k, t]) => `<option value="${k}" ${k === D().track ? "selected" : ""}>${esc(t.label)}</option>`).join("")}</select></label>
      </div>
      <button class="btn btn--accent btn--lg btn--block" id="jGo" title="Ctrl+Enter">Tailor my CV ${ICON.arrow}</button>`;
  $("#jobCard").innerHTML = `
    <article class="card plunk job">
      <div class="job__head">
        ${logoTile(done && S.app.id, done && S.app.logo, j.company || j.role, "logo--lg")}
        <div class="job__titles">${running || done
          ? `<h2 class="job__role">${esc(j.role || "Untitled role")}</h2><span class="job__company">${esc(j.company)}</span>`
          : `<input class="inline-input job__role" id="jRole" value="${esc(j.role)}" placeholder="Role (read from the description)" aria-label="Role">
          <input class="inline-input job__company" id="jCompany" value="${esc(j.company)}" placeholder="Company (read from the description)" aria-label="Company">`}
        </div>
      </div>
      ${facts ? `<div class="chip-row job__facts">${facts}</div>` : ""}
      ${state}
      ${j.description ? `<details class="disclosure"><summary>Job description<span class="disclosure__aside num">${words} words${j.method === "pasted" ? ", pasted" : ""}</span></summary>
        <div class="disclosure__body" style="padding-left:0"><div class="textbox textbox--scroll">${esc(j.description)}</div></div></details>` : ""}
    </article>`;
  const go = $("#jGo");
  if (go) go.onclick = () => tailorJob();
  const retry = $("#jRetry");
  if (retry) retry.onclick = () => { S.item = null; renderJobCard(); tailorJob(); };
  const prep = $("#jPrep");
  if (prep) prep.onclick = () => openInterview(S.app.id);
  const redo = $("#jRedo");
  if (redo) redo.onclick = () => {
    const old = S.app;
    S.job.market = old.market;
    S.app = null; S.item = null;
    tailorJob({ replace_id: old.id, market: old.market, track: old.options.track, variant: old.options.variant, max_pages: String(old.options.max_pages) });
  };
}

async function tailorJob(extra = {}) {
  const j = S.job;
  if (!j || S.item) return;
  const market = $("#jMarket") ? $("#jMarket").value : (j.market || "auto");
  const item = {
    jd: j.description, company: ($("#jCompany")?.value || j.company || "").trim(), role: ($("#jRole")?.value || j.role || "").trim(),
    job_url: j.url || "", job_id: j.job_id || "", location: j.location || "", posted: j.posted || "",
    market: market === "auto" ? (j.market && j.market !== "auto" ? j.market : "auto") : market,
    track: $("#jTrack") ? $("#jTrack").value : D().track, ...extra,
  };
  j.company = item.company; j.role = item.role;
  const { ids } = await api("/api/batch", { body: { items: [item] } });
  S.item = ids[0];
  S.batch.unshift({ id: S.item, status: "queued", stage: "Waiting for a free slot", company: item.company, role: item.role });
  renderJobCard();
  $("#insights").innerHTML = "";
  const pv = $("#pv");
  pv.classList.add("loading");
  $("#busyMsg").textContent = "Tailoring your CV for this job";
  pollBatch(true);
}

// called by the batch poller when the Studio's item changes
async function onStudioItem(item) {
  if (item.status === "done" && item.app_id && (!S.app || S.app.id !== item.app_id)) {
    S.app = await api(`/api/apps/${item.app_id}`);
    S.item = null;
    renderJobCard(); renderInsights(); S.mode = "pdf"; showApp(true);
    toast(`Tailored for ${S.app.company}. The report is on the left.`, 4000);
  } else if (item.status === "error") {
    $("#pv").classList.remove("loading");
    renderJobCard();
  } else {
    $("#busyMsg").textContent = item.stage || "Tailoring";
    const t = $("#jobStage");
    if (t) t.textContent = item.stage || "Working";
    else renderJobCard();
  }
}

// ================= empty state =================
async function renderEmpty() {
  if (S.job) return;
  const rows = await api("/api/apps").catch(() => []);
  if (S.job) return;
  $("#emptyHero").innerHTML = `
    <div class="hero">
      <span class="eyebrow">Tailor</span>
      <h1 class="type-display">One paste.<br><em>A CV built for the job.</em></h1>
      <p class="type-body">Drop in a job link or the description. Your CV is rewritten from your own profile, checked line by line the way an ATS reads it, and saved as a PDF.</p>
    </div>`;
  const steps = [["01", "Paste", "A careers-page link or the full description"], ["02", "Tailor", "Rewritten from your profile only, never invented"], ["03", "Send", "PDF, cover letter and a tracked application"]];
  $("#emptyState").innerHTML = `
    <div class="steps">${steps.map(([n, t, s]) => `<div class="card steps__item"><span class="steps__n">${n}</span><span class="type-h3">${t}</span><span class="type-meta">${s}</span></div>`).join("")}</div>
    ${rows.length ? `<section>
      <div class="recent__head"><span class="eyebrow">Recent</span><button class="btn btn--link" data-goto="pipeline">All ${rows.length} in Pipeline</button></div>
      <div class="list">${rows.slice(0, 5).map((r) => `
        <div class="item item--link" data-open-app="${esc(r.id)}">${logoTile(r.id, r.logo, r.company)}
          <span class="item__body"><span class="item__title">${esc(r.role)}</span><span class="item__meta">${esc(r.company)} · ${esc(r.status)} · ${esc(r.created.slice(0, 10))}</span></span>
          <span class="item__aside">${scoreNum(r.score_after)}</span></div>`).join("")}</div></section>` : ""}`;
  $$("[data-open-app]").forEach((el) => (el.onclick = () => openApp(el.dataset.openApp)));
  $$("[data-goto]").forEach((el) => (el.onclick = () => showView(el.dataset.goto)));
}

// ================= preview stage =================
function wireStage() {
  $("#modeSeg").onclick = (e) => { const b = e.target.closest("button"); if (b) setMode(b.dataset.v); };
  $("#editToggle").onclick = () => ($("#editor").hidden ? openEditor() : closeEditor());
  $("#edClose").onclick = closeEditor;
  $("#edSave").onclick = saveSections;
  $("#edDiscard").onclick = () => openEditor(true);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && !$("#editor").hidden) { e.preventDefault(); saveSections(); }
  });
  $("#markApplied").onclick = async () => {
    if (!S.app) return;
    await api(`/api/apps/${S.app.id}/track`, { body: { status: "Applied" } });
    $("#markApplied").textContent = "Applied"; $("#markApplied").disabled = true;
    toast(`Marked applied: ${S.app.company}`);
  };
  $("#designChip").onclick = () => toggleMenu("#designMenu");
  $("#lengthChip").onclick = () => toggleMenu("#lengthMenu");
}

function closeMenus() { $$(".menu").forEach((m) => FX.hide(m, "menu")); }
function toggleMenu(sel) { const m = $(sel); const open = FX.isOpen(m); closeMenus(); if (!open) FX.show(m, "menu"); }

function currentVariant() { return S.app ? S.app.options.variant : D().variant; }
function currentPages() { return S.app ? String(S.app.options.max_pages) : String(D().max_pages); }

function renderChips() {
  const v = currentVariant(), p = currentPages();
  $("#designChip").textContent = S.state.layouts[v].label;
  $("#lengthChip").textContent = p === "1" ? "1 page" : p === "auto" ? "2 pages" : pages(+p);
  $("#designMenu").innerHTML = Object.entries(S.state.layouts).map(([k, l]) => `<button data-v="${k}" class="${k === v ? "on" : ""}">${esc(l.label)}<small>${esc(l.blurb)}</small></button>`).join("");
  $("#lengthMenu").innerHTML = [["1", "1 page", "Tighter bullets, compact spacing"], ["2", "2 pages", "The norm for 7+ years everywhere"]]
    .map(([k, t, s]) => `<button data-v="${k}" class="${(p === "auto" ? "2" : p) === k ? "on" : ""}">${t}<small>${s}</small></button>`).join("");
  $("#designMenu").onclick = (e) => { const b = e.target.closest("button"); if (b) chooseDesign({ variant: b.dataset.v }); };
  $("#lengthMenu").onclick = (e) => { const b = e.target.closest("button"); if (b) chooseDesign({ max_pages: b.dataset.v }); };
}

async function chooseDesign(change) {
  closeMenus();
  if (S.app) {
    $("#pv").classList.add("loading"); $("#busyMsg").textContent = "Re-rendering";
    try {
      S.app = await api(`/api/apps/${S.app.id}/render`, { body: change });
      renderInsights(); S.mode = "pdf"; showApp(true);
      toast(`${S.state.layouts[S.app.options.variant].label}, ${pages(S.app.pages)}, ${S.app.scores.after}% keyword match`);
    } catch (e) { toast(e.message, 4000); $("#pv").classList.remove("loading"); }
  } else {
    await savePrefs({ defaults: change });
    previewBase();
  }
  renderChips();
}

function setFrame(src) {
  const pv = $("#pv");
  pv.classList.add("loading");
  $("#busyMsg").textContent = "Rendering";
  const f = $("#frame");
  f.onload = () => {
    if (!S.item) pv.classList.remove("loading");
    if (S.mode === "edit") { try { f.contentDocument.designMode = "on"; } catch { /* same origin always */ } }
  };
  f.src = src;
}

function previewBase() {
  S.mode = "pdf"; setSeg("pdf");
  $("#pvTitle").innerHTML = `<span class="tag">Your profile</span><span class="type-meta">Not tailored yet, ${esc(S.state.layouts[D().variant].label)}</span>`;
  ["#download", "#markApplied", "#editToggle"].forEach((s) => ($(s).hidden = true));
  closeEditor();
  const market = D().market === "auto" ? "india" : D().market;
  const q = new URLSearchParams({ variant: D().variant, max_pages: D().max_pages, market, t: Date.now() });
  renderChips();
  setFrame(`/api/preview.pdf?${q}`);
}

function setSeg(m) { $$("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.v === m)); }

function showApp(flash) {
  const a = S.app;
  const when = new Date(a.created).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  $("#pvTitle").innerHTML = `<span class="tag tag--ok">Tailored ${esc(when)}</span><span class="type-meta">${esc(a.company)}, ${esc(a.role)}${a.edited ? ", edited by hand" : ""}</span>`;
  $("#download").hidden = false;
  $("#download").href = `/apps/${a.id}/pdf?download=1`;
  $("#download").title = a.pdf;
  const applied = a.status && a.status !== "Generated";
  $("#markApplied").hidden = false;
  $("#editToggle").hidden = false;
  $("#markApplied").textContent = applied ? "Applied" : "Mark applied";
  $("#markApplied").disabled = !!applied;
  renderChips();
  setMode(S.mode);
  if (flash) { const pv = $("#pv"); pv.classList.remove("done"); void pv.offsetWidth; pv.classList.add("done"); }
}

function setMode(m) {
  if (!S.app && m !== "pdf") { toast("Tailor a CV first. Then you can see its changes and edit it here."); setSeg("pdf"); return; }
  S.mode = m; setSeg(m);
  if (!S.app) return previewBase();
  const src = { changes: "changes.html", pdf: "pdf" }[m] || "pdf";
  setFrame(`/apps/${S.app.id}/${src}?t=${Date.now()}`);
}

// ================= section editor (beside the PDF) =================
let ED = null;          // the sections as loaded/edited
let edDirty = false;

function grow(t) { t.style.height = "auto"; t.style.height = t.scrollHeight + 2 + "px"; }
function markDirty(on = true) {
  edDirty = on;
  const s = $("#edState");
  s.textContent = on ? "Unsaved changes" : "Saved";
  s.classList.toggle("dirty", on);
}

async function openEditor(reload) {
  if (!S.app) return;
  if (reload && edDirty && !confirm("Discard your unsaved changes?")) return;
  $("#editor").hidden = false;
  $(".studio").classList.add("editing");
  $("#editToggle").textContent = "Close editor";
  if (!ED || reload || ED.appId !== S.app.id) {
    $("#edBody").innerHTML = `<p class="type-meta" style="padding:16px 0">Loading the sections…</p>`;
    ED = { ...(await api(`/api/apps/${S.app.id}/plan`)), appId: S.app.id };
    markDirty(false); $("#edState").textContent = "";
  }
  renderEditor();
}

function closeEditor() {
  if (edDirty && !confirm("Close the editor and discard your unsaved changes?")) return;
  $("#editor").hidden = true;
  $(".studio").classList.remove("editing");
  $("#editToggle").textContent = "Edit";
  if (edDirty) { ED = null; markDirty(false); }
}

function renderEditor() {
  const ta = (v, attrs) => `<textarea rows="1" ${attrs}>${esc(v)}</textarea>`;
  const row = (v, attrs, tools) => `<div class="editor__row">${ta(v, attrs)}<span class="editor__tools">${tools}</span></div>`;
  const tool = (attr, label, icon) => `<button class="icon-btn icon-btn--sm" ${attr} title="${label}" aria-label="${label}">${icon}</button>`;
  const sec = (open, summary, body) => `<details class="disclosure" ${open ? "open" : ""}><summary>${summary}</summary><div class="disclosure__body">${body}</div></details>`;
  $("#edBody").innerHTML = [
    sec(true, "Headline", `<input type="text" data-f="headline" value="${esc(ED.headline)}">`),
    sec(true, "Professional summary", ta(ED.summary, 'data-f="summary"')),
    sec(false, `Skills<span class="disclosure__aside">${ED.skills.length} groups</span>`,
      ED.skills.map((g, i) => `<span class="eyebrow" style="margin-top:6px">Group ${i + 1}</span>
        ${row(g.label, `data-g="${i}" data-k="label"`, tool(`data-delg="${i}"`, "Remove this group", ICON.x))}
        ${ta(g.items.join(", "), `data-g="${i}" data-k="items"`)}`).join("") + `<button class="btn btn--sm btn--dashed" data-addg>Add a skills group</button>`),
    ...ED.roles.map((r, ri) => sec(ri < 2, `<span>${esc(r.title)}</span><span class="disclosure__sub">${esc(r.company)}, ${esc(r.dates)}</span>`,
      r.bullets.map((b, bi) => row(b, `data-r="${ri}" data-b="${bi}"`,
        `${bi ? tool(`data-up="${ri}:${bi}"`, "Move up", ICON.up) : ""}${tool(`data-del="${ri}:${bi}"`, "Remove this bullet", ICON.x)}`)).join("")
      + `<button class="btn btn--sm btn--dashed" data-addb="${ri}">Add a bullet</button>`)),
    sec(false, `Cover letter<span class="disclosure__aside">${ED.cover_letter ? `${ED.cover_letter.split(/\s+/).length} words` : "none yet"}</span>`,
      ta(ED.cover_letter || "", 'data-f="cover_letter" style="min-height:160px"')),
  ].join("");
  const body = $("#edBody");
  $$("textarea", body).forEach(grow);
  $$("details", body).forEach((d) => d.addEventListener("toggle", () => $$("textarea", d).forEach(grow)));
  body.oninput = (e) => {
    const t = e.target;
    if (t.tagName === "TEXTAREA") grow(t);
    if (t.dataset.f) ED[t.dataset.f] = t.value;
    else if (t.dataset.g !== undefined) {
      const g = ED.skills[+t.dataset.g];
      if (t.dataset.k === "label") g.label = t.value; else g.items = t.value.split(",").map((s) => s.trim()).filter(Boolean);
    } else if (t.dataset.r !== undefined) ED.roles[+t.dataset.r].bullets[+t.dataset.b] = t.value;
    markDirty();
  };
  body.onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.del) { const [r, i] = b.dataset.del.split(":").map(Number); ED.roles[r].bullets.splice(i, 1); }
    else if (b.dataset.up) { const [r, i] = b.dataset.up.split(":").map(Number); const l = ED.roles[r].bullets; [l[i - 1], l[i]] = [l[i], l[i - 1]]; }
    else if (b.dataset.addb !== undefined) ED.roles[+b.dataset.addb].bullets.push("");
    else if (b.dataset.delg !== undefined) ED.skills.splice(+b.dataset.delg, 1);
    else if (b.dataset.addg !== undefined) ED.skills.push({ label: "New group", items: [] });
    else return;
    const open = $$("details", body).map((d) => d.open);
    renderEditor();
    $$("details", $("#edBody")).forEach((d, i) => (d.open = open[i] ?? d.open));
    markDirty();
  };
}

async function saveSections() {
  if (!S.app || !ED) return;
  $("#pv").classList.add("loading"); $("#busyMsg").textContent = "Updating the PDF and re-checking it";
  $("#edSave").disabled = true;
  try {
    S.app = await api(`/api/apps/${S.app.id}/plan`, { method: "PUT",
      body: { headline: ED.headline, summary: ED.summary, skills: ED.skills, roles: ED.roles.map((r) => ({ id: r.id, bullets: r.bullets })), cover_letter: ED.cover_letter } });
    markDirty(false);
    renderInsights(); S.mode = "pdf"; showApp(true);
    const errs = [...(S.app.plan_flags || []), ...(S.app.analysis_flags || [])].filter((f) => f.level === "error").length;
    toast(errs ? `PDF updated. ${errs} check${errs > 1 ? "s" : ""} flagged on the left; have a look.` : `PDF updated: ${pages(S.app.pages)}, ${S.app.scores.after}% keyword match`, 4000);
  } catch (e) { toast(e.message, 5000); $("#pv").classList.remove("loading"); }
  finally { $("#edSave").disabled = false; }
}

async function openApp(id) {
  if (edDirty && !confirm("You have unsaved edits. Discard them and open this CV?")) return;
  ED = null; edDirty = false; $("#editor").hidden = true; $(".studio").classList.remove("editing"); $("#editToggle").textContent = "Edit";
  const a = await api(`/api/apps/${id}`);
  S.app = a; S.item = null;
  S.job = { role: a.role, company: a.company, location: a.job?.location || "", job_id: a.job?.job_id || "", posted: a.job?.posted || "",
    url: a.job_url || a.job?.job_url || "", description: a.jd_text || "", method: "", market: a.market };
  const row = await api("/api/apps").then((rows) => rows.find((r) => r.id === id)).catch(() => null);
  S.app.logo = !!row?.logo;
  S.app.status = row?.status || "";
  $("#emptyHero").innerHTML = ""; $("#emptyState").innerHTML = "";
  $("#side").classList.add("has-job");
  renderJobCard(); renderInsights(); S.mode = "pdf"; showApp(); showView("tailor");
}

// ================= report: score + tabs =================
const flagsHtml = (list) => (list || []).map((f) => `<div class="notice ${f.level === "error" ? "notice--bad" : ""}">${esc(f.text)}</div>`).join("");

function setTab(name, animate = true) {
  const btns = $$("#reportTabs [data-tab]");
  if (!btns.some((b) => b.dataset.tab === name)) name = "changes";
  S.tab = name; store.set("cv.tab", name);
  btns.forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  $$("#insights [data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== name));
  if (animate) FX.rise($(`#insights [data-panel=${name}]`).children, { y: 10 });
}

function renderInsights() {
  const a = S.app;
  if (!a) { $("#insights").innerHTML = ""; return; }
  const flags = [...(a.plan_flags || []), ...(a.analysis_flags || [])];
  const req = a.keywords.filter((k) => k.tier === "required"), other = a.keywords.filter((k) => k.tier !== "required");
  const chip = (k) => `<span class="chip ${k.covered ? "chip--ok" : k.tier === "required" ? "chip--bad" : ""} ${k.in_profile ? "" : "chip--muted"}"
    title="${k.covered ? "In this CV" : k.in_profile ? "Your profile has it, this CV doesn't say it" : "Not in your profile"}"><span>${esc(k.term)}</span></span>`;
  const passed = a.ats.filter((c) => c.ok).length;
  const covered = a.keywords.filter((k) => k.covered).length;
  const gaps = a.gaps || [];
  const s = a.scores;
  const verdict = s.ceiling < 60
    ? `This CV covers <b>${s.after}%</b> of the job's keywords, and ${s.ceiling}% is the most your profile can truthfully reach. The job asks for a lot your profile doesn't show; check the gaps before you spend time on it.`
    : s.after >= s.ceiling - 2
      ? `This CV covers <b>${s.after}%</b> of the job's keywords, the most your profile can truthfully reach. Your current CV covers ${s.before}%.`
      : `This CV covers <b>${s.after}%</b> of the job's keywords; your profile could reach ${s.ceiling}%. Your current CV covers ${s.before}%.`;
  const notes = (a.fit_notes || []).length || a.recruiter_note;
  const tabs = [["changes", "Changes", ""], ["keywords", "Keywords", `${covered}/${a.keywords.length}`], ["letter", "Letter", `<span id="letterCount"></span>`],
    ...(notes ? [["notes", "Notes", ""]] : []), ["ats", "ATS", `${passed}/${a.ats.length}`]];
  $("#insights").innerHTML = `
    <section class="score ${tone(s.after)}">
      <div class="score__top">
        <div class="stat stat--xl"><span class="eyebrow">Keyword match</span><span class="stat__value">${s.after}<small>%</small></span></div>
        <div class="score__stats">
          <div class="stat"><span class="eyebrow">Before</span><span class="stat__value">${s.before}<small>%</small></span></div>
          <div class="stat"><span class="eyebrow">Ceiling</span><span class="stat__value">${s.ceiling}<small>%</small></span></div>
        </div>
      </div>
      <div class="score__meter" title="Before ${s.before}%, now ${s.after}%, ceiling ${s.ceiling}%">
        <div class="meter meter--segmented"><span class="meter__ceil" style="width:${s.ceiling}%"></span><span class="meter__fill" style="width:${s.after}%"></span></div>
        <span class="meter__mark" style="left:${s.before}%"></span>
        <div class="meter-legend"><span class="type-meta">${esc(a.headline)}</span><span class="type-meta num">${pages(a.pages)}, ${a.experience_years} years</span></div>
      </div>
      <p class="score__verdict">${verdict}</p>
      ${flags.length ? `<div>${flagsHtml(flags)}</div>` : ""}
    </section>
    <section class="report">
      <div class="tabs" id="reportTabs" role="tablist">${tabs.map(([k, l, n]) => `<button data-tab="${k}" role="tab">${l}${n ? ` <span class="tabs__count num">${n}</span>` : ""}</button>`).join("")}</div>
      <div class="report__panel" data-panel="changes" id="changes"><p class="type-meta">Comparing with your profile…</p></div>
      <div class="report__panel" data-panel="keywords">
        <p class="type-meta">Green: in this CV. Red: required and missing. Struck through: not in your profile.</p>
        ${req.length ? `<div><div class="eyebrow" style="margin-bottom:10px">Required</div><div class="chip-row">${req.map(chip).join("")}</div></div>` : ""}
        ${other.length ? `<div><div class="eyebrow" style="margin-bottom:10px">Also scored</div><div class="chip-row">${other.slice(0, 40).map(chip).join("")}</div></div>` : ""}
        ${(a.jd_terms || []).length ? `<div><div class="eyebrow" style="margin-bottom:10px">Also asked for, read by the writer but not scored</div><div class="chip-row">${a.jd_terms.map(chip).join("")}</div></div>` : ""}
        ${gaps.length ? `<div id="gaps"><div class="eyebrow">Gaps: tick only the ones that are true</div>
          <div style="margin-top:6px">${gaps.map((g) => `<label class="gap"><input type="checkbox" data-term="${esc(g.term)}"><span class="gap__term">${esc(g.term)}${g.must_have ? '<span class="tag tag--bad">Required</span>' : ""}</span><span class="type-meta">${esc(g.note)}</span></label>`).join("")}</div>
          <button class="btn btn--primary" id="confirmGaps" style="margin-top:12px">Add the ticked ones and re-tailor</button></div>` : ""}
      </div>
      <div class="report__panel" data-panel="letter" id="letters"></div>
      ${notes ? `<div class="report__panel" data-panel="notes">
        ${(a.fit_notes || []).length ? `<div><div class="eyebrow" style="margin-bottom:10px">What a recruiter will notice</div><ul class="bullets">${a.fit_notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>` : ""}
        ${a.recruiter_note ? `<div><div class="report__row" style="margin-bottom:10px"><span class="eyebrow">Note to the recruiter <span class="tabs__count num">${a.recruiter_note.length}/300</span></span><button class="btn btn--sm" id="copyNote">Copy</button></div>
          <div class="textbox">${esc(a.recruiter_note)}</div></div>` : ""}
      </div>` : ""}
      <div class="report__panel" data-panel="ats">
        <ul class="checklist">${a.ats.map((c) => `<li><span class="dot ${c.ok ? "dot--ok" : c.level === "warn" ? "dot--warn" : "dot--bad"}"></span>${esc(c.label)}<span class="checklist__detail" title="${esc(c.detail)}">${esc(c.detail)}</span></li>`).join("")}</ul>
        ${(a.trims || []).length ? `<p class="type-meta">Fitted to ${pages(a.options.max_pages)}: ${a.trims.map(esc).join("; ")}.</p>` : ""}
      </div>
    </section>`;
  $("#reportTabs").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) setTab(b.dataset.tab); };
  setTab(S.tab, false);
  const cn = $("#copyNote"); if (cn) cn.onclick = () => copy(a.recruiter_note);
  const cg = $("#confirmGaps"); if (cg) cg.onclick = confirmGaps;
  renderLetters();
  loadChanges(a.id);
}

async function loadChanges(id) {
  let c;
  try { c = await api(`/api/apps/${id}/changes`); } catch { $("#changes").innerHTML = `<p class="type-meta">Couldn't compare this CV with your profile.</p>`; return; }
  if (!S.app || S.app.id !== id) return;
  const n = c.counts;
  const tagFor = { rewritten: "tag--warn", merged: "tag--merge" };
  const diffRow = (b) => `<div class="diff"><span class="tag ${tagFor[b.status] || ""}">${b.status === "kept" ? "kept" : esc(b.status)}</span>${esc(b.after.replace(/\*\*/g, ""))}
    ${b.status !== "kept" ? b.before.map((x) => `<div class="diff__was">${esc(x)}</div>`).join("") : ""}</div>`;
  $("#changes").innerHTML = `
    <div class="report__row"><span class="eyebrow">At a glance</span><button class="btn btn--sm" id="hl">Highlight in preview</button></div>
    <dl class="kv">
      <dt class="eyebrow">Headline</dt><dd>${c.headline.before !== c.headline.after ? `${esc(c.headline.before)} became <b>${esc(c.headline.after)}</b>` : `${esc(c.headline.after)}, unchanged`}</dd>
      <dt class="eyebrow">Summary</dt><dd>Rewritten for this job</dd>
      <dt class="eyebrow">Bullets</dt><dd class="chip-row"><span class="tag tag--warn num">${n.rewritten} rewritten</span><span class="tag tag--merge num">${n.merged} merged</span><span class="tag num">${n.kept} kept</span><span class="tag num">${n.left_out} left out</span></dd>
      <dt class="eyebrow">Skills</dt><dd>${c.skills.after.length} groups, led by ${esc(c.skills.after[0]?.label || "")}</dd>
      ${c.omitted_roles.length ? `<dt class="eyebrow">Roles</dt><dd>Left out: ${esc(c.omitted_roles.join(", "))}</dd>` : ""}
    </dl>
    <div class="list roles">${c.roles.map((r) => `<details class="disclosure"><summary><span>${esc(r.title)}</span><span class="disclosure__sub">${esc(r.company)}</span><span class="disclosure__aside num">${r.bullets.length}</span></summary>
      <div class="disclosure__body">${r.bullets.map(diffRow).join("")}
      ${r.left_out.length ? `<div class="diff"><span class="tag">left out for this job</span>${r.left_out.map((x) => `<div class="diff__was">${esc(x)}</div>`).join("")}</div>` : ""}</div></details>`).join("")}</div>`;
  $("#hl").onclick = () => setMode("changes");
}

async function confirmGaps() {
  const terms = $$("#gaps input:checked").map((i) => i.dataset.term);
  if (!terms.length) { toast("Tick the skills you genuinely have first."); return; }
  if (!confirm(`Add these to your profile as true skills?\n\n${terms.join(", ")}\n\nEvery future CV may use them, and interviewers may ask about them.`)) return;
  await api("/api/profile", { body: { confirm: terms } });
  const replace = S.app.id;
  S.app = null;
  toast("Added. Re-tailoring this job.");
  tailorJob({ replace_id: replace });
}

// cover letters: drafts per application live in this browser
const lettersKey = (id) => `cv.letters.${id}`;
function renderLetters() {
  const a = S.app, box = $("#letters");
  if (!a || !box) return;
  const drafts = store.get(lettersKey(a.id), []);
  const all = [...drafts.slice().reverse(), ...(a.cover_letter ? [{ id: "saved", voice: "saved", label: a.cover_voice ? "Kept letter" : "First draft", text: a.cover_letter, kept: true, flags: [] }] : [])];
  const count = $("#letterCount"); if (count) count.textContent = all.length || "";
  box.innerHTML = `
    <div><div class="eyebrow" style="margin-bottom:10px">Try another voice</div>
      <div class="chip-row">${Object.entries(S.state.voices).map(([k, v]) => `<button class="chip" data-voice="${k}" title="${esc(v.best_for)}">${esc(v.label)}</button>`).join("")}</div>
      <p class="type-meta" id="voiceBusy" hidden style="margin-top:10px"></p></div>
    ${all.map((d, i) => `<div class="letter" data-i="${i}"><div class="letter__head"><b>${esc(d.label)}</b>${d.words ? `<span class="tag num">${d.words} words</span>` : ""}${d.kept ? `<span class="tag tag--ok">Kept</span>` : ""}<span class="sp"></span>
      <button class="btn btn--sm btn--ghost" data-act="copy">Copy</button>${d.voice !== "saved" ? `<button class="btn btn--sm ${d.kept ? "" : "btn--ghost"}" data-act="keep">${d.kept ? "Kept" : "Keep"}</button><button class="btn btn--sm btn--ghost" data-act="skip">Skip</button>` : ""}</div>
      <div class="textbox ${d.kept ? "textbox--kept" : ""}">${esc(d.text)}</div>${flagsHtml(d.flags)}</div>`).join("") || `<p class="type-meta">No letter yet. Pick a voice above.</p>`}`;
  $$("[data-voice]", box).forEach((b) => (b.onclick = () => writeVoice(b.dataset.voice, b)));
  $$(".letter", box).forEach((el) => {
    const d = all[+el.dataset.i];
    $$("[data-act]", el).forEach((b) => (b.onclick = async () => {
      if (b.dataset.act === "copy") return copy(d.text);
      const list = store.get(lettersKey(a.id), []);
      if (b.dataset.act === "skip") { store.set(lettersKey(a.id), list.filter((x) => x.id !== d.id)); return renderLetters(); }
      list.forEach((x) => (x.kept = x.id === d.id));
      store.set(lettersKey(a.id), list);
      await api(`/api/apps/${a.id}/letter/keep`, { body: { text: d.text, voice: d.voice } });
      S.app.cover_letter = d.text; S.app.cover_voice = d.voice;
      toast(`Kept the ${d.label} letter. It's saved next to the PDF.`);
      renderLetters();
    }));
  });
}

async function writeVoice(voice, btn) {
  const a = S.app, busy = $("#voiceBusy");
  busy.hidden = false;
  busy.textContent = `Writing the ${S.state.voices[voice].label} version, about half a minute`;
  btn.disabled = true;
  try {
    const { job } = await api(`/api/apps/${a.id}/letter`, { body: { voice, engine: D().engine } });
    const res = await pollJob(job, () => {});
    const list = store.get(lettersKey(a.id), []);
    list.push({ ...res, id: `${voice}-${Date.now()}`, kept: false });
    store.set(lettersKey(a.id), list);
    if (S.app && S.app.id === a.id) { renderLetters(); setTab("letter"); }
  } catch (e) { busy.textContent = e.message; return; }
  finally { btn.disabled = false; }
  busy.hidden = true;
}

// ================= batch polling (Studio progress, Pipeline, live badge) =================
async function pollBatch(once) {
  try {
    S.batch = await api("/api/batch");
    const live = S.batch.filter((b) => ["queued", "running"].includes(b.status)).length;
    $("#liveCount").hidden = !live; $("#liveCount").textContent = live;
    if (S.item) { const it = S.batch.find((b) => b.id === S.item); if (it) await onStudioItem(it); }
    if ($("#v-pipeline").classList.contains("on")) renderPipeline();
  } catch { /* server restarting; try again */ }
  if (once) return;
  const live = S.batch.some((b) => ["queued", "running"].includes(b.status));
  setTimeout(() => pollBatch(), live || S.item ? 1500 : 6000);
}

// ================= discover =================
function wireDiscover() {
  $("#days").value = String(S.prefs.days || 14);
  $("#minMatch").value = String(store.get("cv.minMatch", 40));
  $("#sortBy").value = store.get("cv.sortBy", "match");
  $("#days").onchange = async () => { await savePrefs({ days: +$("#days").value }); loadFound(); };
  $("#minMatch").onchange = () => { store.set("cv.minMatch", +$("#minMatch").value); loadFound(); };
  $("#sortBy").onchange = () => { store.set("cv.sortBy", $("#sortBy").value); loadFound(); };
  $("#scanAll").onclick = () => scanNow(false);
  $("#scanFav").onclick = () => scanNow(true);
  $("#dockClear").onclick = () => { S.selected.clear(); loadFound(); };
  $("#dockGo").onclick = tailorSelected;
}

async function scanNow(favourites) {
  const line = $("#scanLine"), btns = [$("#scanAll"), $("#scanFav")];
  line.hidden = false;
  line.innerHTML = `<div class="progress"></div><div class="progress__text">${favourites ? "Scanning your favourite companies" : "Scanning your sources, favourites first"}</div>`;
  btns.forEach((b) => (b.disabled = true));
  try {
    const { job } = await api("/api/scan", { body: { days: +$("#days").value, favourites: !!favourites } });
    const res = await pollJob(job, (l) => ($(".progress__text", line).textContent = l.msg));
    await loadFound();
    toast(`${res.jobs.length} fresh jobs found`);
  } catch (e) { toast(e.message, 4000); }
  finally { btns.forEach((b) => (b.disabled = false)); line.hidden = true; }
}

// Area filter: a per-browser convenience, so it lives in local storage.
const REGIONS = [["remote", "Remote"], ["bengaluru", "Bengaluru"], ["india", "Rest of India"],
  ["world", "Rest of the world"]];
function renderAreaBar() {
  const sel = new Set(store.get("cv.areas", []));
  const strict = store.get("cv.areasStrict", false);
  const regions = new Set(S.prefs.regions || REGIONS.map((r) => r[0]));
  $("#areaBar").innerHTML = `<div class="filter-group"><span class="eyebrow">Region</span><div class="chip-row">
    ${REGIONS.map(([k, label]) => `<button data-reg="${k}" class="chip ${regions.has(k) ? "on" : ""}" ${k === "world" ? 'title="UK, EU, the Gulf and Singapore: your visa targets. US roles stay out."' : ""}>${label}</button>`).join("")}</div></div>
    ${regions.has("bengaluru") ? `<div class="filter-group"><span class="eyebrow">Bengaluru area</span><div class="chip-row">
      ${(S.state.areas.Bengaluru || []).map((z) => `<button data-z="${esc(z)}" class="chip ${sel.has(z) ? "on" : ""}">${esc(z)}</button>`).join("")}</div>
      ${sel.size ? `<label class="check"><input type="checkbox" id="areaStrict" ${strict ? "checked" : ""}> Hide Bengaluru jobs that don't say where</label>` : ""}</div>` : ""}`;
  $$("#areaBar [data-reg]").forEach((b) => (b.onclick = async () => {
    regions.has(b.dataset.reg) ? regions.delete(b.dataset.reg) : regions.add(b.dataset.reg);
    if (!regions.size) { toast("Keep at least one region."); return; }
    await savePrefs({ regions: [...regions] });
    renderAreaBar(); loadFound();
  }));
  $$("#areaBar [data-z]").forEach((b) => (b.onclick = () => {
    sel.has(b.dataset.z) ? sel.delete(b.dataset.z) : sel.add(b.dataset.z);
    store.set("cv.areas", [...sel]); renderAreaBar(); loadFound();
  }));
  const st = $("#areaStrict");
  if (st) st.onchange = () => { store.set("cv.areasStrict", st.checked); loadFound(); };
}

function areaFilter(jobs) {
  const sel = new Set(store.get("cv.areas", []));
  if (!sel.size || !(S.prefs.regions || []).includes("bengaluru")) return jobs;
  const strict = store.get("cv.areasStrict", false);
  const blr = (j) => /bengaluru|bangalore/i.test(j.location || "");
  // the area filter narrows Bengaluru jobs only; remote and other regions pass through
  return jobs.filter((j) => !blr(j) || (j.areas || []).some((a) => sel.has(a)) || (!strict && !(j.areas || []).length));
}

let FOUND = [];
async function loadFound() {
  if (!$("#areaBar").innerHTML) renderAreaBar();
  // Favourites look back over the longest window the app keeps (30 days), whatever Posted within says.
  const [all, sources, scanLog, month] = await Promise.all([api(`/api/found?days=${$("#days").value}`), api("/api/sources"), api("/api/scans"), api("/api/found?days=30")]);
  const lastScan = scanLog.length ? scanLog[scanLog.length - 1] : null;
  const min = +$("#minMatch").value;
  // Favourite companies: their jobs ignore the match slider and sit pinned at the top.
  const favs = sources.filter((s) => s.favourite);
  const favNames = favs.map((s) => (s.company || s.label || "").toLowerCase()).filter(Boolean);
  const isFav = (j) => !!j.favourite || favNames.some((n) => (j.company || "").toLowerCase().includes(n));
  $("#scanFav").hidden = !favs.length;
  const jobs = areaFilter(all.filter((j) => j.match >= min && !isFav(j)));
  const pinned = areaFilter(month.filter(isFav));
  if ($("#sortBy").value === "newest") jobs.sort((a, b) => (b.posted || "").localeCompare(a.posted || "") || b.match - a.match);
  FOUND = jobs;
  const last = sources.map((s) => s.last_scan).filter(Boolean).sort().pop();
  $("#discoverLede").textContent = sources.length
    ? `${jobs.length} fresh posting${jobs.length === 1 ? "" : "s"} from ${sources.length} sources. Match is the share of each job's keywords your profile can truthfully cover, the same number tailoring reaches.${last ? ` Last scanned ${last.slice(11, 16)}.` : ""}${all.length > jobs.length ? ` ${all.length - jobs.length} more below ${min}%.` : ""}`
    : "Add a careers page or job board in Settings, then scan.";
  const isNew = (j) => lastScan && (j.first_seen || "") >= lastScan.at.slice(0, 16) && scanLog.length > 1;
  const card = (j) => {
    const d = daysAgo(j.posted);
    const where = [j.company || j.source, j.location, j.remote ? "remote" : ""].filter(Boolean).map(esc).join(" · ");
    return `<div class="card card--interactive job-card ${S.selected.has(j.id) ? "sel" : ""}" data-id="${j.id}">
      <div class="job-card__top">
        <input type="checkbox" ${S.selected.has(j.id) ? "checked" : ""} aria-label="Select ${esc(j.title)}">
        <span class="logo logo--sm">${initial(j.company || j.source)}</span>
        <span class="tag ${d === null ? "tag--warn" : d <= 3 ? "tag--ok" : ""}">${d === null ? "Date unknown" : ago(j.posted)}</span>
        ${isNew(j) ? `<span class="tag tag--accent tag--solid">New</span>` : ""}
        ${isFav(j) ? `<span class="tag tag--accent" title="One of your favourite companies">${ICON.star.replace("<svg", "<svg style=\"width:9px;height:9px\"")}Favourite</span>` : ""}
        <a class="icon-btn icon-btn--sm" href="${esc(j.url)}" target="_blank" rel="noopener" title="Open the posting" aria-label="Open the posting">${ICON.out}</a>
      </div>
      <div><div class="job-card__title">${esc(j.title)}</div>
        <div class="job-card__where">${where}${(j.areas || []).length ? ` · <span class="zone">${esc(j.areas.join(" or "))}</span>` : ""}</div></div>
      <div class="job-card__match ${tone(j.match)}" title="Keyword match your profile can reach for this job (the same measure as after tailoring)">
        <span class="eyebrow">Match</span><span class="meter meter--sm"><span class="meter__fill" style="width:${j.match}%"></span></span><span class="num">${j.match}%</span></div>
      <div class="job-card__why" title="${esc((j.reasons || []).join(", "))}">${esc((j.reasons || []).join(", "))}, via ${esc(j.source)}</div>
    </div>`;
  };
  // Sections by the day the app first found each job, newest first; each says which scan it came from.
  const rest = jobs;
  const favHtml = favs.length ? `<details class="disclosure disclosure--lg day day--fav" open><summary><span class="day__star">${ICON.star}</span><span>Your favourite companies</span>
      <span class="disclosure__sub num">${pinned.length} job${pinned.length === 1 ? "" : "s"} · last 30 days</span><span class="disclosure__aside">${favs.length} compan${favs.length === 1 ? "y" : "ies"}</span></summary>
      ${pinned.length ? `<div class="card-grid">${pinned.map(card).join("")}</div>`
        : `<p class="type-meta" style="margin-bottom:16px">Nothing open from ${esc(favs.map((f) => f.company || f.label).join(", "))} in the last 30 days that fits your roles and regions. Press Scan favourites to check now.</p>`}</details>` : "";
  const groups = {};
  for (const j of rest) (groups[(j.first_seen || "").slice(0, 10) || "earlier"] ||= []).push(j);
  const dayLabel = (d) => {
    if (d === "earlier") return "Found earlier";
    const today = new Date().toISOString().slice(0, 10), y = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    const nice = new Date(d + "T00:00:00").toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
    return d === today ? `Found today, ${nice}` : d === y ? `Found yesterday, ${nice}` : `Found ${nice}`;
  };
  const scansOn = (d) => scanLog.filter((s) => s.at.slice(0, 10) === d);
  const days = Object.keys(groups).sort().reverse();
  $("#foundList").innerHTML = favHtml + (rest.length
    ? days.map((d, i) => {
        const sc = scansOn(d);
        const meta = sc.length ? `${sc.length} scan${sc.length > 1 ? "s" : ""}, last at ${sc[sc.length - 1].at.slice(11, 16)}, ${sc.reduce((n, s) => n + s.new, 0)} new` : "";
        const list = groups[d];
        const dated = list.filter((j) => j.posted), unknown = list.filter((j) => !j.posted);
        return `<details class="disclosure disclosure--lg day" ${i === 0 && !pinned.length ? "open" : ""}><summary><span>${dayLabel(d)}</span><span class="disclosure__sub num">${list.length} job${list.length === 1 ? "" : "s"}</span><span class="disclosure__aside">${meta}</span></summary>
          ${dated.length ? `<div class="card-grid">${dated.map(card).join("")}</div>` : ""}
          ${unknown.length ? `<div class="eyebrow group-title">Posting date unknown. Check the posting before you spend time on it.</div><div class="card-grid">${unknown.map(card).join("")}</div>` : ""}</details>`;
      }).join("")
    : pinned.length ? "" : `<div class="empty">${sources.length ? "Nothing fresh at this match level. Widen the window or lower the match, or scan again." : "No sources yet. Open <b>Settings</b> and add a favourite company or a careers page, like https://jobs.lever.co/cred."}</div>`);
  $$(".job-card").forEach((el) => {
    el.onclick = (e) => {
      if (e.target.closest("a")) return;
      const id = el.dataset.id;
      S.selected.has(id) ? S.selected.delete(id) : S.selected.add(id);
      el.classList.toggle("sel", S.selected.has(id));
      $("input", el).checked = S.selected.has(id);
      renderDock();
    };
  });
  renderDock();
}

function renderDock() {
  const n = S.selected.size;
  FX.toggle($("#dock"), !!n && $("#v-discover").classList.contains("on"), "dock");
  $("#dockText").textContent = `${n} selected`;
  $("#dockGo").textContent = `Tailor ${n} job${n === 1 ? "" : "s"}`;
}

async function tailorSelected() {
  const items = [...S.selected].map((id) => ({ found_id: id }));
  if (!items.length) return;
  await api("/api/batch", { body: { items } });
  S.selected.clear();
  toast(`Tailoring ${items.length} job${items.length === 1 ? "" : "s"}, ${S.prefs.concurrency || 2} at a time. Follow them in Pipeline.`, 4000);
  loadFound();
  pollBatch(true);
}

// ================= pipeline =================
async function loadPipeline() {
  S.apps = await api("/api/apps");
  S.batch = await api("/api/batch");
  renderPipeline();
}

function renderPipeline() {
  const rows = S.apps || [];
  const inflight = S.batch.filter((b) => b.status !== "done");
  const by = (sts) => rows.filter((r) => sts.includes(r.status));
  const closedSts = ["Rejected", "No response", "Withdrawn"];
  const stages = [
    ["all", "All", [...inflight, ...rows.filter((r) => !closedSts.includes(r.status))], "Nothing here yet. Tailor a CV from the Tailor screen or Discover."],
    ["tailoring", "Tailoring", inflight, "Jobs you send from Tailor or Discover appear here while they're written."],
    ["tailored", "Tailored", by(["Generated"]), "Ready to send. Mark one applied when you do."],
    ["applied", "Applied", by(["Applied"]), "Nothing applied yet."],
    ["interviews", "Interviews", by(["Recruiter call", "Interviewing", "Offer"]), "Calls and interviews land here."],
    ["closed", "Closed", by(closedSts), "Rejected, withdrawn and unanswered applications fold away here."],
  ];
  const cur = stages.find((x) => x[0] === store.get("cv.stage", "all")) || stages[0];
  const applied = rows.filter((r) => r.status !== "Generated").length;
  const writing = inflight.filter((b) => b.status !== "error").length;
  $("#pipeLede").textContent = `${rows.length} CV${rows.length === 1 ? "" : "s"} tailored, ${applied} applied${writing ? `, ${writing} being written now` : ""}.`;
  const navHtml = stages.map(([k, label, list]) => `<button data-stage="${k}" class="${k === cur[0] ? "on" : ""}">
      <span class="side-nav__label">${label}${k === "tailoring" && writing ? `<span class="spinner" style="width:10px;height:10px"></span>` : ""}</span><span class="side-nav__count">${list.length}</span></button>`).join("");
  $("#stageNav").onclick = (e) => {
    const b = e.target.closest("[data-stage]");
    if (b) { store.set("cv.stage", b.dataset.stage); renderPipeline(); FX.rise($("#board").children); }
  };
  const headHtml = `<h2 class="type-h2">${cur[1]}</h2><span class="type-meta num">${cur[2].length} ${cur[2].length === 1 ? "application" : "applications"}</span>`;
  const statusSel = (r) => `<select class="control--sm" data-status="${esc(r.id)}" aria-label="Status">${S.state.statuses.map((s) => `<option ${s === r.status ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>`;
  const appCard = (r) => `<div class="card card--interactive app-card" data-open="${esc(r.id)}">
      <div class="item">${logoTile(r.id, r.logo, r.company)}
        <span class="item__body"><span class="item__title">${esc(r.company)}</span><span class="item__meta" title="${esc(r.role)}">${esc(r.role)}</span></span>
        <span class="item__aside">${scoreNum(r.score_after)}</span></div>
      <div class="app-card__meta"><span class="num">${esc(r.applied_on || r.created.slice(0, 10))}</span><span class="app-card__host" title="${esc(r.job_url || "")}${r.job_id ? `, ID ${esc(r.job_id)}` : ""}">${esc(host(r.job_url || ""))}</span>${statusSel(r)}</div>
      <div class="app-card__acts">
        ${r.job_url ? `<a class="btn btn--sm" href="${esc(r.job_url)}" target="_blank" rel="noopener" data-stop title="${esc(r.job_url)}">Open posting</a>` : `<span class="type-meta">No link saved</span>`}
        <a class="btn btn--sm btn--ghost" href="/apps/${esc(r.id)}/pdf?download=1" data-stop title="${esc(r.pdf)}">PDF</a>
        ${r.status === "Generated" ? `<button class="btn btn--sm btn--ghost" data-applied="${esc(r.id)}" data-stop>Mark applied</button>` : ""}
        <button class="btn btn--sm btn--ghost" data-prep="${esc(r.id)}" data-stop title="Interview questions for this job">Interview</button>
      </div></div>`;
  const flightCard = (b) => `<div class="card app-card ${b.status === "error" ? "err" : ""}" data-flight="${esc(b.id)}">
      <div class="item"><span class="logo logo--sm">${initial(b.company || b.role)}</span><span class="item__body"><span class="item__title">${esc(b.company || "Reading the posting")}</span><span class="item__meta">${esc(b.role || "")}</span></span><span class="tag ${b.status === "error" ? "tag--bad" : "tag--accent"}">${b.status === "error" ? "Failed" : "Writing"}</span></div>
      ${b.status === "error" ? "" : `<div class="progress"></div>`}
      <div class="progress__text" style="margin-top:0">${esc(b.status === "queued" ? "Waiting for a free slot" : b.stage || "Working")}</div>
      ${b.status === "error" ? `<div class="app-card__acts"><button class="btn btn--sm btn--primary" data-retry="${esc(b.id)}">Try again</button><button class="btn btn--sm btn--ghost" data-remove="${esc(b.id)}">Remove</button></div>` : ""}</div>`;
  // queue items (still being written) and tracker rows (finished CVs) share the grid
  const isFlight = (x) => inflight.includes(x);
  const boardHtml = cur[2].length ? cur[2].map((x) => (isFlight(x) ? flightCard(x) : appCard(x))).join("") : `<div class="board__none">${cur[3]}</div>`;
  // Polling re-renders every few seconds; leave the DOM alone when nothing changed (an open status menu stays open).
  const sig = navHtml + headHtml + boardHtml;
  if (sig === renderPipeline.sig) return;
  renderPipeline.sig = sig;
  $("#stageNav").innerHTML = navHtml; $("#stageHead").innerHTML = headHtml; $("#board").innerHTML = boardHtml;
  $$("[data-open]").forEach((el) => (el.onclick = (e) => { if (!e.target.closest("select, [data-stop]")) openApp(el.dataset.open); }));
  $$("[data-prep]").forEach((b) => (b.onclick = () => openInterview(b.dataset.prep)));
  $$("[data-applied]").forEach((b) => (b.onclick = async () => {
    const r = await api(`/api/apps/${b.dataset.applied}/track`, { body: { status: "Applied" } });
    toast(`Applied: ${r.company}`);
    loadPipeline();
  }));
  $$("[data-status]").forEach((sel) => (sel.onchange = async () => {
    const r = await api(`/api/apps/${sel.dataset.status}/track`, { body: { status: sel.value } });
    toast(`${r.company}: ${r.status}`);
    loadPipeline();
  }));
  $$("[data-retry]").forEach((b) => (b.onclick = async () => { await api(`/api/batch/${b.dataset.retry}/retry`, { body: {} }); loadPipeline(); pollBatch(true); }));
  $$("[data-remove]").forEach((b) => (b.onclick = async () => { await api(`/api/batch/${b.dataset.remove}`, { method: "DELETE" }); loadPipeline(); }));
}

// ================= interview prep =================
// One step at a time: a setup flow, a mission log while it prepares, then the three rounds as adaptive decks,
// and for roles that code, an adaptive coding test. Marks, test progress and code drafts are practice
// conveniences, so they live in this browser.
const IV = { list: [], sel: store.get("cv.iv", ""), data: null, token: 0, tab: "", filter: "all", err: "", setup: null, deck: null };
const ivMarksKey = (id) => `cv.ivmarks.${id}`;
const LEVEL_TONE = { basic: "tag--ok", intermediate: "tag--info", advanced: "tag--merge" };
const LEVEL_ORDER = ["basic", "intermediate", "advanced"];
const SAY_KINDS = ["behavioural", "culture", "managerial", "scenario", "concept"];   // questions answered by talking
const pad2 = (n) => String(n).padStart(2, "0");
const ivApp = () => IV.list.find((x) => x.id === IV.sel) || {};
// Bring the practice frame up under the top bar when a round or a problem starts, so it's never below the fold.
function ivFocusHud() {
  const h = $("#ivPanel .hud"); if (!h) return;
  const top = h.getBoundingClientRect().top;
  if (top < 60 || top > innerHeight * 0.35) h.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}
const hud = (inner, controls = "") => `<div class="hud" data-fx-skip><div class="hud__frame"></div><div class="hud__scan"></div>${inner}
  ${controls ? `<div class="hud__controls">${controls}</div>` : ""}</div>`;

function openInterview(id) { IV.sel = id; store.set("cv.iv", id); IV.tab = ""; showView("interview"); }

async function loadInterview() {
  let r;
  try { r = await api("/api/interview"); }
  catch (e) {
    $("#ivList").innerHTML = "";
    $("#ivMain").innerHTML = `<div class="notice notice--bad">Couldn't load your applications (${esc(e.message)}).
      ${/not found/i.test(e.message) ? "The app's server is older than this page: close the start.bat window and run start.bat again." : "Is the app running? Start it with start.bat."}</div>`;
    return;
  }
  IV.list = r.apps;
  if (!IV.list.some((a) => a.id === IV.sel)) IV.sel = IV.list[0]?.id || "";
  renderIvList();
  if (IV.sel) selectIv(IV.sel);
  else $("#ivMain").innerHTML = `<div class="empty">Tailor a CV first. Every application you tailor shows up here, ready for interview prep.</div>`;
}

function renderIvList() {
  $("#ivList").innerHTML = IV.list.map((a) => `<div class="item item--link item--nav ${a.id === IV.sel ? "on" : ""}" data-iv="${esc(a.id)}">${logoTile(a.id, a.logo, a.company)}
      <span class="item__body"><span class="item__title">${esc(a.company)}</span><span class="item__meta" title="${esc(a.role)}">${esc(a.role)}</span></span>
      <span class="item__aside">${a.preparing ? `<span class="spinner" style="width:12px;height:12px" title="Preparing"></span>` : a.questions ? `<span class="tag tag--ok num" title="${a.questions} questions ready">${a.questions}</span>` : ""}</span></div>`).join("");
  $$("#ivList [data-iv]").forEach((el) => (el.onclick = () => { if (el.dataset.iv !== IV.sel) { IV.tab = ""; selectIv(el.dataset.iv); } }));
}

async function selectIv(id) {
  IV.sel = id; store.set("cv.iv", id); IV.setup = IV.deck = null; renderIvList();
  const token = ++IV.token;
  const r = await api(`/api/apps/${id}/interview`);
  if (token !== IV.token) return;
  IV.data = r.prep;
  if (r.job) return ivWatch(id, r.job, token);
  r.prep ? renderIv() : renderIvSetup(false);
}

// ---------- setup: a flow form ----------
const SETUP_STEPS = ["depth", "focus", "launch"];
const DEPTHS = [[4, "Quick", "Four questions a round. A warm-up the night before."], [6, "Standard", "Six a round, climbing from basic to advanced."], [8, "Deep", "Eight a round, with more advanced questions to climb into."]];

function renderIvSetup(again) {
  IV.deck = null;
  const a = ivApp(), prev = IV.data?.options || {};
  IV.setup = { step: 0, again, per: [4, 6, 8].includes(prev.per_round) ? prev.per_round : 6, focus: prev.focus || "", reuse: !!(again && IV.data?.research) };
  $("#ivMain").innerHTML = `${IV.err ? `<div class="notice notice--bad">${esc(IV.err)}</div>` : ""}` + hud(
    `<div class="hud__bar"><span>${again ? "Recalibrate" : "New session"} · ${esc(a.company)}</span><span id="ivStepNo"></span></div>
     <div class="hud__segs" id="ivSegs">${SETUP_STEPS.map(() => "<i></i>").join("")}</div>
     <div class="hud__stage" id="ivStage"></div>`,
    `<button class="btn btn--sm btn--ghost" id="ivStepBack">Back</button>${again ? `<button class="btn btn--sm btn--ghost" id="ivBack">Back to my questions</button>` : ""}
     <span class="sp"></span><span class="type-meta"><span class="kbd">Enter</span> next</span><button class="btn btn--primary" id="ivStepNext">Next ${ICON.arrow}</button>`);
  IV.err = "";
  $("#ivStepBack").onclick = () => IV.setup.step && ivStep(IV.setup.step - 1, -1);
  $("#ivStepNext").onclick = ivStepNext;
  const back = $("#ivBack"); if (back) back.onclick = renderIv;
  ivStep(0, 1);
}

function ivStep(i, dir) {
  const st = IV.setup, a = ivApp();
  st.step = i;
  const html = {
    depth: () => `<span class="hud-mono" data-flow-in>Step 01 · Depth</span><h2 class="flow-title" data-scramble>How deep do we go?</h2>
      <p class="type-body" data-flow-in>You get the three rounds ${esc(a.company)} is most likely to run for this role, planned from how it really interviews when that can be found. Coding only if the role codes; managerial and case questions if it doesn't; culture fit in the final round.</p>
      <div class="choice-grid choice-grid--3" data-flow-in>${DEPTHS.map(([k, t, d]) => `<button class="choice ${st.per === k ? "on" : ""}" data-per="${k}"><span class="choice__n num">${k}</span><b>${t}</b><small>${d}</small></button>`).join("")}</div>`,
    focus: () => `<span class="hud-mono" data-flow-in>Step 02 · Intel</span><h2 class="flow-title" data-scramble>Anything we should know?</h2>
      <p class="type-body" data-flow-in>What the recruiter told you, the round you're worried about, the stack they mentioned. Leave it empty if nothing.</p>
      <textarea id="ivFocus" rows="4" data-flow-in placeholder="e.g. Round two is live coding in Python, then system design for payments.">${esc(st.focus)}</textarea>
      ${st.again && IV.data?.research ? `<label class="check" data-flow-in><input type="checkbox" id="ivReuse" ${st.reuse ? "checked" : ""}> Reuse the company research from ${esc(IV.data.generated_at.slice(0, 10))} (faster)</label>` : ""}`,
    launch: () => `<span class="hud-mono" data-flow-in>Step 03 · Launch</span><h2 class="flow-title" data-scramble>Ready when you are.</h2>
      <div class="iv-stats" data-flow-in>
        <div class="stat"><span class="eyebrow">Rounds</span><span class="stat__value num">3</span></div>
        <div class="stat"><span class="eyebrow">Questions</span><span class="stat__value num">${3 * st.per}</span></div>
        <div class="stat"><span class="eyebrow">Takes</span><span class="stat__value num">3–8<small> min</small></span></div></div>
      <p class="type-body" data-flow-in>${st.reuse ? "Using the research you already have" : `First the web: what ${esc(a.company)} actually builds, what it values and how it interviews`}, then the three rounds, written to industry standard for this role and level.</p>
      <div data-flow-in><button class="btn btn--accent btn--lg" id="ivLaunch">Research ${esc(a.company)} and write my interview ${ICON.arrow}</button></div>
      <p class="type-meta" data-flow-in>The research runs on Claude with web search; the questions use the writer chosen at the top.</p>`,
  }[SETUP_STEPS[i]]();
  const el = FX.flowSwap($("#ivStage"), html, dir);
  el.onclick = (e) => {
    const d = e.target.closest("[data-per]");
    if (d) { st.per = +d.dataset.per; $$("[data-per]", el).forEach((x) => x.classList.toggle("on", x === d)); FX.pulse(d); }
  };
  const f = $("#ivFocus", el); if (f) { f.oninput = () => (st.focus = f.value); setTimeout(() => f.focus(), 400); }
  const rc = $("#ivReuse", el); if (rc) rc.onchange = () => (st.reuse = rc.checked);
  const go = $("#ivLaunch", el); if (go) go.onclick = ivLaunch;
  $("#ivStepNo").textContent = `${pad2(i + 1)} / ${pad2(SETUP_STEPS.length)}`;
  $$("#ivSegs i").forEach((s, k) => { s.className = k < i ? "done" : k === i ? "cur" : ""; });
  $("#ivStepBack").disabled = i === 0;
  $("#ivStepNext").hidden = i === SETUP_STEPS.length - 1;
}

function ivStepNext() {
  const st = IV.setup;
  if (st.step < SETUP_STEPS.length - 1) ivStep(st.step + 1, 1); else ivLaunch();
}

async function ivLaunch() {
  const st = IV.setup;
  try { const { job } = await api(`/api/apps/${IV.sel}/interview`, { body: { per_round: st.per, focus: st.focus, reuse_research: st.reuse } }); ivWatch(IV.sel, job, ++IV.token); }
  catch (e) { toast(e.message, 5000); }
}

// ---------- preparing: a mission log ----------
function missionLog(title) {
  return `<div class="hud__stage"><div class="flow-prep">
      <div class="hud-radar" aria-hidden="true"><i></i></div>
      <div class="flow-prep__body"><h2 class="flow-title" data-scramble>${title}</h2>
        <div class="flow-log" id="ivLog"></div>
        <p class="type-meta">You can leave this screen; it carries on.</p></div></div></div>`;
}
function logLine(token, msg, t) {
  const log = $("#ivLog"); if (!log || token !== IV.token) return;
  log.insertAdjacentHTML("beforeend", `<div class="flow-log__line"><span class="hud-mono">${esc(t || "")}</span><span class="flow-log__msg"></span></div>`);
  FX.scramble(log.lastElementChild.querySelector(".flow-log__msg"), msg);
}
async function runClock(job, el) {
  const info = await api(`/api/jobs/${job}`).catch(() => null);
  const started = info?.started ? new Date(info.started).getTime() : Date.now();
  const tick = setInterval(() => {
    if (!el.isConnected) return clearInterval(tick);
    const s = Math.round((Date.now() - started) / 1000); el.textContent = `${pad2(Math.floor(s / 60))}:${pad2(s % 60)}`;
  }, 1000);
  return () => clearInterval(tick);
}

async function ivWatch(id, job, token) {
  IV.setup = IV.deck = null;
  const a = IV.list.find((x) => x.id === id);
  if (a) { a.preparing = true; renderIvList(); }
  $("#ivMain").innerHTML = hud(`<div class="hud__bar"><span>Preparing · ${esc(a?.company || "")}</span><span id="ivClock">00:00</span></div>
    ${missionLog("Researching the company, then planning your three rounds")}`);
  FX.flowIn($("#ivMain .hud"));
  const stop = await runClock(job, $("#ivClock"));
  let ok = false;
  try { await pollJob(job, (l) => logLine(token, l.msg, l.t)); ok = true; toast(`Interview ready: ${a?.company || ""}`, 4000); }
  catch (e) { IV.err = e.message; }
  stop();
  const r = await api("/api/interview").catch(() => null);
  if (r) { IV.list = r.apps; renderIvList(); }
  if (token !== IV.token || !$("#v-interview").classList.contains("on")) return;
  if (ok) { IV.tab = ""; selectIv(id); }
  else { IV.data = (await api(`/api/apps/${id}/interview`)).prep; renderIvSetup(!!IV.data); }
}

// ---------- prepared: overview, then tabs: the company, the three rounds, the coding test ----------
function renderIv() {
  IV.setup = null;
  const d = IV.data, marks = store.get(ivMarksKey(IV.sel), {});
  const a = ivApp();
  const total = d.rounds.reduce((n, x) => n + x.questions.length, 0);
  const got = Object.values(marks).filter((v) => v === "got").length;
  const tabs = [["intel", "Company", ""], ...d.rounds.map((r, i) => [r.id, `${pad2(i + 1)} ${r.name}`, `${r.questions.length}`]), ...(d.needs_coding ? [["code", "Coding test", ""]] : [])];
  if (!tabs.some((t) => t[0] === IV.tab)) IV.tab = d.rounds[0]?.id || "intel";
  $("#ivMain").innerHTML = `
    <header class="iv-head">${logoTile(a.id, a.logo, d.company, "logo--lg")}
      <div class="iv-head__titles"><span class="eyebrow">Interview prep</span><h2 class="type-h2">${esc(d.role)}</h2>
        <span class="type-meta">${esc(d.company)} · prepared ${esc(new Date(d.generated_at).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))}${d.needs_coding ? "" : " · no coding rounds for this role"}</span></div>
      <div class="iv-stats">
        <div class="stat"><span class="eyebrow">Questions</span><span class="stat__value num">${total}</span></div>
        <div class="stat"><span class="eyebrow">Got it</span><span class="stat__value num" id="ivGot">${got}<small>/${total}</small></span></div></div>
      <div class="iv-head__acts"><a class="btn btn--sm" href="/apps/${esc(IV.sel)}/interview.md" download>Download notes</a><button class="btn btn--sm btn--ghost" id="ivAgain">Prepare again</button></div>
    </header>
    ${d.overview ? `<p class="type-body iv-overview" id="ivOverview" title="Click to read it all">${esc(d.overview)}</p>` : ""}
    <div class="tabs tabs--wrap" id="ivTabs">${tabs.map(([k, l, n]) => `<button data-tab="${k}" class="${k === IV.tab ? "on" : ""}" title="${esc(l)}"><span class="tabs__label">${esc(l)}</span>${n ? ` <span class="tabs__count num">${n}</span>` : ""}</button>`).join("")}</div>
    <div class="iv-panel" id="ivPanel"></div>`;
  $("#ivAgain").onclick = () => renderIvSetup(true);
  const ov = $("#ivOverview"); if (ov) ov.onclick = () => ov.classList.toggle("open");
  $("#ivTabs").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) ivTab(b.dataset.tab); };
  renderIvPanel();
}

function ivTab(id) {
  IV.tab = id;
  $$("#ivTabs [data-tab]").forEach((x) => x.classList.toggle("on", x.dataset.tab === id));
  renderIvPanel();
}

function renderIvPanel() {
  IV.deck = null;
  if (IV.tab === "intel") { $("#ivPanel").innerHTML = ivCompany(IV.data); return; }
  if (IV.tab === "code") return ctOpen();
  ivDeck(IV.data.rounds.find((r) => r.id === IV.tab));
}

// ---------- a round as an adaptive deck ----------
// It opens on the round's brief, then serves one question at a time. Two "got it" in a row raise the
// difficulty; a "review again" lowers it. Back walks through the questions already seen.
function ivDeck(round) {
  const marks = store.get(ivMarksKey(IV.sel), {});
  const items = round.questions.map((q, i) => [q, i, `${round.id}:${i}`])
    .filter(([, , k]) => IV.filter === "all" || (IV.filter === "again" ? marks[k] === "again" : !marks[k]));
  const ri = IV.data.rounds.indexOf(round);
  IV.deck = { round, items, order: [], at: -1, target: 0, streak: 0, done: false };
  $("#ivPanel").innerHTML = `
    <div class="report__row"><span class="type-meta">${esc(round.decides || "")}</span>
      <div class="seg" id="ivFilter">${[["all", "All"], ["new", "Not marked"], ["again", "To review"]].map(([k, l]) => `<button data-f="${k}" class="${IV.filter === k ? "on" : ""}">${l}</button>`).join("")}</div></div>` + hud(
    `<div class="hud__bar"><span>Round ${pad2(ri + 1)} / ${pad2(IV.data.rounds.length)} · ${esc(round.name)}</span><span id="ivQNo"></span></div>
     <div class="hud__segs" id="ivSegs">${items.map(() => "<i></i>").join("")}</div>
     <div class="hud__stage" id="ivStage"></div>`,
    `<button class="btn btn--sm btn--ghost" id="ivPrev" title="Left arrow">Back</button>
     <button class="btn btn--sm" id="ivReveal" title="Space">Reveal answer <span class="kbd">Space</span></button><span class="sp"></span>
     <span class="hud-mono" id="ivLevel"></span>
     <button class="chip" data-mark="again" title="R">Review again <span class="kbd">R</span></button>
     <button class="chip" data-mark="got" title="G">Got it <span class="kbd">G</span></button>
     <button class="btn btn--primary" id="ivNext" title="Right arrow or Enter">Next ${ICON.arrow}</button>`);
  $("#ivFilter").onclick = (e) => { const b = e.target.closest("[data-f]"); if (b) { IV.filter = b.dataset.f; ivDeck(round); } };
  $("#ivPrev").onclick = () => ivMove(-1);
  $("#ivNext").onclick = () => ivMove(1);
  $("#ivReveal").onclick = ivReveal;
  $$(".hud__controls [data-mark]").forEach((b) => (b.onclick = () => ivMark(b.dataset.mark)));
  $(".hud__controls").hidden = true;
  if (!items.length) {
    FX.flowSwap($("#ivStage"), `<div class="flow-round"><span class="hud-mono" data-flow-in>Nothing here</span><h2 class="flow-title" data-scramble>${IV.filter === "again" ? "Nothing marked to review." : "Every question in this round is marked."}</h2>
      <p class="type-body" data-flow-in>Switch the filter to All to run the round again.</p></div>`, 1);
    return;
  }
  ivBrief(1);
}

function ivBrief(dir) {
  const D = IV.deck, r = D.round, ri = IV.data.rounds.indexOf(r);
  D.at = -1;
  FX.flowSwap($("#ivStage"), `<div class="flow-round">
      <span class="hud-mono" data-flow-in>Round ${pad2(ri + 1)} of ${pad2(IV.data.rounds.length)}</span>
      <h2 class="flow-title flow-title--xl" data-scramble>${esc(r.name)}</h2>
      ${r.format ? `<p class="type-body" data-flow-in>${esc(r.format)}</p>` : ""}
      ${r.decides ? `<p class="type-meta" data-flow-in><b class="text-2">What it decides:</b> ${esc(r.decides)}</p>` : ""}
      ${r.source ? `<div data-flow-in><a class="btn btn--link" href="${esc(r.source)}" target="_blank" rel="noopener">How we know this round</a></div>` : ""}
      <div data-flow-in><button class="btn btn--accent btn--lg" id="ivBegin">Begin round ${ICON.arrow}</button>
        <span class="type-meta" style="margin-left:12px">Questions get harder as you get them right.</span></div></div>`, dir);
  $("#ivBegin").onclick = () => { ivFocusHud(); ivMove(1); };
  $(".hud__controls").hidden = true;
  $("#ivQNo").textContent = `${D.items.length} questions`;
  ivSegs();
}

// The next question: unseen, at the target difficulty if there is one, else the nearest difficulty to it.
function ivPick() {
  const D = IV.deck, seen = new Set(D.order);
  const left = D.items.map((it, k) => [k, LEVEL_ORDER.indexOf(it[0].level)]).filter(([k]) => !seen.has(k));
  if (!left.length) return -1;
  left.sort((a, b) => Math.abs(a[1] - D.target) - Math.abs(b[1] - D.target) || a[0] - b[0]);
  return left[0][0];
}

function ivShow(dir) {
  const D = IV.deck, [q, i, key] = D.items[D.order[D.at]];
  const mark = store.get(ivMarksKey(IV.sel), {})[key];
  const li = (items) => items.map((t) => `<li>${esc(t)}</li>`).join("");
  D.revealed = false; D.done = false;
  FX.flowSwap($("#ivStage"), `<div class="flow-q">
      <div class="flow-q__meta" data-flow-in><span class="flow-q__n num">${pad2(D.at + 1)}</span><span class="tag ${LEVEL_TONE[q.level] || ""}">${esc(q.level)}</span><span class="tag">${esc(q.kind)}</span>
        ${mark ? `<span class="tag ${mark === "got" ? "tag--ok" : "tag--warn"}">${mark === "got" ? "Got it" : "To review"}</span>` : ""}</div>
      <h3 class="flow-q__text" data-scramble>${esc(q.q)}</h3>
      ${q.why ? `<p class="type-meta" data-flow-in><b class="text-2">Why they ask:</b> ${esc(q.why)}</p>` : ""}
      <div class="flow-q__answer" id="ivAnswer" hidden>
        ${q.say ? ivSayBlock(q.say) : SAY_KINDS.includes(q.kind) ? `<div class="flow-q__say-ask" id="ivSayBox"><button class="btn btn--sm btn--primary" id="ivSay">Write what I'd say</button>
          <span class="type-meta">A spoken answer, in your words and from your real experience, for every talking question in this round. About a minute.</span></div>` : ""}
        ${q.answer.length ? `<div><div class="hud-mono iv-points" style="margin-bottom:10px">${q.say ? "Points to hit" : "Model answer"}</div><ol class="bullets">${li(q.answer)}</ol></div>` : ""}
        ${q.code ? `<pre class="code"><code>${esc(q.code)}</code></pre>` : ""}
        ${q.follow_ups.length ? `<div><div class="hud-mono" style="margin-bottom:8px">Follow-ups</div><ul class="bullets">${li(q.follow_ups)}</ul></div>` : ""}
        ${q.pitfalls.length ? `<div><div class="hud-mono" style="margin-bottom:8px">Pitfalls</div><ul class="bullets bullets--warn">${li(q.pitfalls)}</ul></div>` : ""}
        ${q.source ? `<div><a class="btn btn--link" href="${esc(q.source)}" target="_blank" rel="noopener">Where this comes from</a></div>` : ""}
      </div></div>`, dir);
  $(".hud__controls").hidden = false;
  const sayBtn = $("#ivSay"); if (sayBtn) sayBtn.onclick = () => ivWriteSay(D);
  $("#ivQNo").textContent = `Q ${pad2(D.at + 1)} / ${pad2(D.items.length)}`;
  $("#ivReveal").innerHTML = `Reveal answer <span class="kbd">Space</span>`;
  $("#ivNext").innerHTML = D.order.length === D.items.length && D.at === D.order.length - 1 ? `Finish round ${ICON.arrow}` : `Next ${ICON.arrow}`;
  $("#ivLevel").textContent = `Difficulty ${["▮▯▯", "▮▮▯", "▮▮▮"][D.target]}`;
  $$(".hud__controls [data-mark]").forEach((b) => b.classList.toggle("on", mark === b.dataset.mark));
  ivSegs();
}

const ivSayBlock = (text) => `<div class="flow-q__say"><div class="hud-mono">What you could say</div><p>${esc(text)}</p></div>`;

// Older preps have no spoken answers: write them for the whole round, then show this one's.
async function ivWriteSay(D) {
  const box = $("#ivSayBox"), token = IV.token;
  box.innerHTML = `<div style="flex:1"><div class="progress"></div><div class="progress__text">Writing what you could say for this round</div></div>`;
  try {
    const { job } = await api(`/api/apps/${IV.sel}/interview/say`, { body: { round: D.round.id } });
    const d = await pollJob(job, (l) => { const t = $(".progress__text", box); if (t) t.textContent = l.msg; });
    if (token !== IV.token) return;
    IV.data = d;
    const round = d.rounds.find((r) => r.id === D.round.id);
    D.round = round;
    D.items.forEach((it) => (it[0] = round.questions[it[1]]));
    const q = D.items[D.order[D.at]]?.[0];
    if (IV.deck !== D || !box.isConnected) return;
    if (q?.say) { box.outerHTML = ivSayBlock(q.say); FX.flowReveal($("#ivAnswer .flow-q__say")); const h = $("#ivAnswer .iv-points"); if (h) h.textContent = "Points to hit"; }
    else box.innerHTML = `<span class="type-meta">This one is better answered with the points below.</span>`;
  } catch (e) { box.innerHTML = `<span class="type-meta" style="color:var(--bad)">${esc(e.message)}</span>`; }
}

function ivSegs() {
  const D = IV.deck, marks = store.get(ivMarksKey(IV.sel), {});
  $$("#ivSegs i").forEach((s, k) => {
    const shown = k < D.order.length, m = shown ? marks[D.items[D.order[k]][2]] : "";
    s.className = [shown ? "done" : "", k === D.at && !D.done ? "cur" : "", m || ""].join(" ").trim();
  });
}

function ivReveal() {
  const D = IV.deck, a = $("#ivAnswer");
  if (!D || D.done || D.at < 0 || !a) return;
  D.revealed = !D.revealed;
  D.revealed ? FX.flowReveal(a) : (a.hidden = true);
  $("#ivReveal").innerHTML = D.revealed ? `Hide answer <span class="kbd">Space</span>` : `Reveal answer <span class="kbd">Space</span>`;
}

function ivMark(kind) {
  const D = IV.deck; if (!D || D.done || D.at < 0) return;
  const key = D.items[D.order[D.at]][2], all = store.get(ivMarksKey(IV.sel), {});
  const was = all[key];
  all[key] === kind ? delete all[key] : (all[key] = kind);
  store.set(ivMarksKey(IV.sel), all);
  const total = IV.data.rounds.reduce((n, x) => n + x.questions.length, 0);
  $("#ivGot").innerHTML = `${Object.values(all).filter((v) => v === "got").length}<small>/${total}</small>`;
  $$(".hud__controls [data-mark]").forEach((b) => b.classList.toggle("on", all[key] === b.dataset.mark));
  ivSegs();
  if (!all[key] || was) return;          // only a fresh mark moves the difficulty and the deck
  if (kind === "got") { D.streak += 1; if (D.streak >= 2 && D.target < 2) { D.target += 1; D.streak = 0; ivLevelShift(1); } }
  else { D.streak = 0; if (D.target > 0) { D.target -= 1; ivLevelShift(-1); } }
  setTimeout(() => IV.deck === D && D.items[D.order[D.at]]?.[2] === key && ivMove(1), 420);
}

function ivLevelShift(dir) {
  const el = $("#ivLevel"); if (!el) return;
  FX.scramble(el, dir > 0 ? "Difficulty up ▲" : "Easing off ▼");
  toast(dir > 0 ? "Two in a row. The questions get harder." : "Easing off a level.", 1800);
}

function ivMove(step) {
  const D = IV.deck; if (!D) return;
  if (D.done) return step > 0 ? ivNextRound() : (D.done = false, D.at = D.order.length - 1, ivShow(-1));
  if (step < 0) { if (D.at > 0) { D.at -= 1; ivShow(-1); } else if (D.at === 0) ivBrief(-1); return; }
  if (D.at < D.order.length - 1) { D.at += 1; return ivShow(1); }
  const k = ivPick();
  if (k < 0) return ivRoundDone();
  D.order.push(k); D.at = D.order.length - 1; ivShow(1);
}

function ivRoundDone() {
  const D = IV.deck, d = IV.data, ri = d.rounds.indexOf(D.round), next = d.rounds[ri + 1];
  const marks = store.get(ivMarksKey(IV.sel), {});
  const got = D.round.questions.filter((_, i) => marks[`${D.round.id}:${i}`] === "got").length;
  D.done = true;
  const after = next ? next.name : d.needs_coding ? "Coding test" : "Interview complete";
  FX.flowSwap($("#ivStage"), `<div class="flow-round">
      <span class="hud-mono" data-flow-in>Round ${pad2(ri + 1)} complete · ${got} of ${D.round.questions.length} got it</span>
      <h2 class="flow-title flow-title--xl" data-scramble>${esc(after)}</h2>
      <p class="type-body" data-flow-in>${next ? esc(next.format || "") : d.needs_coding ? "An adaptive, LeetCode-style test. Your code runs here, against hidden tests." : "Run the ones you marked to review again, or download the notes to read on the way."}</p>
      <div data-flow-in><button class="btn btn--accent btn--lg" id="ivNextRound">${next ? `Start round ${pad2(ri + 2)}` : d.needs_coding ? "Open the coding test" : "Review what's left"} ${ICON.arrow}</button></div></div>`, 1);
  $("#ivNextRound").onclick = ivNextRound;
  $("#ivQNo").textContent = "Round complete";
  $(".hud__controls").hidden = true;
  ivSegs();
}

function ivNextRound() {
  const d = IV.data, ri = d.rounds.indexOf(IV.deck.round), next = d.rounds[ri + 1];
  if (next) return ivTab(next.id);
  if (d.needs_coding) return ivTab("code");
  IV.filter = "again"; ivTab(d.rounds[0].id);
}

document.addEventListener("keydown", (e) => {
  if (!$("#v-interview").classList.contains("on") || e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = document.activeElement.tagName;
  if (/INPUT|TEXTAREA|SELECT/.test(tag) || document.activeElement.closest?.(".CodeMirror")) return;
  if (/^(BUTTON|A)$/.test(tag) && (e.key === "Enter" || e.key === " ")) return;   // the focused button handles it
  if (IV.setup && e.key === "Enter") { e.preventDefault(); return ivStepNext(); }
  if (CT.next && e.key === "Enter") { e.preventDefault(); return CT.next(); }
  if (!IV.deck) return;
  const k = e.key.toLowerCase();
  if (k === "arrowright" || k === "enter") { e.preventDefault(); ivMove(1); }
  else if (k === "arrowleft") { e.preventDefault(); ivMove(-1); }
  else if (k === " ") { e.preventDefault(); ivReveal(); }
  else if (k === "g") ivMark("got");
  else if (k === "r") ivMark("again");
});

// ---------- the coding test: adaptive, LeetCode-style, run in a browser sandbox ----------
// Solve a problem and the next is a level harder; skip one or look at the solution and the next is a level
// easier. Each problem's tests are first checked against its reference solution; tests it fails are dropped.
const CT = { data: null, st: null, cm: null, workers: {}, ready: {}, checked: {}, next: null, timer: null };
const ctKey = (id) => `cv.codetest.${id}`;
const LANG = { python: "Python", javascript: "JavaScript" };

function ctFresh() {
  return { level: 2, streak: 0, score: 0, history: [], drafts: {}, lang: IV.data?.coding_language || "python", current: null, started: Date.now() };
}
const ctSave = () => store.set(ctKey(IV.sel), CT.st);

async function ctOpen() {
  const token = IV.token;
  const r = await api(`/api/apps/${IV.sel}/codetest`);
  if (token !== IV.token || IV.tab !== "code") return;
  CT.data = r.test; CT.st = store.get(ctKey(IV.sel), null) || ctFresh(); CT.next = null;
  if (r.job) return ctWait(r.job, "Writing your coding test");
  if (!CT.data) return ctIntro();
  ctNext();
}

function ctHud(inner, controls) {
  $("#ivPanel").innerHTML = hud(inner, controls);
  return $("#ivPanel .hud");
}

function ctBar() {
  const s = CT.st;
  return `<div class="hud__bar"><span>Coding test · Level ${pad2(s.level)} / 05 · Streak ${s.streak}</span><span>Score ${s.score} · <span id="ctClock">00:00</span></span></div>
    <div class="hud__segs" id="ctSegs">${[1, 2, 3, 4, 5].map((l) => `<i class="${l < s.level ? "done" : l === s.level ? "cur" : ""}"></i>`).join("")}</div>`;
}

function ctClock() {
  clearInterval(CT.timer);
  CT.timer = setInterval(() => {
    const el = $("#ctClock"); if (!el) return clearInterval(CT.timer);
    const s = Math.round((Date.now() - CT.st.started) / 1000); el.textContent = `${pad2(Math.floor(s / 60))}:${pad2(s % 60)}`;
  }, 1000);
}

function ctIntro() {
  ctHud(`<div class="hud__bar"><span>Coding test · ${esc(IV.data.company)}</span><span>Adaptive · 5 levels</span></div><div class="hud__stage" id="ctStage"></div>`);
  FX.flowSwap($("#ctStage"), `<div class="flow-round">
      <span class="hud-mono" data-flow-in>Online assessment</span>
      <h2 class="flow-title" data-scramble>An adaptive coding test, set for this job.</h2>
      <p class="type-body" data-flow-in>Like a real online assessment. You start at level 2: solve a problem and the next one is a level harder; skip one and it eases off. Write in Python or JavaScript. Your code runs right here against hidden tests, in a sandbox that can't touch the network or your files.</p>
      <div class="iv-stats" data-flow-in>
        <div class="stat"><span class="eyebrow">Levels</span><span class="stat__value num">5</span></div>
        <div class="stat"><span class="eyebrow">Problems</span><span class="stat__value num">10</span></div>
        <div class="stat"><span class="eyebrow">Writing it takes</span><span class="stat__value num">2–5<small> min</small></span></div></div>
      <div data-flow-in><button class="btn btn--accent btn--lg" id="ctGen">Write my coding test ${ICON.arrow}</button></div></div>`, 1);
  $("#ctGen").onclick = async () => {
    try { const { job } = await api(`/api/apps/${IV.sel}/codetest`, { body: {} }); ctWait(job, "Writing your coding test"); }
    catch (e) { toast(e.message, 5000); }
  };
}

async function ctWait(job, title) {
  const token = IV.token;
  ctHud(`<div class="hud__bar"><span>Coding test · ${esc(IV.data.company)}</span><span id="ivClock">00:00</span></div>${missionLog(title)}`);
  FX.flowIn($("#ivPanel .hud"));
  const stop = await runClock(job, $("#ivClock"));
  try { await pollJob(job, (l) => logLine(token, l.msg, l.t)); }
  catch (e) { toast(e.message, 5000); }
  stop();
  if (token === IV.token && IV.tab === "code") ctOpen();
}

// ---- the sandbox ----
function ctWorker(lang) {
  if (!CT.workers[lang]) CT.workers[lang] = new Worker(`/static/runner.js?v=1&lang=${lang}`);
  return CT.workers[lang];
}

function ctExec(lang, code, name, tests) {
  // The first Python run downloads and starts Pyodide; after that a run gets six seconds.
  const ms = lang === "python" && !CT.ready.python ? 90000 : 6000;
  return new Promise((resolve) => {
    const w = ctWorker(lang), id = Math.random().toString(36).slice(2);
    const timer = setTimeout(() => {
      w.terminate(); delete CT.workers[lang]; CT.ready[lang] = false;
      resolve({ fatal: `Time limit exceeded (${ms / 1000}s). Look for an infinite loop or an approach that's too slow.` });
    }, ms);
    const on = ({ data }) => {
      if (data.id !== id) return;
      clearTimeout(timer); w.removeEventListener("message", on); CT.ready[lang] = true; resolve(data);
    };
    w.addEventListener("message", on);
    w.postMessage({ id, lang, code, name, tests: tests.map((t) => t.args) });
  });
}

const canon = (v) => JSON.stringify(v, (k, x) => (x && typeof x === "object" && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => (a < b ? -1 : 1))) : x));
function closeEnough(a, b) {
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) <= 1e-6 * Math.max(1, Math.abs(b));
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => closeEnough(x, b[i]));
  return canon(a) === canon(b);
}
function same(got, want, mode) {
  if (mode === "float") return closeEnough(got, want);
  if (mode === "unordered" && Array.isArray(got) && Array.isArray(want)) return got.length === want.length && canon(got.map(canon).sort()) === canon(want.map(canon).sort());
  return canon(got) === canon(want);
}

// A problem's usable tests in a language: the ones its reference solution passes.
async function ctTests(p, lang) {
  const key = `${p.id}:${lang}`;
  if (CT.checked[key]) return CT.checked[key];
  const res = await ctExec(lang, p.solution[lang], p.function[lang], p.tests);
  const ok = res.results ? p.tests.filter((t, i) => res.results[i]?.ok && same(res.results[i].out, t.expected, p.compare)) : [];
  CT.checked[key] = ok;
  return ok;
}

// ---- choosing and showing a problem ----
async function ctNext() {
  const s = CT.st, done = new Set(s.history.map((h) => h.id));
  let p = s.current && CT.data.problems.find((x) => x.id === s.current);
  if (!p) p = CT.data.problems.find((x) => x.level === s.level && !done.has(x.id) && !CT.checked[`${x.id}:bad`]);
  if (!p) {
    const { job } = await api(`/api/apps/${IV.sel}/codetest`, { body: { level: s.level } });
    return ctWait(job, `You've used every level ${s.level} problem. Writing two more.`);
  }
  if (!CT.checked[`${p.id}:${s.lang}`]) {
    ctHud(ctBar() + `<div class="hud__stage" id="ctStage"></div>`);
    FX.flowSwap($("#ctStage"), `<div class="flow-round"><span class="hud-mono" data-flow-in>Level ${pad2(p.level)} · ${esc(p.topic)}</span>
      <h2 class="flow-title" data-scramble>${s.lang === "python" && !CT.ready.python ? "Booting the Python sandbox" : "Checking the tests"}</h2>
      <p class="type-meta" data-flow-in>${s.lang === "python" && !CT.ready.python ? "The first run downloads Python for the browser, about 10 MB; after that it's instant." : "Each test is run against the reference solution first, so a wrong test can't fail you."}</p></div>`, 1);
    const tests = await ctTests(p, s.lang);
    if (tests.length < 3) { CT.checked[`${p.id}:bad`] = true; s.current = null; return ctNext(); }
  }
  s.current = p.id; ctSave();
  ctShow(p);
}

function ctShow(p) {
  const s = CT.st, lang = s.lang;
  CT.next = null;
  const draftKey = `${p.id}:${lang}`;
  const n = s.history.length + 1;
  ctHud(ctBar() + `<div class="hud__stage" id="ctStage"></div>`,
    `<div class="seg" id="ctLang">${Object.entries(LANG).map(([k, l]) => `<button data-lang="${k}" class="${k === lang ? "on" : ""}">${l}</button>`).join("")}</div>
     <button class="btn btn--sm btn--ghost" id="ctReset">Reset code</button><span class="sp"></span>
     <button class="btn btn--sm btn--ghost" id="ctSkip" title="Counts as a miss: the next problem is easier">Skip</button>
     <button class="btn btn--sm btn--ghost" id="ctSolution" title="Counts as a miss">Show solution</button>
     <button class="btn btn--sm" id="ctRun" title="Ctrl+'">Run examples</button>
     <button class="btn btn--accent" id="ctSubmit" title="Ctrl+Enter">Submit ${ICON.arrow}</button>`);
  const el = FX.flowSwap($("#ctStage"), `<div class="code-test">
      <section class="code-test__problem">
        <span class="hud-mono" data-flow-in>Problem ${pad2(n)} · Level ${pad2(p.level)}${p.topic ? ` · ${esc(p.topic)}` : ""}</span>
        <h3 class="flow-q__text" data-scramble>${esc(p.title)}</h3>
        <div class="code-test__statement" data-flow-in>${esc(p.statement)}</div>
        ${p.examples.map((e, i) => `<div class="code-test__example" data-flow-in><b>Example ${i + 1}</b>\nInput: ${esc(e.input)}\nOutput: ${esc(e.output)}${e.explain ? `\nWhy: ${esc(e.explain)}` : ""}</div>`).join("")}
        ${p.constraints.length ? `<div data-flow-in><div class="hud-mono" style="margin-bottom:8px">Constraints</div><ul class="bullets">${p.constraints.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></div>` : ""}
        ${p.hints.length ? `<div data-flow-in><button class="btn btn--sm btn--ghost" id="ctHint">Show a hint</button><ol class="bullets" id="ctHints" style="margin-top:10px"></ol></div>` : ""}
      </section>
      <section class="code-test__work">
        <div class="code-test__editor" id="ctEditor"></div>
        <div class="code-test__console" id="ctOut"><span class="text-3">Run the examples to check your approach, then submit against every test. <span class="kbd">Ctrl</span> <span class="kbd">Enter</span> submits.</span></div>
        <div class="code-test__solution" id="ctSol" hidden></div>
      </section></div>`, 1);
  CT.cm = CodeMirror($("#ctEditor", el), {
    value: s.drafts[draftKey] ?? p.starter[lang], mode: lang === "python" ? "python" : "javascript", theme: "pop",
    lineNumbers: true, indentUnit: lang === "python" ? 4 : 2, tabSize: 4, matchBrackets: true, autoCloseBrackets: true,
    extraKeys: { "Ctrl-Enter": () => ctRun(true), "Cmd-Enter": () => ctRun(true), "Ctrl-'": () => ctRun(false),
      Tab: (cm) => (cm.somethingSelected() ? cm.indentSelection("add") : cm.replaceSelection(" ".repeat(cm.getOption("indentUnit")))) },
  });
  let save;
  CT.cm.on("change", () => { clearTimeout(save); save = setTimeout(() => { s.drafts[draftKey] = CT.cm.getValue(); ctSave(); }, 400); });
  setTimeout(() => { CT.cm.refresh(); CT.cm.focus({ preventScroll: true }); ivFocusHud(); }, 700);
  ctClock();
  $("#ctLang").onclick = (e) => { const b = e.target.closest("[data-lang]"); if (b && b.dataset.lang !== s.lang) { s.lang = b.dataset.lang; ctSave(); ctNext(); } };
  $("#ctReset").onclick = () => { CT.cm.setValue(p.starter[lang]); };
  $("#ctRun").onclick = () => ctRun(false);
  $("#ctSubmit").onclick = () => ctRun(true);
  $("#ctSkip").onclick = () => ctMiss(p, "skipped");
  $("#ctSolution").onclick = () => ctReveal(p);
  if (s.peeked === p.id) ctReveal(p);
  const hint = $("#ctHint");
  if (hint) { let k = 0; hint.onclick = () => { if (k < p.hints.length) { $("#ctHints").insertAdjacentHTML("beforeend", `<li>${esc(p.hints[k++])}</li>`); FX.pulse($("#ctHints").lastElementChild); } if (k >= p.hints.length) hint.disabled = true; }; }
}

async function ctRun(submit) {
  const s = CT.st, p = CT.data.problems.find((x) => x.id === s.current);
  if (!p || !CT.cm) return;
  const out = $("#ctOut"), lang = s.lang;
  const all = await ctTests(p, lang);
  const tests = submit ? all : all.slice(0, Math.max(1, Math.min(2, p.examples.length || 2)));
  $("#ctRun").disabled = $("#ctSubmit").disabled = true;
  out.innerHTML = `<span class="spinner" style="width:12px;height:12px;display:inline-block;vertical-align:-1px"></span> ${submit ? `Running ${tests.length} tests` : "Running the examples"}…`;
  const t0 = performance.now();
  const res = await ctExec(lang, CT.cm.getValue(), p.function[lang], tests);
  const took = Math.round(performance.now() - t0);
  $("#ctRun").disabled = $("#ctSubmit").disabled = false;
  if (res.fatal) { out.innerHTML = `<div class="ct-fail">${esc(res.fatal)}</div>`; FX.pulse(out); return; }
  const rows = tests.map((t, i) => ({ t, r: res.results[i], ok: res.results[i]?.ok && same(res.results[i].out, t.expected, p.compare) }));
  const passed = rows.filter((x) => x.ok).length;
  const show = (x, i) => `<div class="ct-case ${x.ok ? "ct-pass" : "ct-fail"}"><b>${x.ok ? "✓" : "✗"} Test ${i + 1}</b>
      <div>Input: ${esc(JSON.stringify(x.t.args).slice(1, -1))}</div><div>Expected: ${esc(JSON.stringify(x.t.expected))}</div>
      <div>${x.r.ok ? `Output: ${esc(JSON.stringify(x.r.out))}` : esc(x.r.error)}</div>${x.r.stdout ? `<div class="text-3">stdout: ${esc(x.r.stdout)}</div>` : ""}</div>`;
  if (!submit) {
    out.innerHTML = `<div class="${passed === rows.length ? "ct-pass" : "ct-fail"}"><b>${passed} / ${rows.length} examples passed</b> <span class="text-3">· ${took} ms</span></div>${rows.map(show).join("")}`;
    return;
  }
  if (passed < rows.length) {
    const first = rows.findIndex((x) => !x.ok);
    out.innerHTML = `<div class="ct-fail"><b>Wrong answer · ${passed} / ${rows.length} tests passed</b></div>${show(rows[first], first)}`;
    FX.pulse(out);
    return;
  }
  if (CT.st.peeked === p.id) {
    out.innerHTML = `<div class="ct-pass"><b>Accepted · ${passed} / ${rows.length} tests passed</b></div><div class="text-3">Written after viewing the solution, so it doesn't score. The next problem is a level easier.</div>
      <button class="btn btn--sm btn--primary" id="ctAfter2" style="margin-top:10px">Next problem ${ICON.arrow}</button>`;
    $("#ctAfter2").onclick = () => ctMiss(p, "revealed");
    return;
  }
  ctSolved(p, took);
}

function ctSolved(p, ms) {
  const s = CT.st;
  s.streak += 1;
  const gained = p.level * 100 + (s.streak > 1 ? 50 * (s.streak - 1) : 0);
  s.score += gained;
  s.history.push({ id: p.id, level: p.level, result: "solved", ms });
  const up = s.level < 5;
  if (up) s.level += 1;
  s.current = null; ctSave();
  ctInterstitial(`Accepted · +${gained}${s.streak > 1 ? ` · streak ${s.streak}` : ""}`, up ? `Level ${pad2(s.level)}` : "Top level", up ? "Harder problems from here." : "You're at the hardest level. Keep the streak going.");
}

function ctMiss(p, result) {
  const s = CT.st;
  s.streak = 0;
  s.history.push({ id: p.id, level: p.level, result });
  const down = s.level > 1;
  if (down) s.level -= 1;
  s.current = null; ctSave();
  ctInterstitial(result === "skipped" ? "Skipped" : "Solution viewed", down ? `Level ${pad2(s.level)}` : "Level 01", down ? "Easing off a level." : "Staying at the first level.");
}

// The solution opens in its own panel and the editor stays yours: type it out, change it, run it. Having looked,
// the problem can no longer score: a passing submit is accepted without points and the next one eases off.
function ctReveal(p) {
  const s = CT.st, box = $("#ctSol");
  s.peeked = p.id; ctSave();
  box.hidden = false;
  box.innerHTML = `<div class="report__row"><span class="hud-mono">Reference solution · ${esc(LANG[s.lang])} · ${esc(p.complexity)}</span>
      <button class="btn btn--sm btn--primary" id="ctAfter">Next problem ${ICON.arrow}</button></div>
    <pre class="code">${esc(p.solution[s.lang])}</pre>
    <p class="type-meta">You've seen the solution, so this one won't score. Try writing it yourself in the editor and run it; the next problem is a level easier.</p>`;
  FX.flowReveal(box);
  $("#ctSolution").disabled = true;
  $("#ctAfter").onclick = () => ctMiss(p, "revealed");
}

function ctInterstitial(tag, title, line) {
  const s = CT.st;
  ctHud(ctBar() + `<div class="hud__stage" id="ctStage"></div>`);
  FX.flowSwap($("#ctStage"), `<div class="flow-round">
      <span class="hud-mono" data-flow-in>${esc(tag)}</span>
      <h2 class="flow-title flow-title--xl" data-scramble>${esc(title)}</h2>
      <p class="type-body" data-flow-in>${esc(line)}</p>
      <div data-flow-in><button class="btn btn--accent btn--lg" id="ctGo">Next problem ${ICON.arrow}</button>
        <button class="btn btn--ghost" id="ctEnd" style="margin-left:8px">End the test</button></div></div>`, 1);
  ctClock();
  CT.next = () => { CT.next = null; ctNext(); };
  $("#ctGo").onclick = CT.next;
  $("#ctEnd").onclick = ctSummary;
  FX.pulse($("#ctSegs"));
}

function ctSummary() {
  const s = CT.st, solved = s.history.filter((h) => h.result === "solved");
  const best = Math.max(0, ...solved.map((h) => h.level));
  CT.next = null;
  ctHud(ctBar() + `<div class="hud__stage" id="ctStage"></div>`);
  FX.flowSwap($("#ctStage"), `<div class="flow-round">
      <span class="hud-mono" data-flow-in>Test complete</span>
      <h2 class="flow-title flow-title--xl" data-scramble>${best ? `Level ${pad2(best)} cleared` : "Keep practising"}</h2>
      <div class="iv-stats" data-flow-in>
        <div class="stat"><span class="eyebrow">Solved</span><span class="stat__value num">${solved.length}<small>/${s.history.length}</small></span></div>
        <div class="stat"><span class="eyebrow">Score</span><span class="stat__value num">${s.score}</span></div>
        <div class="stat"><span class="eyebrow">Highest level</span><span class="stat__value num">${best || "–"}</span></div></div>
      <div class="list" data-flow-in>${s.history.map((h) => { const p = CT.data.problems.find((x) => x.id === h.id); return `<div class="item"><span class="tag ${h.result === "solved" ? "tag--ok" : "tag--warn"}">${h.result}</span><span class="item__body"><span class="item__title">${esc(p?.title || h.id)}</span></span><span class="item__aside hud-mono">L${h.level}</span></div>`; }).join("")}</div>
      <div data-flow-in><button class="btn btn--accent btn--lg" id="ctRestart">Start a new test ${ICON.arrow}</button></div></div>`, 1);
  $("#ctRestart").onclick = () => { CT.st = ctFresh(); ctSave(); ctNext(); };
}

function ivCompany(d) {
  const r = d.research || {};
  const src = (u, label = "Source") => `<a class="btn btn--link" href="${esc(u)}" target="_blank" rel="noopener">${label}</a>`;
  const empty = !r.summary && !(r.products || []).length && !(r.process || []).length;
  const rows = (list, text, sub) => `<div class="list">${list.map((x) => `<div class="item"><span class="item__body"><span class="item__text">${esc(x[text])}</span>${x[sub] ? `<span class="item__meta item__meta--wrap">${esc(x[sub])}</span>` : ""}</span><span class="item__aside">${src(x.source)}</span></div>`).join("")}</div>`;
  return `
    ${empty ? `<div class="notice notice--info">The web research found nothing it could source for ${esc(d.company)}, so the questions lean on the job description.</div>` : ""}
    ${r.summary ? `<section class="iv-section"><span class="eyebrow">What ${esc(d.company)} does</span><p class="type-body">${esc(r.summary)}</p>
      ${r.team_guess ? `<p class="type-meta"><b class="text-2">Likely team:</b> ${esc(r.team_guess)}</p>` : ""}</section>` : ""}
    ${(r.products || []).length ? `<section class="iv-section"><span class="eyebrow">What they build</span><div class="card-grid">
      ${r.products.map((p) => `<div class="card iv-product"><span class="type-h3">${esc(p.name)}</span><span class="type-body" style="font-size:13px">${esc(p.what)}</span>
        ${p.relevance ? `<span class="type-meta">${esc(p.relevance)}</span>` : ""}${src(p.source)}</div>`).join("")}</div></section>` : ""}
    ${(r.values || []).length ? `<section class="iv-section"><span class="eyebrow">What they value (the culture-fit round leans on this)</span>${rows(r.values, "name", "what")}</section>` : ""}
    ${(r.stack || []).length ? `<section class="iv-section"><span class="eyebrow">Their stack</span><div class="chip-row">
      ${r.stack.map((s) => `<a class="chip" href="${esc(s.source)}" target="_blank" rel="noopener" title="Source: ${esc(s.source)}"><span>${esc(s.name)}</span></a>`).join("")}</div></section>` : ""}
    ${(r.process || []).length ? `<section class="iv-section"><span class="eyebrow">How they interview</span><div class="list">
      ${r.process.map((p, i) => `<div class="item" style="align-items:flex-start"><span class="steps__n">${pad2(i + 1)}</span>
        <span class="item__body"><span class="item__title item__title--wrap">${esc(p.stage)}${p.format ? ` <span class="tag">${esc(p.format)}</span>` : ""}</span>
          ${p.detail ? `<span class="item__meta item__meta--wrap">${esc(p.detail)}</span>` : ""}</span><span class="item__aside">${src(p.source)}</span></div>`).join("")}</div></section>` : ""}
    ${(r.recent || []).length ? `<section class="iv-section"><span class="eyebrow">Lately</span>${rows(r.recent, "text", "date")}</section>` : ""}
    ${(r.reported_questions || []).length ? `<section class="iv-section"><span class="eyebrow">What candidates report being asked</span>${rows(r.reported_questions, "text", "round")}</section>` : ""}`;
}

// ================= global Claude / Gemini switch =================
function wireEngineSwitch() {
  const paint = () => {
    const fam = (D().engine || "claude:opus").startsWith("gemini") ? "gemini" : "claude";
    $$("#engineSeg button").forEach((b) => b.classList.toggle("on", b.dataset.e === fam));
  };
  paint();
  $("#engineSeg").onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const lastClaude = store.get("cv.claudeModel", "claude:opus");
    if ((D().engine || "").startsWith("claude")) store.set("cv.claudeModel", D().engine);
    const engine = b.dataset.e === "gemini" ? "gemini:default" : lastClaude;
    await savePrefs({ defaults: { engine } });
    paint();
    if ($("#dEngine")) $("#dEngine").value = engine;
    toast(b.dataset.e === "gemini"
      ? "Gemini now writes CVs, letters, page reads and career maps. Web searches stay on Claude. If Gemini isn't signed in, run gemini once in a terminal."
      : "Claude now writes and reads everywhere.", 5000);
  };
}

// ================= career map =================
let CAREER = null;
async function loadCareer() {
  CAREER = await api("/api/career");
  const m = CAREER.map;
  $("#careerGen").textContent = m ? "Map my routes again" : "Map my routes";
  $("#careerStamp").textContent = m ? `Mapped ${new Date(m.generated_at).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}` : "";
  $("#careerLede").textContent = m && m.summary ? m.summary : "Your history, and the routes it opens over the next five years. Map your routes to see them.";
  $("#careerLede").title = m && m.summary ? "Click to read it all" : "";
  $("#careerLede").onclick = () => $("#careerLede").classList.toggle("open");
  window.Career.render($("#flow"), CAREER.history, m, S.state.profile.experience_years, onCareerClick, null, CAREER.goal);
  renderGoal(); renderSalary(); renderTrends();
  if (!$("#stepPanel").innerHTML) $("#stepPanel").innerHTML = `<p class="type-meta">${m ? "Click any card to see what that step takes, what you already have, and the jobs for it." : "Press <b>Map my routes</b>: the writer reads your whole history and your growth log, then lays out five or six routes."}</p>`;
  renderLog();
}

function onCareerClick(d) {
  const p = $("#stepPanel");
  const list = (items, cls) => (items || []).length ? `<ul class="bullets ${cls}">${items.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : `<p class="type-meta">Nothing listed.</p>`;
  p.style.removeProperty("--c");
  if (d.type === "history") {
    const x = d.item;
    p.innerHTML = `<span class="eyebrow">${x.kind === "project" ? "Project" : x.kind === "education" ? "Education" : "Role"}</span>
      <h2 class="type-h2">${esc(x.title)}</h2><p class="type-meta">${esc(x.org)}, ${esc(x.when)}</p>
      ${(x.highlights || []).length ? list(x.highlights.map((t) => t.replace(/\*\*/g, "")), "") : ""}`;
    return;
  }
  if (d.type === "now") {
    p.innerHTML = `<span class="eyebrow">Now</span><h2 class="type-h2">You, now</h2><p class="type-meta">${esc(S.state.profile.experience_years)} years of experience</p>
      <p class="step__why">${esc((CAREER.map && CAREER.map.summary) || "Map your routes to see where this goes.")}</p>`;
    return;
  }
  if (d.type === "goal") {
    const m = CAREER.map || {};
    const reach = (m.paths || []).filter((x) => x.reaches_goal).map((x) => x.name);
    p.innerHTML = `<span class="eyebrow">Your goal</span><h2 class="type-h2">${esc(d.title)}</h2><p class="type-meta">${esc(d.sub)} ${esc(d.when)}</p>
      <p class="step__why">${esc(m.goal_assessment || "Map your routes again to see which ones reach this goal.")}</p>
      ${reach.length ? `<h4 class="eyebrow">Routes that lead here</h4>${list(reach, "")}` : ""}`;
    return;
  }
  const s = d.step, path = d.path;
  p.style.setProperty("--c", d.color);
  p.innerHTML = `<span class="eyebrow step__route">${esc(path.name)}, ${esc(path.fit)} fit</span>
    <h2 class="type-h2">${esc(s.title)}</h2><p class="type-meta">${esc(s.level)}${s.when ? `, ${esc(s.when)}` : ""}${s.typical_tenure ? `, typically ${esc(s.typical_tenure)}` : ""}</p>
    <p class="step__why">${esc(path.why)}</p>
    <h4 class="eyebrow">What employers screen for</h4>${list(s.what_it_takes, "")}
    <h4 class="eyebrow">What you already have</h4>${list(s.you_have, "bullets--ok")}
    <h4 class="eyebrow">What to build</h4>${list(s.to_build, "bullets--warn")}
    ${(s.certifications || []).length ? `<h4 class="eyebrow">Certifications</h4><ul class="bullets">${s.certifications.map((c) => `<li>${esc(c.name)} <span class="tag ${c.need === "screened" ? "tag--warn" : ""}">${c.need === "screened" ? "Screened for" : "Helps"}</span>${c.why ? `<div class="type-meta">${esc(c.why)}</div>` : ""}</li>`).join("")}</ul>` : ""}
    ${(s.projects || []).length ? `<h4 class="eyebrow">Projects to take on</h4>${list(s.projects, "bullets--warn")}` : ""}
    <h4 class="eyebrow">Pay at this step</h4>
    ${s.salary_band ? `<div class="step__band num">${s.salary_band.low}–${s.salary_band.high} ${esc(s.salary_band.unit)}<a class="btn btn--link" href="${esc(s.salary_band.source)}" target="_blank" rel="noopener">Source</a></div>` : `<p class="type-meta">No sourced range found, so none is shown.</p>`}
    ${s.ladder_note ? `<h4 class="eyebrow">How companies name this level</h4><p class="step__why">${esc(s.ladder_note)}</p>` : ""}
    ${(path.promotion_playbook || []).length ? `<h4 class="eyebrow">How people move up on this route</h4>${list(path.promotion_playbook, "")}` : ""}
    ${(path.sources || []).length ? `<p class="type-meta">Sources: ${path.sources.map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener">${i + 1}</a>`).join(", ")}</p>` : ""}
    <div class="step__acts"><button class="btn btn--sm btn--primary" id="stepJobs">Jobs from your scans</button><button class="btn btn--sm" id="stepWeb">Search the web</button>
      <button class="btn btn--sm btn--ghost" id="stepSource" title="Add a web search for these titles to Discover">Track in Discover</button></div>
    <p class="type-meta">Searches: ${esc((s.search_titles || []).join(", "))}</p>
    <div class="list" id="stepResults"></div>`;
  const titles = s.search_titles && s.search_titles.length ? s.search_titles : [s.title];
  const showJobs = (jobs, fromWeb) => {
    $("#stepResults").innerHTML = jobs.length ? jobs.map((j) => `<div class="item"><span class="item__body"><span class="item__title item__title--wrap">${esc(j.title)}</span>
        <span class="item__meta">${esc(j.company || "")}${j.location ? `, ${esc(j.location)}` : ""}${j.posted ? `, posted ${esc(ago(j.posted))}` : ""}</span></span>
        <span class="item__aside">${j.match !== undefined ? scoreNum(j.match) : ""}<a class="btn btn--sm btn--ghost" href="${esc(j.url)}" target="_blank" rel="noopener">Open</a><button class="btn btn--sm" data-tl="${esc(fromWeb ? j.url : j.id)}">Tailor</button></span></div>`).join("")
      : `<p class="type-meta">${fromWeb ? "The web search found nothing fresh for these titles." : "None in your recent scans. Try Search the web, or Track in Discover."}</p>`;
    $$("[data-tl]", $("#stepResults")).forEach((b) => (b.onclick = async () => {
      await api("/api/batch", { body: { items: [fromWeb ? { url: b.dataset.tl } : { found_id: b.dataset.tl }] } });
      b.textContent = "Queued"; b.disabled = true; pollBatch(true);
      toast("Tailoring it now. Follow it in Pipeline.");
    }));
  };
  $("#stepJobs").onclick = async () => { $("#stepResults").innerHTML = `<p class="type-meta">Looking through your scans…</p>`; showJobs(await api("/api/career/jobs", { body: { titles, days: 30 } }), false); };
  $("#stepWeb").onclick = async () => {
    $("#stepResults").innerHTML = `<div><div class="progress"></div><p class="progress__text">Searching the web for these titles in your regions, about a minute…</p></div>`;
    try { const { job } = await api("/api/career/search", { body: { titles } }); const res = await pollJob(job, () => {}); showJobs(res.jobs.filter((j) => !j.excluded), true); }
    catch (e) { $("#stepResults").innerHTML = `<p class="type-meta">${esc(e.message)}</p>`; }
  };
  $("#stepSource").onclick = async () => {
    try { await api("/api/sources", { body: { kind: "websearch", value: `${titles.join(" OR ")} jobs`, label: s.title } }); toast(`Discover now tracks ${s.title} on every scan`); }
    catch (e) { toast(e.message, 4000); }
  };
}

function renderLog() {
  const kinds = CAREER.kinds;
  if (!$("#logKind").options.length) {
    $("#logKind").innerHTML = Object.entries(kinds).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("#logDate").value = new Date().toISOString().slice(0, 10);
    const cvOk = () => { const ok = ["skill", "cert"].includes($("#logKind").value); $("#logCvLine").hidden = !ok; if (!ok) $("#logCv").checked = false; };
    $("#logKind").onchange = cvOk; cvOk();
    $("#logAdd").onclick = async () => {
      const text = $("#logText").value.trim();
      if (!text) { $("#logText").classList.add("invalid"); $("#logText").focus(); return; }
      $("#logText").classList.remove("invalid");
      const useOnCv = $("#logCv").checked;
      await api("/api/career/log", { body: { kind: $("#logKind").value, text, date: $("#logDate").value, use_on_cv: useOnCv } });
      $("#logText").value = ""; $("#logCv").checked = false;
      toast($("#logKind").value === "skill" ? "Logged. Skills marked for CVs are in your profile now." : "Logged. Map your routes again to use it.");
      CAREER = await api("/api/career"); renderLog();
      if (useOnCv) { S.state = await api("/api/state"); S.prefs = S.state.profile.preferences; }
    };
  }
  const rows = (CAREER.log || []).slice().reverse();
  const stale = CAREER.map && rows.some((r) => r.date > CAREER.map.generated_at.slice(0, 10) || (r.id && !CAREER.map.generated_at));
  $("#logHint").textContent = rows.length ? `${rows.length}${stale ? ", newer than the map" : ""}` : "";
  const kindTone = { doing: "tag--warn", goal: "tag--warn", skill: "tag--ok", cert: "tag--ok" };
  $("#logList").innerHTML = rows.map((r) => `<div class="item" style="align-items:flex-start"><span class="tag ${kindTone[r.kind] || ""}" style="margin-top:2px">${esc(kinds[r.kind] || r.kind)}</span>
      <span class="item__body"><span class="item__text">${esc(r.text)}</span><span class="item__meta">${esc(r.date)}${r.use_on_cv ? ", on your CVs" : ""}</span></span>
      <button class="icon-btn icon-btn--sm" data-dellog="${r.id}" aria-label="Remove" title="Remove">${ICON.x}</button></div>`).join("") || `<p class="type-meta">Nothing logged yet.</p>`;
  $$("[data-dellog]").forEach((b) => (b.onclick = async () => { await api(`/api/career/log/${b.dataset.dellog}`, { method: "DELETE" }); CAREER = await api("/api/career"); renderLog(); }));
}

function renderGoal() {
  const g = CAREER.goal || {};
  if (!$("#goalSave").onclick) {
    $("#goalSave").onclick = async () => {
      await api("/api/career/goal", { body: { role: $("#goalRole").value, year: $("#goalYear").value, salary: $("#goalSalary").value, unit: $("#goalUnit").value } });
      CAREER = await api("/api/career");
      window.Career.render($("#flow"), CAREER.history, CAREER.map, S.state.profile.experience_years, onCareerClick, null, CAREER.goal);
      renderSalary();
      toast("Goal saved. Map your routes again so they aim at it.");
    };
  }
  $("#goalRole").value = g.role || ""; $("#goalYear").value = g.year || ""; $("#goalSalary").value = g.salary ?? ""; $("#goalUnit").value = g.unit || "LPA";
}

function renderTrends() {
  const m = CAREER.map;
  $("#trendsBox").innerHTML = m && (m.trends || []).length ? `<h3 class="eyebrow">What's moving in your market</h3><ul class="bullets">
    ${m.trends.map((t) => `<li>${esc(t.text)}${t.source ? ` <a href="${esc(t.source)}" target="_blank" rel="noopener" class="text-3">source</a>` : ""}</li>`).join("")}</ul>` : "";
}

// Salary chart: three series (your pay, your pace, what the goal needs), one axis, line styles + end labels.
const CHART = { pay: "#e5fe40", pace: "#4fe3a3", goal: "#ffab7c", grid: "rgba(255,255,255,.1)", label: "rgba(255,255,255,.5)", ink: "rgba(255,255,255,.7)", bg: "#121212" };
function renderSalary() {
  const P = CAREER.projection || {}, rows = CAREER.salaries || [];
  if (!$("#salAdd").onclick) {
    $("#salYear").value = new Date().getFullYear();
    $("#salAdd").onclick = async () => {
      try {
        await api("/api/career/salary", { body: { year: $("#salYear").value, amount: $("#salAmount").value, unit: $("#salUnit").value } });
        $("#salAmount").value = ""; CAREER = await api("/api/career"); renderSalary();
      } catch (e) { toast(e.message, 4000); }
    };
  }
  $("#salaryList").innerHTML = rows.slice().reverse().map((r) => `<div class="item"><span class="type-h3 num">${r.year}</span><span class="item__body num text-2">${r.amount} ${esc(r.unit)}</span>
    <button class="icon-btn icon-btn--sm" data-delsal="${r.id}" aria-label="Remove ${r.year}" title="Remove">${ICON.x}</button></div>`).join("");
  $$("[data-delsal]").forEach((b) => (b.onclick = async () => { await api(`/api/career/salary/${b.dataset.delsal}`, { method: "DELETE" }); CAREER = await api("/api/career"); renderSalary(); }));
  const g = CAREER.goal || {};
  const notes = [];
  if (!rows.length) notes.push("Add your yearly CTC for a few past years to see your growth and where it leads.");
  if (P.mixed_units) notes.push("Your entries use different currencies; the projection uses the latest one only.");
  if (P.past_growth !== undefined) notes.push(`Your pay has grown ${P.past_growth}% a year.`);
  if (P.projected_at_goal_year) notes.push(`At that pace you reach ${P.projected_at_goal_year} ${P.unit} by ${g.year}.`);
  if (P.needed_growth !== undefined) notes.push(`Your goal of ${g.salary} ${g.unit} by ${g.year} needs ${P.needed_growth}% a year from now${P.past_growth !== undefined ? (P.needed_growth > P.past_growth ? ", faster than your pace so far: that usually takes a role change, not raises" : ", within your pace so far") : ""}.`);
  $("#salaryNote").textContent = notes.join(" ");
  const series = [["Your pay", P.actual, CHART.pay, ""], ["At your pace", P.projected, CHART.pace, "6 5"], ["What the goal needs", P.needed, CHART.goal, "2 4"]].filter((s) => (s[1] || []).length > 1 || (s[0] === "Your pay" && (s[1] || []).length));
  if (!series.length) { $("#salaryChart").innerHTML = ""; return; }
  const W = 380, H = 190, L = 40, R = 86, T = 10, B = 24;
  const all = series.flatMap((s) => s[1]);
  const x0 = Math.min(...all.map((p) => p[0])), x1 = Math.max(...all.map((p) => p[0]), x0 + 1);
  const yMax = Math.max(...all.map((p) => p[1])) * 1.1;
  const sx = (x) => L + ((x - x0) / (x1 - x0)) * (W - L - R), sy = (y) => T + (1 - y / yMax) * (H - T - B);
  const ticks = [0, 0.5, 1].map((f) => Math.round(yMax * f / 1.1));
  const years = [...new Set(all.map((p) => p[0]))].sort((a, b) => a - b);
  const xt = years.filter((y, i) => i === 0 || i === years.length - 1 || (y - x0) % Math.max(1, Math.round((x1 - x0) / 4)) === 0);
  const path = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("");
  $("#salaryChart").innerHTML = `<div class="chart"><div class="chart__legend">${series.map((s) => `<span><i style="border-color:${s[2]};border-top-style:${s[3] ? (s[3] === "2 4" ? "dotted" : "dashed") : "solid"}"></i>${s[0]}</span>`).join("")}</div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Salary over time">
      ${ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${sy(t)}" y2="${sy(t)}" stroke="${CHART.grid}" stroke-width="1"/><text x="${L - 6}" y="${sy(t) + 4}" text-anchor="end" fill="${CHART.label}" font-size="10">${t}</text>`).join("")}
      ${xt.map((y) => `<text x="${sx(y)}" y="${H - 6}" text-anchor="middle" fill="${CHART.label}" font-size="10">${y}</text>`).join("")}
      ${series.map((s) => `<path d="${path(s[1])}" fill="none" stroke="${s[2]}" stroke-width="2" stroke-linejoin="round" ${s[3] ? `stroke-dasharray="${s[3]}"` : ""}/>`).join("")}
      ${(P.actual || []).map((p) => `<rect x="${sx(p[0]) - 4}" y="${sy(p[1]) - 4}" width="8" height="8" fill="${CHART.pay}" stroke="${CHART.bg}" stroke-width="2"/>`).join("")}
      ${series.map((s) => { const e = s[1][s[1].length - 1]; return `<text x="${sx(e[0]) + 6}" y="${sy(e[1]) + 4}" fill="${CHART.ink}" font-size="10.5" font-weight="700">${e[1]} ${esc(P.unit || "")}</text>`; }).join("")}
      <line id="salX" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="${CHART.label}" stroke-width="1" opacity="0"/>
      <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" id="salHit"/>
    </svg><div class="chart__tip" id="salTip" hidden></div></div>`;
  const svg = $("#salaryChart svg"), tip = $("#salTip"), cross = $("#salX");
  $("#salHit").onmousemove = (e) => {
    const r = svg.getBoundingClientRect(), xv = x0 + ((e.clientX - r.left) * (W / r.width) - L) / (W - L - R) * (x1 - x0);
    const yr = years.reduce((a, b) => (Math.abs(b - xv) < Math.abs(a - xv) ? b : a), years[0]);
    const vals = series.map((s) => [s[0], (s[1].find((p) => p[0] === yr) || [])[1], s[2]]).filter((v) => v[1] !== undefined);
    cross.setAttribute("x1", sx(yr)); cross.setAttribute("x2", sx(yr)); cross.setAttribute("opacity", "1");
    tip.hidden = false;
    tip.innerHTML = `<b>${yr}</b>${vals.map((v) => `<div><span style="color:${v[2]}">■</span> ${esc(v[0])}: ${v[1]} ${esc(P.unit || "")}</div>`).join("")}`;
    tip.style.left = Math.min(r.width - 150, (sx(yr) / W) * r.width + 10) + "px"; tip.style.top = "30px";
  };
  $("#salHit").onmouseleave = () => { tip.hidden = true; cross.setAttribute("opacity", "0"); };
}

async function generateCareer() {
  $("#careerBusy").hidden = false; $("#careerGen").disabled = true;
  try {
    const { job } = await api("/api/career/generate", { body: {} });
    await pollJob(job, (l) => ($("#careerBusyMsg").textContent = l.msg));
    $("#stepPanel").innerHTML = "";
    await loadCareer();
    toast("Routes mapped. Click a card for what it takes and the jobs for it.");
  } catch (e) { toast(e.message, 5000); }
  finally { $("#careerBusy").hidden = true; $("#careerGen").disabled = false; }
}

// ================= settings drawer =================
function openDrawer() { FX.show($("#scrim"), "scrim"); FX.show($("#drawer"), "drawer"); fillDrawer(); }
function closeDrawer() { FX.hide($("#drawer"), "drawer"); FX.hide($("#scrim"), "scrim"); }

function wireDrawer() {
  $("#openSettings").onclick = openDrawer;
  $("#closeSettings").onclick = closeDrawer;
  $("#scrim").onclick = closeDrawer;
  const st = S.state;
  $("#dMarket").innerHTML = `<option value="auto">Detect from the job's location</option>` + Object.entries(st.markets).map(([k, m]) => `<option value="${k}">${esc(m.label)}</option>`).join("");
  $("#dTrack").innerHTML = `<option value="auto">Pick from the job title</option>` + Object.entries(st.tracks).map(([k, t]) => `<option value="${k}">${esc(t.label)}</option>`).join("");
  $("#dVariant").innerHTML = Object.entries(st.layouts).map(([k, l]) => `<option value="${k}">${esc(l.label)}</option>`).join("");
  $("#dEngine").innerHTML = st.engines.map((e) => `<option value="${e.id}" ${e.available ? "" : "disabled"}>${esc(e.label)}${e.available ? "" : " (not set up)"}</option>`).join("");
  const def = (id, key) => ($(id).onchange = async () => { await savePrefs({ defaults: { [key]: $(id).value } }); renderChips(); if (!S.app && !S.job) previewBase(); toast("Saved"); });
  def("#dMarket", "market"); def("#dTrack", "track"); def("#dVariant", "variant"); def("#dPages", "max_pages"); def("#dEngine", "engine");
  $("#dConc").onchange = async () => { await savePrefs({ concurrency: +$("#dConc").value }); toast("Saved"); };
  $("#dSenior").onchange = async () => { await savePrefs({ include_senior: $("#dSenior").checked }); toast("Saved. Discover is re-ranked."); };
  $("#dMaxYears").onchange = async () => { await savePrefs({ max_years_asked: +$("#dMaxYears").value }); toast(`Saved. Jobs asking more than ${$("#dMaxYears").value} years are hidden.`); };
  $("#dAvail").onchange = async () => {
    await api("/api/profile", { body: { availability: $("#dAvail").value } });
    S.state = await api("/api/state"); S.prefs = S.state.profile.preferences;
    toast("Availability saved. New CVs use it.");
  };
  $("#favBtn").onclick = addFavourite;
  $("#favAdd").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); addFavourite(); } };
  $("#addUrl").onclick = () => addSource("url", $("#srcUrl"));
  $("#addQuery").onclick = () => addSource("websearch", $("#srcQuery"));
  $("#savePeople").onclick = savePeople;
  $("#saveOffices").onclick = async () => { await savePrefs({ offices: $("#offices").value }); toast("Saved. Discover shows those areas now."); };
  $("#excludeBtn").onclick = async () => {
    const v = $("#excludeAdd").value.trim(); if (!v) return;
    await savePrefs({ exclude_skills: [...new Set([...(S.prefs.exclude_skills || []), v])] });
    $("#excludeAdd").value = ""; fillDrawer(); toast(`Jobs that need ${v} are now skipped`);
  };
}

async function fillDrawer() {
  const d = D();
  $("#dMarket").value = d.market; $("#dTrack").value = d.track; $("#dVariant").value = d.variant;
  $("#dPages").value = String(d.max_pages); $("#dEngine").value = d.engine; $("#dConc").value = String(S.prefs.concurrency || 2);
  $("#dSenior").checked = !!S.prefs.include_senior;
  $("#dMaxYears").value = String(S.prefs.max_years_asked || 11);
  $("#offices").value = S.prefs.offices || "";
  $("#dAvail").value = S.state.profile.basics.availability || "";
  const on = new Set(S.prefs.tracks || []);
  $("#rankTracks").innerHTML = Object.entries(S.state.tracks).map(([k, t]) => `<button data-v="${k}" class="chip ${on.has(k) ? "on" : ""}">${esc(t.label)}</button>`).join("");
  $("#rankTracks").onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    on.has(b.dataset.v) ? on.delete(b.dataset.v) : on.add(b.dataset.v);
    b.classList.toggle("on");
    await savePrefs({ tracks: [...on] });
  };
  $("#excludeList").innerHTML = (S.prefs.exclude_skills || []).map((s) => `<button class="chip chip--x" data-s="${esc(s)}" title="Stop skipping these">${esc(s)}</button>`).join("") || `<span class="type-meta">None</span>`;
  $("#excludeList").onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    await savePrefs({ exclude_skills: (S.prefs.exclude_skills || []).filter((x) => x !== b.dataset.s) });
    fillDrawer();
  };
  const conf = S.state.profile.confirmed_skills || [];
  $("#confirmed").innerHTML = conf.map((s) => `<button class="chip chip--x" data-s="${esc(s)}" title="Remove from your profile">${esc(s)}</button>`).join("") || `<span class="type-meta">None yet</span>`;
  $("#confirmed").onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const p = await api("/api/profile", { body: { unconfirm: [b.dataset.s] } });
    S.state.profile.confirmed_skills = p.confirmed_skills; fillDrawer();
  };
  const rows = await api("/api/sources");
  const people = rows.find((r) => r.kind === "people");
  $("#people").value = people ? people.value : "";
  $("#people").dataset.sid = people ? people.id : "";
  $("#favList").innerHTML = rows.filter((r) => r.favourite).map((r) => `<div class="item"><span class="day__star">${ICON.star}</span>
      <span class="item__body"><span class="item__title">${esc(r.company || r.label)}</span>
      <span class="item__meta ${r.last_error ? "item__meta--bad" : ""}" title="${esc(r.value)}">${r.last_error ? esc(r.last_error) : `Read from ${esc(r.found_via || "its careers page")}${r.last_scan ? `, ${r.last_count} jobs at the last scan` : ""}`}</span></span>
      <button class="icon-btn icon-btn--sm" data-del="${r.id}" aria-label="Remove ${esc(r.company || r.label)}" title="Remove">${ICON.x}</button></div>`).join("")
    || `<p class="type-meta">None yet.</p>`;
  $("#sourceList").innerHTML = rows.filter((r) => r.kind !== "people" && !r.favourite).map((r) => `<div class="item"><span class="item__body"><span class="item__title">${esc(r.label)}</span>
      <span class="item__meta ${r.last_error ? "item__meta--bad" : ""}" title="${esc(r.value)}">${r.last_error ? esc(r.last_error) : r.last_scan ? `${r.last_count} jobs, ${esc(r.method || "")}` : esc(r.value)}</span></span>
      <button class="icon-btn icon-btn--sm" data-del="${r.id}" aria-label="Remove ${esc(r.label)}" title="Remove">${ICON.x}</button></div>`).join("")
    || `<p class="type-meta">No sources yet.</p>`;
  $$("#sourceList [data-del], #favList [data-del]").forEach((b) => (b.onclick = async () => { await api(`/api/sources/${b.dataset.del}`, { method: "DELETE" }); fillDrawer(); }));
}

async function addFavourite() {
  const input = $("#favAdd"), v = input.value.trim(), btn = $("#favBtn");
  $("#favErr").hidden = true;
  if (!v) { input.focus(); return; }
  btn.disabled = true; btn.textContent = "Finding its jobs…";
  try {
    const r = await api("/api/favourites", { body: { value: v } });
    input.value = ""; fillDrawer();
    toast(`${r.company} added. Its jobs come from ${r.found_via}, scanned first on every run.`, 4500);
    if ($("#v-discover").classList.contains("on")) loadFound();
  } catch (e) { $("#favErr").textContent = e.message; $("#favErr").hidden = false; }
  finally { btn.disabled = false; btn.textContent = "Add"; }
}

async function addSource(kind, input) {
  const v = input.value.trim();
  $("#srcErr").hidden = true;
  if (!v) { input.focus(); return; }
  try {
    await api("/api/sources", { body: { kind, value: v } });
    input.value = ""; fillDrawer(); toast("Source added. It's included in the next scan.");
  } catch (e) { $("#srcErr").textContent = e.message; $("#srcErr").hidden = false; }
}

async function savePeople() {
  const v = $("#people").value.trim(), sid = $("#people").dataset.sid;
  if (!v && !sid) return;
  if (sid && !v) await api(`/api/sources/${sid}`, { method: "DELETE" });
  else if (sid) await api(`/api/sources/${sid}`, { method: "PUT", body: { value: v } });
  else await api("/api/sources", { body: { kind: "people", value: v } });
  toast("Saved. Each scan searches for their recent hiring posts.");
  fillDrawer();
}

boot().catch((e) => {
  document.body.insertAdjacentHTML("afterbegin", `<div class="notice notice--bad" style="margin:12px">${esc(e.message)}. Is the app running? Start it with start.bat.</div>`);
});
