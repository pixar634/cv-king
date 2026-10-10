"use strict";
/* Career map: React Flow canvas (React 18 + React Flow 11, loaded locally as UMD globals).
   History is the profile's own facts; routes are the model's reading of the market. */
(function () {
  const h = React.createElement;
  const RFlib = window.ReactFlow;
  const { Background, Controls, Handle, Position, MarkerType } = RFlib;
  const Flow = RFlib.ReactFlow || RFlib.default;
  const COLORS = ["#8ab4ff", "#7ee2b8", "#f2c46d", "#c9a7ff", "#ff9fb2", "#7fd4ff"];

  function CareerNode({ data }) {
    return h("div", { className: `cn cn-${data.kind}${data.selected ? " sel" : ""}`, style: data.color ? { "--c": data.color } : null },
      h(Handle, { type: "target", position: Position.Left, style: { opacity: 0 } }),
      data.route ? h("div", { className: "cn-route" }, data.route) : null,
      h("div", { className: "cn-title" }, data.title),
      data.sub ? h("div", { className: "cn-sub" }, data.sub) : null,
      data.when ? h("div", { className: "cn-when" }, data.when) : null,
      h(Handle, { type: "source", position: Position.Right, style: { opacity: 0 } }));
  }
  const nodeTypes = { career: CareerNode };

  function build(history, map, years, selected, goal) {
    const nodes = [], edges = [];
    const roles = history.filter((x) => x.kind !== "project");
    const projects = history.filter((x) => x.kind === "project");
    roles.forEach((x, i) => {
      nodes.push({ id: x.id, type: "career", position: { x: i * 260, y: 0 }, draggable: false,
        data: { kind: x.current ? "hist cn-current" : "hist", title: x.title, sub: x.org, when: x.when, item: x, type: "history" } });
      if (i) edges.push({ id: `h${i}`, source: roles[i - 1].id, target: x.id, style: { stroke: "#3a4766", strokeWidth: 1.5 } });
    });
    const nowX = roles.length * 260 + 40;
    projects.forEach((p, i) => {
      nodes.push({ id: p.id, type: "career", position: { x: nowX - 300, y: 150 + i * 120 }, draggable: false,
        data: { kind: "project", title: p.title, sub: p.org, when: p.when, item: p, type: "history" } });
      edges.push({ id: `p${i}`, source: p.id, target: "now", style: { stroke: "#3a4766", strokeDasharray: "4 4" } });
    });
    if (roles.length) edges.push({ id: "tonow", source: roles[roles.length - 1].id, target: "now", style: { stroke: "#5a6a8c", strokeWidth: 1.5 } });
    nodes.push({ id: "now", type: "career", position: { x: nowX, y: 0 }, draggable: false,
      data: { kind: "now", title: "You, now", sub: `${years} years of experience`, when: new Date().toLocaleDateString([], { month: "short", year: "numeric" }), type: "now" } });
    const paths = (map && map.paths) || [];
    paths.forEach((p, i) => {
      const color = COLORS[i % COLORS.length];
      const y = (i - (paths.length - 1) / 2) * 175;
      p.steps.forEach((s, k) => {
        const id = `${p.id}-${k}`;
        nodes.push({ id, type: "career", position: { x: nowX + 320 * (k + 1), y }, draggable: false,
          data: { kind: "step", color, route: k === 0 ? `${p.name}${p.fit ? `, ${p.fit} fit` : ""}` : "", title: s.title, sub: s.level, when: s.when,
            step: s, path: p, type: "step", selected: selected === id } });
        edges.push({ id: `e-${id}`, source: k === 0 ? "now" : `${p.id}-${k - 1}`, target: id, animated: k > 0,
          style: { stroke: color, strokeWidth: 1.8, opacity: 0.85 }, markerEnd: { type: MarkerType.ArrowClosed, color } });
      });
    });
    if (goal && goal.role) {
      const far = Math.max(1, ...paths.map((p) => p.steps.length));
      nodes.push({ id: "goal", type: "career", position: { x: nowX + 320 * (far + 1), y: 0 }, draggable: false,
        data: { kind: "goal", title: goal.role, sub: goal.salary ? `${goal.salary} ${goal.unit || "LPA"} a year` : "Your goal",
          when: goal.year ? `by ${goal.year}` : "", type: "goal" } });
      paths.forEach((p, i) => {
        if (!p.reaches_goal || !p.steps.length) return;
        const color = COLORS[i % COLORS.length];
        edges.push({ id: `g-${p.id}`, source: `${p.id}-${p.steps.length - 1}`, target: "goal",
          style: { stroke: color, strokeDasharray: "2 5", strokeWidth: 1.6, opacity: 0.7 } });
      });
    }
    nodes.forEach((n) => { if (n.id === selected) n.data = { ...n.data, selected: true }; });
    return { nodes, edges };
  }

  let root = null, last = null;
  window.Career = {
    render(el, history, map, years, onClick, selected, goal) {
      if (!root) root = ReactDOM.createRoot(el);
      last = { el, history, map, years, onClick, goal };
      const { nodes, edges } = build(history, map, years, selected, goal);
      root.render(h(Flow, {
        // Open on what matters: you now, the routes and the goal. History is a pan to the left.
        nodes, edges, nodeTypes, fitView: true, minZoom: 0.2, maxZoom: 1.6,
        fitViewOptions: { padding: 0.08, maxZoom: 1, nodes: nodes.filter((n) => ["now", "step", "goal"].includes(n.data.type)).map((n) => ({ id: n.id })) },
        nodesDraggable: false, nodesConnectable: false, elementsSelectable: true,
        onNodeClick: (_, n) => { onClick(n.data, n.id); window.Career.select(n.id); },
      }, h(Background, { color: "#1e2a40", gap: 22 }), h(Controls, { showInteractive: false })));
    },
    select(id) { if (last) window.Career.render(last.el, last.history, last.map, last.years, last.onClick, id, last.goal); },
  };
})();
