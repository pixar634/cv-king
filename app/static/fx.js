"use strict";
/* Motion system: the one place that decides how the app moves.
   - Components and text use Motion (motion.dev, the framework-free build of Framer Motion):
     content rises in as it renders, headings reveal word by word, scores count up, and
     overlays (drawer, menus, dock) animate both in and out.
   - GSAP runs the big set pieces only: the page transition (a two-panel curtain on one timeline) and the
     interview flow, where one step leaves as the next arrives, text decodes in and answers unfold.
   - With prefers-reduced-motion, everything resolves instantly. */
(function () {
  const M = window.Motion, G = window.gsap;
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const EASE = [0.2, 0.7, 0.2, 1];          // the app's --ease
  const EASE_OUT = [0.16, 1, 0.3, 1];       // expo-like settle for text and numbers
  const STAGGER = 0.035, MAX_STAGGERED = 14, MAX_ANIMATED = 40;

  // What rises in when it renders. Only the outermost match animates, so a card's insides move with it.
  const ENTERS = [".card", ".item", ".notice", ".score", ".kv", ".diff", ".gap", ".letter", ".checklist",
    ".board__none", ".empty", ".hero > *", ".report__panel > *", ".step > *", ".career__block > *", ".career__canvas",
    ".drawer__section", ".filters", ".chip-row", ".textbox", ".disclosure"].join(",");
  const HEADINGS = ".type-display, .type-h1";
  // Re-rendered lists keep their items' keys; an item whose key was there a moment ago stays still.
  const KEYS = ["open", "id", "openApp", "flight"];
  const keyOf = (el) => { for (const k of KEYS) if (el.dataset && el.dataset[k]) return `${k}:${el.dataset[k]}`; return null; };
  const moving = new WeakSet();

  const clear = (el) => { el.style.removeProperty("opacity"); el.style.removeProperty("transform"); moving.delete(el); };
  const visible = (el) => el.offsetParent !== null || getComputedStyle(el).position === "fixed";
  const onScreen = (el) => { const r = el.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; };
  const done = (anim, fn) => { if (anim && anim.then) anim.then(fn, fn); else fn(); };

  function outermost(list) {
    const set = new Set(list);
    return list.filter((el) => { for (let p = el.parentElement; p; p = p.parentElement) if (set.has(p)) return false; return true; });
  }

  // ---------- components: rise in ----------
  function rise(els, { y = 14, delay = 0 } = {}) {
    els = [...els].filter((el) => !moving.has(el)).slice(0, MAX_ANIMATED);
    if (!els.length || reduced()) return;
    els.forEach((el, i) => {
      moving.add(el);
      el.style.opacity = "0";
      const d = delay + Math.min(i, MAX_STAGGERED) * STAGGER;
      done(M.animate(el, { opacity: [0, 1], transform: [`translateY(${y}px)`, "translateY(0px)"] }, { duration: 0.5, ease: EASE, delay: d }), () => clear(el));
      el.querySelectorAll(".meter__fill, .meter__ceil").forEach((bar) => grow(bar, d + 0.1));
    });
  }

  function grow(bar, delay = 0) {
    if (reduced()) return;
    done(M.animate(bar, { transform: ["scaleX(0)", "scaleX(1)"] }, { duration: 0.9, ease: EASE_OUT, delay }), () => bar.style.removeProperty("transform"));
  }

  // ---------- text: word reveal and count-up ----------
  function splitWords(el) {
    if (!el.dataset.split) {
      el.dataset.split = "1";
      const walk = (node) => [...node.childNodes].forEach((n) => {
        if (n.nodeType === 3) {
          const frag = document.createDocumentFragment();
          n.textContent.split(/(\s+)/).forEach((part) => {
            if (!part) return;
            if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); return; }
            const w = document.createElement("span"), inner = document.createElement("span");
            w.className = "fx-word"; inner.textContent = part; w.appendChild(inner); frag.appendChild(w);
          });
          n.replaceWith(frag);
        } else if (n.nodeType === 1 && n.tagName !== "BR" && !n.classList.contains("fx-word")) walk(n);
      });
      walk(el);
    }
    return [...el.querySelectorAll(".fx-word > span")];
  }

  function words(el, delay = 0) {
    if (reduced() || moving.has(el)) return;
    moving.add(el);
    const parts = splitWords(el);
    parts.forEach((p, i) => {
      p.style.transform = "translateY(110%)";
      done(M.animate(p, { transform: ["translateY(110%)", "translateY(0%)"] }, { duration: 0.8, ease: EASE_OUT, delay: delay + i * 0.045 }),
        () => p.style.removeProperty("transform"));
    });
    setTimeout(() => moving.delete(el), 1200);
  }

  function count(el, delay = 0) {
    const t = el.firstChild;
    if (!t || t.nodeType !== 3 || reduced()) return;
    const end = parseFloat(t.textContent);
    if (!isFinite(end)) return;
    t.textContent = "0";
    M.animate(0, end, { duration: 1.2, delay, ease: EASE_OUT, onUpdate: (v) => (t.textContent = String(Math.round(v))) });
  }

  // ---------- new content, as it renders ----------
  const watch = new MutationObserver((muts) => {
    const removed = new Set(), roots = [];
    for (const m of muts) {
      m.removedNodes.forEach((n) => { if (n.nodeType === 1) [n, ...n.querySelectorAll("[data-open],[data-id],[data-open-app],[data-flight]")].forEach((e) => { const k = keyOf(e); if (k) removed.add(k); }); });
      m.addedNodes.forEach((n) => { if (n.nodeType === 1 && n.isConnected && !n.closest("[data-fx-skip]") && !n.classList.contains("fx-word")) roots.push(n); });
    }
    if (!roots.length) return;
    const rises = [], heads = [], numbers = [];
    for (const root of roots) {
      const found = outermost([...(root.matches(ENTERS) ? [root] : []), ...root.querySelectorAll(ENTERS)]);
      for (const el of found) {
        const k = keyOf(el);
        if (k && removed.has(k)) continue;
        if (!visible(el)) continue;               // hidden views animate when they're shown
        (el.matches(HEADINGS) ? heads : rises).push(el);
      }
      root.querySelectorAll(".score .stat__value").forEach((n) => visible(n) && numbers.push(n));
      root.querySelectorAll(HEADINGS).forEach((h) => visible(h) && !heads.includes(h) && heads.push(h));
    }
    rise(rises.filter(onScreen));
    heads.forEach((h, i) => words(h, i * 0.1));
    numbers.forEach((n, i) => count(n, 0.15 + i * 0.08));
  });

  // ---------- overlays: in and out ----------
  const VARIANTS = {
    drawer: { from: { transform: "translateX(100%)" }, to: { transform: "translateX(0%)" }, duration: 0.45 },
    scrim: { from: { opacity: 0 }, to: { opacity: 1 }, duration: 0.3 },
    menu: { from: { opacity: 0, transform: "translateY(-6px) scale(0.98)" }, to: { opacity: 1, transform: "translateY(0px) scale(1)" }, duration: 0.18 },
    dock: { from: { opacity: 0, transform: "translateY(28px)" }, to: { opacity: 1, transform: "translateY(0px)" }, duration: 0.35 },
  };
  const keyframes = (a, b) => Object.fromEntries(Object.keys(b).map((p) => [p, [a[p], b[p]]]));

  function show(el, kind) {
    const v = VARIANTS[kind];
    const leaving = el.dataset.fxLeaving === "1";
    if (!el.hidden && !leaving) return;
    const token = (el._fxToken = (el._fxToken || 0) + 1);
    if (el._fx) el._fx.stop();
    el.dataset.fxLeaving = ""; el.hidden = false;
    if (reduced()) return;
    el._fx = M.animate(el, keyframes(v.from, v.to), { duration: v.duration, ease: EASE });
    done(el._fx, () => { if (el._fxToken === token) clear(el); });
  }

  function hide(el, kind) {
    const v = VARIANTS[kind];
    if (el.hidden || el.dataset.fxLeaving === "1") return;
    if (reduced()) { el.hidden = true; return; }
    const token = (el._fxToken = (el._fxToken || 0) + 1);
    if (el._fx) el._fx.stop();
    el.dataset.fxLeaving = "1";
    el._fx = M.animate(el, keyframes(v.to, v.from), { duration: v.duration * 0.8, ease: [0.4, 0, 1, 1] });
    done(el._fx, () => { if (el._fxToken !== token) return; el.hidden = true; el.dataset.fxLeaving = ""; clear(el); });
  }

  const toggle = (el, on, kind) => (on ? show(el, kind) : hide(el, kind));
  const isOpen = (el) => !el.hidden && el.dataset.fxLeaving !== "1";

  // ---------- a view arriving: its heading, intro and what's on screen ----------
  function enterView(view = document.querySelector(".view.on")) {
    if (!view || reduced()) return;
    view.querySelectorAll(".page__intro .type-h1, .hero .type-display").forEach((h) => words(h));
    const intro = [...view.querySelectorAll(".page__intro > :not(.type-h1), .page__controls, .composer, .stage__bar, .split__nav, .split__head")];
    const content = outermost([...view.querySelectorAll(ENTERS)]).filter((el) => !el.matches(HEADINGS) && visible(el) && onScreen(el));
    rise([...intro, ...content]);
    view.querySelectorAll(".score .stat__value").forEach((n, i) => count(n, 0.2 + i * 0.08));
  }

  // ---------- page transition (GSAP only) ----------
  let curtain = null, pageTl = null;
  function buildCurtain() {
    curtain = document.createElement("div");
    curtain.className = "curtain"; curtain.setAttribute("aria-hidden", "true"); curtain.dataset.fxSkip = "1";
    curtain.innerHTML = `<div class="curtain__panel curtain__panel--accent"></div><div class="curtain__panel curtain__panel--ink"></div>
      <div class="curtain__label"><div class="curtain__mask"><span class="curtain__index"></span></div><div class="curtain__mask"><span class="curtain__name"></span></div></div>`;
    document.body.appendChild(curtain);
    G.set(curtain.querySelectorAll(".curtain__panel"), { yPercent: 100 });
  }

  function page(index, name, swap) {
    if (pageTl) pageTl.progress(1);                 // a click mid-transition finishes the last one at once
    if (reduced() || !G) { swap(); enterView(); return; }
    if (!curtain) buildCurtain();
    const panels = curtain.querySelectorAll(".curtain__panel");
    const text = curtain.querySelectorAll(".curtain__index, .curtain__name");
    const total = document.querySelectorAll("#nav [data-view]").length;
    curtain.querySelector(".curtain__index").textContent = `${String(index + 1).padStart(2, "0")} / ${String(total).padStart(2, "0")}`;
    curtain.querySelector(".curtain__name").textContent = name;
    const main = document.querySelector("main");
    pageTl = G.timeline({ defaults: { duration: 0.42, ease: "expo.inOut" }, onComplete: () => { pageTl = null; } })
      .set(curtain, { autoAlpha: 1 })
      .addLabel("cover")
      .fromTo(panels, { yPercent: 100 }, { yPercent: 0, stagger: 0.07 }, "cover")
      .to(main, { y: -36, autoAlpha: 0.35, duration: 0.38, ease: "power3.in" }, "cover")
      .fromTo(text, { yPercent: 110 }, { yPercent: 0, duration: 0.45, ease: "expo.out", stagger: 0.05 }, "cover+=0.24")
      .addLabel("swap", "cover+=0.52")
      .add(swap, "swap")
      .set(main, { clearProps: "transform,opacity,visibility" }, "swap")
      .to(text, { yPercent: -110, duration: 0.28, ease: "expo.in", stagger: 0.03 }, "swap+=0.06")
      .addLabel("reveal", "swap+=0.24")
      .to(panels, { yPercent: -100, stagger: { each: 0.07, from: "end" } }, "reveal")
      .add(() => enterView(), "reveal+=0.12")
      .set(curtain, { autoAlpha: 0 });
  }

  function boot() {
    watch.observe(document.body, { childList: true, subtree: true });
    if (reduced()) return;
    rise([...document.querySelectorAll(".topbar > *")], { y: -8 });
    rise([...document.querySelectorAll(".view.on .composer, .view.on .stage__bar")], { delay: 0.15 });
  }

  // ---------- interview flow (GSAP): one step leaves, the next arrives ----------
  const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<>/#%&*+=";
  let radar = null;

  // Text decodes into place, left to right; the element keeps its final height so nothing jumps.
  function scramble(el, text, { delay = 0 } = {}) {
    if (!el) return;
    text = text ?? el.textContent;
    el.textContent = text;
    if (reduced() || !G) return;
    el.style.minHeight = `${el.offsetHeight}px`;
    el.textContent = "";
    const o = { p: 0 };
    G.to(o, { p: 1, delay, duration: Math.min(1.1, 0.35 + text.length * 0.008), ease: "power2.out",
      onUpdate: () => {
        const n = Math.floor(o.p * text.length);
        let s = text.slice(0, n);
        for (let i = n; i < Math.min(text.length, n + 10); i++) s += /\s/.test(text[i]) ? text[i] : GLYPHS[(Math.random() * GLYPHS.length) | 0];
        el.textContent = s;
      },
      onComplete: () => { el.textContent = text; el.style.removeProperty("min-height"); } });
  }

  // Replace the step on a HUD stage: the old one skews out, the new one wipes in, a scan line sweeps the frame.
  function flowSwap(stage, html, dir = 1) {
    stage.querySelectorAll(":scope > .is-leaving").forEach((x) => x.remove());
    const old = stage.querySelector(":scope > .hud__slide");
    const next = document.createElement("div");
    next.className = "hud__slide"; next.innerHTML = html;
    if (reduced() || !G) { if (old) old.remove(); stage.appendChild(next); return next; }
    if (old) { G.killTweensOf(old); old.classList.add("is-leaving"); }
    stage.appendChild(next);
    const scan = stage.closest(".hud")?.querySelector(".hud__scan");
    const tl = G.timeline({ defaults: { ease: "expo.out" } });
    if (old) tl.to(old, { x: -48 * dir, skewX: 6 * dir, autoAlpha: 0, duration: 0.3, ease: "power3.in", onComplete: () => old.remove() }, 0);
    tl.fromTo(next, { x: 64 * dir, skewX: -4 * dir, autoAlpha: 0, clipPath: dir > 0 ? "inset(0% 0% 0% 100%)" : "inset(0% 100% 0% 0%)" },
                    { x: 0, skewX: 0, autoAlpha: 1, clipPath: "inset(0% 0% 0% 0%)", duration: 0.7, clearProps: "transform,clipPath" }, old ? 0.16 : 0)
      .fromTo(next.querySelectorAll("[data-flow-in]"), { y: 18, autoAlpha: 0 },
                    { y: 0, autoAlpha: 1, duration: 0.55, stagger: 0.06, clearProps: "transform" }, "<0.2");
    if (scan) tl.fromTo(scan, { yPercent: -100, autoAlpha: 1 }, { yPercent: 900, autoAlpha: 0, duration: 0.9, ease: "power2.inOut" }, 0);
    next.querySelectorAll("[data-scramble]").forEach((el, i) => scramble(el, null, { delay: (old ? 0.3 : 0.12) + i * 0.1 }));
    return next;
  }

  // A whole HUD arriving (the preparing screen): it unfolds from the top, and its radar starts turning.
  function flowIn(el) {
    if (!el) return;
    if (radar) { radar.kill(); radar = null; }
    if (reduced() || !G) return;
    const dish = el.querySelector(".hud-radar i");
    G.fromTo(el, { autoAlpha: 0, y: 16, clipPath: "inset(0% 0% 100% 0%)" },
                 { autoAlpha: 1, y: 0, clipPath: "inset(0% 0% 0% 0%)", duration: 0.7, ease: "expo.out", clearProps: "transform,clipPath" });
    el.querySelectorAll("[data-scramble]").forEach((t, i) => scramble(t, null, { delay: 0.2 + i * 0.1 }));
    if (dish) radar = G.to(dish, { rotation: 360, duration: 2.4, ease: "none", repeat: -1 });
  }

  // An answer unfolding downwards, its points sliding in one after another.
  function flowReveal(el) {
    el.hidden = false;
    if (reduced() || !G) return;
    G.timeline({ defaults: { ease: "expo.out" } })
      .fromTo(el, { clipPath: "inset(0% 0% 100% 0%)" }, { clipPath: "inset(0% 0% 0% 0%)", duration: 0.6, clearProps: "clipPath" })
      .fromTo(el.querySelectorAll("li, .code, .hud-mono, .btn--link"), { x: -14, autoAlpha: 0 },
              { x: 0, autoAlpha: 1, duration: 0.45, stagger: 0.035, clearProps: "transform" }, 0.08);
  }

  const pulse = (el) => { if (!reduced() && G) G.fromTo(el, { scale: 0.96 }, { scale: 1, duration: 0.4, ease: "back.out(3)", clearProps: "transform" }); };

  window.FX = { rise, words, count, show, hide, toggle, isOpen, enterView, page, scramble, flowSwap, flowIn, flowReveal, pulse };
  boot();
})();
