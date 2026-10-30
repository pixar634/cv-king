// Runs inside the job page (every frame). The side panel calls window.__jm.<command>(arg) through
// chrome.scripting; each command returns plain data. Nothing here talks to the server.
(() => {
  if (window.__jm) return;
  const MARK = "data-jm-id";
  const clean = (s) => String(s || "").replace(/\s+/g, " ").replace(/\s*\*\s*$/, "").trim();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== "hidden";

  // Job boards that show a posting beside a list: their job pane, so the list around it isn't read.
  const PANES = [".jobs-search__job-details--container", ".jobs-details", ".job-view-layout",   // LinkedIn
    "#jobsearch-ViewjobPaneWrapper", ".jobsearch-JobComponent",                                  // Indeed
    "[data-test='job-details']", "#job-details", "[class*='jd-container']", "[class*='job-desc']"];

  function grab() {
    const pane = PANES.map((s) => document.querySelector(s)).find((el) => el && el.innerText.trim().length > 400);
    return {
      url: location.href, title: document.title,
      text: (document.body?.innerText || "").slice(0, 60000),
      focus: pane ? pane.innerText.slice(0, 40000) : "",
      selection: String(getSelection() || "").slice(0, 40000),
      jsonld: [...document.querySelectorAll('script[type="application/ld+json"]')].map((s) => s.textContent),
      frames: [...document.querySelectorAll("iframe[src]")].map((f) => f.src),
      links: [...document.querySelectorAll("a[href]")].slice(0, 800).map((a) => ({ t: (a.innerText || "").trim().slice(0, 160), h: a.href })).filter((l) => l.h.startsWith("http")),
      html: document.documentElement.outerHTML.slice(0, 600000),
    };
  }

  // LinkedIn's job pane (a job page, or the job open beside a search list), read from its markup: no model needed,
  // so each job you click is scored in about a second. paneId says which job the pane shows while it's still swapping.
  function linkedin() {
    const top = document.querySelector(".job-details-jobs-unified-top-card__container--two-pane, .job-details-jobs-unified-top-card__container, .jobs-unified-top-card, .jobs-details__main-content, .job-view-layout") || document;
    const txt = (...sels) => { for (const s of sels) { const t = clean(top.querySelector(s)?.innerText); if (t) return t; } return ""; };
    const desc = document.querySelector("#job-details, .jobs-description__content, .jobs-description-content__text, .jobs-box__html-content");
    const facts = txt(".job-details-jobs-unified-top-card__tertiary-description-container", ".job-details-jobs-unified-top-card__primary-description-container", ".jobs-unified-top-card__subtitle-primary-grouping");
    const head = clean(top === document ? "" : top.innerText).slice(0, 2000);
    const link = top.querySelector(".job-details-jobs-unified-top-card__job-title a[href*='/jobs/view/'], a[href*='/jobs/view/']");
    // "Bengaluru · Reposted 2 weeks ago · 100 applicants": the server turns the wording into a date.
    const when = /\b(Reposted\s+)?(\d+\s+(?:minute|hour|day|week|month|year)s?\s+ago|just now|today|yesterday)\b/i.exec(`${facts} ${head}`);
    return {
      url: location.href, paneId: (/\/jobs\/view\/(\d+)/.exec(link?.href || "") || [])[1] || "",
      role: txt(".job-details-jobs-unified-top-card__job-title h1", ".job-details-jobs-unified-top-card__job-title", ".jobs-unified-top-card__job-title", ".t-24", "h1"),
      company: txt(".job-details-jobs-unified-top-card__company-name a", ".job-details-jobs-unified-top-card__company-name", ".jobs-unified-top-card__company-name"),
      posted: when ? when[2] : "", reposted: !!(when && when[1]),
      place: clean(facts.split("·")[0]), workplace: (/\b(Remote|Hybrid|On-site)\b/.exec(head) || [])[1] || "",
      description: desc ? desc.innerText.trim().slice(0, 40000) : "",
      easy: [...top.querySelectorAll("button, a")].some((b) => /easy apply/i.test(b.innerText || b.getAttribute("aria-label") || "")),
      applied: /\bApplied \d+ \w+ ago\b|Application submitted/i.test(head),
    };
  }

  // An application in a popup (LinkedIn Easy Apply and the like): read, fill and submit inside it, not the page behind.
  // A cookie banner or a sign-in box doesn't count: it must be an Easy Apply popup or hold a real form.
  function root() {
    const d = [...document.querySelectorAll(".jobs-easy-apply-modal, [data-test-modal-id*='easy-apply'], [role=dialog]")].filter(visible)
      .find((x) => x.matches(".jobs-easy-apply-modal, [data-test-modal-id*='easy-apply']") || x.querySelector("input[type=file]")
        || x.querySelectorAll("input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea").length >= 2);
    return d || document;
  }

  // ---------- reading a form ----------
  // The text inside el, leaving out what sits inside elements matching `skip`. Read in place, never from a copy:
  // copying makes Chrome re-check every date and number field, and a page that left one at "undefined" (Ashby)
  // logs an error against the extension.
  function textWithout(el, skip) {
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, { acceptNode: (t) => {
      const hit = t.parentElement?.closest(skip);
      return hit && hit !== el && el.contains(hit) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
    } });
    let out = "";
    while (walk.nextNode()) out += walk.currentNode.nodeValue;
    return clean(out);
  }

  function textOf(el) {
    return el ? textWithout(el, "input,select,textarea,button,script,style,[aria-hidden=true]") : "";
  }

  function labelFor(el) {
    const by = el.getAttribute("aria-labelledby");
    if (by) { const t = by.split(/\s+/).map((id) => textOf(document.getElementById(id))).join(" "); if (clean(t)) return clean(t); }
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && textOf(l)) return textOf(l); }
    const wrap = el.closest("label"); if (wrap && textOf(wrap)) return textOf(wrap);
    if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
    // The question text around the field: climb until a container holds some text besides the field.
    let n = el.parentElement;
    for (let i = 0; n && i < 5; i++, n = n.parentElement) {
      const lab = n.querySelector(":scope > label, :scope > legend, :scope > [class*=label], :scope > [class*=question]");
      if (lab && textOf(lab)) return textOf(lab);
      const t = textOf(n);
      if (t && t.length < 300) return t;
    }
    return clean(el.placeholder || el.name || "");
  }

  function groupLabel(inputs) {
    const fs = inputs[0].closest("fieldset");
    if (fs) { const lg = fs.querySelector("legend"); if (lg && textOf(lg)) return textOf(lg); }
    const by = inputs[0].closest("[role=radiogroup],[role=group]");
    if (by) { const l = by.getAttribute("aria-labelledby"); if (l && document.getElementById(l)) return textOf(document.getElementById(l)); if (by.getAttribute("aria-label")) return clean(by.getAttribute("aria-label")); }
    // The nearest ancestor holding the whole group, minus the option labels.
    let n = inputs[0].parentElement;
    while (n && !inputs.every((i) => n.contains(i))) n = n.parentElement;
    for (let i = 0; n && i < 3; i++, n = n.parentElement) {
      const t = textWithout(n, "label, input");
      if (t) return t.slice(0, 300);
    }
    return clean(inputs[0].name);
  }

  // Required: marked so, or its label carries the usual asterisk (labels are read without it).
  const starred = (el) => {
    const l = (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) || el.closest("label")
      || el.closest("div, li, fieldset")?.querySelector("label, legend, [class*=label]");
    return !!l && /\*/.test(l.textContent);
  };
  const required = (el) => el.required || el.getAttribute("aria-required") === "true" || starred(el);
  let seq = 0;
  const mark = (el) => { if (!el.getAttribute(MARK)) el.setAttribute(MARK, `f${++seq}`); return el.getAttribute(MARK); };

  async function comboOptions(el) {
    // React-select and similar: options exist only while open. Open, read, close.
    try {
      el.focus(); el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })); el.click();
      await sleep(250);
      const list = el.getAttribute("aria-controls") && document.getElementById(el.getAttribute("aria-controls"));
      const opts = [...(list || document).querySelectorAll("[role=option]")].map((o) => clean(o.textContent)).filter(Boolean);
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); el.blur();
      return [...new Set(opts)].slice(0, 200);
    } catch { return []; }
  }

  async function scan() {
    const fields = [];
    const seenGroups = new Set();
    const els = [...root().querySelectorAll("input, textarea, select")].filter((el) => !el.disabled && !el.closest("[aria-hidden=true]"));
    for (const el of els) {
      const type = (el.type || "").toLowerCase();
      if (["hidden", "submit", "button", "reset", "image", "search", "password"].includes(type)) continue;
      if (type !== "file" && !visible(el)) continue;
      if (el.closest("header, nav, footer, [role=search]")) continue;
      if (type === "radio" || type === "checkbox") {
        const group = el.name ? [...document.querySelectorAll(`input[type=${type}][name="${CSS.escape(el.name)}"]`)] : [el];
        if (group.length > 1) {
          if (seenGroups.has(type + el.name)) continue;
          seenGroups.add(type + el.name);
          const id = mark(el);
          group.forEach((g) => g.setAttribute(MARK, id));
          fields.push({ id, kind: type === "radio" ? "radio" : "checkgroup", label: groupLabel(group), options: group.map(labelFor).filter(Boolean), required: group.some(required) });
        } else {
          fields.push({ id: mark(el), kind: "checkbox", label: labelFor(el), required: required(el), value: el.checked });
        }
        continue;
      }
      const f = { id: mark(el), label: labelFor(el), required: required(el) };
      if (el.tagName === "SELECT") Object.assign(f, { kind: "select", options: [...el.options].filter((o) => o.value !== "" && clean(o.textContent) && !/^select|^choose|^--/i.test(clean(o.textContent))).map((o) => clean(o.textContent)), value: el.value ? clean(el.selectedOptions[0]?.textContent) : "" });
      else if (type === "file") Object.assign(f, { kind: "file", label: f.label || textOf(el.closest("div")) });
      else if (el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete") === "list") Object.assign(f, { kind: "combobox", options: await comboOptions(el), value: el.value });
      else Object.assign(f, { kind: el.tagName === "TEXTAREA" ? "textarea" : (type || "text"), value: el.value, max: el.maxLength > 0 ? el.maxLength : undefined });
      fields.push(f);
    }
    return { url: location.href, fields, submit: submitButton()?.text || "" };
  }

  // Is this an application form? Cheap: no dropdowns are opened.
  function probe() {
    const els = [...root().querySelectorAll("input, textarea, select")].filter((el) => !el.disabled && (el.type === "file" || visible(el))
      && !["hidden", "submit", "button", "reset", "image", "search", "password"].includes((el.type || "").toLowerCase()) && !el.closest("header, nav, footer, [role=search]"));
    const thanks = /thank you for (applying|your application)|application (has been |was )?(submitted|received|sent)|we('ve| have) received your application|your application was sent/i
      .test((document.body?.innerText || "").slice(0, 20000));
    // A visible password box means a sign-in or create-account step: never filled, but worth telling apart from a form.
    const password = [...document.querySelectorAll("input[type=password]")].filter((el) => !el.disabled && visible(el)).length;
    return { fields: els.length, file: els.some((el) => el.type === "file"), url: location.href, thanks, password, popup: root() !== document };
  }

  // ---------- filling ----------
  function setValue(el, v) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);   // React tracks the native setter
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  const same = (a, b) => clean(a).toLowerCase() === clean(b).toLowerCase();
  const paint = (el, ok) => { const t = el.type === "file" || el.type === "radio" || el.type === "checkbox" ? el.parentElement : el; if (t) { t.style.outline = `2px solid ${ok ? "#e5fe40" : "#f08d32"}`; t.style.outlineOffset = "2px"; } };

  async function fillOne(id, v) {
    const els = [...document.querySelectorAll(`[${MARK}="${id}"]`)];
    const el = els[0];
    if (!el) return false;
    const type = (el.type || "").toLowerCase();
    if (type === "radio" || (type === "checkbox" && els.length > 1)) {
      const want = Array.isArray(v) ? v : [v];
      for (const g of els) { const on = want.some((w) => same(labelFor(g), w)); if (g.checked !== on && (on || type === "checkbox")) g.click(); }
      paint(el, true); return true;
    }
    if (type === "checkbox") { if (el.checked !== !!v) el.click(); paint(el, true); return true; }
    if (el.tagName === "SELECT") {
      const o = [...el.options].find((x) => same(x.textContent, v));
      if (!o) return false;
      setValue(el, o.value); paint(el, true); return true;
    }
    if (el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete") === "list") {
      el.focus(); el.click(); setValue(el, String(v)); await sleep(400);
      const list = el.getAttribute("aria-controls") && document.getElementById(el.getAttribute("aria-controls"));
      const o = [...(list || document).querySelectorAll("[role=option]")].find((x) => same(x.textContent, v)) || [...(list || document).querySelectorAll("[role=option]")][0];
      if (o) { o.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })); o.click(); }
      else el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      paint(el, !!o); return !!o;
    }
    el.focus(); setValue(el, String(v)); el.blur();
    paint(el, true); return true;
  }

  async function attach(id, file) {
    const el = document.querySelector(`[${MARK}="${id}"]`);
    if (!el) return false;
    const bytes = Uint8Array.from(atob(file.b64), (c) => c.charCodeAt(0));
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], file.name, { type: file.type }));
    el.files = dt.files;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    paint(el, true);
    return true;
  }

  // What a text box or dropdown holds now; null for the kinds that aren't read back (choices are clicked, not typed).
  function current(el) {
    const type = (el.type || "").toLowerCase();
    if (["radio", "checkbox", "file"].includes(type) || el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete") === "list") return null;
    return el.tagName === "SELECT" ? (el.value ? clean(el.selectedOptions[0]?.textContent) : "") : el.value;
  }
  const bare = (s) => String(s ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  // A phone box may reformat the number ("+91 90000 00000" -> "090000 00000"): the last ten digits are what count.
  function holds(id, v) {
    const el = document.querySelector(`[${MARK}="${id}"]`);
    const now = el && current(el);
    if (now == null) return true;
    const a = bare(now), b = bare(v);
    return a === b || (/^\+?[\d\s().-]{7,}$/.test(String(v)) && a.slice(-10) === b.slice(-10));
  }
  const snapshot = () => [...document.querySelectorAll(`[${MARK}]`)].map((el) => (el.type === "checkbox" || el.type === "radio" ? el.checked : el.value)).join("\u0001");

  // Sites that read an uploaded CV fill fields from it a moment later: wait until the form stops changing.
  async function settle() {
    const end = Date.now() + 10000;
    await sleep(2000);
    let last = snapshot(), still = 0;
    while (still < 3 && Date.now() < end) { await sleep(500); const now = snapshot(); still = now === last ? still + 1 : 0; last = now; }
  }

  async function fill({ answers = {}, files = {}, needs = [] }) {
    const done = [], failed = [], set = [];
    // The CV goes in first, so the site's own read of it (which can put the address in the phone box) can't
    // overwrite the answers; they go in after it and are read back in case the site changed them again.
    for (const [id, f] of Object.entries(files)) (await attach(id, f).catch(() => false) ? done : failed).push(id);
    if (Object.keys(files).length) await settle();
    for (const [id, v] of Object.entries(answers)) (await fillOne(id, v).catch(() => false) ? set : failed).push(id);
    if (set.length) await sleep(800);
    for (const id of set) {
      if (!holds(id, answers[id])) await fillOne(id, answers[id]).catch(() => false);
      if (holds(id, answers[id])) done.push(id);
      else { failed.push(id); const el = document.querySelector(`[${MARK}="${id}"]`); if (el) paint(el, false); }
    }
    needs.forEach((n) => { const el = document.querySelector(`[${MARK}="${n.id}"]`); if (el) paint(el, false); });
    return { done, failed };
  }

  // The current values of some fields, to keep what you typed into the ones that needed you.
  function values(ids) {
    const out = {};
    for (const id of ids) {
      const els = [...document.querySelectorAll(`[${MARK}="${id}"]`)];
      if (!els.length) continue;
      const el = els[0], type = (el.type || "").toLowerCase();
      if (type === "radio") { const on = els.find((g) => g.checked); out[id] = on ? labelFor(on) : ""; }
      else if (type === "checkbox" && els.length > 1) out[id] = els.filter((g) => g.checked).map(labelFor);
      else if (type === "checkbox") out[id] = el.checked;
      else if (el.tagName === "SELECT") out[id] = el.value ? clean(el.selectedOptions[0]?.textContent) : "";
      else if (type !== "file") out[id] = el.value;
    }
    return out;
  }

  // ---------- submitting ----------
  function submitButton() {
    const cands = [...root().querySelectorAll("button, input[type=submit], [role=button]")].filter(visible);
    const text = (b) => clean(b.value && b.tagName === "INPUT" ? b.value : b.innerText || b.getAttribute("aria-label"));
    const score = (b) => {
      const t = text(b).toLowerCase();
      if (!t || t.length > 40 || /cancel|back|dismiss|discard|save draft|sign in|log in|search|upload|attach/.test(t)) return 0;
      if (/^submit|submit application|send application|submit your application/.test(t)) return 3;
      if (/^(apply|apply now|review|review your application|next|continue|continue to next step|save and continue)$/.test(t)) return 2;
      if (b.type === "submit" && b.closest("form")) return 1;
      return 0;
    };
    const best = cands.map((b) => ({ b, s: score(b) })).filter((x) => x.s).sort((a, b) => b.s - a.s)[0];
    return best ? { el: best.b, text: text(best.b) } : null;
  }

  function submit() {
    const s = submitButton();
    if (!s) return { clicked: "" };
    s.el.scrollIntoView({ block: "center" });
    s.el.click();
    return { clicked: s.text };
  }

  window.__jm = { grab, linkedin, probe, scan, fill, values, submit };
})();
