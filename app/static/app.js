"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// Browser storage only holds conveniences (cover-letter drafts, list filters); it may be unavailable.
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

const S = { state: null, prefs: null, app: null, job: null, item: null, mode: "pdf", batch: [], selected: new Set() };

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
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && S.job && !S.item && $("#v-tailor").classList.contains("on") && document.activeElement !== $("#cmd")) tailorJob();
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

// ================= capsule: paste a link or a JD =================
function wireCapsule() {
  const cmd = $("#cmd");
  const size = () => { cmd.style.height = "auto"; cmd.style.height = Math.min(cmd.scrollHeight, 220) + "px"; };
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

function hintCapsule() {
  const v = $("#cmd").value.trim();
  const st = $("#cmdStatus");
  st.classList.remove("err");
  if (!v) st.innerHTML = `<span class="kbd">/</span> to focus. A link from any careers page, or the full description.`;
  else if (isUrl(v)) st.innerHTML = `<span class="kbd">Enter</span> to read the posting`;
  else st.innerHTML = `${v.split(/\s+/).length} words. <span class="kbd">Ctrl</span> <span class="kbd">Enter</span> to use this description`;
}

async function readInput() {
  const v = $("#cmd").value.trim();
  const st = $("#cmdStatus");
  if (!v) { $("#cmd").focus(); return; }
  if (!isUrl(v)) {
    if (v.length < 200) { st.classList.add("err"); st.textContent = "That's too short for a job description. Paste the whole posting, or its link."; return; }
    setJob({ role: "", company: "", location: "", job_id: "", posted: "", url: "", description: v, method: "pasted", market: "auto" });
    $("#cmd").value = ""; $("#cmd").style.height = "auto"; hintCapsule();
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
    $("#cmd").value = ""; $("#cmd").style.height = "auto"; hintCapsule();
  } catch (e) {
    st.classList.add("err"); st.textContent = e.message;
  } finally {
    cap.classList.remove("working");
    $("#cmdGo").disabled = false;
  }
}

function setJob(job) {
  S.job = job; S.app = null; S.item = null;
  renderJobCard();
  $("#insights").innerHTML = "";
  $("#emptyState").innerHTML = "";
  previewBase();
}

// ================= job card =================
function marketOptions(cur) {
  const auto = `<option value="auto" ${cur === "auto" ? "selected" : ""}>Market: auto</option>`;
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
    j.location && `<span class="fact">${esc(j.location)}</span>`,
    j.job_id && `<span class="fact">ID ${esc(j.job_id)}</span>`,
    j.posted && `<span class="fact ${daysAgo(j.posted) <= 3 ? "fresh" : ""}">Posted ${esc(ago(j.posted))}</span>`,
    `<span class="fact">${words} words${j.method === "pasted" ? ", pasted" : ""}</span>`,
  ].filter(Boolean).join("");
  $("#jobCard").innerHTML = `
    <div class="job">
      <div class="job-top">
        <span class="logo">${done && S.app.logo ? `<img src="/apps/${esc(S.app.id)}/logo" alt="">` : initial(j.company || j.role)}</span>
        <div class="job-fields">
          <input class="inline job-role" id="jRole" value="${esc(j.role)}" placeholder="Role (read from the description)" ${running || done ? "readonly" : ""}>
          <input class="inline job-company" id="jCompany" value="${esc(j.company)}" placeholder="Company (read from the description)" ${running || done ? "readonly" : ""}>
        </div>
        ${j.url ? `<a class="icon-btn" href="${esc(j.url)}" target="_blank" rel="noopener" title="Open the posting" aria-label="Open the posting"><svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></a>` : ""}
      </div>
      <div class="facts">${facts}${j.url ? `<a class="fact" href="${esc(j.url)}" target="_blank" rel="noopener" title="${esc(j.url)}">Open posting on ${esc((() => { try { return new URL(j.url).hostname.replace(/^www\./, ""); } catch { return "the site"; } })())}</a>` : ""}</div>
      ${running ? `<div class="progress"><div class="progress-line"></div><div class="progress-text">${esc(item.stage || "Working")}</div></div>`
        : failed ? `<div class="flag error">${esc(item.stage)}</div><div class="job-actions"><span class="hint"></span><button class="btn primary" id="jRetry">Try again</button></div>`
        : done ? (S.app.scores.after < S.app.scores.ceiling - 3
            ? `<div class="job-actions"><span class="hint">Your profile can now reach ${S.app.scores.ceiling}% for this job; this CV is at ${S.app.scores.after}%.</span><button class="btn" id="jRedo">Re-tailor</button></div>`
            : `<div class="job-actions"><span class="hint"></span><button class="btn ghost sm" id="jRedo" title="Rewrite this CV with your current profile">Re-tailor</button></div>`)
        : `<div class="job-actions">
            <select id="jMarket" aria-label="Market">${marketOptions(market)}</select>
            <select id="jTrack" aria-label="Career track"><option value="auto" ${D().track === "auto" ? "selected" : ""}>Track: from title</option>${Object.entries(S.state.tracks).map(([k, t]) => `<option value="${k}" ${k === D().track ? "selected" : ""}>${esc(t.label)}</option>`).join("")}</select>
            <span class="hint"></span>
            <button class="btn primary" id="jGo" title="Ctrl+Enter">Tailor</button>
          </div>`}
    </div>`;
  const go = $("#jGo");
  if (go) go.onclick = tailorJob;
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
    toast(`Tailored for ${S.app.company}. "What changed" is on the left.`, 4000);
  } else if (item.status === "error") {
    $("#pv").classList.remove("loading");
    renderJobCard();
  } else {
    $("#busyMsg").textContent = item.stage || "Tailoring";
    const t = $(".progress-text");
    if (t) t.textContent = item.stage || "Working";
    else renderJobCard();
  }
}

// ================= empty state =================
async function renderEmpty() {
  if (S.job) return;
  const rows = await api("/api/apps").catch(() => []);
  $("#emptyState").innerHTML = `
    <div class="empty-state">
      <h2>Tailor a CV in one paste.</h2>
      Paste a job link and the posting is read for you: company, role, location, job ID and the full description.
      Or paste the description itself. Your CV is rewritten from your own profile only, checked line by line, and saved as a PDF.
      ${rows.length ? `<div class="recent">${rows.slice(0, 5).map((r) => `
        <div class="recent-item" data-id="${esc(r.id)}"><span class="logo sm">${r.logo ? `<img src="/apps/${esc(r.id)}/logo" alt="">` : initial(r.company)}</span>
          <span class="t"><b>${esc(r.role)}</b><small>${esc(r.company)}, ${esc(r.status.toLowerCase())}, ${esc(r.created.slice(0, 10))}</small></span>
          <span class="hint num">${r.score_after}%</span></div>`).join("")}</div>` : ""}
    </div>`;
  $$(".recent-item").forEach((el) => (el.onclick = () => openApp(el.dataset.id)));
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
  $("#pvTitle").innerHTML = `<b>Your profile</b><span class="state base">not tailored yet</span><small>${esc(S.state.layouts[D().variant].label)}</small>`;
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
  $("#pvTitle").innerHTML = `<b>${esc(a.company)}</b><span class="state done">Tailored ${esc(when)}</span><small>${esc(a.role)}${a.edited ? ", edited by hand" : ""}</small>`;
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
    $("#edBody").innerHTML = `<p class="hint" style="padding:12px 0">Loading the sections…</p>`;
    ED = { ...(await api(`/api/apps/${S.app.id}/plan`)), appId: S.app.id };
    markDirty(false); $("#edState").textContent = "";
  }
  renderEditor();
}

function closeEditor() {
  if (edDirty && !confirm("Close the editor and discard your unsaved changes?")) return;
  $("#editor").hidden = true;
  $(".studio").classList.remove("editing");
  $("#editToggle").textContent = "Edit sections";
  if (edDirty) { ED = null; markDirty(false); }
}

function renderEditor() {
  const ta = (v, attrs) => `<textarea rows="1" ${attrs}>${esc(v)}</textarea>`;
  const row = (v, attrs, tools) => `<div class="ed-row">${ta(v, attrs)}<span class="tools">${tools}</span></div>`;
  $("#edBody").innerHTML = `
    <details class="ed-sec" open><summary>Headline</summary><div class="inner">
      <input type="text" data-f="headline" value="${esc(ED.headline)}"></div></details>
    <details class="ed-sec" open><summary>Professional summary</summary><div class="inner">${ta(ED.summary, 'data-f="summary"')}</div></details>
    <details class="ed-sec"><summary>Skills <small>${ED.skills.length} groups</small></summary><div class="inner">
      ${ED.skills.map((g, i) => `<span class="ed-label">Group ${i + 1}</span>
        ${row(g.label, `data-g="${i}" data-k="label"`, `<button data-delg="${i}" title="Remove this group" aria-label="Remove group">✕</button>`)}
        ${ta(g.items.join(", "), `data-g="${i}" data-k="items"`)}`).join("")}
      <button class="ed-add" data-addg>Add a skills group</button></div></details>
    ${ED.roles.map((r, ri) => `<details class="ed-sec" ${ri < 2 ? "open" : ""}><summary>${esc(r.title)}<small>${esc(r.company)}, ${esc(r.dates)}</small></summary><div class="inner">
      ${r.bullets.map((b, bi) => row(b, `data-r="${ri}" data-b="${bi}"`,
        `${bi ? `<button data-up="${ri}:${bi}" title="Move up" aria-label="Move up">↑</button>` : ""}<button data-del="${ri}:${bi}" title="Remove this bullet" aria-label="Remove bullet">✕</button>`)).join("")}
      <button class="ed-add" data-addb="${ri}">Add a bullet</button></div></details>`).join("")}
    <details class="ed-sec"><summary>Cover letter <small>${ED.cover_letter ? `${ED.cover_letter.split(/\s+/).length} words` : "none yet"}</small></summary><div class="inner">
      ${ta(ED.cover_letter || "", 'data-f="cover_letter" style="min-height:160px"')}</div></details>`;
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
  ED = null; edDirty = false; $("#editor").hidden = true; $(".studio").classList.remove("editing"); $("#editToggle").textContent = "Edit sections";
  const a = await api(`/api/apps/${id}`);
  S.app = a; S.item = null;
  S.job = { role: a.role, company: a.company, location: a.job?.location || "", job_id: a.job?.job_id || "", posted: a.job?.posted || "",
    url: a.job_url || a.job?.job_url || "", description: a.jd_text || "", method: "", market: a.market };
  S.app.logo = !!(await api("/api/apps").then((rows) => rows.find((r) => r.id === id)?.logo).catch(() => false));
  S.app.status = (await api("/api/apps").then((rows) => rows.find((r) => r.id === id)?.status).catch(() => "")) || "";
  $("#emptyState").innerHTML = "";
  renderJobCard(); renderInsights(); S.mode = "pdf"; showApp(); showView("tailor");
}

// ================= insights =================
const flagsHtml = (list) => (list || []).map((f) => `<div class="flag ${f.level === "error" ? "error" : "warn"}">${esc(f.text)}</div>`).join("");

function renderInsights() {
  const a = S.app;
  if (!a) { $("#insights").innerHTML = ""; return; }
  const flags = [...(a.plan_flags || []), ...(a.analysis_flags || [])];
  const req = a.keywords.filter((k) => k.tier === "required"), other = a.keywords.filter((k) => k.tier !== "required");
  const chip = (k) => `<span class="kw ${k.covered ? "hit" : "miss"} ${k.tier === "required" ? "req" : ""} ${k.in_profile ? "" : "noprof"}"
    title="${k.covered ? "In this CV" : k.in_profile ? "Your profile has it, this CV doesn't say it" : "Not in your profile"}">${esc(k.term)}</span>`;
  const passed = a.ats.filter((c) => c.ok).length;
  const gaps = a.gaps || [];
  const s = a.scores;
  const verdict = s.ceiling < 60
    ? `This CV covers <b>${s.after}%</b> of the job's keywords, and ${s.ceiling}% is the most your profile can truthfully reach. The job asks for a lot your profile doesn't show; check the gaps before you spend time on it.`
    : s.after >= s.ceiling - 2
      ? `This CV covers <b>${s.after}%</b> of the job's keywords, the most your profile can truthfully reach. Your current CV covers ${s.before}%.`
      : `This CV covers <b>${s.after}%</b> of the job's keywords; your profile could reach ${s.ceiling}%. Your current CV covers ${s.before}%.`;
  $("#insights").innerHTML = `
    <div class="block">
      <div class="verdict">${verdict}</div>
      <div class="meter" title="Before ${s.before}%, now ${s.after}%, ceiling ${s.ceiling}%">
        <span class="ceil" style="width:${s.ceiling}%"></span><span class="now" style="width:${s.after}%"></span><span class="was" style="left:${s.before}%"></span>
      </div>
      <div class="meter-legend"><span>Headline: ${esc(a.headline)}</span><span>${pages(a.pages)}, ${a.experience_years} years</span></div>
      ${flagsHtml(flags)}
    </div>
    <div class="block" id="changes"><h3>What changed</h3><div class="hint">Comparing with your profile…</div></div>
    ${(a.fit_notes || []).length ? `<details class="more" open><summary>What a recruiter will notice</summary><div class="inner"><ul style="margin:0;padding-left:18px;color:var(--text-2);font-size:13px;line-height:1.6">${a.fit_notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div></details>` : ""}
    <details class="more"><summary>Keywords and gaps <span class="aside">${a.keywords.filter((k) => k.covered).length} of ${a.keywords.length} covered</span></summary><div class="inner">
      <p class="hint" style="margin:0 0 8px">Mint: in this CV. Red: required and missing. Struck through: not in your profile.</p>
      ${req.length ? `<div class="kws">${req.map(chip).join("")}</div>` : ""}
      ${other.length ? `<div class="kws" style="margin-top:10px">${other.slice(0, 40).map(chip).join("")}</div>` : ""}
      ${(a.jd_terms || []).length ? `<p class="hint" style="margin:14px 0 6px">Also asked for in this JD (read by the writer, not counted in the score):</p>
        <div class="kws">${a.jd_terms.map(chip).join("")}</div>` : ""}
      ${gaps.length ? `<div style="margin-top:14px" id="gaps">${gaps.map((g) => `<label class="gap"><input type="checkbox" data-term="${esc(g.term)}"><span>${esc(g.term)}${g.must_have ? '<span class="must">required</span>' : ""}</span><span class="note">${esc(g.note)}</span></label>`).join("")}
        <button class="btn" id="confirmGaps" style="margin-top:10px">I have the ticked ones. Add them and re-tailor</button></div>` : ""}
    </div></details>
    <details class="more" id="letterBox"><summary>Cover letter <span class="aside" id="letterAside"></span></summary><div class="inner" id="letters"></div></details>
    ${a.recruiter_note ? `<details class="more"><summary>Note to the recruiter <span class="aside">${a.recruiter_note.length} of 300 characters</span></summary><div class="inner">
      <div class="text-box">${esc(a.recruiter_note)}</div><button class="btn sm ghost" id="copyNote" style="margin-top:8px">Copy</button></div></details>` : ""}
    <details class="more"><summary>ATS read-back <span class="aside">${passed} of ${a.ats.length} checks passed</span></summary><div class="inner">
      <ul class="checks">${a.ats.map((c) => `<li><span class="dot ${c.ok ? "ok" : c.level === "warn" ? "warn" : "bad"}"></span>${esc(c.label)}<span class="d" title="${esc(c.detail)}">${esc(c.detail)}</span></li>`).join("")}</ul>
      ${(a.trims || []).length ? `<p class="hint" style="margin-top:8px">Fitted to ${pages(a.options.max_pages)}: ${a.trims.map(esc).join("; ")}.</p>` : ""}
    </div></details>`;
  const cn = $("#copyNote"); if (cn) cn.onclick = () => copy(a.recruiter_note);
  const cg = $("#confirmGaps"); if (cg) cg.onclick = confirmGaps;
  renderLetters();
  loadChanges(a.id);
}

async function loadChanges(id) {
  let c;
  try { c = await api(`/api/apps/${id}/changes`); } catch { $("#changes").hidden = true; return; }
  if (!S.app || S.app.id !== id) return;
  const n = c.counts;
  const diffRow = (b) => `<div class="diff"><span class="tag ${b.status}">${b.status === "kept" ? "kept as is" : b.status}</span>${esc(b.after.replace(/\*\*/g, ""))}
    ${b.status !== "kept" ? b.before.map((x) => `<div class="was">${esc(x)}</div>`).join("") : ""}</div>`;
  $("#changes").innerHTML = `
    <h3>What changed <span class="sp"></span><button class="btn sm ghost" id="hl">Highlight in preview</button></h3>
    <div class="rows">
      <span>Headline</span><span>${c.headline.before !== c.headline.after ? `${esc(c.headline.before)} became <b>${esc(c.headline.after)}</b>` : `${esc(c.headline.after)}, unchanged`}</span>
      <span>Summary</span><span>Rewritten for this job</span>
      <span>Bullets</span><span class="counts"><span class="count rw"><b>${n.rewritten}</b> rewritten</span><span class="count mg"><b>${n.merged}</b> merged</span><span class="count"><b>${n.kept}</b> kept as is</span><span class="count"><b>${n.left_out}</b> left out</span></span>
      <span>Skills</span><span>${c.skills.after.length} groups, led by ${esc(c.skills.after[0]?.label || "")}</span>
      ${c.omitted_roles.length ? `<span>Roles</span><span>Left out: ${esc(c.omitted_roles.join(", "))}</span>` : ""}
    </div>
    ${c.roles.map((r) => `<details class="role"><summary>${esc(r.title)}, ${esc(r.company)}</summary>${r.bullets.map(diffRow).join("")}
      ${r.left_out.length ? `<div class="diff"><span class="tag">left out for this job</span>${r.left_out.map((x) => `<div class="was">${esc(x)}</div>`).join("")}</div>` : ""}</details>`).join("")}`;
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
  $("#letterAside").textContent = drafts.length ? `${drafts.length} voice${drafts.length > 1 ? "s" : ""} tried` : "first draft ready";
  box.innerHTML = `
    <div class="voices">${Object.entries(S.state.voices).map(([k, v]) => `<button class="btn sm ghost" data-voice="${k}" title="${esc(v.best_for)}">${esc(v.label)}</button>`).join("")}</div>
    <div class="hint" id="voiceBusy" hidden></div>
    ${all.map((d, i) => `<div class="letter ${d.kept ? "kept" : ""}" data-i="${i}"><header><b>${esc(d.label)}</b>${d.words ? `<span class="hint">${d.words} words</span>` : ""}<span class="sp"></span>
      <button class="btn sm ghost" data-act="copy">Copy</button>${d.voice !== "saved" ? `<button class="btn sm ghost" data-act="keep">${d.kept ? "Kept" : "Keep"}</button><button class="btn sm ghost" data-act="skip">Skip</button>` : ""}</header>
      <div class="text-box">${esc(d.text)}</div>${flagsHtml(d.flags)}</div>`).join("")}`;
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
    if (S.app && S.app.id === a.id) { renderLetters(); $("#letterBox").open = true; }
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
  line.innerHTML = `<div class="progress-line"></div><div class="progress-text">Scanning your sources</div>`;
  btn.disabled = true;
  try {
    const { job } = await api("/api/scan", { body: { days: +$("#days").value } });
    const res = await pollJob(job, (l) => ($(".progress-text", line).textContent = l.msg));
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
  $("#areaBar").innerHTML = `<span class="lbl">Region</span>
    ${REGIONS.map(([k, label]) => `<button data-reg="${k}" class="${regions.has(k) ? "on" : ""}" ${k === "world" ? 'title="UK, EU, the Gulf and Singapore: your visa targets. US roles stay out."' : ""}>${label}</button>`).join("")}
    ${regions.has("bengaluru") ? `<span class="lbl" style="margin-left:14px">Bengaluru area</span>
      ${(S.state.areas.Bengaluru || []).map((z) => `<button data-z="${esc(z)}" class="${sel.has(z) ? "on" : ""}">${esc(z)}</button>`).join("")}
      ${sel.size ? `<label><input type="checkbox" id="areaStrict" ${strict ? "checked" : ""}> Hide Bengaluru jobs that don't say where</label>` : ""}` : ""}`;
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
    return `<div class="jr ${S.selected.has(j.id) ? "sel" : ""}" data-id="${j.id}">
      <input type="checkbox" ${S.selected.has(j.id) ? "checked" : ""} aria-label="Select ${esc(j.title)}">
      <span class="logo sm">${initial(j.company || j.source)}</span>
      <span class="t"><b>${esc(j.title)}${isNew(j) ? ` <span class="new-tag">New</span>` : ""}</b><small>${esc(j.company || j.source)}${j.location ? `, ${esc(j.location)}` : ""}${j.remote ? ", remote" : ""}${(j.areas || []).length ? `, <span class="zone">${esc(j.areas.join(" or "))}</span>` : ""}</small></span>
      <span class="when ${d === null ? "unknown" : d <= 3 ? "new" : ""}">${d === null ? "Date unknown" : ago(j.posted)}</span>
      <span class="m" title="Keyword match your profile can reach for this job (the same measure as after tailoring)"><span class="mbar"><i style="width:${j.match}%"></i></span><span class="n num">${j.match}%</span></span>
      <a class="out" href="${esc(j.url)}" target="_blank" rel="noopener" title="Open the posting" aria-label="Open the posting"><svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></a>
      <span class="why">${esc((j.reasons || []).join(", "))}, via ${esc(j.source)}</span>
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
        return `<details class="day" ${i === 0 ? "open" : ""}><summary><span>${dayLabel(d)}</span><span class="day-n">${list.length} job${list.length === 1 ? "" : "s"}</span><span class="day-meta">${meta}</span></summary>
          <div class="jobs">${dated.map(row).join("")}</div>
          ${unknown.length ? `<div class="group-title">Posting date unknown. Check the posting before you spend time on it.</div><div class="jobs">${unknown.map(row).join("")}</div>` : ""}</details>`;
      }).join("")
    : `<div class="empty">${sources.length ? "Nothing fresh at this match level. Widen the window or lower the match, or scan again." : "No sources yet. Open <b>Settings</b> and add a careers page, like https://jobs.lever.co/cred."}</div>`;
  $$(".jr").forEach((el) => {
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
  $("#pipeLede").textContent = `${rows.length} CV${rows.length === 1 ? "" : "s"} tailored, ${applied} applied${inflight.filter((b) => b.status !== "error").length ? `, ${inflight.filter((b) => b.status !== "error").length} being written now` : ""}.`;
  const statusSel = (r) => `<select data-status="${esc(r.id)}" aria-label="Status">${S.state.statuses.map((s) => `<option ${s === r.status ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>`;
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
  const appCard = (r) => `<div class="card" data-open="${esc(r.id)}">
      <div class="top"><span class="logo sm">${r.logo ? `<img src="/apps/${esc(r.id)}/logo" alt="">` : initial(r.company)}</span>
        <span class="t"><b>${esc(r.company)}</b><small>${esc(r.role)}</small></span></div>
      <div class="meta"><span class="num">${r.score_after}%</span><span>${esc(r.applied_on || r.created.slice(0, 10))}</span><span class="sp"></span>${statusSel(r)}</div>
      <div class="acts">
        ${r.job_url ? `<a class="btn sm primary" href="${esc(r.job_url)}" target="_blank" rel="noopener" data-stop title="${esc(r.job_url)}">Open posting</a>` : `<span class="hint">No link saved</span>`}
        <a class="btn sm ghost" href="/apps/${esc(r.id)}/pdf?download=1" data-stop title="${esc(r.pdf)}">PDF</a>
        ${r.status === "Generated" ? `<button class="btn sm ghost" data-applied="${esc(r.id)}" data-stop>Mark applied</button>` : ""}
      </div>
      ${r.job_url ? `<div class="src-host">${esc(host(r.job_url))}${r.job_id ? `, ID ${esc(r.job_id)}` : ""}</div>` : ""}</div>`;
  const flightCard = (b) => `<div class="card ${b.status === "error" ? "err" : ""}">
      <div class="top"><span class="logo sm">${initial(b.company || b.role)}</span><span class="t"><b>${esc(b.company || "Reading the posting")}</b><small>${esc(b.role || "")}</small></span></div>
      ${b.status === "error" ? "" : `<div class="progress-line" style="margin-top:10px"></div>`}
      <div class="stage-txt">${esc(b.status === "queued" ? "Waiting for a free slot" : b.stage || "Working")}</div>
      ${b.status === "error" ? `<div class="acts"><button class="btn sm" data-retry="${esc(b.id)}">Try again</button><button class="btn sm ghost" data-remove="${esc(b.id)}">Remove</button></div>` : ""}</div>`;
  $("#board").innerHTML = cols.map(([name, list, none], i) => `<div class="col"><h2>${name} <span class="n num">${list.length}</span></h2>
      <div class="lane">${list.length ? list.map(i === 0 ? flightCard : appCard).join("") : `<div class="none">${none}</div>`}</div></div>`).join("");
  $("#closed").innerHTML = closed.length ? `<details class="closed"><summary>Closed (${closed.length})</summary><div class="list">${closed.map(appCard).join("")}</div></details>` : "";
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
  if (!$("#stepPanel").innerHTML) $("#stepPanel").innerHTML = `<p class="hint">${m ? "Click any card to see what that step takes, what you already have, and the jobs for it." : "Press <b>Map my routes</b>: the writer reads your whole history and your growth log, then lays out five or six routes."}</p>`;
  renderLog();
}

function onCareerClick(d) {
  const p = $("#stepPanel");
  if (d.type === "history") {
    const x = d.item;
    p.innerHTML = `<div class="route">${x.kind === "project" ? "Project" : x.kind === "education" ? "Education" : "Role"}</div>
      <h2>${esc(x.title)}</h2><div class="meta">${esc(x.org)}, ${esc(x.when)}</div>
      ${(x.highlights || []).length ? `<ul>${x.highlights.map((t) => `<li>${esc(t.replace(/\*\*/g, ""))}</li>`).join("")}</ul>` : ""}`;
    return;
  }
  if (d.type === "now") {
    p.innerHTML = `<h2>You, now</h2><div class="meta">${esc(S.state.profile.experience_years)} years of experience</div>
      <div class="why">${esc((CAREER.map && CAREER.map.summary) || "Map your routes to see where this goes.")}</div>`;
    return;
  }
  if (d.type === "goal") {
    const m = CAREER.map || {};
    const reach = (m.paths || []).filter((x) => x.reaches_goal).map((x) => x.name);
    p.innerHTML = `<div class="route">Your goal</div><h2>${esc(d.title)}</h2><div class="meta">${esc(d.sub)} ${esc(d.when)}</div>
      <div class="why">${esc(m.goal_assessment || "Map your routes again to see which ones reach this goal.")}</div>
      ${reach.length ? `<h4>Routes that lead here</h4><ul>${reach.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}`;
    return;
  }
  const s = d.step, path = d.path;
  const list = (items, cls) => (items || []).length ? `<ul class="${cls}">${items.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : `<p class="hint">Nothing listed.</p>`;
  p.style.setProperty("--c", d.color);
  p.innerHTML = `<div class="route">${esc(path.name)}, ${esc(path.fit)} fit</div>
    <h2>${esc(s.title)}</h2><div class="meta">${esc(s.level)}${s.when ? `, ${esc(s.when)}` : ""}${s.typical_tenure ? `, typically ${esc(s.typical_tenure)}` : ""}</div>
    <div class="why">${esc(path.why)}</div>
    <h4>What employers screen for</h4>${list(s.what_it_takes, "")}
    <h4>What you already have</h4>${list(s.you_have, "have")}
    <h4>What to build</h4>${list(s.to_build, "build")}
    ${(s.certifications || []).length ? `<h4>Certifications</h4><ul>${s.certifications.map((c) => `<li>${esc(c.name)}<span class="cert-tag ${c.need === "screened" ? "screened" : "helps"}">${c.need === "screened" ? "employers screen for it" : "helps"}</span>${c.why ? `<div class="hint">${esc(c.why)}</div>` : ""}</li>`).join("")}</ul>` : ""}
    ${(s.projects || []).length ? `<h4>Projects to take on</h4>${list(s.projects, "build")}` : ""}
    ${s.salary_band ? `<h4>Pay at this step</h4><div class="band">${s.salary_band.low}–${s.salary_band.high} ${esc(s.salary_band.unit)}<a href="${esc(s.salary_band.source)}" target="_blank" rel="noopener">source</a></div>` : `<h4>Pay at this step</h4><p class="hint">No sourced range found, so none is shown.</p>`}
    ${s.ladder_note ? `<h4>How companies name this level</h4><p class="why">${esc(s.ladder_note)}</p>` : ""}
    ${(path.promotion_playbook || []).length ? `<h4>How people move up on this route</h4>${list(path.promotion_playbook, "")}` : ""}
    ${(path.sources || []).length ? `<p class="hint" style="margin-top:8px">Sources: ${path.sources.map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener">${i + 1}</a>`).join(", ")}</p>` : ""}
    <div class="acts"><button class="btn primary sm" id="stepJobs">Jobs from your scans</button><button class="btn ghost sm" id="stepWeb">Search the web</button>
      <button class="btn ghost sm" id="stepSource" title="Add a web search for these titles to Discover">Track in Discover</button></div>
    <p class="hint" style="margin-top:6px">Searches: ${esc((s.search_titles || []).join(", "))}</p>
    <div class="mini-jobs" id="stepResults"></div>`;
  const titles = s.search_titles && s.search_titles.length ? s.search_titles : [s.title];
  const showJobs = (jobs, fromWeb) => {
    $("#stepResults").innerHTML = jobs.length ? jobs.map((j) => `<div class="mini-job"><span><b>${esc(j.title)}</b></span>
        <span class="r">${j.match !== undefined ? `<span class="num hint">${j.match}%</span>` : ""}<a class="btn sm ghost" href="${esc(j.url)}" target="_blank" rel="noopener">Open</a><button class="btn sm" data-tl="${esc(fromWeb ? j.url : j.id)}">Tailor</button></span>
        <small>${esc(j.company || "")}${j.location ? `, ${esc(j.location)}` : ""}${j.posted ? `, posted ${esc(ago(j.posted))}` : ""}</small></div>`).join("")
      : `<p class="hint">${fromWeb ? "The web search found nothing fresh for these titles." : "None in your recent scans. Try Search the web, or Track in Discover."}</p>`;
    $$("[data-tl]", $("#stepResults")).forEach((b) => (b.onclick = async () => {
      await api("/api/batch", { body: { items: [fromWeb ? { url: b.dataset.tl } : { found_id: b.dataset.tl }] } });
      b.textContent = "Queued"; b.disabled = true; pollBatch(true);
      toast("Tailoring it now. Follow it in Pipeline.");
    }));
  };
  $("#stepJobs").onclick = async () => { $("#stepResults").innerHTML = `<p class="hint">Looking through your scans…</p>`; showJobs(await api("/api/career/jobs", { body: { titles, days: 30 } }), false); };
  $("#stepWeb").onclick = async () => {
    $("#stepResults").innerHTML = `<div class="progress-line"></div><p class="hint">Searching the web for these titles in your regions, about a minute…</p>`;
    try { const { job } = await api("/api/career/search", { body: { titles } }); const res = await pollJob(job, () => {}); showJobs(res.jobs.filter((j) => !j.excluded), true); }
    catch (e) { $("#stepResults").innerHTML = `<p class="hint">${esc(e.message)}</p>`; }
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
      await api("/api/career/log", { body: { kind: $("#logKind").value, text, date: $("#logDate").value, use_on_cv: $("#logCv").checked } });
      $("#logText").value = ""; $("#logCv").checked = false;
      toast($("#logKind").value === "skill" ? "Logged. Skills marked for CVs are in your profile now." : "Logged. Map your routes again to use it.");
      CAREER = await api("/api/career"); renderLog();
      if ($("#logCv").checked) { S.state = await api("/api/state"); }
    };
  }
  const rows = (CAREER.log || []).slice().reverse();
  const stale = CAREER.map && rows.some((r) => r.date > CAREER.map.generated_at.slice(0, 10) || (r.id && !CAREER.map.generated_at));
  $("#logHint").textContent = rows.length ? `${rows.length} entr${rows.length === 1 ? "y" : "ies"}${stale ? ", newer than the map" : ""}` : "";
  $("#logList").innerHTML = rows.map((r) => `<div class="log-item"><span class="k ${r.kind}">${esc(kinds[r.kind] || r.kind)}</span><span>${esc(r.text)}</span>
      <button class="icon-btn" data-dellog="${r.id}" aria-label="Remove" title="Remove"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></button>
      <small>${esc(r.date)}${r.use_on_cv ? ", on your CVs" : ""}</small></div>`).join("") || `<p class="hint">Nothing logged yet.</p>`;
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
  $("#trendsBox").innerHTML = m && (m.trends || []).length ? `<h3>What's moving in your market</h3><ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.55">
    ${m.trends.map((t) => `<li>${esc(t.text)}${t.source ? ` <a href="${esc(t.source)}" target="_blank" rel="noopener" style="font-size:12px">source</a>` : ""}</li>`).join("")}</ul>` : "";
}

// Salary chart: three series (your pay, your pace, what the goal needs), one axis, line styles + end labels.
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
  $("#salaryList").innerHTML = rows.slice().reverse().map((r) => `<div class="sal-row"><span class="num">${r.year}</span><span class="num">${r.amount} ${esc(r.unit)}</span><span class="sp"></span>
    <button class="icon-btn" data-delsal="${r.id}" aria-label="Remove ${r.year}" title="Remove"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></button></div>`).join("");
  $$("[data-delsal]").forEach((b) => (b.onclick = async () => { await api(`/api/career/salary/${b.dataset.delsal}`, { method: "DELETE" }); CAREER = await api("/api/career"); renderSalary(); }));
  const g = CAREER.goal || {};
  const notes = [];
  if (!rows.length) notes.push("Add your yearly CTC for a few past years to see your growth and where it leads.");
  if (P.mixed_units) notes.push("Your entries use different currencies; the projection uses the latest one only.");
  if (P.past_growth !== undefined) notes.push(`Your pay has grown ${P.past_growth}% a year.`);
  if (P.projected_at_goal_year) notes.push(`At that pace you reach ${P.projected_at_goal_year} ${P.unit} by ${g.year}.`);
  if (P.needed_growth !== undefined) notes.push(`Your goal of ${g.salary} ${g.unit} by ${g.year} needs ${P.needed_growth}% a year from now${P.past_growth !== undefined ? (P.needed_growth > P.past_growth ? ", faster than your pace so far: that usually takes a role change, not raises" : ", within your pace so far") : ""}.`);
  $("#salaryNote").textContent = notes.join(" ");
  const series = [["Your pay", P.actual, "#5f8fe6", ""], ["At your pace", P.projected, "#2ea67a", "6 5"], ["What the goal needs", P.needed, "#b8822a", "2 4"]].filter((s) => (s[1] || []).length > 1 || (s[0] === "Your pay" && (s[1] || []).length));
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
  $("#salaryChart").innerHTML = `<div class="chart"><div class="legend">${series.map((s) => `<span><i style="border-color:${s[2]};border-top-style:${s[3] ? (s[3] === "2 4" ? "dotted" : "dashed") : "solid"}"></i>${s[0]}</span>`).join("")}</div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Salary over time">
      ${ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${sy(t)}" y2="${sy(t)}" stroke="#222d44" stroke-width="1"/><text x="${L - 6}" y="${sy(t) + 4}" text-anchor="end" fill="#6e7c95" font-size="10">${t}</text>`).join("")}
      ${xt.map((y) => `<text x="${sx(y)}" y="${H - 6}" text-anchor="middle" fill="#6e7c95" font-size="10">${y}</text>`).join("")}
      ${series.map((s) => `<path d="${path(s[1])}" fill="none" stroke="${s[2]}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ${s[3] ? `stroke-dasharray="${s[3]}"` : ""}/>`).join("")}
      ${(P.actual || []).map((p) => `<circle cx="${sx(p[0])}" cy="${sy(p[1])}" r="4" fill="#5f8fe6" stroke="#121a29" stroke-width="2"/>`).join("")}
      ${series.map((s) => { const e = s[1][s[1].length - 1]; return `<text x="${sx(e[0]) + 6}" y="${sy(e[1]) + 4}" fill="#a3b0c6" font-size="10.5">${e[1]} ${esc(P.unit || "")}</text>`; }).join("")}
      <line id="salX" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="#6e7c95" stroke-width="1" opacity="0"/>
      <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" id="salHit"/>
    </svg><div class="tip" id="salTip" hidden></div></div>`;
  const svg = $("#salaryChart svg"), tip = $("#salTip"), cross = $("#salX");
  $("#salHit").onmousemove = (e) => {
    const r = svg.getBoundingClientRect(), xv = x0 + ((e.clientX - r.left) * (W / r.width) - L) / (W - L - R) * (x1 - x0);
    const yr = years.reduce((a, b) => (Math.abs(b - xv) < Math.abs(a - xv) ? b : a), years[0]);
    const vals = series.map((s) => [s[0], (s[1].find((p) => p[0] === yr) || [])[1], s[2]]).filter((v) => v[1] !== undefined);
    cross.setAttribute("x1", sx(yr)); cross.setAttribute("x2", sx(yr)); cross.setAttribute("opacity", "1");
    tip.hidden = false;
    tip.innerHTML = `<b>${yr}</b>${vals.map((v) => `<div><span style="color:${v[2]}">●</span> ${esc(v[0])}: ${v[1]} ${esc(P.unit || "")}</div>`).join("")}`;
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
  $("#rankTracks").innerHTML = Object.entries(S.state.tracks).map(([k, t]) => `<button data-v="${k}" class="${on.has(k) ? "on" : ""}">${esc(t.label)}</button>`).join("");
  $("#rankTracks").onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    on.has(b.dataset.v) ? on.delete(b.dataset.v) : on.add(b.dataset.v);
    b.classList.toggle("on");
    await savePrefs({ tracks: [...on] });
  };
  $("#excludeList").innerHTML = (S.prefs.exclude_skills || []).map((s) => `<button class="x" data-s="${esc(s)}" title="Stop skipping these">${esc(s)}</button>`).join("") || `<span class="hint">None</span>`;
  $("#excludeList").onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    await savePrefs({ exclude_skills: (S.prefs.exclude_skills || []).filter((x) => x !== b.dataset.s) });
    fillDrawer();
  };
  const conf = S.state.profile.confirmed_skills || [];
  $("#confirmed").innerHTML = conf.map((s) => `<button class="x" data-s="${esc(s)}" title="Remove from your profile">${esc(s)}</button>`).join("") || `<span class="hint">None yet</span>`;
  $("#confirmed").onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const p = await api("/api/profile", { body: { unconfirm: [b.dataset.s] } });
    S.state.profile.confirmed_skills = p.confirmed_skills; fillDrawer();
  };
  const rows = await api("/api/sources");
  const people = rows.find((r) => r.kind === "people");
  $("#people").value = people ? people.value : "";
  $("#people").dataset.sid = people ? people.id : "";
  $("#sourceList").innerHTML = rows.filter((r) => r.kind !== "people").map((r) => `<div class="src"><span>${esc(r.label)}</span>
      <button class="icon-btn" data-del="${r.id}" aria-label="Remove ${esc(r.label)}" title="Remove"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></button>
      <small class="${r.last_error ? "bad" : ""}" title="${esc(r.value)}">${r.last_error ? esc(r.last_error) : r.last_scan ? `${r.last_count} jobs, ${esc(r.method || "")}` : esc(r.value)}</small></div>`).join("")
    || `<p class="note">No sources yet.</p>`;
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
  document.body.insertAdjacentHTML("afterbegin", `<div class="flag error" style="margin:12px">${esc(e.message)}. Is the app running? Start it with start.bat.</div>`);
});
