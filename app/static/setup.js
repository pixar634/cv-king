"use strict";
// Profiles and onboarding: switching between people, starting a new profile from a CV, reviewing what was read,
// and the strategy section of Settings. Loaded before app.js; it uses app.js's helpers (api, pollJob, $, esc, ...)
// only when called, after both files have loaded.

// ---------- the profile switcher in the top bar ----------
function wireProfileMenu() {
  const st = S.state, list = st.profiles || [], cur = st.setup ? st.setup.current : st.current;
  const me = list.find((p) => p.id === cur);
  const chip = $("#profileChip"), menu = $("#profileMenu");
  chip.textContent = me ? me.name : "New profile";
  const dot = (p) => (p.status === "ready" ? "" : ` <span class="type-meta">${p.status === "review" ? "(check it)" : "(not set up)"}</span>`);
  menu.innerHTML = list.map((p) => `<div class="menu__row"><button data-pid="${esc(p.id)}" class="${p.id === cur ? "on" : ""}">${esc(p.name)}${dot(p)}</button>`
    + (p.status !== "ready" && p.id !== "me" ? `<button class="icon-btn icon-btn--sm" data-del="${esc(p.id)}" aria-label="Delete ${esc(p.name)}" title="Delete this unfinished profile">${ICON.x}</button>` : "")
    + `</div>`).join("") + `<button data-new="1">+ New profile from a CV</button>`;
  chip.onclick = (e) => { e.stopPropagation(); toggleMenu("#profileMenu"); };
  menu.onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    try {
      if (b.dataset.pid) { if (b.dataset.pid !== cur) { await api("/api/profiles/switch", { body: { id: b.dataset.pid } }); location.reload(); } closeMenus(); }
      else if (b.dataset.new) { await api("/api/profiles/new", { body: { name: "New profile" } }); location.reload(); }
      else if (b.dataset.del) { if (confirm("Delete this profile and everything saved under it?")) { await api(`/api/profiles/${b.dataset.del}`, { method: "DELETE" }); location.reload(); } }
    } catch (err) { toast(err.message, 5000); }
  };
}

// ---------- the setup screen ----------
function renderSetup() {
  document.body.classList.add("is-setup");
  const st = S.state.setup, root = $("#setup");
  root.hidden = false;
  if (st.mode === "review") return renderReview(root);
  if (st.setup_job) return watchSetup(root, st.setup_job);
  renderCvForm(root, st.setup_error ? `The last attempt failed: ${st.setup_error}` : (st.status === "setting_up" ? "Setup was interrupted (the app was restarted). Add the CV again." : ""));
}

const WANTS_HINT = "e.g. Move from business analysis into product management. Or: stay in banking tech, but at a product company, and grow to a senior role in about three years.";

function renderCvForm(root, problem) {
  root.innerHTML = `
    <div class="page setup__page">
      <header class="page__head"><div class="page__intro">
        <span class="eyebrow">New profile</span>
        <h1 class="type-h1">Start with the CV</h1>
        <p class="page__lede">Give me the CV you use today. I read it, build the profile every tailored CV will draw from, work out where the career can go next, and set up the job search for it: the roles to look for, the employers, the searches. Nothing is invented; you check it all before anything is used.</p>
      </div></header>
      ${problem ? `<div class="notice notice--bad">${esc(problem)}</div>` : ""}
      <div class="setup__form">
        <label class="dropzone" id="dz"><input type="file" id="cvFile" accept=".pdf,.docx,.txt,.md" hidden>
          <span class="type-h3" id="dzTitle">Drop the CV here, or click to choose it</span>
          <span class="type-meta" id="dzMeta">PDF or Word (.docx). It stays on this computer; only its text goes to the AI you choose.</span></label>
        <details class="disclosure"><summary>Or paste the CV text</summary><textarea id="cvText" rows="8" placeholder="Paste the whole CV"></textarea></details>
        <label class="field"><span class="eyebrow">What do you want next? (optional)</span>
          <textarea id="cvWants" rows="3" placeholder="${esc(WANTS_HINT)}"></textarea>
          <span class="type-meta">Leave it empty and the routes that fit the CV best are proposed.</span></label>
        <div class="setup__acts"><button class="btn btn--accent btn--lg" id="cvGo" disabled>Read my CV</button></div>
      </div>
    </div>`;
  let file = null;
  const dz = $("#dz"), go = $("#cvGo");
  const ready = () => { go.disabled = !(file || $("#cvText").value.trim().length > 300); };
  const pick = (f) => { file = f; $("#dzTitle").textContent = f ? f.name : "Drop the CV here, or click to choose it"; $("#dzMeta").textContent = f ? `${Math.round(f.size / 1024)} KB. Click to choose a different file.` : ""; ready(); };
  $("#cvFile").onchange = (e) => pick(e.target.files[0] || null);
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) pick(e.dataTransfer.files[0]); });
  $("#cvText").oninput = ready;
  go.onclick = async () => {
    go.disabled = true; go.textContent = "Uploading…";
    try {
      let pid = S.state.setup.current;
      if (!pid || !(S.state.profiles || []).some((p) => p.id === pid)) { pid = (await api("/api/profiles/new", { body: { name: "New profile" } })).id; PROFILE_ID = pid; }
      const body = { wants: $("#cvWants").value, engine: (S.state.engines.find((e) => e.id === "claude:opus" && e.available) || S.state.engines.find((e) => e.available) || {}).id };
      if (file) body.data = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1]); r.onerror = rej; r.readAsDataURL(file); }), body.filename = file.name;
      else body.text = $("#cvText").value;
      const { job } = await api(`/api/profiles/${pid}/cv`, { body });
      S.state.setup.current = pid;
      watchSetup(root, job);
    } catch (e) { renderCvForm(root, e.message); }
  };
}

function watchSetup(root, jid) {
  root.innerHTML = `
    <div class="page setup__page">
      <header class="page__head"><div class="page__intro">
        <span class="eyebrow">Setting you up</span>
        <h1 class="type-h1">Reading your CV</h1>
        <p class="page__lede">This takes four to eight minutes: the CV is read, then the web is searched for how careers like yours progress, who hires, and what to search for. You can leave this page open and come back.</p>
      </div></header>
      <div class="flow-log" id="setupLog"></div><div class="progress"></div>
    </div>`;
  pollJob(jid, (l) => { $("#setupLog").insertAdjacentHTML("beforeend", `<div class="flow-log__line"><span class="type-meta">${esc(l.t)}</span> ${esc(l.msg)}</div>`); })
    .then(() => location.reload())
    .catch((e) => renderCvForm(root, `Setup failed: ${e.message}`));
}

// ---------- the review: check what was read before anything uses it ----------
const monthYear = (v) => { if (!v) return "Present"; const [y, m] = String(v).split("-"); return m ? `${m}/${y}` : y; };

function renderReview(root) {
  const R = S.state.review, p = R.profile, b = p.basics, st = R.strategy;
  const fields = [["name", "Name"], ["email", "Email"], ["phone", "Phone"], ["location", "Location (City, Country)"], ["linkedin", "LinkedIn"], ["nationality", "Nationality (for visa-sponsorship lines)"], ["availability", "Availability line (e.g. Serving notice, available from 1 Dec)"]];
  const flags = R.flags.map((f) => `<div class="notice ${f.level === "error" ? "notice--bad" : ""}">${esc(f.text)}</div>`).join("");
  root.innerHTML = `
    <div class="page setup__page">
      <header class="page__head"><div class="page__intro">
        <span class="eyebrow">Check what I read</span>
        <h1 class="type-h1">Is this you?</h1>
        <p class="page__lede">Every CV, cover letter and email will use only what is below. Fix anything wrong now, and add what the CV left out. Nothing here is invented: where something couldn't be read, it is flagged.</p>
      </div></header>
      ${flags ? `<section class="setup__block"><h3 class="eyebrow">Worth a look</h3>${flags}</section>` : ""}
      <section class="setup__block"><h3 class="eyebrow">You</h3>
        <div class="setup__grid">${fields.map(([k, label]) => `<label class="field"><span class="type-meta">${label}</span><input type="text" data-b="${k}" value="${esc(b[k] || "")}"></label>`).join("")}</div>
      </section>
      <section class="setup__block"><h3 class="eyebrow">How I read your career</h3>
        <div class="card setup__read">
          <p class="type-h3">${esc(st.field || "")}</p>
          <p class="type-body">${esc(st.summary || "")}</p>
          <p class="type-meta">Level: <b>${esc(st.level.label)}</b> · ${esc(st.level.current_title)}</p>
          ${st.progression ? `<p class="type-meta">Typical ladder: ${esc(st.progression)}</p>` : ""}
          <p class="type-meta">What you want next: ${esc(st.career_wants || "not stated")}</p>
        </div>
        <h3 class="eyebrow">Roles I will look for</h3>
        <div class="list">${Object.values(R.tracks).map((t) => `<div class="item"><span class="item__body"><span class="item__title">${esc(t.label)}</span>
          <span class="item__meta item__meta--wrap">${esc((t.titles || []).join(" · "))}</span></span></div>`).join("")}</div>
        <p class="type-meta">${(st.companies || []).filter((c) => c.added).length} employers with public job boards are already in your Favourites; ${(st.companies || []).filter((c) => !c.added).length} more are suggested in Settings. ${(st.searches || []).length} web searches are set up for Discover.</p>
      </section>
      <section class="setup__block"><h3 class="eyebrow">Your experience</h3>
        ${p.experience.map((r) => `<div class="card setup__role">
          <div class="setup__rolehead"><b>${esc(r.title)}</b> · ${esc(r.company)}${r.client ? ` · for ${esc(r.client)}` : ""} <span class="type-meta">${monthYear(r.start)} – ${monthYear(r.end)}${r.location ? ` · ${esc(r.location)}` : ""}</span></div>
          ${r.bullets.map((x) => `<textarea rows="2" data-role="${esc(r.id)}" data-bid="${esc(x.id)}">${esc(x.text)}</textarea>`).join("")}
          <textarea rows="2" data-role="${esc(r.id)}" data-bid="" placeholder="Add something this role missed (a result, a tool, a responsibility)"></textarea>
        </div>`).join("")}
        <p class="type-meta">Empty a box to remove that bullet. Education: ${p.education.map((e) => esc(e.degree) + (e.institution ? `, ${esc(e.institution)}` : "")).join(" · ") || "none found"}. ${Object.keys(p.skills).length} skill groups.</p>
      </section>
      <div class="setup__acts"><button class="btn btn--accent btn--lg" id="revOk">Looks right, open the app</button>
        <button class="btn btn--ghost" id="revAgain">Read a different CV</button></div>
    </div>`;
  $$("#setup [data-b]").forEach((i) => (i.onchange = async () => { await api("/api/profile/basics", { body: { [i.dataset.b]: i.value } }); toast("Saved"); }));
  $$("#setup [data-bid]").forEach((t) => (t.onchange = async () => {
    await api("/api/profile/bullet", { body: { role: t.dataset.role, id: t.dataset.bid, text: t.value } });
    toast(t.dataset.bid && !t.value.trim() ? "Removed" : "Saved");
  }));
  $("#revOk").onclick = async () => { await api("/api/profiles/ready", { method: "POST", body: {} }); location.reload(); };
  $("#revAgain").onclick = () => renderCvForm(root, "");
}

// ---------- Settings: the strategy section ----------
function fillStrategy() {
  const box = $("#stratBox"); if (!box) return;
  const s = S.state.strategy, legacy = S.state.legacy;
  if (!s) { box.innerHTML = ""; return; }
  const rest = (s.companies || []).filter((c) => !c.added);
  box.innerHTML = `<h3 class="eyebrow">Your search strategy</h3>
    <p class="type-meta">${esc(s.field || "")}${s.ready ? "" : " (not analysed yet)"} · level: ${esc(s.level.label)} · ${S.state.home.city ? esc(S.state.home.city) : ""}</p>
    ${s.progression ? `<p class="type-meta">Ladder: ${esc(s.progression)}</p>` : ""}
    <label class="field"><span class="type-meta">What you want next</span><textarea id="stWants" rows="2">${esc(s.career_wants || "")}</textarea></label>
    <div><button class="btn btn--sm" id="stRefresh">${s.ready ? "Re-read my profile and redo the strategy" : "Analyse my profile"}</button></div>
    <div class="type-meta" id="stMsg"></div>
    ${rest.length ? `<div class="field"><span class="type-meta">Employers worth watching (click to add to Favourites)</span><div class="chip-row" id="stCompanies">
      ${rest.map((c) => `<button class="chip" data-c="${esc(c.name)}" title="${esc(c.why || "")}">${esc(c.name)}</button>`).join("")}</div></div>` : ""}
    ${legacy ? "" : `<div><button class="btn btn--sm btn--ghost" id="stDelete">Delete this profile</button></div>`}`;
  $("#stRefresh").onclick = async () => {
    const msg = $("#stMsg"), btn = $("#stRefresh"); btn.disabled = true;
    msg.textContent = "Starting…";
    try {
      const { job } = await api("/api/strategy/refresh", { body: { wants: $("#stWants").value } });
      await pollJob(job, (l) => { msg.textContent = l.msg; });
      toast("Strategy updated. Discover is re-ranked.", 4000); location.reload();
    } catch (e) { msg.textContent = e.message; btn.disabled = false; }
  };
  const comp = $("#stCompanies");
  if (comp) comp.onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    b.disabled = true; b.textContent = "Finding its jobs…";
    try { const r = await api("/api/strategy/company", { body: { name: b.dataset.c } }); toast(`${r.company} added to Favourites`); b.remove(); }
    catch (err) { toast(err.message, 5000); b.disabled = false; b.textContent = b.dataset.c; }
  };
  const del = $("#stDelete");
  if (del) del.onclick = async () => {
    if (!confirm("Delete this profile with its CV, applications and everything else saved under it?")) return;
    await api(`/api/profiles/${S.state.current}`, { method: "DELETE" }); location.reload();
  };
}
