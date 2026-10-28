"use strict";
// The side panel. It reads the page you're on (through page.js) and talks to JobMagnet on this machine.
// On a job posting: read it and tailor a CV, with the scores. On an application form: fill it from your
// profile, attach the CV and cover letter, and submit only when you press Submit.
const SERVER = "http://127.0.0.1:8765";
const ALL_SITES = { origins: ["https://*/*", "http://*/*"] };
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const S = { tab: null, ctx: null, rows: [], reports: {}, items: {}, busy: {}, granted: false, warnedEmpty: false, session: null, view: null, li: {}, watch: {} };
chrome.permissions.contains(ALL_SITES).then((g) => (S.granted = g));

async function api(path, opts = {}) {
  const r = await fetch(SERVER + path, { method: opts.method || (opts.body ? "POST" : "GET"), credentials: "include",
    headers: opts.body ? { "Content-Type": "application/json" } : {}, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return opts.raw ? r : r.json();
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

// What the panel remembers per tab (this browser session only): the posting read, the tailoring, the form.
const key = (tabId) => `tab:${tabId}`;
async function memory(tabId) {
  try { return (await chrome.storage.session.get(key(tabId)))[key(tabId)] || {}; } catch { return {}; }
}
async function remember(tabId, patch) {
  const m = { ...(await memory(tabId)), ...patch };
  try { await chrome.storage.session.set({ [key(tabId)]: m }); } catch { /* storage unavailable */ }
  if (S.ctx && S.ctx.tab.id === tabId) S.ctx.mem = m;
  return m;
}

// ---------- Easy Apply session ----------
// You set the search and LinkedIn opens with Easy Apply jobs only. You click through the jobs yourself; the panel
// reads each one, tailors the ones you queue, and fills the Easy Apply form for you to check and submit. Nothing
// browses, clicks or submits on its own: LinkedIn bans that, and it's your account.
async function loadSession() {
  try { S.session = (await chrome.storage.local.get("session")).session || null; } catch { S.session = null; }
  return S.session;
}
async function saveSession(patch) {
  S.session = patch && { ...(S.session || {}), ...patch };
  try { await chrome.storage.local.set({ session: S.session }); } catch { /* storage unavailable */ }
  return S.session;
}
const MODES = [["remote", "Remote"], ["hybrid", "Hybrid"], ["on-site", "On-site"]];
const WITHIN = [[1, "Past 24 hours"], [3, "Past 3 days"], [7, "Past week"], [30, "Past month"]];

function sessionForm() {
  const s = S.session || {}, modes = s.modes || ["remote", "hybrid"];
  return `<div class="card"><span class="eyebrow">Easy Apply session</span>
    <p class="sub" style="margin:0">Opens LinkedIn's search with Easy Apply jobs only. Click through the jobs yourself: I read each one, tailor a CV for the ones you queue, and fill the form when you press Easy Apply. You check it and submit.</p>
    <label class="need"><span>Title</span><input id="sTitle" value="${esc(s.title || "")}" placeholder="Lead AI Engineer"></label>
    <div class="need"><span>Where you'd work</span><div class="row">${MODES.map(([v, l]) => `<label class="check"><input type="checkbox" data-mode="${v}" ${modes.includes(v) ? "checked" : ""}>${l}</label>`).join("")}</div></div>
    <label class="need"><span>City or country</span><input id="sPlace" value="${esc(s.place || "")}" placeholder="Bengaluru, or India for remote"></label>
    <label class="need"><span>Posted</span><select id="sDays">${WITHIN.map(([d, l]) => `<option value="${d}" ${Number(s.days || 3) === d ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <button class="btn btn--primary btn--block" id="sStart">${s.active ? "Search again" : "Start the session"}</button>
    <button class="btn btn--ghost btn--block" id="sCancel">Back</button></div>`;
}

// Where a queued job has got to: tailoring, a CV ready to send, or sent.
function queueState(q) {
  const it = S.items[q.itemId];
  const app = S.rows.find((r) => (it?.app_id && r.id === it.app_id) || String(r.job_id || "") === q.jid);
  if (app) return app.status === "Generated" ? ["CV ready", "ok"] : [app.status, "ok"];
  if (it?.status === "error") return ["Stopped", "warn"];
  return [it?.stage || "Waiting", ""];
}

function sessionCard() {
  const s = S.session, queue = s.queue || [];
  const where = [...MODES.filter(([v]) => (s.modes || []).includes(v)).map(([, l]) => l), s.place].filter(Boolean).join(" · ");
  const states = queue.map(queueState);
  const count = (w) => states.filter(([t]) => w(t)).length;
  const list = queue.length ? `<div class="meta">${queue.length} queued · ${count((t) => t === "CV ready")} ready to send · ${count((t) => !["CV ready", "Stopped", "Waiting"].includes(t) && S.rows.some((r) => r.status === t))} sent</div>
    <ul class="list queue">${queue.map((q, i) => `<li><span class="chip ${states[i][1]}">${esc(states[i][0])}</span><button class="linkbtn" data-goto="${esc(q.jid)}" title="Open this job">${esc(q.company)} · ${esc(q.role)}</button></li>`).join("")}</ul>`
    : `<p class="meta" style="margin:0">Click a job in LinkedIn's list: I read it here. Queue the ones worth a tailored CV and keep going; they're written in the background.</p>`;
  return `<div class="card session"><div class="row"><span class="eyebrow">Easy Apply session</span><span class="meta">${esc(s.title)}${where ? ` · ${esc(where)}` : ""}</span></div>
    ${list}
    <div class="row"><button class="btn btn--sm" id="sOpen">Open the search</button><button class="btn btn--sm btn--ghost" id="sEdit">Change it</button>
      <button class="btn btn--sm btn--ghost" id="sEnd">End session</button></div></div>`;
}

async function startSession() {
  const title = $("#sTitle").value.trim();
  if (!title) return $("#sTitle").focus();
  const filters = { title, place: $("#sPlace").value.trim(), days: Number($("#sDays").value),
    modes: [...document.querySelectorAll("[data-mode]")].filter((b) => b.checked).map((b) => b.dataset.mode) };
  try {
    const { url } = await api("/api/session/search", { body: filters });
    await saveSession({ ...filters, active: true, url, queue: S.session?.active ? S.session.queue || [] : [] });
    S.view = null;
    await chrome.tabs.update(S.tab.id, { url });
  } catch (e) { draw(`<div class="card"><div class="notice bad">${esc(e.message)}</div></div>`); }
}

function liJob(u) {
  try {
    const x = new URL(u);
    if (!x.hostname.endsWith("linkedin.com")) return "";
    const m = /\/jobs\/view\/(?:[^/]*?-)?(\d{6,})/.exec(x.pathname);
    const cur = x.searchParams.get("currentJobId") || "";
    return m ? m[1] : /^\d+$/.test(cur) ? cur : "";
  } catch { return ""; }
}
const liUrl = (jid) => `https://www.linkedin.com/jobs/view/${jid}/`;

// Read the job LinkedIn shows and score it. On a search page the pane swaps a moment after the address changes:
// wait until it shows this job.
async function readLinkedIn(tabId, jid) {
  if (S.li[jid]) return;
  for (let i = 0; i < 5; i++) {
    const [j] = await inPage("linkedin", null, { tabId }).catch(() => []);
    if (j && j.description.length > 300 && (!j.paneId || j.paneId === jid)) {
      const job = { ...j, location: [j.place, j.workplace && `(${j.workplace})`].filter(Boolean).join(" ") };
      const fit = await api("/api/fit", { body: fitBody({ description: job.description, role: job.role, company: job.company,
        url: liUrl(jid), location: job.location, remote: j.workplace === "Remote", posted: job.posted }) }).catch(() => null);
      S.li[jid] = { job, fit };
      return;
    }
    await sleep(1200);
  }
  S.li[jid] = { failed: true };
}

function liCard(jid, li, q) {
  const it = q && S.items[q.itemId];
  if (li.failed) {
    return `<div class="card"><span class="eyebrow">LinkedIn job</span><p class="sub" style="margin:0">I couldn't read this job's pane. Open it on its own page, or use Tailor below.</p>
      <button class="btn btn--block" id="liAgain">Read it again</button></div>`;
  }
  const { job, fit } = li;
  const risky = fit?.below || (fit?.gate && fit.gate.verdict !== "ok") || fit?.label === "Long shot";
  return `<div class="card"><span class="eyebrow">LinkedIn job</span><h2>${esc(job.role || "Untitled role")}</h2>
    <div class="sub">${esc(job.company)}${job.location ? ` · ${esc(job.location)}` : ""}</div>
    <div class="row">${postedChip(job.posted, job.reposted)}${job.easy ? `<span class="chip ok">Easy Apply</span>` : `<span class="chip">Applies on the company's site</span>`}
      ${job.applied ? `<span class="chip warn">You applied on LinkedIn</span>` : ""}</div>
    ${it?.status === "error" ? `<div class="notice">${esc(it.stage)}</div>` : ""}
    ${fitHtml(fit)}
    ${job.applied ? "" : `<button class="btn ${risky ? "" : "btn--primary"} btn--block" id="liQueue" data-force="${risky ? 1 : ""}">${risky ? "Queue anyway" : "Queue: tailor a CV for it"}</button>`}
    <p class="meta" style="margin:0">${risky ? "Long shots rarely call back without a referral. " : ""}Queued CVs are written in the background; keep clicking through the list.</p>
    ${watchHtml(job.company, fit?.watched)}</div>`;
}

async function queueJob(jid, force) {
  const { job, fit } = S.li[jid];
  try {
    const { ids } = await api("/api/batch", { body: { items: [{ jd: job.description, company: job.company, role: job.role,
      job_url: liUrl(jid), job_id: jid, location: job.location, posted: job.posted, market: fit?.market || "auto", ...(force ? { force: true } : {}) }] } });
    S.items[ids[0]] = { id: ids[0], status: "queued", stage: "Waiting for a free slot", company: job.company, role: job.role };
    await saveSession({ queue: [...(S.session.queue || []).filter((q) => q.jid !== jid),
      { jid, itemId: ids[0], company: job.company, role: job.role, label: fit?.label || "" }] });
  } catch (e) { return draw(`<div class="card"><div class="notice bad">${esc(e.message)}</div></div>`); }
  render();
}

// Queued CVs: follow them while the panel is open, and redraw only when one has moved on.
setInterval(async () => {
  const queue = S.session?.active ? S.session.queue || [] : [];
  const open = queue.filter((q) => !["CV ready", "Stopped"].includes(queueState(q)[0]) && !S.rows.some((r) => String(r.job_id || "") === q.jid));
  if (!open.length || document.hidden) return;
  try {
    const items = await api("/api/batch");
    const before = open.map((q) => queueState(q).join()).join("|");
    for (const q of open) { const it = items.find((i) => i.id === q.itemId); if (it) S.items[q.itemId] = it; }
    if (open.some((q) => S.items[q.itemId]?.status === "done")) S.rows = await api("/api/apps").catch(() => S.rows);
    const c = S.ctx;
    if (c && !S.busy[c.tab.id] && S.view !== "session" && open.map((q) => queueState(q).join()).join("|") !== before) refresh();
  } catch { /* the app stopped; the next refresh says so */ }
}, 4000);

// Reading every site needs your OK once; without it the panel can still read the tab you opened it on.
function askAccess() {
  if (S.granted) return Promise.resolve(true);
  return chrome.permissions.request(ALL_SITES).then((g) => (S.granted = g)).catch(() => false);
}

async function inPage(cmd, arg = null, where = {}) {
  const target = { tabId: where.tabId ?? S.tab.id };
  if (where.all) target.allFrames = true;
  else if (where.frame != null) target.frameIds = [where.frame];
  await chrome.scripting.executeScript({ target, files: ["page.js"] });
  const res = await chrome.scripting.executeScript({ target, func: (c, a) => window.__jm[c](a), args: [cmd, arg] });
  return res.filter((r) => r.result != null).map((r) => ({ frame: r.frameId, ...r.result }));
}

// ---------- matching a page to an application ----------
function urlKey(u) {
  try {
    const x = new URL(u);
    return (x.hostname.replace(/^www\./, "") + x.pathname.replace(/\/(apply|application)\/?$/i, "").replace(/\/+$/, "")).toLowerCase();
  } catch { return ""; }
}
function matchApp(url) {
  const k = urlKey(url);
  if (!k) return null;
  let host = "";
  try { host = new URL(url).hostname.replace(/^www\./, ""); } catch { /* not a url */ }
  return S.rows.find((r) => r.job_url && (urlKey(r.job_url) === k || k.startsWith(urlKey(r.job_url) + "/")))
    || S.rows.find((r) => r.job_id && String(r.job_id).length >= 5 && r.job_url && urlKey(r.job_url).startsWith(host) && url.includes(r.job_id))
    || null;
}

async function probeTab(tabId) {
  try {
    return (await inPage("probe", null, { all: true, tabId })).reduce((a, p) => ({ fields: a.fields + p.fields, file: a.file || p.file,
      thanks: a.thanks || p.thanks, password: a.password + (p.password || 0), popup: a.popup || !!p.popup }), { fields: 0, file: false, thanks: false, password: 0, popup: false });
  } catch { return null; }
}

// Sites like Workday move from sign-in to each form step without loading a new page, so look again every few
// seconds while the panel is open, and redraw only when what the page holds has changed.
const probeSig = (p) => (p ? `${p.fields}|${p.file}|${p.thanks}|${p.password}|${p.popup}` : "none");
setInterval(async () => {
  const c = S.ctx;
  if (!c || document.hidden || S.busy[c.tab.id]) return;
  const p = await probeTab(c.tab.id);
  if (S.ctx === c && probeSig(p) !== probeSig(c.probe)) refresh();
}, 3000);

// ---------- refresh: which tab, which page, which application ----------
let refreshing = null;
function refresh() {
  clearTimeout(refreshing);
  refreshing = setTimeout(doRefresh, 150);
}

async function doRefresh() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  S.tab = tab;
  let state;
  try { state = await api("/api/state"); $("#dot").className = "dot on"; $("#dot").title = "JobMagnet is running"; }
  catch { $("#dot").className = "dot off"; $("#dot").title = "JobMagnet isn't running"; return renderOffline(); }
  if (state.setup) return draw(`<div class="card"><h2>Finish setting up first</h2><p class="sub">JobMagnet hasn't read your CV yet.</p>
    <a class="btn btn--primary" href="${SERVER}/" target="_blank">Open JobMagnet</a></div>`);
  S.rows = await api("/api/apps").catch(() => []);
  await loadSession();
  const mem = await memory(tab.id);
  let probe = null;
  probe = await probeTab(tab.id);
  if (S.tab.id !== tab.id) return;
  // A page you said is the form counts as one, however few fields it shows.
  // An application popup (LinkedIn Easy Apply) counts from its first field: its contact step has only two or three.
  const isForm = !!probe && (probe.fields >= 4 || (probe.file && probe.fields >= 2) || (probe.popup && probe.fields > 0) || (mem.formUrl === tab.url && probe.fields > 0));
  const here = mem.url && urlKey(mem.url) === urlKey(tab.url);
  // An application from this page, or the one you were tailoring in this tab when the form opened.
  const app = matchApp(tab.url) || ((isForm || here) && mem.appId ? S.rows.find((r) => r.id === mem.appId) : null);
  const jid = S.session?.active ? liJob(tab.url) : "";
  if (jid && !app) await readLinkedIn(tab.id, jid);
  if (S.tab.id !== tab.id) return;
  S.ctx = { tab, mem, probe, isForm, here, app, jid };
  if (mem.itemId && !mem.appId) watchItem(tab.id, mem.itemId);
  if (here && mem.job && !mem.fit) S.ctx.mem = await remember(tab.id, { fit: await api("/api/fit", { body: fitBody(mem.job) }).catch(() => null) });
  if (app && !S.reports[app.id]) S.reports[app.id] = await api(`/api/apps/${app.id}`).catch(() => null);
  render();
}

// ---------- drawing ----------
function draw(html) { $("#main").innerHTML = html; }

function renderOffline() {
  draw(`<div class="card"><h2>JobMagnet isn't running</h2>
    <p class="sub">Double-click <b>start.bat</b> in your JobMagnet folder, then come back here.</p>
    <button class="btn btn--primary" id="retry">Try again</button></div>`);
  $("#retry").onclick = refresh;
}

function render() {
  const c = S.ctx;
  if (!c) return;
  const { tab, mem, probe, isForm, here, app, jid } = c;
  const busy = S.busy[tab.id];
  const parts = [];
  if (S.view === "session") return finish([sessionForm()]);
  if (S.session?.active) parts.push(sessionCard());
  if (busy) parts.push(`<div class="card"><div class="progress"></div><div class="sub">${esc(busy)}</div></div>`);
  if (!probe) {
    parts.push(`<div class="card"><h2>Open a job posting</h2><p class="sub">I can't read this page. Open a job posting or an application form, then press the button again.</p>
      ${S.granted ? "" : `<button class="btn" id="access">Let JobMagnet read job pages</button>`}</div>`);
    return finish(parts);
  }
  const item = mem.itemId && S.items[mem.itemId];
  // Still writing for the posting this tab was on: keep saying so after Apply opens a form at another address,
  // instead of offering to tailor the same job again.
  const inFlight = item && ["queued", "running"].includes(item.status);
  const q = jid && (S.session?.queue || []).find((x) => x.jid === jid);
  const qItem = q && S.items[q.itemId];
  if (item && (here || isForm || inFlight) && item.status !== "done") parts.push(itemCard(item));
  else if (app) parts.push(appCard(app, S.reports[app.id]));
  else if (qItem && ["queued", "running"].includes(qItem.status)) parts.push(itemCard(qItem));
  else if (jid && S.li[jid] && !busy) parts.push(liCard(jid, S.li[jid], q));
  else if (!busy) parts.push(postingCard(mem, here));
  if (isForm) parts.push(formCard(app, mem));
  else if (app && !busy && probe.password) parts.push(`<div class="card"><span class="eyebrow">Sign in first</span>
    <p class="sub" style="margin:0">This is the site's sign-in or create-account step. Sign in or create your account yourself: I never fill passwords. When the application form opens, I'll pick it up.</p>
    ${probe.fields ? `<button class="btn btn--block" id="fillHere">Fill the email and other fields here</button>` : ""}</div>`);
  else if (app && !busy) parts.push(`<div class="card"><span class="eyebrow">Next</span><p class="sub" style="margin:0">Press <b>${jid ? "Easy Apply" : "Apply"}</b> on the page. When the form opens, I'll fill it in for you to check${jid ? ", one step at a time, with this CV attached" : ""}.</p>
    ${probe.fields ? `<button class="btn btn--block" id="fillHere">This is the form: fill it</button>` : ""}</div>`);
  finish(parts);
}

function finish(parts) {
  draw(parts.join(""));
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  on("access", () => askAccess().then(refresh));
  on("sStart", startSession);
  on("sCancel", () => { S.view = null; render(); });
  on("sEdit", () => { S.view = "session"; render(); });
  on("sOpen", () => chrome.tabs.update(S.tab.id, { url: S.session.url }));
  on("sEnd", () => saveSession(null).then(render));
  on("liQueue", (e) => queueJob(S.ctx.jid, !!e.currentTarget.dataset.force));
  on("liAgain", () => { delete S.li[S.ctx.jid]; refresh(); });
  document.querySelectorAll("[data-goto]").forEach((b) => (b.onclick = () => chrome.tabs.update(S.tab.id, { url: liUrl(b.dataset.goto) })));
  on("tailor", () => tailorHere(false));
  on("readOnly", () => tailorHere(true));
  on("tailorForce", () => tailorHere(false, "", true));
  on("memSave", saveMemory);
  on("memCancel", () => { S.mem = null; render(); });
  // The panel redraws now and then (a page change, a finished CV): keep what you're typing across redraws.
  const mt = document.getElementById("memText"), mr = document.getElementById("memRole");
  if (mt) mt.oninput = () => { if (S.mem) S.mem.text = mt.value; };
  if (mr) mr.onchange = () => { if (S.mem) S.mem.role = mr.value; };
  document.querySelectorAll("[data-gap]").forEach((b) => (b.onclick = () => openMemory(b.dataset.gap)));
  document.querySelectorAll("[data-watch]").forEach((b) => (b.onclick = () => watchCompany(b.dataset.watch)));
  on("tailorAgain", () => tailorHere(false, S.ctx.app?.id));
  on("fill", fillForm);
  on("fillHere", () => remember(S.tab.id, { formUrl: S.tab.url }).then(fillForm));
  on("submit", submitForm);
  on("markApplied", markApplied);
  on("retryItem", () => remember(S.tab.id, { itemId: null }).then(() => tailorHere(false)));
  on("forceItem", () => remember(S.tab.id, { itemId: null }).then(() => tailorHere(false, "", true)));
  const pick = document.getElementById("pickApp");
  if (pick) pick.onchange = async () => { await remember(S.tab.id, { appId: pick.value || null }); refresh(); };
  document.querySelectorAll("[data-need]").forEach((b) => (b.onclick = () => fillNeed(b.dataset.need)));
}

// The job against your profile before any writing: measured in code, the same numbers tailoring will report.
const fitBody = (j) => ({ jd: j.description, role: j.role || "", company: j.company || "", url: j.url || "", location: j.location || "", market: j.market || "", remote: !!j.remote, posted: j.posted || "" });

// When the job was posted: a date read from the page, or the page's own words ("2 weeks ago"). Over a month old
// is marked: such postings are often filled or forgotten.
function postedChip(posted, reposted) {
  if (!posted) return `<span class="chip">Post date not shown</span>`;
  const day = /^\d{4}-\d{2}-\d{2}/.test(posted) ? new Date(`${posted.slice(0, 10)}T00:00:00`) : null;
  const days = day && Math.floor((Date.now() - day) / 864e5);
  const when = day ? `${days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`}, ${day.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}` : posted;
  const old = day ? days > 30 : /\b(months?|years?)\b|\b([5-9]|\d\d)\s+weeks?\b/i.test(posted);
  return `<span class="chip ${old ? "warn" : ""}" title="${old ? "Over a month old: it may be filled already" : "When the posting went up"}">${reposted ? "Reposted" : "Posted"} ${esc(when)}</span>`;
}

// Languages and frameworks the posting names that aren't on your CV; must-haves first.
const stackChips = (missing) => (missing || []).length ? `<div class="row">${[...missing].sort((x, y) => y.must - x.must)
  .map((s) => `<span class="chip ${s.must ? "bad" : "warn"}">No ${esc(s.name)}${s.must ? " · must-have" : ""}</span>`).join("")}</div>` : "";

const companyHtml = (list) => (list || []).length ? `<div class="notice"><b>Applied here before:</b> ${list.slice(0, 3).map((x) => `${esc(x.role)} (${esc(x.story)})`).join("; ")}.</div>` : "";

function gateHtml(g, a) {
  if (a) return `<div class="notice ${a.sent ? "bad" : ""}"><b>You already have this one</b> (${esc(a.story)}).${a.sent ? " Use Replace on it in JobMagnet to write it again." : ""}</div>`;
  if (!g || g.verdict === "ok") return stackChips(g?.missing);
  return `<div class="notice ${g.verdict === "stop" ? "bad" : ""}"><b>${g.verdict === "stop" ? "Fails a screen" : "A stretch"}.</b> ${g.reasons.map(esc).join(" · ")}. Worth tailoring only with a referral.</div>${stackChips(g.missing)}`;
}

function fitHtml(f) {
  if (!f) return "";
  const warn = (f.warnings || []).length ? `<div class="notice">This posting has text aimed at AI tools. JobMagnet reads it as text and never follows it:
    ${f.warnings.map((w) => `<div class="meta">“${esc(w)}”</div>`).join("")}</div>` : "";
  if (!f.measured) return gateHtml(f.gate, f.already) + companyHtml(f.at_company) + warn + `<p class="meta" style="margin:0">This posting names too few standard skills to score. Read it yourself before tailoring.</p>`;
  const list = (label, terms, cls) => terms.length ? `<div class="fit__list"><span class="meta">${label}</span><div class="row">${terms.map((t) => `<span class="chip ${cls}">${esc(t)}</span>`).join("")}</div></div>` : "";
  const label = f.label ? `<div class="row"><span class="chip ${f.label === "Strong" ? "ok" : f.label === "Long shot" ? "warn" : ""}">${esc(f.label)}</span><span class="meta">${esc((f.label_why || []).join(", "))}</span></div>` : "";
  return `${gateHtml(f.gate, f.already)}${companyHtml(f.at_company)}${label}${warn}<div class="score"><div><span class="meta">Your CV</span><b>${f.now}%</b></div><div class="now"><span class="meta">Can reach</span><b>${f.reach}%</b></div><div><span class="meta">Your filter</span><b>${f.minimum ? `${f.minimum}%` : "Any"}</b></div></div>
    <div class="meter" title="Keyword match: ${f.now}% with your CV as it is, ${f.reach}% is the most your profile can truthfully reach"><span style="width:${f.now}%"></span><i style="left:${f.reach}%"></i></div>
    ${f.below ? `<div class="notice">Your profile can reach ${f.reach}% of this job's keywords, below your ${f.minimum}% filter. Worth a look before you spend a tailor on it.</div>` : ""}
    ${list("Tailoring adds (in your profile, not yet in your CV)", f.adds, "ok")}
    ${f.gaps.length ? `<div class="fit__list"><span class="meta">You don't have (not in your profile). Have one after all? Click it.</span>
      <div class="row">${f.gaps.map((t) => `<button class="chip warn chip--btn" data-gap="${esc(t)}" title="I have this: say where">${esc(t)}</button>`).join("")}</div></div>` : ""}
    ${memoryForm()}`;
}

// Watch this job's company in Discover: the job board or careers site you're on, scanned with your favourites.
function watchHtml(company, known) {
  if (!company) return "";
  const got = S.watch[company.toLowerCase()] ?? known;
  if (got?.error) return `<div class="notice">${esc(got.error)}</div>`;
  if (got) return `<div class="row watch"><span class="chip ok">Watched</span><span class="meta">${got.added ? "Added to" : "Already in"} Discover's favourites, through ${esc(got.found_via || got.label)}. Scanned first, pinned at the top.</span></div>`;
  return `<button class="btn btn--ghost btn--block" data-watch="${esc(company)}" title="Add this company to Discover's favourites, through the job board or careers site you're on">Watch ${esc(company)}'s jobs in Discover</button>`;
}

async function watchCompany(company) {
  const tabId = S.tab.id;
  S.busy[tabId] = `Finding where ${company} lists its jobs`;
  render();
  try {
    const [page] = await inPage("grab", null, { tabId });
    S.watch[company.toLowerCase()] = await api("/api/discover/watch", { body: { url: S.tab.url, company, links: page.links, frames: page.frames, html: page.html } });
  } catch (e) { S.watch[company.toLowerCase()] = { error: e.message }; }
  delete S.busy[tabId];
  if (S.tab.id === tabId) render();
}

// Add to your memory from a skill this job says you don't have: where you used it, in your own words.
function memoryForm() {
  const m = S.mem;
  if (!m || m.tabId !== S.tab?.id) return "";
  return `<div class="memform"><span class="eyebrow">Add to your memory</span>
    <select id="memRole" aria-label="Where you did it">${m.roles.map((r) => `<option value="${esc(r.id)}" ${r.id === m.role ? "selected" : ""}>${esc(r.label)}</option>`).join("")}</select>
    <textarea id="memText" rows="3" aria-label="What you did">${esc(m.text)}</textarea>
    <div class="row"><button class="btn btn--sm btn--primary" id="memSave" ${m.busy ? "disabled" : ""}>${m.busy ? esc(m.busy) : "Remember this"}</button>
      <button class="btn btn--sm btn--ghost" id="memCancel">Cancel</button></div>
    ${m.error ? `<div class="notice bad">${esc(m.error)}</div>` : ""}
    <p class="meta" style="margin:0">It's tidied into facts that every score and CV may use. Keep to what you can talk about in an interview.</p></div>`;
}

async function openMemory(term) {
  const { roles } = await api("/api/memory");
  S.mem = { tabId: S.tab.id, roles, role: roles[0]?.id || "", text: `${term}: `, busy: "", error: "" };
  render();
  const t = document.getElementById("memText");
  if (t) { t.focus(); t.setSelectionRange(t.value.length, t.value.length); }
}

async function saveMemory() {
  const m = S.mem, tabId = S.tab.id;
  const role = m.role;
  if (m.text.trim().length < 10) { m.error = "Write a sentence or two about where and how you used it."; render(); return; }
  m.busy = "Tidying your note"; m.error = "";
  render();
  try {
    const { job } = await api("/api/memory", { body: { role, text: m.text.trim() } });
    await pollJob(job, () => {});
    S.mem = null;
    await remember(tabId, { fit: null });   // score the job again with what you just added
    await doRefresh();
  } catch (e) {
    m.busy = ""; m.error = e.message;
    render();
  }
}

function postingCard(mem, here) {
  const j = here && mem.job;
  if (j) {
    return `<div class="card"><span class="eyebrow">This job</span><h2>${esc(j.role || "Untitled role")}</h2>
      <div class="sub">${esc(j.company)}${j.location ? ` · ${esc(j.location)}` : ""}</div>
      <div class="row">${postedChip(j.posted)}<span class="meta">${j.description.split(/\s+/).length} words read</span></div>
      ${fitHtml(mem.fit)}
      ${mem.fit?.below || (mem.fit?.gate && mem.fit.gate.verdict !== "ok") ? `<button class="btn btn--primary btn--block" id="tailorForce">Tailor anyway</button>`
        : `<button class="btn btn--primary btn--block" id="tailor">Tailor my CV</button>`}
      <a class="btn btn--ghost btn--block" href="${SERVER}/?job=${esc(mem.jobJid)}" target="_blank">Review it in JobMagnet first</a>
      ${watchHtml(j.company, mem.fit?.watched)}</div>`;
  }
  return `<div class="card"><span class="eyebrow">This page</span>
    <p class="sub" style="margin:0">On a job posting? I'll read it and write a CV for it. Select the description first if the page shows more than one job.</p>
    <button class="btn btn--primary btn--block" id="tailor">Tailor my CV for this job</button>
    <button class="btn btn--ghost btn--block" id="readOnly">Just read it</button></div>`;
}

function itemCard(it) {
  if (it.status === "error" && it.dup && !it.below_min) {
    return `<div class="card"><span class="eyebrow">Tailoring</span><div class="notice">${esc(it.stage)}</div></div>`;
  }
  if (it.status === "error" && it.below_min) {
    return `<div class="card"><span class="eyebrow">Tailoring</span><div class="notice">${esc(it.stage)}</div>
      <button class="btn btn--primary" id="forceItem">Tailor anyway</button></div>`;
  }
  if (it.status === "error") {
    return `<div class="card"><span class="eyebrow">Tailoring</span><div class="notice bad">${esc(it.stage)}</div>
      <button class="btn btn--primary" id="retryItem">Try again</button></div>`;
  }
  return `<div class="card"><span class="eyebrow">Tailoring</span><h2>${esc(it.role || "This job")}</h2><div class="sub">${esc(it.company || "")}</div>
    <div class="progress"></div><div class="meta">${esc(it.stage || "Working")}. Usually one to three minutes; it carries on if you close this.
    To tailor a different job meanwhile, open it in a new tab.</div></div>`;
}

function appCard(row, rep) {
  const sc = rep?.scores || { before: row.score_before, after: row.score_after, ceiling: row.score_ceiling };
  const ats = rep?.ats || [];
  const failed = ats.filter((a) => !a.ok);
  const flags = (rep?.analysis_flags || []).filter((f) => f.level === "error");
  return `<div class="card"><span class="eyebrow">Your CV for this job</span>
    <h2>${esc(row.role)}</h2><div class="sub">${esc(row.company)}</div>
    <div class="row"><span class="chip ${row.status === "Applied" ? "ok" : ""}">${esc(row.status)}${row.applied_on ? ` ${esc(row.applied_on)}` : ""}</span>
      ${rep ? `<span class="chip ${failed.length ? "warn" : "ok"}">ATS ${ats.length - failed.length}/${ats.length} checks</span>` : ""}
      ${flags.length ? `<span class="chip warn">${flags.length} to check</span>` : ""}</div>
    ${rep?.callback ? `<div class="row"><span class="chip ${rep.callback.label === "Strong" ? "ok" : rep.callback.label === "Long shot" ? "warn" : ""}">Callback: ${esc(rep.callback.label)}</span><span class="meta">${esc(rep.callback.band)}${rep.callback.warm.length ? `; with a referral ${esc(rep.callback.warm_band)}` : ""}</span></div>
      ${(rep.callback.why || []).length ? `<div class="meta">${rep.callback.why.map(esc).join(" · ")}</div>` : ""}${stackChips(rep.callback.missing)}` : ""}
    <span class="meta">ATS keywords (what a search or a skim finds)</span>
    <div class="score"><div><span class="meta">Before</span><b>${sc.before}%</b></div><div class="now"><span class="meta">Now</span><b>${sc.after}%</b></div><div><span class="meta">Ceiling</span><b>${sc.ceiling}%</b></div></div>
    <div class="meter" title="Keyword match: ${sc.after}% now, ${sc.ceiling}% is the most your profile can truthfully reach"><span style="width:${sc.after}%"></span><i style="left:${sc.ceiling}%"></i></div>
    ${(() => {   // must-haves aren't standard keywords: a 100% match can still miss several
      const missing = (rep?.gaps || []).filter((g) => g.must_have).length;
      const total = Math.max((rep?.jd_meta?.must_have || []).length, missing);
      return missing ? `<div class="notice">${missing} of the posting's ${total} must-haves aren't on this CV (years in a role, a domain, a practice). Open it in JobMagnet to see them.</div>` : "";
    })()}
    ${failed.length ? `<ul class="list">${failed.map((a) => `<li><span class="chip warn">✕</span><span>${esc(a.label)}${a.detail ? `<span class="meta"> ${esc(a.detail)}</span>` : ""}</span></li>`).join("")}</ul>` : ""}
    ${flags.length ? `<details><summary>What to check</summary><ul class="list">${flags.map((f) => `<li>${esc(f.text)}</li>`).join("")}</ul></details>` : ""}
    <div class="row"><a class="btn btn--sm" href="${SERVER}/?app=${encodeURIComponent(row.id)}" target="_blank">Open in JobMagnet</a>
      <a class="btn btn--sm" href="${SERVER}/apps/${encodeURIComponent(row.id)}/pdf?download=1" target="_blank">Download CV</a>
      <button class="btn btn--sm btn--ghost" id="tailorAgain">Tailor again</button></div>
    ${watchHtml(row.company)}</div>`;
}

// Right after sending: the people you know there, while it's fresh. A referral turns a cold application warm.
function outreachHtml(app) {
  const warm = (app && S.rows.find((r) => r.id === app.id)?.warm) || [];
  if (!warm.length) return "";
  return `<div class="fit__list"><span class="meta">Message someone you know at ${esc(app.company)} today:</span>
    <ul class="list">${warm.slice(0, 3).map((w) => `<li><a href="${esc(w.url)}" target="_blank">${esc(w.name)}</a><span class="meta">${esc(w.position)}</span></li>`).join("")}</ul>
    <a class="btn btn--sm" href="${SERVER}/?app=${encodeURIComponent(app.id)}" target="_blank">Write the note in JobMagnet</a></div>`;
}

function formCard(app, mem) {
  const f = mem.fill && mem.fill.url === S.tab.url ? mem.fill : null;
  const options = S.rows.slice(0, 40).map((r) => `<option value="${esc(r.id)}" ${app && r.id === app.id ? "selected" : ""}>${esc(r.company)} · ${esc(r.role)}</option>`).join("");
  const picker = `<label class="need"><span class="eyebrow">Application</span><select id="pickApp"><option value="">Pick the CV for this form</option>${options}</select></label>`;
  if (mem.submitted && mem.submittedUrl === S.tab.url) {
    return `<div class="card"><span class="eyebrow">Application form</span><div class="notice ok">Submitted (pressed “${esc(mem.submitted)}”). It's marked Applied in Pipeline.</div>${outreachHtml(app)}</div>`;
  }
  if (!f) {
    return `<div class="card"><span class="eyebrow">Application form</span>${picker}
      ${mem.step ? `<p class="meta" style="margin:0">Pressed “${esc(mem.step)}”. Fill this step when it's loaded.</p>` : ""}
      <button class="btn btn--primary btn--block" id="fill" ${app ? "" : "disabled"}>${mem.step ? "Fill this step" : "Fill the form"}</button>
      <p class="meta" style="margin:0">Answers come from your profile and this CV. Anything I can't answer truthfully is left for you. Nothing is sent until you press Submit.</p></div>`;
  }
  const needs = f.needs.map((n) => {
    const opts = n.options && n.options.length;
    const input = n.kind === "file" ? `<span class="meta">Attach it on the page.</span>`
      : n.kind === "checkbox" ? `<span class="meta">Tick it on the page if it applies.</span>`
      : opts ? `<select id="n-${esc(n.id)}"><option value="">Choose</option>${n.options.map((o) => `<option>${esc(o)}</option>`).join("")}</select>`
      : `<input id="n-${esc(n.id)}" placeholder="Your answer">`;
    const can = n.kind !== "file" && n.kind !== "checkbox";
    return `<div class="need"><label for="n-${esc(n.id)}">${esc(n.label)}${n.required ? " *" : ""}</label><span class="meta">${esc(n.why)}</span>${input}
      ${can ? `<div class="row"><button class="btn btn--sm" data-need="${esc(n.id)}">Fill and remember</button></div>` : ""}</div>`;
  }).join("");
  return `<div class="card"><span class="eyebrow">Application form</span>
    <div class="row"><span class="chip ok">${f.filled} filled</span>${f.attached ? `<span class="chip ok">${f.attached} file${f.attached > 1 ? "s" : ""} attached</span>` : ""}${f.needs.length ? `<span class="chip warn">${f.needs.length} need you</span>` : ""}</div>
    <p class="meta" style="margin:0">Filled fields are outlined in yellow on the page, the ones that need you in orange. Check them, then submit.</p>
    ${f.needs.length ? `<div>${needs}</div>` : ""}
    ${S.warnedEmpty ? `<div class="notice">${esc(S.warnedEmpty)}</div>` : ""}
    <button class="btn btn--primary btn--block" id="submit" ${f.submitText ? "" : "disabled"}>${!f.submitText ? "No submit button found" : /submit|send/i.test(f.submitText) ? `Submit: press “${esc(f.submitText)}”` : `Next step: press “${esc(f.submitText)}”`}</button>
    <button class="btn btn--ghost btn--block" id="fill">Fill again</button></div>`;
}

// ---------- posting -> tailored CV ----------
async function tailorHere(readOnly, replaceId = "", force = false) {
  await askAccess();
  const tabId = S.tab.id;
  const say = (m) => { S.busy[tabId] = m; if (S.tab.id === tabId) render(); };
  say("Reading the posting");
  try {
    const [page] = await inPage("grab", null, { tabId });
    const { job } = await api("/api/clip", { body: page });
    const got = await pollJob(job, (l) => say(l.msg));
    const fit = await api("/api/fit", { body: fitBody(got) }).catch(() => null);
    await remember(tabId, { url: page.url, job: got, jobJid: job, itemId: null, appId: null, fit });
    if (!readOnly) {
      say("Starting the CV");
      const { ids } = await api("/api/batch", { body: { items: [{ jd: got.description, company: got.company, role: got.role,
        job_url: got.url, job_id: got.job_id, location: got.location, posted: got.posted, market: got.market || "auto",
        ...(replaceId ? { replace_id: replaceId } : {}), ...(force ? { force: true } : {}) }] } });
      await remember(tabId, { itemId: ids[0] });
      S.items[ids[0]] = { id: ids[0], status: "queued", stage: "Waiting for a free slot", company: got.company, role: got.role };
    }
  } catch (e) {
    delete S.busy[tabId];
    draw(`<div class="card"><div class="notice bad">${esc(e.message)}</div><button class="btn" id="back">Back</button></div>`);
    $("#back").onclick = refresh;
    return;
  }
  delete S.busy[tabId];
  refresh();
}

const watching = new Set();
async function watchItem(tabId, itemId) {
  if (watching.has(itemId)) return;
  watching.add(itemId);
  try {
    for (;;) {
      const it = (await api("/api/batch")).find((i) => i.id === itemId);
      if (!it) break;
      S.items[itemId] = it;
      if (it.status === "done") { await remember(tabId, { appId: it.app_id }); break; }
      if (it.status === "error") break;
      if (S.tab?.id === tabId) render();
      await sleep(2000);
    }
  } catch { /* the app stopped; the next refresh says so */ }
  watching.delete(itemId);
  if (S.tab?.id === tabId) refresh();
}

// ---------- form -> filled ----------
const split = (fid) => { const i = fid.indexOf(":"); return [Number(fid.slice(0, i)), fid.slice(i + 1)]; };
const blank = (v) => v == null || v === "" || v === false || (Array.isArray(v) && !v.length);

async function fileFor(appId, which) {
  const r = await api(which === "cv" ? `/apps/${appId}/pdf` : `/api/apps/${appId}/letter.pdf`, { raw: true });
  const name = /filename="?([^";]+)"?/.exec(r.headers.get("Content-Disposition") || "")?.[1] || (which === "cv" ? "CV.pdf" : "Cover_Letter.pdf");
  const buf = new Uint8Array(await r.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return { name, type: "application/pdf", b64: btoa(bin) };
}

async function fillForm() {
  await askAccess();
  const tabId = S.tab.id, url = S.tab.url, app = S.ctx.app;
  if (!app) return;
  const say = (m) => { S.busy[tabId] = m; if (S.tab.id === tabId) render(); };
  S.warnedEmpty = false;
  say("Reading the form");
  try {
    const scans = await inPage("scan", null, { all: true, tabId });
    const fields = scans.flatMap((s) => s.fields.map((f) => ({ ...f, id: `${s.frame}:${f.id}` })));
    if (!fields.length) throw new Error("I couldn't find form fields on this page. If the form sits in a box from another site, open the form on its own page.");
    const submitAt = scans.filter((s) => s.submit).sort((a, b) => b.fields.length - a.fields.length)[0];
    const todo = fields.filter((f) => f.kind === "file" || blank(typeof f.value === "string" ? f.value.trim() : f.value));
    say("Working out the answers");
    const { job } = await api(`/api/apps/${app.id}/fill`, { body: { fields: todo, url } });
    const res = await pollJob(job, (l) => say(l.msg));
    const byId = Object.fromEntries(fields.map((f) => [f.id, f]));
    const needs = res.needs.map((n) => ({ ...n, ...byId[n.id], why: n.why }));
    const frames = {};
    const at = (fid) => { const [fr, id] = split(fid); frames[fr] ||= { answers: {}, files: {}, needs: [] }; return [frames[fr], id]; };
    for (const [fid, v] of Object.entries(res.answers)) { const [fr, id] = at(fid); fr.answers[id] = v; }
    say("Attaching your CV and cover letter");
    let attached = 0;
    for (const [fid, which] of Object.entries(res.files)) {
      try { const [fr, id] = at(fid); fr.files[id] = await fileFor(app.id, which); attached++; }
      catch (e) { needs.push({ ...byId[fid], why: which === "letter" ? `No cover letter yet (${e.message}). Write one in JobMagnet, or attach your own.` : e.message }); }
    }
    for (const n of needs) { const [fr, id] = at(n.id); fr.needs.push({ id }); }
    say("Filling the form");
    let filled = 0;
    for (const [frame, payload] of Object.entries(frames)) {
      const [r] = await inPage("fill", payload, { frame: Number(frame), tabId });
      if (!r) continue;
      filled += r.done.length;
      for (const id of r.failed) {
        const fid = `${frame}:${id}`;
        if (!needs.some((n) => n.id === fid)) needs.push({ ...byId[fid], why: "I couldn't set it; please do it on the page" });
      }
    }
    await remember(tabId, { appId: app.id, step: null, fill: { url, needs, filled: filled - attached, attached,
      submitText: submitAt?.submit || "", submitFrame: submitAt?.frame ?? 0 } });
  } catch (e) {
    delete S.busy[tabId];
    draw(`<div class="card"><div class="notice bad">${esc(e.message)}</div><button class="btn" id="back">Back</button></div>`);
    $("#back").onclick = refresh;
    return;
  }
  delete S.busy[tabId];
  render();
}

async function fillNeed(fid) {
  const f = S.ctx.mem.fill, n = f.needs.find((x) => x.id === fid);
  const el = document.getElementById(`n-${fid}`);
  const v = el && el.value.trim();
  if (!n || !v) return el && el.focus();
  const [frame, id] = split(fid);
  const [r] = await inPage("fill", { answers: { [id]: n.kind === "checkgroup" ? [v] : v } }, { frame });
  if (!r || !r.done.length) return;
  await api("/api/answers", { body: { q: n.label, a: v, app: S.ctx.app?.id } }).catch(() => {});
  await remember(S.tab.id, { fill: { ...f, filled: f.filled + 1, needs: f.needs.filter((x) => x.id !== fid) } });
  render();
}

async function submitForm() {
  const tabId = S.tab.id, mem = S.ctx.mem, f = mem.fill, app = S.ctx.app;
  // What you typed on the page into the fields that needed you: kept, so the next form asks once fewer.
  const vals = {};
  const byFrame = {};
  for (const n of f.needs) { const [fr, id] = split(n.id); (byFrame[fr] ||= []).push(id); }
  for (const [frame, ids] of Object.entries(byFrame)) {
    const [r] = await inPage("values", ids, { frame: Number(frame), tabId }).catch(() => []);
    for (const [id, v] of Object.entries(r || {})) if (id !== "frame") vals[`${frame}:${id}`] = v;
  }
  const empty = f.needs.filter((n) => n.required && n.id in vals && blank(vals[n.id]));
  if (empty.length && !S.warnedEmpty) {
    S.warnedEmpty = `Still empty: ${empty.map((n) => n.label).join("; ")}. Press Submit again to send it anyway.`;
    return render();
  }
  S.warnedEmpty = false;
  for (const n of f.needs) if (!blank(vals[n.id]) && n.kind !== "checkbox") await api("/api/answers", { body: { q: n.label, a: vals[n.id], app: app?.id } }).catch(() => {});
  const before = S.ctx.probe?.fields || 0;
  const [r] = await inPage("submit", null, { frame: f.submitFrame, tabId });
  if (!r?.clicked) return stuck("I couldn't find the button. Press it on the page, then mark it applied here.");
  S.busy[tabId] = `Pressed “${r.clicked}”. Checking it went through`;
  render();
  await sleep(3500);
  const after = (await inPage("probe", null, { all: true, tabId }).catch(() => [])).reduce((a, p) => ({ fields: a.fields + p.fields, thanks: a.thanks || p.thanks, url: a.url || p.url }), { fields: 0, thanks: false, url: "" });
  delete S.busy[tabId];
  const final = /submit|send/i.test(r.clicked);
  const left = after.thanks || (after.url && after.url !== f.url) || after.fields < before / 2;
  if (final && left) {
    await api(`/api/apps/${app.id}/track`, { body: { status: "Applied", source: "extension", ...(S.ctx.jid ? { channel: "Easy Apply" } : {}) } }).catch(() => {});
    await remember(tabId, { fill: null, submitted: r.clicked, submittedUrl: S.tab.url });
  } else if (!final) {
    await remember(tabId, { fill: null, step: r.clicked });
    // LinkedIn's popup moves to its next step in place: fill it as soon as it's there, for you to check again.
    if (S.ctx.jid && S.tab.id === tabId) { await sleep(800); await doRefresh(); if (S.ctx.isForm) return fillForm(); }
  } else {
    return stuck("The form is still here, so it may not have gone through. Check the page for errors, fix them, and press Submit again. If it did go through, mark it applied.");
  }
  refresh();
}

function stuck(msg) {
  draw(`<div class="card"><div class="notice">${esc(msg)}</div><button class="btn btn--primary" id="markApplied">Mark applied</button>
    <button class="btn btn--ghost" id="back">Back to the form</button></div>`);
  $("#markApplied").onclick = markApplied;
  $("#back").onclick = refresh;
}

async function markApplied() {
  const app = S.ctx.app;
  if (!app) return;
  await api(`/api/apps/${app.id}/track`, { body: { status: "Applied", source: "extension" } }).catch(() => {});
  await remember(S.tab.id, { fill: null, submitted: "Mark applied", submittedUrl: S.tab.url });
  S.warnedEmpty = false;
  refresh();
}

// ---------- follow the tab you're on ----------
// Look again now, without waiting for the next automatic check: the page, the application and its report.
$("#reload").onclick = async () => {
  const b = $("#reload");
  if (b.classList.contains("spin")) return;
  b.classList.add("spin");
  clearTimeout(refreshing);
  if (S.ctx?.app) delete S.reports[S.ctx.app.id];
  if (S.ctx?.mem?.fit) await remember(S.ctx.tab.id, { fit: null });   // score the posting again, in case the profile changed
  if (S.ctx?.jid) delete S.li[S.ctx.jid];
  try { await doRefresh(); } finally { b.classList.remove("spin"); }
};

$("#sessionBtn").onclick = async () => { await loadSession(); S.view = S.view === "session" ? null : "session"; if (S.ctx) render(); };

chrome.tabs.onActivated.addListener(refresh);
chrome.tabs.onUpdated.addListener((id, info) => { if (id === S.tab?.id && (info.status === "complete" || info.url)) refresh(); });
chrome.permissions.onAdded.addListener(() => { S.granted = true; refresh(); });
setInterval(() => { if ($("#dot").classList.contains("off")) refresh(); }, 5000);
refresh();
