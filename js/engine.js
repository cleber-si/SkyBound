"use strict";
/* SkyBound · the in-browser comparison-star engine, page side.

   Starts js/engine-worker.js (s4field.py on Pyodide) when the visitor agrees to
   the one-time download, and answers requests shaped like the s4field helper's
   HTTP API: S4Engine.request("GET", "/api/field", {target: ...}) resolves to
   {status, body} as the helper would answer. The visitor's choice is kept in the
   browser ("skybound.engine.v1"); later visits start the engine from the
   browser's cache.

   For testing, ?pyodide=URL loads Pyodide from another place than its CDN.
   By Cleber Silva · UFC/MALTED */

const S4Engine = (() => {
  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.29.5/full/";
  const SIZE_MB = 11;                    // Python + numpy, as downloaded (compressed), once
  const PREF = "skybound.engine.v1";
  const STEPS = 3;
  let worker = null, state = "off", status = null, error = null, progress = null, boot = null;
  const waiting = new Map(), listeners = new Set();
  let nextId = 1;

  const emit = (ev) => { for (const f of listeners) { try { f(ev); } catch (e) { /* a listener's own problem */ } } };
  const setState = (s) => { state = s; emit({ type: "state", state: s }); };

  function supported() {
    return typeof Worker !== "undefined" && typeof WebAssembly === "object";
  }
  function enabled() {
    try { return localStorage.getItem(PREF) === "on"; } catch (e) { return false; }
  }
  function setEnabled(on) {
    try { localStorage.setItem(PREF, on ? "on" : "off"); } catch (e) { /* this visit only */ }
    if (!on) stop();
  }
  function indexURL() {
    const u = new URLSearchParams(location.search).get("pyodide");
    return u ? (u.endsWith("/") ? u : u + "/") : PYODIDE;
  }
  const site = () => new URL(".", location.href).href;

  function start() {
    if (boot) return boot;
    if (!supported()) {
      error = "this browser cannot run it (no WebAssembly or Web Workers)";
      setState("failed");
      return Promise.reject(new Error(error));
    }
    error = null;
    progress = { step: 0, steps: STEPS, text: "starting" };
    setState("loading");
    boot = new Promise((resolve, reject) => {
      worker = new Worker("js/engine-worker.js");
      worker.onmessage = (ev) => {
        const m = ev.data || {};
        if (m.type === "progress") {
          progress = { step: m.step, steps: STEPS, text: m.text };
          emit({ type: "progress", progress });
        } else if (m.type === "ready") {
          status = m.status;
          setState("ready");
          resolve(status);
        } else if (m.type === "failed") {
          error = m.error;
          setState("failed");
          reject(new Error(m.error));
          for (const [, w] of waiting) w.reject(new Error(`ENGINE: ${m.error}`));
          waiting.clear();
        } else if (m.type === "response") {
          const w = waiting.get(m.id);
          if (w) { waiting.delete(m.id); clearTimeout(w.timer); w.resolve({ status: m.status, body: m.body }); }
        } else if (m.type === "status") {
          const n = (status && status.cached || []).length;
          status = m.status;
          if (m.status.cached.length !== n) emit({ type: "status", status });
        } else if (m.type === "updated") {
          status = m.status;
          emit({ type: "updated", status });
        } else if (m.type === "log") {
          console.info("[s4field engine]", m.text);
        }
      };
      worker.onerror = (ev) => {
        error = (ev && ev.message) || "the engine stopped";
        setState("failed");
        reject(new Error(error));
        for (const [, w] of waiting) w.reject(new Error(`ENGINE: ${error}`));
        waiting.clear();
      };
      worker.postMessage({ type: "boot", indexURL: indexURL(), site: site() });
    });
    boot.catch(() => { boot = null; if (worker) { worker.terminate(); worker = null; } });
    return boot;
  }

  function stop() {
    if (worker) worker.terminate();
    worker = null; boot = null; status = null;
    for (const [, w] of waiting) w.reject(new Error("ENGINE: switched off"));
    waiting.clear();
    setState("off");
  }

  // {status, body}, as the helper answers; ms: how long to wait for the answer
  async function request(method, path, query, body, ms = 600000) {
    await start();
    const id = nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { waiting.delete(id); reject(new Error("TIMEOUT")); }, ms);
      waiting.set(id, { resolve, reject, timer });
      worker.postMessage({ type: "request", id, method, path, query: query || {},
                           body: body == null ? "" : (typeof body === "string" ? body : JSON.stringify(body)) });
    });
  }

  return {
    SIZE_MB, PYODIDE, supported, enabled, setEnabled, start, stop, request,
    state: () => state, status: () => status, error: () => error, progress: () => progress,
    on: (f) => { listeners.add(f); return () => listeners.delete(f); },
  };
})();
