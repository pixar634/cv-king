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
  arrow: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="square"/></svg>`,
};

const S = { state: null, prefs: null, app: null, job: null, item: null, mode: "pdf", batch: [], selected: new Set(), tab: store.get("cv.tab", "changes") };

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

function showView(v) {
  $$("#nav button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  $$(".view").forEach((s) => s.classList.toggle("on", s.id === `v-${v}`));
  if (v === "discover") loadFound();
  if (v === "pipeline") loadPipeline();
  if (v === "career") loadCareer();
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

function closeMenus() { $$(".menu").forEach((m) => (m.hidden = true)); }
function toggleMenu(sel) { const m = $(sel); const was = m.hidden; closeMenus(); m.hidden = !was; }

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

function setTab(name) {
  const btns = $$("#reportTabs [data-tab]");
  if (!btns.some((b) => b.dataset.tab === name)) name = "changes";
  S.tab = name; store.set("cv.tab", name);
  btns.forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  $$("#insights [data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== name));
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
  setTab(S.tab);
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
  $("#scanAll").onclick = scanNow;
  $("#dockClear").onclick = () => { S.selected.clear(); loadFound(); };
  $("#dockGo").onclick = tailorSelected;
}

async function scanNow() {
  const line = $("#scanLine"), btn = $("#scanAll");
  line.hidden = false;
  line.innerHTML = `<div class="progress"></div><div class="progress__text">Scanning your sources</div>`;
  btn.disabled = true;
  try {
    const { job } = await api("/api/scan", { body: { days: +$("#days").value } });
    const res = await pollJob(job, (l) => ($(".progress__text", line).textContent = l.msg));
    await loadFound();
    toast(`${res.jobs.length} fresh jobs found`);
  } catch (e) { toast(e.message, 4000); }
  finally { btn.disabled = false; line.hidden = true; }
}

// Area filter: a per-browser convenience, so it lives in local storage.
const REGIONS = [["remote", "Remote"], ["bengaluru", "Bengaluru"], ["india", "Rest of India"],
  ["world", "Rest of the world"]];
function renderAreaBar() {
  const sel = new Set(store.get("cv.areas", []));
  const strict = store.get("cv.areasStrict", false);
  const regions = new Set(S.prefs.regions || REGIONS.map((r) => r[0]));
  $("#areaBar").innerHTML = `<span class="eyebrow">Region</span>
    ${REGIONS.map(([k, label]) => `<button data-reg="${k}" class="chip ${regions.has(k) ? "on" : ""}" ${k === "world" ? 'title="UK, EU, the Gulf and Singapore: your visa targets. US roles stay out."' : ""}>${label}</button>`).join("")}
    ${regions.has("bengaluru") ? `<span class="eyebrow">Bengaluru area</span>
      ${(S.state.areas.Bengaluru || []).map((z) => `<button data-z="${esc(z)}" class="chip ${sel.has(z) ? "on" : ""}">${esc(z)}</button>`).join("")}
      ${sel.size ? `<label class="check"><input type="checkbox" id="areaStrict" ${strict ? "checked" : ""}> Hide Bengaluru jobs that don't say where</label>` : ""}` : ""}`;
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
  const [all, sources, scanLog] = await Promise.all([api(`/api/found?days=${$("#days").value}`), api("/api/sources"), api("/api/scans")]);
  const lastScan = scanLog.length ? scanLog[scanLog.length - 1] : null;
  const min = +$("#minMatch").value;
  const jobs = areaFilter(all.filter((j) => j.match >= min));
  if ($("#sortBy").value === "newest") jobs.sort((a, b) => (b.posted || "").localeCompare(a.posted || "") || b.match - a.match);
  FOUND = jobs;
  const last = sources.map((s) => s.last_scan).filter(Boolean).sort().pop();
  $("#discoverLede").textContent = sources.length
    ? `${jobs.length} fresh posting${jobs.length === 1 ? "" : "s"} from ${sources.length} sources. Match is the share of each job's keywords your profile can truthfully cover, the same number tailoring reaches.${last ? ` Last scanned ${last.slice(11, 16)}.` : ""}${all.length > jobs.length ? ` ${all.length - jobs.length} more below ${min}%.` : ""}`
    : "Add a careers page or job board in Settings, then scan.";
  const isNew = (j) => lastScan && (j.first_seen || "") >= lastScan.at.slice(0, 16) && scanLog.length > 1;
  const row = (j) => {
    const d = daysAgo(j.posted);
    return `<div class="job-row ${S.selected.has(j.id) ? "sel" : ""}" data-id="${j.id}">
      <input type="checkbox" ${S.selected.has(j.id) ? "checked" : ""} aria-label="Select ${esc(j.title)}">
      <span class="logo logo--sm">${initial(j.company || j.source)}</span>
      <span class="item__body"><span class="item__title">${isNew(j) ? `<span class="tag tag--accent tag--solid" style="margin-right:8px;vertical-align:2px">New</span>` : ""}${esc(j.title)}</span><span class="item__meta">${esc(j.company || j.source)}${j.location ? `, ${esc(j.location)}` : ""}${j.remote ? ", remote" : ""}${(j.areas || []).length ? `, <span class="zone">${esc(j.areas.join(" or "))}</span>` : ""}</span></span>
      <span class="job-row__when"><span class="tag ${d === null ? "tag--warn" : d <= 3 ? "tag--ok" : ""}">${d === null ? "Date unknown" : ago(j.posted)}</span></span>
      <span class="job-row__match ${tone(j.match)}" title="Keyword match your profile can reach for this job (the same measure as after tailoring)"><span class="meter meter--sm"><span class="meter__fill" style="width:${j.match}%"></span></span><span class="num">${j.match}%</span></span>
      <a class="icon-btn icon-btn--sm" href="${esc(j.url)}" target="_blank" rel="noopener" title="Open the posting" aria-label="Open the posting">${ICON.out}</a>
      <span class="job-row__why">${esc((j.reasons || []).join(", "))}, via ${esc(j.source)}</span>
    </div>`;
  };
  // Sections by the day the app first found each job, newest first; each says which scan it came from.
  const groups = {};
  for (const j of jobs) (groups[(j.first_seen || "").slice(0, 10) || "earlier"] ||= []).push(j);
  const dayLabel = (d) => {
    if (d === "earlier") return "Found earlier";
    const today = new Date().toISOString().slice(0, 10), y = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    const nice = new Date(d + "T00:00:00").toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
    return d === today ? `Found today, ${nice}` : d === y ? `Found yesterday, ${nice}` : `Found ${nice}`;
  };
  const scansOn = (d) => scanLog.filter((s) => s.at.slice(0, 10) === d);
  const days = Object.keys(groups).sort().reverse();
  $("#foundList").innerHTML = jobs.length
    ? days.map((d, i) => {
        const sc = scansOn(d);
        const meta = sc.length ? `${sc.length} scan${sc.length > 1 ? "s" : ""}, last at ${sc[sc.length - 1].at.slice(11, 16)}, ${sc.reduce((n, s) => n + s.new, 0)} new` : "";
        const list = groups[d];
        const dated = list.filter((j) => j.posted), unknown = list.filter((j) => !j.posted);
        return `<details class="disclosure disclosure--lg day" ${i === 0 ? "open" : ""}><summary><span>${dayLabel(d)}</span><span class="disclosure__sub num">${list.length} job${list.length === 1 ? "" : "s"}</span><span class="disclosure__aside">${meta}</span></summary>
          <div>${dated.map(row).join("")}</div>
          ${unknown.length ? `<div class="eyebrow group-title">Posting date unknown. Check the posting before you spend time on it.</div><div>${unknown.map(row).join("")}</div>` : ""}</details>`;
      }).join("")
    : `<div class="empty">${sources.length ? "Nothing fresh at this match level. Widen the window or lower the match, or scan again." : "No sources yet. Open <b>Settings</b> and add a careers page, like https://jobs.lever.co/cred."}</div>`;
  $$(".job-row").forEach((el) => {
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
  $("#dock").hidden = !n || !$("#v-discover").classList.contains("on");
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
  const cols = [
    ["Tailoring", inflight, "Jobs you send from Tailor or Discover appear here while they're written."],
    ["Tailored", by(["Generated"]), "Ready to send. Mark one applied when you do."],
    ["Applied", by(["Applied"]), "Nothing applied yet."],
    ["Interviews", by(["Recruiter call", "Interviewing", "Offer"]), "Calls and interviews land here."],
  ];
  const closed = by(["Rejected", "No response", "Withdrawn"]);
  const applied = rows.filter((r) => r.status !== "Generated").length;
  const writing = inflight.filter((b) => b.status !== "error").length;
  $("#pipeLede").textContent = `${rows.length} CV${rows.length === 1 ? "" : "s"} tailored, ${applied} applied${writing ? `, ${writing} being written now` : ""}.`;
  const statusSel = (r) => `<select class="control--sm" data-status="${esc(r.id)}" aria-label="Status">${S.state.statuses.map((s) => `<option ${s === r.status ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>`;
  const appCard = (r) => `<div class="card card--interactive app-card" data-open="${esc(r.id)}">
      <div class="item">${logoTile(r.id, r.logo, r.company)}
        <span class="item__body"><span class="item__title">${esc(r.company)}</span><span class="item__meta">${esc(r.role)}</span></span>
        <span class="item__aside">${scoreNum(r.score_after)}</span></div>
      <div class="app-card__meta"><span class="num">${esc(r.applied_on || r.created.slice(0, 10))}</span><span class="app-card__host" title="${esc(r.job_url || "")}${r.job_id ? `, ID ${esc(r.job_id)}` : ""}">${esc(host(r.job_url || ""))}</span>${statusSel(r)}</div>
      <div class="app-card__acts">
        ${r.job_url ? `<a class="btn btn--sm" href="${esc(r.job_url)}" target="_blank" rel="noopener" data-stop title="${esc(r.job_url)}">Open posting</a>` : `<span class="type-meta">No link saved</span>`}
        <a class="btn btn--sm btn--ghost" href="/apps/${esc(r.id)}/pdf?download=1" data-stop title="${esc(r.pdf)}">PDF</a>
        ${r.status === "Generated" ? `<button class="btn btn--sm btn--ghost" data-applied="${esc(r.id)}" data-stop>Mark applied</button>` : ""}
      </div></div>`;
  const flightCard = (b) => `<div class="card app-card ${b.status === "error" ? "err" : ""}">
      <div class="item"><span class="logo logo--sm">${initial(b.company || b.role)}</span><span class="item__body"><span class="item__title">${esc(b.company || "Reading the posting")}</span><span class="item__meta">${esc(b.role || "")}</span></span></div>
      ${b.status === "error" ? "" : `<div class="progress"></div>`}
      <div class="progress__text" style="margin-top:0">${esc(b.status === "queued" ? "Waiting for a free slot" : b.stage || "Working")}</div>
      ${b.status === "error" ? `<div class="app-card__acts"><button class="btn btn--sm btn--primary" data-retry="${esc(b.id)}">Try again</button><button class="btn btn--sm btn--ghost" data-remove="${esc(b.id)}">Remove</button></div>` : ""}</div>`;
  $("#board").innerHTML = cols.map(([name, list, none], i) => `<div class="board__col"><div class="board__head"><span class="eyebrow">${name}</span><span class="tag num">${list.length}</span></div>
      <div class="board__lane">${list.length ? list.map(i === 0 ? flightCard : appCard).join("") : `<div class="board__none">${none}</div>`}</div></div>`).join("");
  $("#closed").innerHTML = closed.length ? `<details class="disclosure closed"><summary>Closed<span class="disclosure__aside num">${closed.length}</span></summary><div class="closed__list">${closed.map(appCard).join("")}</div></details>` : "";
  $$("[data-open]").forEach((el) => (el.onclick = (e) => { if (!e.target.closest("select, [data-stop]")) openApp(el.dataset.open); }));
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
function openDrawer() { $("#drawer").hidden = false; $("#scrim").hidden = false; fillDrawer(); }
function closeDrawer() { $("#drawer").hidden = true; $("#scrim").hidden = true; }

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
  $("#sourceList").innerHTML = rows.filter((r) => r.kind !== "people").map((r) => `<div class="item"><span class="item__body"><span class="item__title">${esc(r.label)}</span>
      <span class="item__meta ${r.last_error ? "item__meta--bad" : ""}" title="${esc(r.value)}">${r.last_error ? esc(r.last_error) : r.last_scan ? `${r.last_count} jobs, ${esc(r.method || "")}` : esc(r.value)}</span></span>
      <button class="icon-btn icon-btn--sm" data-del="${r.id}" aria-label="Remove ${esc(r.label)}" title="Remove">${ICON.x}</button></div>`).join("")
    || `<p class="type-meta">No sources yet.</p>`;
  $$("[data-del]", $("#sourceList")).forEach((b) => (b.onclick = async () => { await api(`/api/sources/${b.dataset.del}`, { method: "DELETE" }); fillDrawer(); }));
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
