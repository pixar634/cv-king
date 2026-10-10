"use strict";
/* Code runner for the coding test. Runs in a Web Worker: no page, no cookies' DOM, and (once loaded) no network,
   so neither the candidate's code nor a problem's reference solution can reach the app's API or anything else.
   JavaScript runs directly; Python runs on Pyodide (CPython compiled to WebAssembly), loaded once from jsDelivr.
   The page owns the clock: a run that takes too long is stopped by terminating this worker. */

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
let py = null;

// Everything that could talk to the outside world, removed before any submitted code runs: from the global object
// and from every prototype above it (fetch and importScripts live on WorkerGlobalScope.prototype), and nested
// workers, which would arrive with their own network access. The page runs one worker per language, so a sealed
// JavaScript worker never needs to load Pyodide.
const BLOCKED = ["fetch", "XMLHttpRequest", "WebSocket", "EventSource", "importScripts", "indexedDB", "caches",
  "BroadcastChannel", "Worker", "SharedWorker", "WebTransport"];
let sealed = false;
function seal() {
  if (sealed) return;
  for (let o = self; o; o = Object.getPrototypeOf(o)) {
    for (const k of BLOCKED) {
      if (!Object.prototype.hasOwnProperty.call(o, k)) continue;
      try { delete o[k]; } catch { /* not configurable */ }
      try { Object.defineProperty(o, k, { value: undefined, configurable: false, writable: false }); } catch { /* frozen */ }
    }
  }
  try { Object.defineProperty(self, "fetch", { value: undefined, configurable: false, writable: false }); } catch { /* done above */ }
  sealed = true;
}

async function python() {
  if (py) return py;
  importScripts(`${PYODIDE}pyodide.js`);
  py = await self.loadPyodide({ indexURL: PYODIDE });
  py.runPython(HARNESS);
  return py;
}

// Calls the named function once per test, catching errors and capturing print() output per test.
const HARNESS = `
import json, sys, io, time
def __cv_run(ns, name, tests_json):
    fn = ns.get(name)
    if not callable(fn):
        return json.dumps({"fatal": f"No function named {name}. Keep the name from the starter code."})
    out = []
    for args in json.loads(tests_json):
        buf, old = io.StringIO(), sys.stdout
        sys.stdout = buf
        t0 = time.perf_counter()
        try:
            r = fn(*args)
            out.append({"ok": True, "out": json.loads(json.dumps(r, default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else list(o)))})
        except Exception as e:
            out.append({"ok": False, "error": f"{type(e).__name__}: {e}"})
        finally:
            sys.stdout = old
        out[-1]["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        out[-1]["stdout"] = buf.getvalue()[-2000:]
    return json.dumps(out)
`;

function runJs(code, name, tests) {
  let fn;
  try {
    fn = new Function(`"use strict";\n${code}\n;return typeof ${name} === "function" ? ${name} : undefined;`)();
  } catch (e) { return { fatal: `${e.name}: ${e.message}` }; }
  if (typeof fn !== "function") return { fatal: `No function named ${name}. Keep the name from the starter code.` };
  return tests.map((args) => {
    const lines = [];
    const log = console.log;
    console.log = (...a) => lines.push(a.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" "));
    const t0 = performance.now();
    let row;
    try {
      const r = fn(...structuredClone(args));
      row = { ok: true, out: r === undefined ? null : JSON.parse(JSON.stringify(r)) };
    } catch (e) { row = { ok: false, error: `${e.name}: ${e.message}` }; }
    finally { console.log = log; }
    return { ...row, ms: Math.round((performance.now() - t0) * 100) / 100, stdout: lines.join("\n").slice(-2000) };
  });
}

self.onmessage = async ({ data }) => {
  const { id, lang, code, name, tests } = data;
  try {
    if (lang === "python") {
      const p = await python();
      seal();
      const ns = p.globals.get("dict")();
      try {
        p.runPython(code, { globals: ns });
      } catch (e) {
        const msg = String(e.message || e).trim().split("\n").filter(Boolean);
        self.postMessage({ id, fatal: msg.slice(-3).join("\n") });
        return;
      }
      const res = JSON.parse(p.globals.get("__cv_run")(ns, name, JSON.stringify(tests)));
      ns.destroy();
      self.postMessage({ id, ...(Array.isArray(res) ? { results: res } : res) });
    } else {
      seal();
      const res = runJs(code, name, tests);
      self.postMessage({ id, ...(Array.isArray(res) ? { results: res } : res) });
    }
  } catch (e) {
    self.postMessage({ id, fatal: String(e.message || e) });
  }
};
