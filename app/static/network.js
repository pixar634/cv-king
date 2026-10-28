// Network: you in the middle, the companies your connections work at in rings around you (closest: hiring for you),
// and the people at each company fanned out around it like a web. Plain SVG, no library: the company layout is a
// small force simulation (60 nodes at most), people are placed around their company, so it stays fast with
// thousands of connections.
const NET = { data: null, view: { x: 0, y: 0, k: 0.9 }, filter: "all", hide: new Set(), sel: null, sample: false };
const RING_LABEL = { strong: "Strong jobs open", possible: "Possible jobs open", applied: "Applied", favourite: "Favourites", other: "Everyone else" };
const KIND_LABEL = { leader: "Manager or leader", recruiter: "Recruiter", peer: "Your function", other: "Other" };

async function loadNetwork(sample = NET.sample) {
  NET.sample = sample;
  $("#netCanvas").innerHTML = `<div class="net__empty"><span class="spinner"></span></div>`;
  NET.data = await api(`/api/network${sample ? "?sample=1" : ""}`);
  layoutNetwork();
  renderNetwork();
}

function layoutNetwork() {
  const nodes = NET.data.nodes, byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const companies = nodes.filter((n) => n.type === "company");
  const people = {};
  for (const l of NET.data.links) if (l.source !== "me") (people[l.source] ||= []).push(byId[l.target]);
  // Each company is a disc big enough for its people. Each status gets its own ring, as wide as its companies
  // need, and its companies are spaced evenly around it.
  const order = ["strong", "possible", "applied", "favourite", "other"];
  companies.sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status) || b.count - a.count);
  for (const c of companies) {
    c.r = 10 + Math.sqrt(c.count) * 4;
    c.halo = c.r + 34 + Math.min(people[c.id]?.length || 0, 14) * 1.6;
  }
  let inner = 120;
  NET.rings = {};
  order.forEach((status, g) => {
    const group = companies.filter((c) => c.status === status);
    if (!group.length) return;
    const widest = Math.max(...group.map((c) => c.halo));
    const radius = Math.max(inner + widest, group.reduce((s, c) => s + c.halo * 2.1, 0) / (2 * Math.PI));
    NET.rings[status] = radius;
    group.forEach((c, i) => {
      const a = (i / group.length) * Math.PI * 2 + g * 0.55;
      c.x = Math.cos(a) * radius; c.y = Math.sin(a) * radius;
    });
    inner = radius + widest;
  });
  // A few steps to settle: hold each company near its ring, push overlapping discs apart.
  for (let step = 0; step < 120; step++) {
    const t = 1 - step / 120;
    for (const c of companies) {
      const d = Math.hypot(c.x, c.y) || 1, want = NET.rings[c.status];
      c.x += (c.x / d) * (want - d) * 0.08 * t; c.y += (c.y / d) * (want - d) * 0.08 * t;
    }
    for (let i = 0; i < companies.length; i++) for (let j = i + 1; j < companies.length; j++) {
      const a = companies[i], b = companies[j];
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 0.1, min = (a.halo + b.halo) * 0.9;
      if (d < min) { const push = ((min - d) / d) * 0.5 * t; a.x -= dx * push; a.y -= dy * push; b.x += dx * push; b.y += dy * push; }
    }
  }
  for (const c of companies) placePeople(c, people[c.id] || []);
  NET.people = people;
  NET.byId = byId;
}

// People fan out on the side of the company facing away from you, the most senior first.
function placePeople(c, ps) {
  const out = Math.atan2(c.y, c.x), n = ps.length, spread = Math.min(Math.PI * 1.7, 0.5 + n * 0.32);
  ps.forEach((p, i) => {
    const a = out - spread / 2 + (n === 1 ? spread / 2 : (i / (n - 1)) * spread);
    const rr = c.r + 22 + (i % 2) * 14;
    p.x = c.x + Math.cos(a) * rr; p.y = c.y + Math.sin(a) * rr;
  });
}

function shown(c) {
  const f = NET.filter;
  return f === "all" || (f === "hiring" && ["strong", "possible"].includes(c.status)) || (f === "applied" && c.applied.length) || (f === "favourite" && c.favourite);
}

function renderNetwork() {
  const d = NET.data, s = d.stats, v = NET.view;
  $("#netStats").innerHTML = `<b class="num">${s.people}</b> people at <b class="num">${s.companies}</b> companies · <b class="num">${s.hiring}</b> with a job open for you
    · ${s.leaders} managers or leaders · ${s.recruiters} recruiters · ${s.peers} in your function${s.drawn < s.companies ? ` · showing the ${s.drawn} that matter most` : ""}`;
  $("#netSample").hidden = !d.sample;
  const companies = d.nodes.filter((n) => n.type === "company");
  const rings = Object.entries(NET.rings).map(([k, r]) => `<circle class="net__ring" r="${r}"/><text class="net__ringlabel" y="${-r - 8}">${RING_LABEL[k]}</text>`).join("");
  const webs = companies.map((c) => {
    const dim = !shown(c) ? " dim" : "";
    const curve = `M0,0 Q${c.x * 0.5 - c.y * 0.12},${c.y * 0.5 + c.x * 0.12} ${c.x},${c.y}`;
    const ps = (NET.people[c.id] || []).map((p) => {
      const off = NET.hide.has(p.kind) ? " off" : "";
      const r = 3.5 + Math.max(0, (p.level ?? 2) - 2) * 1.1;
      return `<line class="net__thread${off}" x1="${c.x}" y1="${c.y}" x2="${p.x}" y2="${p.y}"/>
        <circle class="net__person net__person--${p.kind}${off}" data-node="${esc(p.id)}" cx="${p.x}" cy="${p.y}" r="${r}"><title>${esc(p.label)}, ${esc(p.position)}</title></circle>`;
    }).join("");
    return `<g class="net__cluster${dim}${NET.sel === c.id ? " sel" : ""}" data-company="${esc(c.id)}">
      <path class="net__spoke net__spoke--${c.status}" d="${curve}"/>${ps}
      <circle class="net__company net__company--${c.status}" data-node="${esc(c.id)}" cx="${c.x}" cy="${c.y}" r="${c.r}"/>
      <text class="net__clabel" x="${c.x}" y="${c.y + c.r}" dy="1.15em">${esc(c.label)}</text>
      <text class="net__count num" x="${c.x}" y="${c.y + 4}">${c.count}</text></g>`;
  }).join("");
  $("#netCanvas").innerHTML = `<svg class="net__svg" id="netSvg"><g id="netWorld" transform="translate(${v.x},${v.y}) scale(${v.k})">
      ${rings}${webs}<circle class="net__me" r="22"/><text class="net__melabel" y="5">You</text></g></svg>
    <div class="net__tip" id="netTip" hidden></div>`;
  centerNetwork();
  wireNetwork();
  renderNetPanel();
}

function centerNetwork() {
  const svg = $("#netSvg"), box = svg.getBoundingClientRect();
  if (!NET.view.placed) {
    const extent = Math.max(200, ...NET.data.nodes.filter((n) => n.x !== undefined).map((n) => Math.hypot(n.x, n.y))) + 40;
    NET.view = { x: box.width / 2, y: box.height / 2, k: Math.min(1.2, Math.min(box.width, box.height) / (2 * extent)), placed: true };
  }
  $("#netWorld").setAttribute("transform", `translate(${NET.view.x},${NET.view.y}) scale(${NET.view.k})`);
  $("#netWorld").style.setProperty("--k", NET.view.k);
}

function wireNetwork() {
  const svg = $("#netSvg"), world = $("#netWorld"), tip = $("#netTip"), v = NET.view;
  const apply = () => { world.setAttribute("transform", `translate(${v.x},${v.y}) scale(${v.k})`); world.style.setProperty("--k", v.k); };
  svg.onwheel = (e) => {
    e.preventDefault();
    const box = svg.getBoundingClientRect(), mx = e.clientX - box.left, my = e.clientY - box.top;
    const k = Math.max(0.25, Math.min(3, v.k * (e.deltaY < 0 ? 1.12 : 0.89)));
    v.x = mx - ((mx - v.x) * k) / v.k; v.y = my - ((my - v.y) * k) / v.k; v.k = k; apply();
  };
  let drag = null;
  svg.onpointerdown = (e) => {
    const node = e.target.closest("[data-node]");
    const c = node && NET.byId[node.dataset.node]?.type === "company" ? NET.byId[node.dataset.node] : null;
    drag = { x: e.clientX, y: e.clientY, c, node: node && NET.byId[node.dataset.node], moved: false };
    svg.setPointerCapture(e.pointerId);
  };
  svg.onpointermove = (e) => {
    if (drag) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      drag.x = e.clientX; drag.y = e.clientY;
      if (drag.c) { drag.c.x += dx / v.k; drag.c.y += dy / v.k; placePeople(drag.c, NET.people[drag.c.id] || []); redrawCluster(drag.c); }
      else { v.x += dx; v.y += dy; apply(); }
      return;
    }
    const node = e.target.closest("[data-node]");
    if (!node) { tip.hidden = true; return; }
    const n = NET.byId[node.dataset.node], box = svg.getBoundingClientRect();
    tip.innerHTML = n.type === "company"
      ? `<b>${esc(n.label)}</b><span>${n.count} ${n.count === 1 ? "person" : "people"} you know${n.jobs.length ? ` · ${n.jobs.length} open job${n.jobs.length === 1 ? "" : "s"}` : ""}</span>`
      : `<b>${esc(n.label)}</b><span>${esc(n.position)}</span><span>${KIND_LABEL[n.kind]} at ${esc(n.company)}</span>`;
    tip.style.left = `${e.clientX - box.left + 14}px`; tip.style.top = `${e.clientY - box.top + 14}px`; tip.hidden = false;
  };
  svg.onpointerup = (e) => {
    const was = drag; drag = null;
    if (!was || was.moved) return;
    const n = was.node;   // the pointer is captured by the canvas, so the node is the one pressed on
    if (!n) { NET.sel = null; return renderNetwork(); }
    if (n.type === "person" && n.url) return window.open(n.url, "_blank", "noopener");
    NET.sel = n.id; renderNetwork();
  };
  svg.onpointerleave = () => { tip.hidden = true; };
}

// While a company is dragged, move only its own web instead of redrawing the whole graph.
function redrawCluster(c) {
  const g = document.querySelector(`[data-company="${CSS.escape(c.id)}"]`);
  if (!g) return;
  g.querySelector(".net__spoke").setAttribute("d", `M0,0 Q${c.x * 0.5 - c.y * 0.12},${c.y * 0.5 + c.x * 0.12} ${c.x},${c.y}`);
  const co = g.querySelector(".net__company"); co.setAttribute("cx", c.x); co.setAttribute("cy", c.y);
  g.querySelector(".net__clabel").setAttribute("x", c.x); g.querySelector(".net__clabel").setAttribute("y", c.y + c.r);
  g.querySelector(".net__count").setAttribute("x", c.x); g.querySelector(".net__count").setAttribute("y", c.y + 4);
  const lines = g.querySelectorAll(".net__thread"), dots = g.querySelectorAll(".net__person");
  (NET.people[c.id] || []).forEach((p, i) => {
    lines[i].setAttribute("x1", c.x); lines[i].setAttribute("y1", c.y); lines[i].setAttribute("x2", p.x); lines[i].setAttribute("y2", p.y);
    dots[i].setAttribute("cx", p.x); dots[i].setAttribute("cy", p.y);
  });
}

function renderNetPanel() {
  const c = NET.sel && NET.byId[NET.sel];
  if (!c) {
    $("#netPanel").innerHTML = `<span class="eyebrow">How to read it</span>
      <p class="type-meta">Companies sit in rings by what they mean for your search: closest are ones with a Strong or Possible job open for you now, then where you've applied, your favourites, and everyone else. A bigger bubble means more people you know there.</p>
      <div class="net__legend">${Object.entries(KIND_LABEL).map(([k, l]) => `<span><i class="net__dot net__person--${k}"></i>${l}</span>`).join("")}</div>
      <p class="type-meta">A bigger dot is a more senior person. Click a company to see its people and open jobs; click a person to open their profile. Drag to move, scroll to zoom.</p>`;
    return;
  }
  const people = NET.people[c.id] || [];
  const tagFor = { Strong: "tag--ok", "Long shot": "tag--warn" };
  $("#netPanel").innerHTML = `<span class="eyebrow">${esc(RING_LABEL[c.status])}</span><h2 class="type-h3">${esc(c.label)}</h2>
    <p class="type-meta">${c.count} ${c.count === 1 ? "person" : "people"} you know${c.favourite ? " · a favourite" : ""}</p>
    ${c.jobs.length ? `<div><span class="eyebrow">Open for you</span><div class="list">${c.jobs.map((j) => `<div class="item"><span class="item__body"><span class="item__title">${esc(j.title)}</span></span>
      ${j.fit ? `<span class="tag ${tagFor[j.fit] || ""}">${esc(j.fit)}</span>` : ""}${j.url ? `<a class="btn btn--sm btn--ghost" href="${esc(j.url)}" target="_blank" rel="noopener">Open</a>` : ""}</div>`).join("")}</div></div>` : ""}
    ${c.applied.length ? `<div><span class="eyebrow">Your applications</span><div class="list">${c.applied.map((a) => `<div class="item"><span class="item__body"><span class="item__title">${esc(a.role)}</span><span class="item__meta">${esc(a.status)}</span></span></div>`).join("")}</div>
      <p class="type-meta">Pipeline has "Ask for a referral" for each person here.</p></div>` : ""}
    <div><span class="eyebrow">People</span><div class="list">${people.map((p) => `<div class="item"><i class="net__dot net__person--${p.kind}"></i><span class="item__body"><span class="item__title">${esc(p.label)}</span><span class="item__meta">${esc(p.position)}</span></span>
      ${p.url ? `<a class="btn btn--sm btn--ghost" href="${esc(p.url)}" target="_blank" rel="noopener">Profile</a>` : ""}</div>`).join("")}</div>
      ${c.more ? `<p class="type-meta">and ${c.more} more</p>` : ""}</div>`;
}

function wireNetworkControls() {
  $("#netFilter").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    NET.filter = b.dataset.f;
    $$("#netFilter button").forEach((x) => x.classList.toggle("on", x === b));
    renderNetwork();
  };
  $("#netKinds").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    NET.hide.has(b.dataset.k) ? NET.hide.delete(b.dataset.k) : NET.hide.add(b.dataset.k);
    b.classList.toggle("on", !NET.hide.has(b.dataset.k));
    renderNetwork();
  };
  $("#netReset").onclick = () => { NET.view.placed = false; NET.sel = null; layoutNetwork(); renderNetwork(); };
  window.addEventListener("resize", () => { if ($("#v-network").classList.contains("on") && NET.data) { NET.view.placed = false; renderNetwork(); } });
}
