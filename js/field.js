"use strict";
/* SkyBound · target name lookup and the field card: the SPARC4 comparison field
   (OPD 1.6 m), or any instrument's FoV loaded from an Aladin file (js/fov.js).
   Both are done by s4field, running on this computer as a small local server
   (without it, names are looked up by js/lookup.js: online from the browser,
   then the reference tables)
   (`python3 s4field/s4field.py --serve`, http://127.0.0.1:8417): a web page
   cannot query SIMBAD/Sesame, the Exoplanet Archive or the Gaia archive itself
   (no CORS), the helper can. This file asks it and draws the answer. It reads
   SkyBound's state (SB_STATE, set by render) and its sky functions
   (observedAltAz, sunAlt, moonAltAz, moonIllum, ...), which share the page's
   global scope.
   By Cleber Silva · UFC/MALTED */

const Field = (() => {
  const BANDS = ["g", "r", "i", "z"];
  const BAND_COL = { g: "#4ade80", r: "#f87171", i: "#c084fc", z: "#fbbf24" };
  const DSS_MIN = 20, DSS_MAX = 60;    // sky-image cutouts [arcmin]: follow the view (STScI max 60')
  const HELPER = "http://127.0.0.1:8417";
  const MIN_HELPER = "0.13.0";          // ... and any instrument's FoV (POST /api/fov)
  // SPARC4 guider (s4field GUIDER_*), used if the helper does not send them
  const GUIDER = { r_in: 569.68, r_out: 758.68, r_box: 664.18, box: 189.0 };
  const st = { js: null, rec: null, key: null, busy: false, pending: false, computed: false,
               plannedKey: null, tab: "basic", name: null, nameFor: null, lastTarget: null,
               helper: null, at: null, fo: null, pa: null, guiderTouched: false,
               v: null, preset: "field", imgFor: null, drag: null,
               // the instrument (js/fov.js); a loaded FoV is drawn here, s4field only does SPARC4
               fov: null, fpa: 0, ppa: null, ppaTouched: false, foFor: null, fovRev: 0, fids: new Map(),
               mode: "field" };      // "field" (comparison stars) or "mosaic"
  const isS4 = () => !st.fov || st.fov.engine === "s4field";
  const CONE_R = 0.27 * 3600;           // s4field's Gaia cone around the target [arcsec]
  const verOK = (v) => {
    const a = String(v || "0").split(".").map(Number), b = MIN_HELPER.split(".").map(Number);
    for (let i = 0; i < 3; i++) if ((a[i] || 0) !== b[i]) return (a[i] || 0) > b[i];
    return true;
  };
  const el = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const sgn = (x, n = 1) => (x < 0 ? "−" : "+") + Math.abs(x).toFixed(n);
  const sb = () => (typeof SB_STATE !== "undefined" ? SB_STATE : null);
  // the target's identity: its Gaia ID, or its position when no star is there (s4field "blank")
  const tkey = (js) => (js && js.target ? js.target.source_id || `${js.target.ra.toFixed(6)},${js.target.dec.toFixed(6)}` : null);

  /* ---- the helper ---------------------------------------------------------- */
  // A hosted page (GitHub Pages) reaching a program on the visitor's own computer
  // makes the browser ask first (Chrome's local network access prompt, since 142;
  // a refusal sticks for the site). So there the helper is only contacted once the
  // user turns it on; a page opened from disk or from localhost uses it as before.
  const LOCAL_PAGE = location.protocol === "file:" || /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  const HELPER_PREF = "skybound.helper.v1";
  function helperOn() {
    try { const v = localStorage.getItem(HELPER_PREF); return v ? v === "on" : LOCAL_PAGE; }
    catch (e) { return LOCAL_PAGE; }
  }
  function setHelper(on) {
    try { localStorage.setItem(HELPER_PREF, on ? "on" : "off"); } catch (e) { /* this session only */ }
    st.helperPref = on;
  }

  async function api(path, ms = 180000) {
    if (!(st.helperPref ?? helperOn())) throw new Error("HELPER_DOWN");   // turned off: as if not running
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), ms);
    let r;
    try { r = await fetch(HELPER + path, { signal: ctl.signal, cache: "no-store" }); }
    catch (e) { throw new Error(e.name === "AbortError" ? "TIMEOUT" : "HELPER_DOWN"); }
    finally { clearTimeout(timer); }
    let js;
    try { js = await r.json(); } catch (e) { throw new Error(`the helper answered HTTP ${r.status} without JSON`); }
    if (!r.ok) throw new Error(js.error || `HTTP ${r.status}`);
    return js;
  }

  const START = "<code>python3 s4field/s4field.py --serve</code>";
  const REPO = "https://github.com/cleber-si/SkyBound";
  function setStatus(kind, word, html) {
    el("fStatus").innerHTML = `<span class="pill ${kind}">${word}</span><div class="fld-status-body">${html}</div>`;
  }
  // how to get the helper running (the hosted page: the visitor may not have SkyBound's files)
  const INSTALL = `<details class="hlp-how"><summary>First time? Set up s4field (once, a few minutes)</summary><ol>
      <li>You need Python 3. Install what s4field uses: <code>pip install numpy astropy matplotlib</code>.</li>
      <li>Download SkyBound (<a href="${REPO}/archive/refs/heads/main.zip" target="_blank" rel="noopener">zip</a>,
        or <code>git clone ${REPO}.git</code>): s4field comes in it, in the <code>s4field/</code> folder.</li>
      <li>In the SkyBound folder, run ${START} and leave the terminal open. It answers only this
        computer, and keeps the Gaia stars it downloads in <code>cones/</code>, so a target also works offline later.</li>
      <li>Come back here and click <b>Connect to s4field on this computer</b>. If your browser asks whether this site may
        reach apps or devices on this computer, allow it.</li></ol>
      <p>Next time: just start s4field (step 3) and connect.</p></details>`;
  const btn = (id, text, primary) => `<button type="button" class="${primary ? "btn-primary" : "btn-ghost"} btn-sm hlp-btn" id="${id}">${text}</button>`;
  // the hosted page, helper not turned on: what it is, the one thing to click, and how to get it
  const offHtml = () => `<div class="hlp-head"><b>Comparison stars need s4field running on this computer.</b>
        This website can't rank them by itself yet: s4field is a small Python program, part of SkyBound, that does it on
        your machine. Everything else here works without it.</div>
      <div class="hlp-act">${btn("fHelperOn", "Connect to s4field on this computer", true)}
        <span class="hlp-alt">Already running it? Connect. Never set it up? Open the steps below.</span></div>${INSTALL}`;
  const lookingHtml = () => `looking for s4field on this computer… If it isn't running, start it now: ${START}`
      + (LOCAL_PAGE ? "" : ". If your browser asks to let this site reach apps or devices on this computer, <b>allow</b> it")
      + `. SkyBound connects by itself. ${LOCAL_PAGE ? "" : btn("fHelperOff", "Cancel")}${LOCAL_PAGE ? "" : INSTALL}`;
  const downHtml = () => (LOCAL_PAGE
    ? `not running. From the SkyBound folder run ${START}, then ${btn("fRetry", "Check again")}`
    : `s4field could not be reached on this computer. Is it running (${START})? If you blocked this site from
       reaching apps on this computer, allow it again in the site settings (the icon left of the address).
       ${btn("fRetry", "Try again", true)} ${btn("fHelperOff", "Don't use it")}${INSTALL}`);
  const okHtml = (js) => `s4field ${esc(js.s4field)} · ${js.cached.length} cached cone${js.cached.length === 1 ? "" : "s"}`
      + (LOCAL_PAGE ? "" : ` ${btn("fHelperOff", "Disconnect")}`);

  // no answer: "not connected" if the user hasn't turned the helper on, else "not reachable"
  // (unless SkyBound is still looking for it after a Connect)
  function noHelperStatus() {
    if (!(st.helperPref ?? helperOn())) setStatus("off", "NOT CONNECTED", offHtml());
    else if (Date.now() >= (st.pollUntil || 0)) setStatus("bad", "NO HELPER", downHtml());
  }

  // the buttons of the status line (rebuilt with it)
  function statusClick(ev) {
    const id = ev.target.id;
    if (id === "fHelperOn") { setHelper(true); ping(true); }
    else if (id === "fRetry") ping(true);
    else if (id === "fHelperOff") { setHelper(false); clearTimeout(st.pollTimer); ping(); }
  }

  // `look`: the user asked (Connect, Try again): keep looking for 3 minutes, so starting
  // s4field or allowing the browser's prompt is enough, with no further click
  async function ping(look) {
    clearTimeout(st.pollTimer);
    if (!(st.helperPref ?? helperOn())) {     // a hosted page, helper not turned on: don't knock
      setStatus("off", "NOT CONNECTED", offHtml());
      return false;
    }
    if (look === true) st.pollUntil = Date.now() + 180000;
    const polling = Date.now() < (st.pollUntil || 0);
    if (!polling) setStatus("wait", "…", "checking the s4field helper");
    try {
      const js = await api("/api/status", 5000);
      st.helper = js.s4field;
      st.pollUntil = 0;
      if (!verOK(js.s4field)) {
        setStatus("bad", "OLD HELPER", `s4field ${esc(js.s4field)} is running, but this page needs ${MIN_HELPER} or newer. `
          + `Stop it (Ctrl-C) and start the one in ${LOCAL_PAGE ? "this SkyBound folder" : `a fresh download of SkyBound (<a href="${REPO}/archive/refs/heads/main.zip">zip</a>)`}: ${START} `
          + `(copy your old <code>cones/</code> folder there to keep its downloads), then ${btn("fRetry", "Check again")}`);
        return false;
      }
      setStatus("ok", "HELPER", okHtml(js));
      if (st.js && st.js.nohelper) { st.key = null; compute(true); }   // drawn without it: now with its stars
      // a reduced (GAVO) cone, or one too small for a loaded FoV, is shown: the helper fetches
      // the better one in the background; when its cache changes, compute again; meanwhile
      // look once a minute
      const cones = js.cached.join("|") + "|" + (js.updated || "");
      const waiting = st.js && st.js.catalogue && (st.js.catalogue.lite || /SMALLER than this FoV/.test(st.js.catalogue.note));
      if (st.cones !== undefined && cones !== st.cones && waiting) {
        st.key = null;
        compute(true);
      }
      st.cones = cones;
      clearTimeout(st.liteTimer);
      if (waiting) st.liteTimer = setTimeout(ping, 60000);
      return true;
    } catch (e) {
      if (Date.now() < (st.pollUntil || 0)) {
        setStatus("wait", "LOOKING", lookingHtml());
        st.pollTimer = setTimeout(() => ping(), 3000);
      } else noHelperStatus();
      return false;
    }
  }

  // a <select> sized to its shown option, as the label is drawn (letter-spacing, capitals);
  // browsers size it by the plain option text, which cuts "TOI-706" to "TOI-70"
  function fitSelect(sel) {
    const o = sel.options[sel.selectedIndex];
    if (!o) return;
    const cs = getComputedStyle(sel), m = document.createElement("span");
    m.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font-style:${cs.fontStyle};font-weight:${cs.fontWeight};`
      + `font-size:${cs.fontSize};font-family:${cs.fontFamily};letter-spacing:${cs.letterSpacing};text-transform:${cs.textTransform}`;
    m.textContent = o.text;
    document.body.appendChild(m);
    const px = (k) => parseFloat(cs[k]) || 0;
    sel.style.width = Math.ceil(m.getBoundingClientRect().width + px("paddingLeft") + px("paddingRight")
                                + px("borderLeftWidth") + px("borderRightWidth") + 4) + "px";
    m.remove();
  }

  /* ---- coordinates --------------------------------------------------------- */
  const pad2 = (n) => String(n).padStart(2, "0");
  function sexa(deg, hours) {          // "HH MM SS.sss" / "+DD MM SS.ss", ASCII minus
    let v = hours ? deg / 15 : deg;
    const neg = v < 0; v = Math.abs(v);
    const dp = hours ? 3 : 2;
    let tot = Math.round(v * 3600 * 10 ** dp) / 10 ** dp;
    const a = Math.floor(tot / 3600); tot -= a * 3600;
    const b = Math.floor(tot / 60), c = tot - b * 60;
    return (neg ? "-" : hours ? "" : "+") + pad2(a) + " " + pad2(b) + " " + (c < 10 ? "0" : "") + c.toFixed(dp);
  }

  function coordText() {
    const ra = parseAngle(el("ra").value), dec = parseAngle(el("dec").value);
    if (!isFinite(ra) || !isFinite(dec)) return null;
    return `${(ra * 15).toFixed(6)} ${dec.toFixed(6)}`;
  }

  // RA and Dec typed together into the name box: "20 45 09.5 -31 20 27", "20:45:09 -31:20:27", "311.29 -31.34"
  function splitCoords(text) {
    const t = text.trim().replace(/[hdm°'"]/g, " ").replace(/\s+/g, " ");
    let m = t.match(/^(\d{1,2})[ :](\d{1,2})[ :](\d{1,2}(?:\.\d*)?) ([+-]?\d{1,2})[ :](\d{1,2})[ :](\d{1,2}(?:\.\d*)?)$/);
    if (m) return [`${m[1]} ${m[2]} ${m[3]}`, `${m[4]} ${m[5]} ${m[6]}`];
    m = t.match(/^(\d+(?:\.\d+)?),? ([+-]?\d+(?:\.\d+)?)$/);
    if (m) {
      const a = parseFloat(m[1]);
      return [a > 24 ? sexa(a, true) : m[1], m[2]];
    }
    return null;
  }

  // the selected target of SkyBound's list (index.html): a looked-up name goes to the helper as
  // the name (it caches the cone under it); "Target N" goes as its coordinates
  const listed = () => {
    const ra = parseAngle(el("ra").value), dec = parseAngle(el("dec").value);
    if (!isFinite(ra) || !isFinite(dec) || typeof tgtFind !== "function") return null;
    const i = tgtFind(ra, dec);
    return i >= 0 ? TARGETS[i] : null;
  };
  function fieldTarget() {
    const t = listed();
    return t && t.named ? t.name : coordText();
  }
  const targetLabel = () => {
    const t = listed();
    if (!fieldTarget()) return "";
    const xy = `${el("ra").value.trim()} ${el("dec").value.trim()}`;
    return t ? (t.named ? t.name : `${t.name} · ${xy}`) : xy;
  };

  /* ---- name lookup (top card) --------------------------------------------- */
  function nameMsg(kind, html) {
    el("tMsg").className = "name-msg" + (kind === "bad" ? " bad" : "");
    el("tMsg").innerHTML = html;
  }

  async function find() {
    const text = el("tName").value.trim();
    if (!text) { nameMsg("", ""); return; }
    const xy = splitCoords(text);
    if (xy) {                            // coordinates, not a name
      el("ra").value = xy[0]; el("dec").value = xy[1];
      st.name = null; st.nameFor = null; el("tName").value = "";
      nameMsg("", "coordinates moved to RA/Dec below");
      if (typeof tgtCommit === "function") tgtCommit();
      save(); render();
      return;
    }
    el("tFind").disabled = true;
    nameMsg("", `looking up <b>${esc(text)}</b>…`);
    try {
      const js = await api("/api/resolve?target=" + encodeURIComponent(text), 90000);
      useName(text, js.ra, js.dec, `${esc(js.source)}${js.note ? " · " + esc(js.note) : ""}`);
      if (el("fieldBody").style.display !== "none") compute(false);   // straight to its field
    } catch (e) {
      if (e.message === "HELPER_DOWN") await findWithoutHelper(text);
      else if (e.message === "TIMEOUT") nameMsg("bad", "The lookup took too long. Try again, or type RA/Dec below.");
      else {                             // the helper answered but doesn't know the name: last resort, the tables
        const hit = typeof Lookup !== "undefined" ? await Lookup.fromTables(text).catch(() => null) : null;
        if (hit && !hit.missing) useName(text, hit.ra, hit.dec, lookupLabel(hit));
        else nameMsg("bad", esc(e.message).replace(/\n\s*/g, "<br>"));
      }
    } finally {
      el("tFind").disabled = false;
    }
  }

  function useName(text, ra, dec, label) {
    el("ra").value = sexa(ra, true);
    el("dec").value = sexa(dec, false);
    if (typeof tgtCommit === "function") tgtCommit(text);      // into the target list, under its name
    st.name = text;
    st.nameFor = { ra: el("ra").value, dec: el("dec").value };
    nameMsg("", `<b>${esc(text)}</b> → ${esc(el("ra").value)} ${esc(el("dec").value)} · ${label}`);
    save();
    render();
  }
  const lookupLabel = (h) => `${esc(h.source)}${h.note ? " · " + esc(h.note) : ""}`;

  // no helper: js/lookup.js asks the online resolvers from the browser, then the reference tables
  async function findWithoutHelper(text) {
    if (typeof Lookup === "undefined") {
      nameMsg("bad", `Name lookup needs the s4field helper: run ${START}, or type RA/Dec below.`);
      return;
    }
    nameMsg("", `looking up <b>${esc(text)}</b> without the helper…`);
    try {
      const h = await Lookup.resolve(text);
      useName(text, h.ra, h.dec, `${lookupLabel(h)} <span class="hint">· without the helper</span>`);
    } catch (e) {
      nameMsg("bad", e.message);         // already HTML, names escaped
    }
  }

  function coordsEdited() {            // RA/Dec typed by hand: the name no longer applies
    if (st.name && st.nameFor && (el("ra").value !== st.nameFor.ra || el("dec").value !== st.nameFor.dec)) {
      st.name = null; st.nameFor = null;
      el("tName").value = "";
      nameMsg("", "");
    }
    save();
  }

  /* ---- time, airmass, Moon (advanced) -------------------------------------- */
  function bestTime(ctx, cw) {         // highest altitude while dark; else highest at all
    let best = null, bestAlt = -99, any = cw.base, anyAlt = -99;
    for (let t = cw.base; t <= cw.end; t += 300000) {
      const d = new Date(t), a = observedAltAz(d, ctx).alt;
      if (a > anyAlt) { anyAlt = a; any = t; }
      if (sunAlt(d, ctx) < DARK && a > bestAlt) { bestAlt = a; best = t; }
    }
    return best != null && bestAlt > 0 ? best : any;
  }

  function plannedMs(cw) {
    const m = String(el("fTime").value).match(/^(\d+):(\d+)/);
    if (!m) return null;
    const d = lp(cw.base);
    let t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), +m[1], +m[2], 0) - siteTZ * 60000;
    if (t < cw.base) t += 86400000;
    return t;
  }

  function fieldTime() {
    const s = sb();
    if (el("fWhen").value === "planned") { const t = plannedMs(s.cw); if (t != null) return t; }
    return s.refMs;
  }

  function conditions() {
    const s = sb(), t = fieldTime(), d = new Date(t);
    let alt = observedAltAz(d, s.ctx).alt, note = "";
    if (alt < 10) {                      // not up: the airmass at the target's best time
      const tX = bestTime(s.ctx, s.cw);
      note = `target at ${alt.toFixed(0)}° then; airmass taken at ${hmL(tX)}`;
      alt = observedAltAz(new Date(tX), s.ctx).alt;
    }
    const X = alt > 1 ? Math.min(4, 1 / Math.sin(alt * D2R)) : 4;
    const m = moonAltAz(d, s.ctx), fli = moonIllum(d);
    const moonAuto = m.alt <= 0 ? "new" : fli < 0.25 ? "new" : fli < 0.75 ? "quarter" : "full";
    return { t, X, note, moonAuto, moonAlt: m.alt, fli };
  }

  const epochAt = (t) => 2000 + (jdFromMs(t) - 2451545) / 365.25;

  function request() {
    const s = sb(), target = fieldTarget();
    if (!target) throw new Error("Give the target a name or valid RA/Dec at the top of the page.");
    const p = new URLSearchParams();
    p.set("target", target);
    p.set("mode", el("fMode").value);
    if (st.at) p.set("at", `${st.at.e.toFixed(1)},${st.at.n.toFixed(1)}`);
    p.set("max_comps", String(maxComps()));
    const [lo, hi] = dvValues();
    p.set("dv", `${lo <= DV_MIN ? "" : lo},${hi >= DV_MAX ? "" : hi}`);
    const f = filterList();
    if (f !== null) p.set("filters", f.length ? f.join(",") : "none");
    if (st.tab === "basic") {
      p.set("basic", "1");
      p.set("epoch", epochAt(s.refMs).toFixed(1));   // proper motion only
      return { p, basic: true };
    }
    const c = conditions();
    p.set("size", el("fSize").value);
    p.set("acq", el("fAcq").value);
    const tx = BANDS.map((b) => el("fT" + b).value.trim());
    if (tx.some((v) => v !== "")) {
      if (tx.some((v) => !(parseFloat(v) > 0))) throw new Error("Give all four exposure times, or leave all four empty.");
      p.set("texp", tx.map(parseFloat).join(","));
    }
    p.set("fwhm", el("fFwhm").value);
    p.set("fwhm_best", el("fFwhmBest").value);
    p.set("sky", el("fSky").value);
    const moon = el("fMoon").value === "auto" ? c.moonAuto : el("fMoon").value;
    p.set("moon", moon);
    const X = Math.round(c.X * 20) / 20;              // 0.05 steps
    p.set("airmass", X.toFixed(2));
    p.set("epoch", epochAt(c.t).toFixed(2));
    return { p, basic: false, X, moon, c };
  }

  function whenNote() {
    if (!sb() || st.tab !== "advanced") return;
    try {
      const c = conditions(), planned = el("fWhen").value === "planned";
      const moon = el("fMoon").value === "auto"
        ? `Moon ${c.moonAuto} (auto: ${c.moonAlt > 0 ? "up, " + Math.round(c.fli * 100) + "% lit" : "down"})`
        : `Moon ${el("fMoon").value}`;
      el("fWhenNote").textContent = `${hmL(c.t)}${planned ? " (planned)" : ""} · airmass `
        + `${(Math.round(c.X * 20) / 20).toFixed(2)} · ${moon}${c.note ? " · " + c.note : ""}`;
    } catch (e) { el("fWhenNote").textContent = ""; }
  }

  /* ---- compute ------------------------------------------------------------- */
  // answers per request (the request holds the target, the settings and the epoch), so going
  // back to a target of the list redraws its field without asking the helper again
  const CACHE = new Map(), CACHE_MAX = 24;
  async function cached(path) {
    if (CACHE.has(path)) return CACHE.get(path);
    const js = await api(path);
    js._label = (listed() || {}).name || "";
    if (js.catalogue && (js.catalogue.lite || /SMALLER than this FoV/.test(js.catalogue.note)))
      return js;                         // a better cone may arrive soon: ask again next time
    CACHE.set(path, js);
    if (CACHE.size > CACHE_MAX) CACHE.delete(CACHE.keys().next().value);
    return js;
  }
  function showError(msg) { el("fError").innerHTML = msg ? `<div class="fld-err">${msg}</div>` : ""; }

  async function compute(auto) {
    if (!sb()) return;
    if (st.mode === "mosaic") return computeMosaic(auto);
    if (st.busy) { st.pending = true; return; }
    if (!isS4()) return computeGeneric(auto);
    let q;
    try { q = request(); } catch (e) { if (!auto) showError(esc(e.message)); return; }
    const key = q.p.toString();
    if (auto && key === st.key) return;
    if (st.helper && !verOK(st.helper)) {
      showError(`The running helper (s4field ${esc(st.helper)}) is older than this page needs (${MIN_HELPER}): see above.`);
      return;
    }
    st.busy = true;
    el("fGo").disabled = true;
    el("fGo").textContent = "Computing…";
    showError("");
    try {
      const js = await cached("/api/field?" + key);
      const noAt = new URLSearchParams(key); noAt.delete("at");
      const recKey = noAt.toString();
      if (!js.pointing.fixed) { st.rec = js; st.recKey = key; }
      else if (st.recKey !== recKey) {   // keep "recommended" in step with the settings
        try { st.rec = await cached("/api/field?" + recKey); st.recKey = recKey; } catch (e) { st.rec = null; }
      }
      st.js = js; st.key = key; st.computed = true; st.lastTarget = fieldTarget();
      draw(js, q);
      ping();
    } catch (e) {
      if (st.js && st.lastTarget !== fieldTarget()) {   // the field on screen is another target's:
        el("fOut").hidden = true; st.js = null; st.key = null;   // hide it (and let its target compute
                                                                    // again), but keep following the selection
      }
      if (e.message === "HELPER_DOWN") {   // SPARC4 still drawn: its footprint and guider on the sky image
        noHelperStatus();
        st.key = key; st.computed = true; st.lastTarget = fieldTarget();
        drawGeneric(genericFrom(null));
      }
      else if (e.message === "TIMEOUT") showError("The helper did not answer within 3 minutes (a slow catalogue download?). Try again.");
      else showError(esc(e.message).replace(/\n/g, "<br>"));
    } finally {
      st.busy = false;
      el("fGo").disabled = false;
      el("fGo").textContent = goLabel();
      if (st.pending) { st.pending = false; compute(true); }
    }
  }
  const goLabel = () => (st.mode === "mosaic" ? "Show mosaic" : isS4() ? "Find field" : "Show field");

  /* ---- a loaded FoV (and SPARC4 without the helper) ------------------------ */
  // The FoV's science regions, turned to the field PA, go to the helper once
  // (POST /api/fov -> an id); /api/field?fov=id then ranks the comparison stars
  // by brightness in the chosen band (s4field basic mode). Without the helper
  // the field is drawn on the sky image alone.
  function genericFrom(res) {
    const _label = (listed() || {}).name || "";
    if (res) return { ...res, generic: true, ranked: !!res.pointing, _label };
    const ra = parseAngle(el("ra").value) * 15, dec = parseAngle(el("dec").value);
    return { generic: true, nohelper: true, ranked: false, _label,
             target: { ra, dec, blank: true, nostar: true, epoch: null, text: fieldTarget() },
             sky: null, catalogue: null, warnings: [] };
  }

  async function post(path, body) {
    let r;
    try {
      r = await fetch(HELPER + path, { method: "POST", headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify(body), cache: "no-store" });
    } catch (e) { throw new Error("HELPER_DOWN"); }
    let js;
    try { js = await r.json(); } catch (e) { throw new Error(`the helper answered HTTP ${r.status} without JSON`); }
    if (!r.ok) throw new Error(js.error || `HTTP ${r.status}`);
    return js;
  }
  // the FoV at this PA, as the helper knows it (its id)
  async function fovId() {
    const k = `${st.fov.id}|${st.fovRev}|${st.fpa}`;
    if (st.fids.has(k)) return st.fids.get(k);
    const sci = placed().science.map((r) => (r.shape === "circle" ? { shape: "circle", c: r.c, r: r.r }
                                                                  : { shape: "polygon", pts: r.pts }));
    const js = await post("/api/fov", { name: st.fov.name, science: sci });
    st.fids.set(k, js.fov);
    return js.fov;
  }
  // a polarimeter's second image, turned with the field [arcsec E, N]
  function twinVec() {
    const sp = FoV.settingsOf(st.fov).split;
    if (!sp) return null;
    const a = sp.pa * D2R;
    return FoV.rot(sp.sep * Math.sin(a), sp.sep * Math.cos(a), st.fpa);
  }
  function genericParams() {
    const s = sb(), cfg = FoV.settingsOf(st.fov), p = new URLSearchParams();
    p.set("target", fieldTarget());
    p.set("band", cfg.band);
    p.set("margin", String(cfg.margin));
    p.set("iso", String(cfg.iso));
    const tw = twinVec();
    if (tw) p.set("twin", `${tw[0].toFixed(2)},${tw[1].toFixed(2)}`);
    p.set("max_comps", String(maxComps()));
    const [lo, hi] = dvValues();
    p.set("dv", `${lo <= DV_MIN ? "" : lo},${hi >= DV_MAX ? "" : hi}`);
    if (!el("fFiltAll").checked) p.set("filters", "none");
    p.set("epoch", epochAt(s.refMs).toFixed(1));
    if (st.at) p.set("at", `${st.at.e.toFixed(1)},${st.at.n.toFixed(1)}`);
    return p;
  }
  async function fieldFov(p) {         // /api/field for this FoV; sent again if the helper forgot it (restarted)
    const ask = async () => {
      const q = new URLSearchParams(p);
      q.set("fov", await fovId());
      return cached("/api/field?" + q.toString());
    };
    try { return await ask(); }
    catch (e) {
      if (!/unknown fov/.test(e.message)) throw e;
      st.fids.clear();
      return ask();
    }
  }
  const genKey = (p) => `gen|${st.fov.id}|${st.fovRev}|${st.fpa}|${p.toString()}`;

  async function computeGeneric(auto) {
    const target = fieldTarget();
    if (!target) { if (!auto) showError("Give the target a name or valid RA/Dec at the top of the page."); return; }
    const p = genericParams(), key = genKey(p);
    if (auto && key === st.key) return;
    if (st.helper && !verOK(st.helper)) {
      showError(`The running helper (s4field ${esc(st.helper)}) is older than this page needs (${MIN_HELPER}): see above.`);
      return;
    }
    st.busy = true;
    el("fGo").disabled = true;
    el("fGo").textContent = "Computing…";
    showError("");
    try {
      let js;
      try {
        js = genericFrom(await fieldFov(p));
        if (!js.pointing.fixed) { st.rec = js; st.recKey = key; }
        else {                             // keep "recommended" in step with the settings
          const r = new URLSearchParams(p);
          r.delete("at");
          if (st.recKey !== genKey(r)) {
            try { st.rec = genericFrom(await fieldFov(r)); st.recKey = genKey(r); } catch (e) { st.rec = null; }
          }
        }
        ping();
      } catch (e) {
        if (e.message !== "HELPER_DOWN") throw e;
        noHelperStatus();
        js = genericFrom(null);
        st.rec = null;
      }
      st.js = js; st.key = key; st.computed = true; st.lastTarget = target;
      drawGeneric(js);
    } catch (e) {
      if (st.js && st.lastTarget !== target) { el("fOut").hidden = true; st.js = null; st.key = null; }
      showError(e.message === "TIMEOUT" ? "The helper did not answer within 3 minutes (a slow catalogue download?). Try again."
                                        : esc(e.message).replace(/\n/g, "<br>"));
    } finally {
      st.busy = false;
      el("fGo").disabled = false;
      el("fGo").textContent = goLabel();
      if (st.pending) { st.pending = false; compute(true); }
    }
  }

  // placed regions, cached while the FoV, its roles and the angles stay the same
  function placed() {
    const k = `${st.fov.id}|${st.fovRev}|${st.fpa}|${st.ppa}`;
    if (st.plc && st.plc.k === k) return st.plc.v;
    st.plc = { k, v: FoV.place(st.fov, st.fpa, st.ppa) };
    return st.plc.v;
  }
  const defaultPPA = () => {
    const A = FoV.analyse(st.fov);
    return A.probePA0 == null ? null : ((A.probePA0 + st.fpa) % 360 + 360) % 360;
  };

  // the stars of the helper's list inside the science area, brightest first
  function starsIn(js, P, role) {
    const sk = js.sky, out = [];
    if (!sk) return out;
    const rg = P[role], b = P.box[role];
    if (!b) return out;
    for (let i = 0; i < sk.E.length; i++) {
      const x = sk.E[i] - st.fo.e, y = sk.N[i] - st.fo.n;
      if (x < b[0] || x > b[2] || y < b[1] || y > b[3]) continue;
      for (const r of rg) if (FoV.contains(r, x, y)) { out.push({ i, G: sk.G[i], id: sk.id[i], E: sk.E[i], N: sk.N[i], reg: r }); break; }
    }
    return out.sort((a, b) => a.G - b.G);
  }

  function drawGeneric(js) {
    setStale(false);
    st.js = js;
    el("fOut").hidden = false;
    const t = js.target, s4 = isS4(), fov = st.fov, ranked = !!js.ranked, blank = !!t.blank;
    const cfg = FoV.settingsOf(fov), band = ranked ? js.settings.band : cfg.band, p = js.pointing;
    const f2 = (x) => (x == null || !isFinite(x) ? "—" : x.toFixed(2));
    const inList = typeof TARGETS !== "undefined" && TARGETS.some((x) => x.name === js._label);
    const tSel = inList ? `<select class="cd-tsel" id="fTargetSum" aria-label="target of this field" title="another target: its field">${tgtOptions()}</select>` : "";
    const colon = (deg, hours) => sexa(deg, hours).replace(/ /g, ":");
    const targetC = `${colon(t.ra, true)} ${colon(t.dec, false)}`;
    const copyBtn = (txt, id) => `<button type="button" class="copy-btn" ${id ? `id="${id}"` : ""} data-copy="${esc(txt)}" title="copy">copy</button>`;
    const tname = js._label || (blank && !t.nostar ? "position" : "target");
    const rec = ranked && st.rec && st.rec.generic && tkey(st.rec) === tkey(js) ? st.rec.pointing : null;
    const offNote = !ranked ? (s4 ? "geometry only: no comparison stars without the helper" : "drag the field to move it")
      : p.fixed ? `your pointing${rec ? ` · recommended ${sgn(rec.offset_ra_cosdec_arcsec)}″, ${sgn(rec.offset_dec_arcsec)}″ (η ${f2(rec.eta)})` : ""}`
      : blank ? "field centred on the position" : "move the field centre from the target";
    const tSmall = t.nostar ? "from RA/Dec (no star list)" : blank ? "no Gaia star here"
      : `G ${t.G.toFixed(2)}${ranked && t.mag != null && band !== "G" ? ` · ${band} ${t.mag.toFixed(2)}` : ""} · Gaia DR3 ${esc(t.source_id)}`;
    const nComp = ranked ? js.comparisons.length : 0;
    let h = `<div class="fld-offset">
        <div class="cd-item"><span class="cd-label">${s4 ? "TCSPD precise offset" : "Offset from the target"}${ranked && p.fixed ? " · manual" : ""}</span>
          <span class="cd-value" id="fOffVal"></span><small${ranked && p.fixed ? ' class="manual"' : ""}>${offNote}</small></div>
        <div class="cd-item"><span class="cd-label">Field centre</span>
          <span class="cd-value cd-coord" id="fCentreVal"></span>
          <small>RA, Dec (${t.nostar ? "from the RA/Dec above" : `ICRS, epoch ${t.epoch.toFixed(1)}`}) ${copyBtn("", "fCentreCopy")}</small></div>
        <div class="cd-item">${tSel || `<span class="cd-label">${esc(tname)}</span>`}
          <span class="cd-value cd-coord">${esc(targetC)}</span><small>${tSmall} ${copyBtn(targetC)}</small></div>`;
    if (ranked && !blank)
      h += `<div class="cd-item dep"><span class="cd-label">Comparisons</span><span class="cd-value">${nComp}</span>
          <small>${p.left_out ? `best ${nComp} of ${nComp + p.left_out} · ` : ""}${esc(band)} · basic${js.settings.twin ? " · two images" : ""}</small></div>
        <div class="cd-item dep"><span class="cd-label">Noise penalty <span style="text-transform:none">η</span> · photon noise</span>
          <span class="cd-value">${f2(p.eta)}</span><small>in ${esc(band)} · 1 = no added noise</small></div>`;
    else
      h += `<div class="cd-item"><span class="cd-label">Stars in the field</span>
          <span class="cd-value" id="fNStar">—</span><small id="fNStarNote"></small></div>`;
    h += `<div class="cd-item" id="fGuideCard"></div></div>`;
    el("fSummary").innerHTML = h;
    const A = FoV.analyse(fov);
    const how = !ranked
      ? (s4 ? "SPARC4 without the helper: its footprint and guider are drawn, but finding comparison stars needs s4field."
            : "Without the helper the field is drawn on the sky image only: comparison stars need s4field.")
      : blank ? "No target star: no comparison stars."
      : `Target BP−RP ${f2(t.bp_rp)}; V ${f2(t.V)}${band !== "V" && band !== "G" ? `; ${esc(band)} ${f2(t.mag)}` : ""}. Ranked by brightness in ${esc(band)}`
        + ` (photon noise only, no exposure model), with Gaia quality, isolation (${js.settings.iso_radius}″) and`
        + ` ${js.settings.edge_margin}″ edge checks.`;
    el("fMeta").innerHTML = `<div class="fld-meta">
        <b>${esc(fov.name)}</b>${fov.origin ? ` · ${esc(fov.origin)}` : ""}${fov.file ? ` · <span class="file-tag">${esc(fov.file)}</span>` : ""}<br>
        ${how}${js.sky ? ` <span id="fNStarMeta"></span>` : ""}<br>
        ${js.sky ? `Epoch ${t.epoch.toFixed(2)}. <span style="color:var(--grey-2)">${esc(js.catalogue.note)}</span>` : ""}</div>`;
    const notes = [];
    if (js.nohelper) notes.push({ w: (st.helperPref ?? helperOn())
      ? "s4field is not answering: the field is drawn on the sky image only, without Gaia stars or comparison stars."
      : "Drawn on the sky image only: Gaia stars and comparison stars need s4field (see Connect to s4field, above)." });
    const coneR = coneRadius(js);
    if (js.sky && A.outer > coneR * 0.95)
      notes.push({ w: `This FoV reaches ${(A.outer / 60).toFixed(1)}′ from its centre, but the Gaia stars cover only `
        + `${(coneR / 60).toFixed(1)}′ around the target (dashed grey circle): stars beyond it are not listed.` });
    for (const w of js.warnings) notes.push({ w, bad: /not on a detector/.test(w) });
    el("fError").innerHTML = notes.map((n) => `<div class="${n.bad ? "fld-err" : "warn fld-warn"}">${esc(n.w)}</div>`).join("");
    el("fNotes").innerHTML = "";
    el("fReport").textContent = ranked ? js.report : "";
    el("fRepWrap").hidden = !ranked;
    if (ranked) genericCompTable(js); else el("fMiss").innerHTML = "";
    viewerNewGeneric(js);
  }
  const coneRadius = (js) => (js && js.catalogue && js.catalogue.radius_deg ? js.catalogue.radius_deg * 3600 : CONE_R);

  // a ranked field: the target and its comparison stars, then the brightest not used and why
  function genericCompTable(js) {
    const band = js.settings.band, rows = js.stars.filter((r) => r.role)
      .sort((a, b) => (a.role === "target" ? -1 : b.role === "target" ? 1 : +a.label.slice(1) - +b.label.slice(1)));
    const f = (x) => (x == null || !isFinite(x) ? "—" : x.toFixed(2));
    const own = band !== "G" && band !== "V";          // its own column only when it is not G or V
    let h = `<tr><th></th><th>G</th><th>V</th>${own ? `<th>${esc(band)}</th>` : ""}<th>Δ${esc(band)}</th><th>BP−RP</th><th>from target</th></tr>`;
    for (const r of rows) {
      const tg = r.role === "target";
      h += `<tr class="${tg ? "tgt" : ""}"><td title="Gaia DR3 ${esc(r.source_id)}">${tg ? "target" : r.label}</td>`
        + `<td>${f(r.G)}</td><td>${f(r.V)}</td>${own ? `<td>${f(r.mag)}</td>` : ""}<td>${tg ? "" : sgn(r.dmag, 2)}</td><td>${f(r.bp_rp)}</td>`
        + `<td>${tg ? "" : Math.hypot(r.dE_target_arcsec, r.dN_target_arcsec).toFixed(0) + "″"}</td></tr>`;
    }
    if (js.target.blank) {             // no target: the brightest stars in the field
      const top = js.stars.slice(0, 12);
      h = "<tr><th></th><th>G</th><th>V</th><th>BP−RP</th><th>from position</th></tr>"
        + top.map((r, k) => `<tr><td title="Gaia DR3 ${esc(r.source_id)}">${k + 1}</td><td>${f(r.G)}</td><td>${f(r.V)}</td><td>${f(r.bp_rp)}</td>`
          + `<td>${Math.hypot(r.dE_target_arcsec, r.dN_target_arcsec).toFixed(0)}″</td></tr>`).join("");
    }
    el("fTable").innerHTML = h;
    const miss = js.target.blank ? [] : js.stars.filter((r) => r.role === "" && r.reasons).slice(0, 8);
    el("fMiss").innerHTML = miss.length
      ? `<details class="rep" ${rows.length <= 1 ? "open" : ""}><summary>Not used: the ${miss.length} brightest other stars in the field, and why</summary>
          <table class="comps miss"><tr><th></th><th>G</th><th>${esc(band)}</th><th>from target</th><th>why not</th></tr>`
        + miss.map((r, k) => `<tr><td title="Gaia DR3 ${esc(r.source_id)}">×${k + 1}</td><td>${f(r.G)}</td><td>${f(r.mag)}</td>`
          + `<td>${Math.hypot(r.dE_target_arcsec, r.dN_target_arcsec).toFixed(0)}″</td><td class="why">${esc(r.reasons)}</td></tr>`).join("")
        + "</table></details>"
      : "";
  }

  function viewerNewGeneric(js) {
    const newTarget = st.imgFor !== tkey(js), forKey = `${st.fov.id}|${tkey(js)}`;
    if (st.foFor !== forKey) {           // another FoV or target: the field centred on the target
      st.foFor = forKey; st.fo = { e: 0, n: 0 };
      st.ppaTouched = false; st.v = null;
    }
    if (js.ranked) st.fo = { e: js.pointing.offset_ra_cosdec_arcsec, n: js.pointing.offset_dec_arcsec };
    if (!st.ppaTouched) st.ppa = defaultPPA();
    if (newTarget || !st.v) preset(st.preset in { field: 1, guider: 1 } ? st.preset : "field", true);
    if (newTarget) { st.imgFor = tkey(js); st.sky = null; st.imgOK = null; el("fLayImg").innerHTML = ""; }
    redraw();
  }

  // while a moved field waits for its answer: old comparisons faded, list and eta dimmed
  function setStale(on) {
    if (st.stale === on) return;
    st.stale = on;
    el("fOut").classList.toggle("stale", on);
    clearTimeout(st.slowTimer);
    st.slow = false;
  }
  function waitingNote() {             // "updating comparisons…" only if the answer takes over 0.5 s
    clearTimeout(st.slowTimer);
    st.slowTimer = setTimeout(() => { if (st.stale) { st.slow = true; redraw(); } }, 500);
  }

  /* ---- output -------------------------------------------------------------- */
  function noteLabel(w) {
    if (/EXPOSURE TIMES ESTIMATED/.test(w)) return "estimated exposure times";
    if (/^No Gaia star within/.test(w)) return "empty position";
    if (/reduced catalogue/.test(w)) return "reduced catalogue (GAVO)";
    if (/readout trail/.test(w)) return "target on a readout trail";
    if (/outside the field/.test(w)) return "target off the field";
    if (/No usable comparison/.test(w)) return "no comparison in some bands";
    return "note";
  }

  function draw(js, q) {
    setStale(false);                     // whatever is drawn now is the answer for what is shown
    el("fOut").hidden = false;
    const p = js.pointing, s = js.settings, t = js.target, basic = !!s.basic, blank = !!t.blank;
    const tp = p.target_pixel.r;
    const f2 = (x) => (x == null ? "—" : x.toFixed(2));
    const eb = BANDS.filter((b) => b in p.eta_bands).map((b) => `${b} ${f2(p.eta_bands[b])}`).join(" · ");
    const comps = js.stars.filter((r) => /^C\d+$/.test(r.label));
    const rec = st.rec && tkey(st.rec) === tkey(js) ? st.rec.pointing : null;
    const manualNote = p.fixed
      ? `<small class="manual">your pointing${rec ? ` · recommended ${sgn(rec.offset_ra_cosdec_arcsec)}″, ${sgn(rec.offset_dec_arcsec)}″ (η ${f2(rec.eta)})` : ""}</small>`
      : `<small>${blank ? "field centred on the position" : "move the field centre from the target"}</small>`;
    const colon = (deg, hours) => sexa(deg, hours).replace(/ /g, ":");
    const fieldC = `${colon(p.ra, true)} ${colon(p.dec, false)}`, targetC = `${colon(t.ra, true)} ${colon(t.dec, false)}`;
    const tname = js._label || (blank ? "position" : "target");
    // a target of the list: its label is a list of all of them, to switch without scrolling up
    const inList = typeof TARGETS !== "undefined" && TARGETS.some((x) => x.name === js._label);
    const tSel = inList ? `<select class="cd-tsel" id="fTargetSum" aria-label="target of this field" title="another target: its field">${tgtOptions()}</select>` : "";
    const copyBtn = (txt) => `<button type="button" class="copy-btn" data-copy="${esc(txt)}" title="copy">copy</button>`;
    el("fSummary").innerHTML = `<div class="fld-offset">
        <div class="cd-item"><span class="cd-label">TCSPD precise offset${p.fixed ? " · manual" : ""}</span>
          <span class="cd-value" id="fOffVal">RA·cosδ ${sgn(p.offset_ra_cosdec_arcsec)}″ &nbsp; DEC ${sgn(p.offset_dec_arcsec)}″</span>
          ${manualNote}</div>
        <div class="cd-item"><span class="cd-label">Field centre</span>
          <span class="cd-value cd-coord" id="fCentreVal">${esc(fieldC)}</span>
          <small>RA, Dec (ICRS, epoch ${t.epoch.toFixed(1)}) ${copyBtn(fieldC)}</small></div>
        <div class="cd-item">${tSel || `<span class="cd-label">${esc(tname)}</span>`}
          <span class="cd-value cd-coord">${esc(targetC)}</span>
          <small>${blank ? "no Gaia star here" : `G ${t.G.toFixed(2)} · Gaia DR3 ${esc(t.source_id)}`} ${copyBtn(targetC)}</small></div>
        <div class="cd-item dep"><span class="cd-label">Comparisons</span>
          <span class="cd-value">${blank ? "—" : comps.length}</span><small>${p.left_out ? `best ${comps.length} of ${comps.length + p.left_out} · ` : ""}${s.mode === "polar" ? "polarimetric" : "photometric"} · ${esc(s.size)}${basic ? " · basic" : ""}</small></div>
        ${blank ? "" : `<div class="cd-item dep"><span class="cd-label">Noise penalty <span style="text-transform:none">η</span>${basic ? " · photon noise" : ""}
            <button type="button" class="info-btn" id="fEtaBtn" aria-expanded="false" title="What is the noise penalty η?">?</button></span>
          <span class="cd-value">${f2(p.eta)}</span><small>${eb}</small></div>`}
        <div class="cd-item" id="fGuideCard"></div>
      </div>
      <div class="eta-help" id="fEtaHelp" hidden>
        <b>Noise penalty η = σ<sub>diff</sub> / σ<sub>target</sub></b>: how much the comparison stars add to the scatter of the
        differential light curve (target ÷ weighted sum of comparisons), per exposure.
        η = 1 would mean they add nothing (infinitely many bright comparisons); η = 1.41 is what one comparison
        exactly as bright as the target gives; η = 2 means the light curve is twice as noisy as the target alone.
        Lower is better. The large number is the worst of the four bands; each band is listed below it.
        ${basic ? "<br><i>Basic</i> counts photon noise only, so faint comparisons look worse than they will be for a bright, scintillation-limited target."
                : "<br><i>Advanced</i> uses the full noise model: photon, sky and read noise, and scintillation at the chosen airmass."}
        A field's η is scored with each star weighted by its noise and by how close its colour is to the target's.
      </div>`;
    const texp = BANDS.every((b) => s.t_exp[b] != null)
      ? BANDS.map((b) => `${b} ${s.t_exp[b]}`).join(" · ") + " s" + (s.texp_estimated ? " (estimated)" : "") : "not given";
    el("fMeta").innerHTML = `<div class="fld-meta">
        ${blank ? "No target star: the field is centred on the position." : `Target BP−RP ${t.bp_rp.toFixed(2)}; g r i z ${BANDS.map((b) => t.mags[b].toFixed(2)).join(" ")}.`}<br>
        After GO the ${blank ? "position" : "target"} should sit near <b>x=${Math.round(tp[0])}, y=${Math.round(tp[1])}</b> on r
        (channel 2, raw orientation).<br>
        ${blank ? "" : basic ? "Ranked by brightness only." : `t_exp ${esc(texp)} · airmass ${Number(s.airmass).toFixed(2)} · Moon ${esc(s.moon)}.`}
        Epoch ${t.epoch.toFixed(2)}. <span style="color:var(--grey-2)">${esc(js.catalogue.note)}</span></div>`;
    const alarm = js.warnings.filter((w) => /outside the field|No usable comparison|^No Gaia star within/.test(w));
    el("fError").innerHTML = alarm.map((w) => `<div class="${/outside the field/.test(w) && p.target_inside === false ? "fld-err" : "warn fld-warn"}">${esc(w)}</div>`).join("");
    el("fNotes").innerHTML = js.warnings.length
      ? `<details class="rep"><summary>Notes: ${js.warnings.map(noteLabel).join(" · ")}</summary>`
        + js.warnings.map((w) => `<div class="warn fld-warn">${esc(w)}</div>`).join("") + "</details>"
      : "";
    if (st.etaHelp && el("fEtaBtn")) { el("fEtaHelp").hidden = false; el("fEtaBtn").setAttribute("aria-expanded", "true"); }
    el("fReport").textContent = js.report;
    el("fRepWrap").hidden = false;
    el("fReset").hidden = !(p.fixed || st.guiderTouched);
    table(js);
    viewerNewResult(js);
  }

  function table(js) {
    if (js.target.blank) {               // no target, no comparisons: the brightest stars in the field
      const top = js.stars.slice(0, 12);
      el("fTable").innerHTML = "<tr><th></th><th>G</th><th>V</th><th>BP−RP</th><th>from position</th><th>channels</th></tr>"
        + top.map((r, k) => `<tr><td title="Gaia DR3 ${esc(r.source_id)}">${k + 1}</td><td>${r.G.toFixed(2)}</td>`
          + `<td>${r.V == null ? "—" : r.V.toFixed(2)}</td><td>${r.bp_rp == null ? "—" : r.bp_rp.toFixed(2)}</td>`
          + `<td>${Math.hypot(r.dE_target_arcsec, r.dN_target_arcsec).toFixed(0)}″</td>`
          + `<td>${BANDS.map((b) => (r["on_" + b] ? b : "·")).join("")}</td></tr>`).join("")
        + (top.length ? "" : '<tr><td colspan="6" style="text-align:left">no Gaia star (G &lt; 18) on the detector</td></tr>');
      el("fMiss").innerHTML = "";
      return;
    }
    const tm = js.target.mags;
    const rows = js.stars.filter((r) => r.role === "target" || /^C\d+$/.test(r.label))
      .sort((a, b) => (a.role === "target" ? -1 : b.role === "target" ? 1 : +a.label.slice(1) - +b.label.slice(1)));
    let h = "<tr><th></th><th>G</th><th>V</th>" + BANDS.map((b) => `<th>Δ${b}</th>`).join("") + "<th>bands</th><th>from target</th></tr>";
    for (const r of rows) {
      const tgt = r.role === "target";
      const bands = BANDS.map((b) => (r["used_" + b] ? b : r["trail_" + b] ? "~" : "·")).join("");
      const dist = Math.hypot(r.dE_target_arcsec, r.dN_target_arcsec);
      h += `<tr class="${tgt ? "tgt" : ""}"><td title="Gaia DR3 ${esc(r.source_id)}">${tgt ? "target" : r.label}</td>`
        + `<td>${r.G.toFixed(2)}</td><td>${r.V == null ? "—" : r.V.toFixed(2)}</td>`
        + BANDS.map((b) => `<td>${tgt ? tm[b].toFixed(2) : sgn(r[b + "_mag"] - tm[b], 2)}</td>`).join("")
        + `<td>${tgt ? "" : bands}</td><td>${tgt ? "" : dist.toFixed(0) + "″"}</td></tr>`;
    }
    el("fTable").innerHTML = h;
    // the brightest stars in the field that are not used, and why
    const miss = js.stars.filter((r) => r.role === "" && r.reasons).slice(0, 8);
    el("fMiss").innerHTML = miss.length
      ? `<details class="rep" ${rows.length <= 1 ? "open" : ""}><summary>Not used: the ${miss.length} brightest other stars in the field, and why</summary>
          <table class="comps miss"><tr><th></th><th>G</th><th>V</th><th>from target</th><th>why not</th></tr>`
        + miss.map((r, k) => `<tr><td title="Gaia DR3 ${esc(r.source_id)}">×${k + 1}</td><td>${r.G.toFixed(2)}</td>`
          + `<td>${r.V == null ? "—" : r.V.toFixed(2)}</td><td>${Math.hypot(r.dE_target_arcsec, r.dN_target_arcsec).toFixed(0)}″</td>`
          + `<td class="why">${esc(r.reasons)}</td></tr>`).join("") + "</table></details>"
      : "";
  }

  /* ---- interactive sky viewer --------------------------------------------- */
  // Sky frame: arcsec East (E) and North (N) of the target; SVG x = -E, y = -N.
  // The field centre sits at st.fo, the guider box at position angle st.pa
  // (east of north) from it; both can be dragged.
  const NS = 'vector-effect="non-scaling-stroke"';
  const PRESET = { field: 320, guider: 860 };

  function viewerNewResult(js) {
    st.foFor = null;                     // a loaded FoV starts again from the target
    const p = js.pointing, newTarget = st.imgFor !== tkey(js);
    st.fo = { e: p.offset_ra_cosdec_arcsec, n: p.offset_dec_arcsec };
    if (newTarget || !st.guiderTouched) {
      st.guiderTouched = false;
      st.pa = js.guider[0] ? js.guider[0].pa : (st.pa == null ? 0 : st.pa);
    }
    if (newTarget || !st.v) preset(st.preset, true);
    if (newTarget) {                     // new sky: drop the old images
      st.imgFor = tkey(js);
      st.sky = null; st.imgOK = null;
      el("fLayImg").innerHTML = "";
    }
    redraw();
  }

  // view half-widths: SPARC4's fixed ones; a loaded FoV's from its size
  function presetHalf(name) {
    if (st.js && st.js.mosaic) {          // the turned mosaic's extent on the sky
      const T = mosaicT(), a = mosPA() * D2R, c = Math.abs(Math.cos(a)), s = Math.abs(Math.sin(a));
      return Math.max(T.width * c + T.height * s, T.width * s + T.height * c) * 0.56 + 10;
    }
    if (!(st.js && st.js.generic) || isS4()) return PRESET[name];
    const A = FoV.analyse(st.fov);
    return Math.max(2, name === "guider" ? A.outer * 1.12 : A.extent * 1.25);
  }
  function zoomRange() {
    if (st.js && st.js.mosaic) { const T = mosaicT(); return [Math.min(40, T.box.w * 0.3), Math.max(1400, Math.max(T.width, T.height) * 1.6)]; }
    if (!(st.js && st.js.generic) || isS4()) return [40, 1400];
    const A = FoV.analyse(st.fov);
    return [Math.min(40, Math.max(1, A.extent * 0.15)), Math.max(1400, A.outer * 1.6)];
  }
  function preset(name, quiet) {
    st.preset = name;                    // remembered for double-click
    st.v = { x: -st.fo.e, y: -st.fo.n, half: presetHalf(name) };
    if (!quiet) redraw();
  }

  function markPreset() {              // a preset button is lit only while the view is exactly it
    const v = st.v, centred = Math.abs(v.x + st.fo.e) < 0.5 && Math.abs(v.y + st.fo.n) < 0.5;
    const near = (h) => Math.abs(v.half - h) < Math.max(0.5, h * 1e-3);
    el("fViewField").classList.toggle("on", centred && near(presetHalf("field")));
    el("fViewGuider").classList.toggle("on", centred && near(presetHalf("guider")));
  }

  // gnomonic offsets [arcsec E, N] from (ra0, dec0) -> RA, Dec [deg]
  function fromTangent(e, n, ra0, dec0) {
    const x = e / 206264.806, y = n / 206264.806, d0 = dec0 * D2R;
    const den = Math.cos(d0) - y * Math.sin(d0);
    const ra = ra0 + Math.atan2(x, den) / D2R;
    const dec = Math.atan2(Math.sin(d0) + y * Math.cos(d0), Math.hypot(x, den)) / D2R;
    return [((ra % 360) + 360) % 360, dec];
  }

  // DSS cutouts follow the view: a new one, centred on the view, whenever the
  // view leaves the area already loaded. The old image stays until the new
  // one has arrived, so panning never shows a blank sky.
  function ensureSky() {
    clearTimeout(st.skyTimer);
    st.skyTimer = setTimeout(() => {
      const js = st.js, v = st.v;
      if (!js || !v) return;
      const need = v.half * 1.1, cur = st.sky;
      if (cur && Math.abs(v.x - cur.x) + need <= cur.W / 2 && Math.abs(v.y - cur.y) + need <= cur.W / 2) return;
      if (cur && cur.W >= DSS_MAX * 60 && Math.abs(v.x - cur.x) < 1 && Math.abs(v.y - cur.y) < 1) return;
      const am = Math.min(DSS_MAX, Math.max(DSS_MIN, Math.ceil((v.half * 2 * 1.6) / 60)));
      const W = am * 60, [ra, dec] = fromTangent(-v.x, -v.y, js.target.ra, js.target.dec);
      const url = "https://archive.stsci.edu/cgi-bin/dss_search?v=poss2ukstu_red"
        + `&r=${ra.toFixed(5)}&d=${dec.toFixed(5)}&e=J2000&h=${am}&w=${am}&f=gif`;
      st.sky = { x: v.x, y: v.y, W };
      const img = document.createElementNS("http://www.w3.org/2000/svg", "image");
      for (const [k, val] of [["x", v.x - W / 2], ["y", v.y - W / 2], ["width", W], ["height", W],
                              ["preserveAspectRatio", "none"], ["opacity", "0"], ["href", url]]) img.setAttribute(k, val);
      el("fImgNote").textContent = "DSS2 red (STScI) · N up, E left · loading…";
      img.addEventListener("load", () => {
        img.setAttribute("opacity", "0.92");
        for (const old of [...el("fLayImg").children]) if (old !== img && +old.getAttribute("width") <= W) old.remove();
        el("fImgNote").textContent = "DSS2 red (STScI) · N up, E left";
        if (st.imgOK !== true) { st.imgOK = true; redraw(); }
      });
      img.addEventListener("error", () => {
        img.remove();
        if (!el("fLayImg").children.length) {
          st.imgOK = false;
          el("fImgNote").textContent = st.js && st.js.sky ? "no sky image (offline?) · Gaia stars drawn instead" : "no sky image (offline?)";
          redraw();
        } else el("fImgNote").textContent = "DSS2 red (STScI) · could not load more sky here";
        // st.sky keeps the failed area, so it is not retried until the view moves past it
      });
      el("fLayImg").appendChild(img);
    }, 300);
  }

  function inField(px, py) {            // a point (E, N from the field centre) inside the common area
    const poly = st.js.footprint.common;
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  // while the field is being moved: is this star (offsets from the target) inside it right now?
  const inMovedField = (r) => inField(r.dE_target_arcsec - st.fo.e, r.dN_target_arcsec - st.fo.n);

  function targetInside() {             // target at least the edge margin inside the common area
    const poly = st.js.footprint.common, m = st.js.settings.edge_margin || 12;
    const px = -st.fo.e, py = -st.fo.n;    // target relative to the field centre (E, N)
    let inside = false, dmin = Infinity;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) inside = !inside;
      const dx = xj - xi, dy = yj - yi, t = Math.max(0, Math.min(1, ((px - xi) * dx + (py - yi) * dy) / (dx * dx + dy * dy)));
      dmin = Math.min(dmin, Math.hypot(px - xi - t * dx, py - yi - t * dy));
    }
    return inside && dmin >= m;
  }

  function guideBox() {                // stars inside the guider box at st.pa, brightest first
    const sky = st.js.sky, gd = st.js.footprint.guider || GUIDER, out = [];
    if (!sky) return out;
    const a = st.pa * D2R, sa = Math.sin(a), ca = Math.cos(a), h = gd.box / 2;
    for (let i = 0; i < sky.E.length; i++) {
      const e = sky.E[i] - st.fo.e, n = sky.N[i] - st.fo.n;
      const rad = e * sa + n * ca, tan = e * ca - n * sa;
      if (Math.abs(rad - gd.r_box) <= h && Math.abs(tan) <= h) out.push({ i, G: sky.G[i], id: sky.id[i], rad, tan });
    }
    return out.sort((x, y) => x.G - y.G);
  }

  // compass and scale bar, fixed in the view's corners
  function decor(out, v, fs) {
    const half = v.half, cx = v.x + half * 0.80, cy = v.y + half * 0.80, L = half * 0.11;
    out.push(`<g stroke="#e2e8f0" stroke-width="1.4" pointer-events="none"><line ${NS} x1="${cx}" y1="${cy}" x2="${cx}" y2="${cy - L}"/><line ${NS} x1="${cx}" y1="${cy}" x2="${cx - L}" y2="${cy}"/></g>`);
    out.push(`<text x="${cx - fs * 0.3}" y="${cy - L - fs * 0.3}" font-size="${fs}" fill="#e2e8f0" pointer-events="none">N</text><text x="${cx - L - fs}" y="${cy + fs * 0.35}" font-size="${fs}" fill="#e2e8f0" pointer-events="none">E</text>`);
    const steps = [1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200, 1800, 3600];
    const bar = steps.find((x) => x >= half * 0.25) || 3600, bx0 = v.x - half * 0.9, by0 = v.y + half * 0.88;
    out.push(`<line x1="${bx0}" y1="${by0}" x2="${bx0 + bar}" y2="${by0}" stroke="#e2e8f0" stroke-width="2" ${NS} pointer-events="none"/><text x="${bx0}" y="${by0 - fs * 0.4}" font-size="${fs}" fill="#e2e8f0" pointer-events="none">${bar >= 60 ? bar / 60 + "′" : bar + "″"}</text>`);
  }

  const fmtOff = () => `RA·cosδ ${sgn(st.fo.e)}″  DEC ${sgn(st.fo.n)}″`;
  function centreText() {
    const t = st.js.target, [ra, dec] = fromTangent(st.fo.e, st.fo.n, t.ra, t.dec);
    return `${sexa(ra, true).replace(/ /g, ":")} ${sexa(dec, false).replace(/ /g, ":")}`;
  }

  // a loaded FoV, or SPARC4 without the helper: every region, at the field PA, on the sky
  function redrawGeneric() {
    const js = st.js, v = st.v, fov = st.fov, s4 = isS4();
    const svg = el("fSvg"), half = v.half, fs = half * 0.042;
    const fx = -st.fo.e, fy = -st.fo.n, out = [];
    const P = placed(), A = P.A;
    const pts = (rg) => rg.pts.map(([e, n]) => `${(-e).toFixed(2)},${(-n).toFixed(2)}`).join(" ");
    const shape = (rg, attrs) => (rg.shape === "circle"
      ? `<circle cx="${-rg.c[0]}" cy="${-rg.c[1]}" r="${rg.r}" ${attrs}/>`
      : `<polygon points="${pts(rg)}" ${attrs}/>`);
    // Gaia stars when there is no sky image
    if (st.imgOK === false && js.sky) {
      const sk = js.sky;
      for (let i = 0; i < sk.E.length; i++) {
        const r = Math.max(1.2, (17.5 - sk.G[i]) * 0.9) * half / 320;
        out.push(`<circle cx="${-sk.E[i]}" cy="${-sk.N[i]}" r="${r.toFixed(2)}" fill="#cbd5e1" opacity=".85"/>`);
      }
    }
    // where the helper's star list ends, when the FoV reaches beyond it
    const coneR = coneRadius(js), ranked = !!js.ranked;
    if (js.sky && A.outer + Math.hypot(st.fo.e, st.fo.n) > coneR * 0.95)
      out.push(`<circle cx="0" cy="0" r="${coneR}" fill="none" stroke="rgba(203,213,225,.55)" stroke-dasharray="10 6" stroke-width="1.2" ${NS} pointer-events="none"><title>edge of the Gaia star list (${(coneR / 60).toFixed(1)}′ around the target)</title></circle>`);
    // the recommended field centre, when the field was moved
    if (ranked && st.rec && st.rec.generic && tkey(st.rec) === tkey(js)) {
      const rp = st.rec.pointing, rx = -rp.offset_ra_cosdec_arcsec, ry = -rp.offset_dec_arcsec, c = fs * 0.6;
      if (Math.hypot(rx - fx, ry - fy) > half * 0.004)
        out.push(`<g stroke="#fbd38d" stroke-width="1.4" pointer-events="none"><line ${NS} x1="${rx - c}" y1="${ry}" x2="${rx + c}" y2="${ry}"/><line ${NS} x1="${rx}" y1="${ry - c}" x2="${rx}" y2="${ry + c}"/></g>`
          + `<text x="${rx + c}" y="${ry - c * 0.6}" font-size="${fs * 0.8}" fill="#fbd38d" pointer-events="none">recommended</text>`);
    }
    // the field: science (draggable), patrol circles, guider, others
    const inSci = P.science.some((r) => FoV.contains(r, -st.fo.e, -st.fo.n));
    const fc = inSci ? "#E2A123" : "#f87171", fill = inSci ? "rgba(226,161,35,.13)" : "rgba(248,113,113,.16)";
    const what = js.target.blank && !js.target.nostar ? "position" : "target";
    const tip = inSci ? "drag to move the field" : `the ${what} is not on a detector here (off the field, or in a gap)`;
    out.push(`<g transform="translate(${fx} ${fy})">`);
    for (const rg of P.other) out.push(shape(rg, `fill="none" stroke="rgba(203,213,225,.6)" stroke-width="1" ${NS} pointer-events="none"`));
    const thin = P.science.length > 40;  // many small shapes (IFU fibres, mosaics): lighter lines
    out.push(`<g><title>${tip}</title>`);
    for (const rg of P.science)
      out.push(shape(rg, `class="drag-field" data-drag="field" fill="${fill}" stroke="${fc}" stroke-width="${thin ? 0.8 : 1.4}" ${NS}`));
    out.push("</g>");
    for (const rg of P.patrol)
      out.push(shape(rg, `fill="none" stroke="rgba(147,197,253,.5)" stroke-dasharray="3 5" stroke-width="1" ${NS} pointer-events="none"`));
    for (const rg of P.guider)
      out.push(shape(rg, `${A.probe ? 'class="drag-guider" data-drag="guider"' : 'pointer-events="none"'} fill="rgba(147,197,253,.08)" stroke="#93c5fd" stroke-width="1.6" ${NS}`));
    if (A.probe && st.ppa != null && P.guider.length) {
      const c = P.guider.map((r) => (r.shape === "circle" ? r.c : [r.pts.reduce((a, p) => a + p[0], 0) / r.pts.length, r.pts.reduce((a, p) => a + p[1], 0) / r.pts.length]));
      const ce = c.reduce((a, p) => a + p[0], 0) / c.length, cn = c.reduce((a, p) => a + p[1], 0) / c.length;
      out.push(`<line x1="0" y1="0" x2="${-ce}" y2="${-cn}" stroke="rgba(147,197,253,.35)" stroke-width="1" ${NS} pointer-events="none"/>`);
    }
    // the field's own axes: a short "up" tick shows its PA when it is turned
    if (st.fpa) {
      const [ue, un] = FoV.rot(0, A.extent * 0.25, st.fpa);
      out.push(`<line x1="0" y1="0" x2="${-ue}" y2="${-un}" stroke="${fc}" stroke-width="1.2" stroke-dasharray="4 3" ${NS} pointer-events="none"/>`);
    }
    out.push("</g>");
    // guide stars in the guider
    const inGd = starsIn(js, P, "guider"), rng = (fov.guide && fov.guide.g) || FoV.settingsOf(fov).guide;
    const guide = rng ? inGd.find((s) => s.G >= rng[0] && s.G <= rng[1]) : inGd[0];
    for (const s of inGd.slice(0, 6)) {
      const best = s === guide;
      out.push(`<circle cx="${-s.E}" cy="${-s.N}" r="${fs * (best ? 0.6 : 0.4)}" fill="none" stroke="#93c5fd" stroke-width="${best ? 1.8 : 1}" ${NS} pointer-events="none"/>`);
      if (best) out.push(`<text x="${-s.E + fs * 0.75}" y="${-s.N + fs * 0.35}" font-size="${fs}" fill="#93c5fd" pointer-events="none">G ${s.G.toFixed(1)}</text>`);
    }
    // ranked: comparison stars and the brightest not used (as in the tables);
    // else the brightest stars in the field, numbered as in the table
    const inF = starsIn(js, P, "science"), top = ranked ? [] : inF.slice(0, GEN_TOP);
    if (ranked) {
      const moved = (r) => P.science.some((rg) => FoV.contains(rg, r.dE_target_arcsec - st.fo.e, r.dN_target_arcsec - st.fo.n));
      const tw = js.settings.twin;
      js.stars.filter((r) => r.role === "" && r.reasons).slice(0, 8).forEach((r, k) => {
        if (st.stale && !moved(r)) return;
        const x = -r.dE_target_arcsec, y = -r.dN_target_arcsec;
        out.push(`<circle cx="${x}" cy="${y}" r="${fs * 0.45}" fill="none" stroke="#94a3b8" stroke-dasharray="3 3" stroke-width="1.2" ${NS}><title>${esc(`×${k + 1} · G ${r.G.toFixed(2)} · not used: ${r.reasons}`)}</title></circle>`);
        out.push(`<text x="${x + fs * 0.6}" y="${y + fs * 0.9}" font-size="${fs * 0.75}" fill="#94a3b8" pointer-events="none">×${k + 1}</text>`);
      });
      for (const r of js.stars.filter((q) => q.role)) {
        const x = -r.dE_target_arcsec, y = -r.dN_target_arcsec, isT = r.role === "target";
        if (st.stale && !isT && !moved(r)) continue;
        if (tw) out.push(`<circle cx="${x - tw[0]}" cy="${y - tw[1]}" r="${fs * 0.35}" fill="none" stroke="rgba(255,255,255,.55)" stroke-dasharray="2 2" stroke-width="1" ${NS} pointer-events="none"/>`);
        if (isT) continue;                 // the target is drawn below
        out.push(`<circle class="drag-field" data-drag="field" cx="${x}" cy="${y}" r="${fs * 0.5}" fill="none" stroke="#ffffff" stroke-width="1.4" ${NS}><title>${esc(`${r.label} · Gaia DR3 ${r.source_id} · G ${r.G.toFixed(2)} · ${js.settings.band} ${r.mag.toFixed(2)} (${sgn(r.dmag, 2)})`)}</title></circle>`);
        out.push(`<text x="${x + fs * 0.75}" y="${y - fs * 0.45}" font-size="${fs}" font-weight="700" fill="#fff" paint-order="stroke" stroke="#0b1424" stroke-width="${fs * 0.12}" pointer-events="none">${r.label}</text>`);
      }
    }
    top.forEach((s, k) => {
      out.push(`<circle class="drag-field" data-drag="field" cx="${-s.E}" cy="${-s.N}" r="${fs * 0.45}" fill="none" stroke="#ffffff" stroke-width="1.2" ${NS}><title>${esc(`${k + 1} · Gaia DR3 ${s.id} · G ${s.G.toFixed(2)}`)}</title></circle>`);
      out.push(`<text x="${-s.E + fs * 0.6}" y="${-s.N - fs * 0.45}" font-size="${fs * 0.85}" font-weight="700" fill="#fff" paint-order="stroke" stroke="#0b1424" stroke-width="${fs * 0.12}" pointer-events="none">${k + 1}</text>`);
    });
    // the target (or the position)
    if (js.target.blank) {
      const c = fs * 0.9;
      out.push(`<g stroke="#E2A123" stroke-width="2" pointer-events="none"><line ${NS} x1="${-c}" y1="0" x2="${-c * 0.3}" y2="0"/><line ${NS} x1="${c * 0.3}" y1="0" x2="${c}" y2="0"/>`
        + `<line ${NS} x1="0" y1="${-c}" x2="0" y2="${-c * 0.3}"/><line ${NS} x1="0" y1="${c * 0.3}" x2="0" y2="${c}"/></g>`);
    } else out.push(`<circle cx="0" cy="0" r="${fs * 0.65}" fill="none" stroke="#E2A123" stroke-width="2" ${NS} pointer-events="none"/>`);
    out.push(`<text x="${fs * 0.85}" y="${-fs * 0.55}" font-size="${fs}" font-weight="700" fill="#E2A123" paint-order="stroke" stroke="#0b1424" stroke-width="${fs * 0.12}" pointer-events="none">${js.target.blank && !js.target.nostar ? "position" : "target"}</text>`);
    if (st.stale && st.slow) out.push(`<text x="${v.x - half * 0.9}" y="${v.y - half * 0.84}" font-size="${fs * 1.05}" font-weight="700" fill="#E2A123" paint-order="stroke" stroke="#0b1424" stroke-width="${fs * 0.15}" pointer-events="none">updating comparisons…</text>`);
    decor(out, v, fs);
    svg.setAttribute("viewBox", `${v.x - half} ${v.y - half} ${2 * half} ${2 * half}`);
    markPreset();
    ensureSky();
    svg.setAttribute("font-family", "Inter, system-ui, sans-serif");
    el("fLay").innerHTML = out.join("");
    // readouts
    if (el("fOffVal")) el("fOffVal").textContent = fmtOff();
    if (el("fCentreVal")) { const c = centreText(); el("fCentreVal").textContent = c; el("fCentreCopy").setAttribute("data-copy", c); }
    if (el("fNStar")) {
      el("fNStar").textContent = js.sky ? String(inF.length) : "—";
      el("fNStarNote").textContent = js.sky ? (inF.length ? `brightest G ${inF[0].G.toFixed(2)} · Gaia G < 18` : "Gaia G < 18") : "needs the helper";
    }
    if (el("fNStarMeta")) el("fNStarMeta").textContent = `${inF.length} Gaia star${inF.length === 1 ? "" : "s"} (G < 18) on the detector${P.science.length > 1 ? "s" : ""}.`;
    const gdCard = el("fGuideCard"), gl = fov.guide && fov.guide.label;
    let gHtml = "", gLine = "";
    if (P.guider.length) {
      const ang = A.probe && st.ppa != null ? (((st.ppa - (fov.guide ? fov.guide.zero : 0)) % 360) + 360) % 360 : null;
      const star = !js.sky ? "no star list without the helper"
        : guide ? `guide star G ${guide.G.toFixed(2)} in the box`
        : rng ? `<span style="color:#fca5a5">no star with G ${rng[0]}–${rng[1]} in the box</span>` : `<span style="color:#fca5a5">no Gaia star in the guider</span>`;
      gHtml = `<span class="cd-label">${ang != null ? esc(gl || "Guide probe PA") : "Guider"}${st.ppaTouched ? " · by hand" : ""}</span>
        <span class="cd-value">${ang != null ? ang.toFixed(1) + "°" : A.probe ? "—" : "fixed"}</span><small>${star}</small>`;
      gLine = `${ang != null ? `${esc(gl || "Guide probe PA")} <b>${ang.toFixed(1)}°</b>${st.ppaTouched ? " (set by hand)" : ""} · ` : "Guider (fixed to the field) · "}`
        + (!js.sky ? "no star list without the helper"
           : guide ? `guide star <b>G ${guide.G.toFixed(2)}</b> (Gaia DR3 ${esc(guide.id)}) in the box`
           : `<span style="color:var(--red)">${rng ? `no star with G ${rng[0]}–${rng[1]}` : "no Gaia star"} in the guider</span>${inGd.length ? ` (brightest there: G ${inGd[0].G.toFixed(1)})` : ""}`);
    }
    if (gdCard) { gdCard.innerHTML = gHtml; gdCard.hidden = !gHtml; }
    el("fGuide").innerHTML = gLine;
    el("fViewGuider").hidden = !(P.guider.length || P.patrol.length);
    if (ranked) {
      el("fReset").hidden = !(js.pointing.fixed || st.ppaTouched);
      el("fReset").textContent = "Recommended";
      el("fReset").title = "back to the recommended pointing and guide star";
    } else {
      el("fReset").hidden = !(st.fo.e || st.fo.n || st.ppaTouched);
      el("fReset").textContent = "Centre";
      el("fReset").title = "field back on the target, guider back to its default";
    }
    el("fLegend").innerHTML = `<span><i class="dot" style="background:#E2A123"></i>${esc(s4 ? "field (all channels)" : "detector(s)")} · ${what}</span>`
      + (ranked && !js.target.blank ? '<span><i class="dot" style="background:#fff;border:1px solid #94a3bb"></i>comparison</span><span><i class="dot" style="background:none;border:1px dashed #94a3b8"></i>not used (×)</span>'
         : js.sky ? '<span><i class="dot" style="background:none;border:1px solid #fff"></i>brightest in the field (numbered)</span>' : "")
      + (ranked && js.settings.twin ? "<span>small dashed: second image</span>" : "")
      + (P.guider.length || P.patrol.length ? '<span><i class="dot" style="background:#93c5fd"></i>guider</span>' : "")
      + (P.other.length ? '<span><i class="dot" style="background:none;border:1px solid #cbd5e1"></i>other regions</span>' : "")
      + (st.fpa ? `<span>field PA ${st.fpa}° (dashed tick: its up)</span>` : "");
    el("fHelp").innerHTML = `Drag the sky to pan, scroll or use +/− to zoom, double-click to recentre.
      Drag the <b>gold field</b> to ${ranked ? "try another pointing (it is scored when you let go)" : "move it"}${A.probe ? ", and the <b>blue box</b> around its circle to place the guide probe" : ""}.`
      + (s4 ? "" : " Turn it with <b>Field PA</b> above.");
    el("fCsv").hidden = !js.sky;
    if (!ranked) genericTable(js, inF);
  }

  const GEN_TOP = 15;
  // which detector a star is on: the region names, or their IDs when the names are all the same ("ccd0" ...)
  function regLabel() {
    const sci = st.fov.regions.filter((r) => r.role === "science");
    if (sci.length < 2) return null;
    if (new Set(sci.map((r) => r.name)).size > 1) return (r) => r.name;
    if (new Set(sci.map((r) => r.id)).size > 1) return (r) => r.id;
    return null;
  }
  function genericTable(js, inF) {
    if (!js.sky) { el("fTable").innerHTML = '<tr><td style="text-align:left">Stars in the field: start the s4field helper for the Gaia list.</td></tr>'; return; }
    const top = inF.slice(0, GEN_TOP), lab = regLabel(), many = !!lab;
    el("fTable").innerHTML = `<tr><th></th><th>G</th><th>from target</th><th>E, N</th>${many ? "<th>region</th>" : ""}</tr>`
      + top.map((s, k) => `<tr><td title="Gaia DR3 ${esc(s.id)}">${k + 1}</td><td>${s.G.toFixed(2)}</td>`
        + `<td>${Math.hypot(s.E, s.N).toFixed(0)}″</td><td>${sgn(s.E, 0)}″, ${sgn(s.N, 0)}″</td>`
        + `${many ? `<td>${esc(lab(s.reg))}</td>` : ""}</tr>`).join("")
      + (inF.length > GEN_TOP ? `<tr><td colspan="${many ? 5 : 4}" style="text-align:left;color:var(--grey-2)">… and ${inF.length - GEN_TOP} fainter (all in the CSV)</td></tr>` : "")
      + (inF.length ? "" : `<tr><td colspan="${many ? 5 : 4}" style="text-align:left">no Gaia star (G &lt; 18) on the detector(s)</td></tr>`);
  }

  function redraw() {
    const js = st.js;
    if (!js || !st.v) return;
    if (js.mosaic) return redrawMosaic();
    el("fTable").classList.remove("mos-grid");
    el("fOut").classList.remove("mos");
    if (js.generic) return redrawGeneric();
    el("fCsv").hidden = false;
    el("fViewGuider").hidden = false;
    el("fReset").textContent = "Recommended";
    el("fReset").title = "back to the recommended pointing and guide star";
    el("fHelp").innerHTML = `Drag the sky to pan, scroll or use +/− to zoom, double-click to recentre.
      Drag the <b>gold field</b> to try another pointing (it is scored when you let go), and the <b>blue box</b> around the
      circle to choose the guide star.`;
    if (el("fGuideCard")) el("fGuideCard").hidden = false;
    const svg = el("fSvg"), fp = js.footprint, gd = fp.guider || GUIDER, v = st.v;
    const half = v.half, fs = half * 0.042;
    const fx = -st.fo.e, fy = -st.fo.n;                       // field centre, SVG coords
    const out = [];
    // Gaia stars when there is no sky image
    if (st.imgOK === false && js.sky) {
      const sk = js.sky;
      for (let i = 0; i < sk.E.length; i++) {
        const r = Math.max(1.2, (17.5 - sk.G[i]) * 0.9) * half / 320;
        out.push(`<circle cx="${-sk.E[i]}" cy="${-sk.N[i]}" r="${r.toFixed(1)}" fill="#cbd5e1" opacity=".85"/>`);
      }
    }
    // comparison search radius (around the target)
    if (!js.target.blank) out.push(`<circle cx="0" cy="0" r="${js.settings.search_radius_arcmin * 60}" fill="none" stroke="rgba(255,255,255,.35)" stroke-dasharray="6 6" stroke-width="1" ${NS} pointer-events="none"/>`);
    // recommended field centre, when the field was moved
    if (st.rec && tkey(st.rec) === tkey(js)) {
      const rp = st.rec.pointing, rx = -rp.offset_ra_cosdec_arcsec, ry = -rp.offset_dec_arcsec;
      if (Math.hypot(rx - fx, ry - fy) > 1) {
        const c = fs * 0.6;
        out.push(`<g stroke="#fbd38d" stroke-width="1.4" pointer-events="none"><line ${NS} x1="${rx - c}" y1="${ry}" x2="${rx + c}" y2="${ry}"/><line ${NS} x1="${rx}" y1="${ry - c}" x2="${rx}" y2="${ry + c}"/></g>`
          + `<text x="${rx + c}" y="${ry - c * 0.6}" font-size="${fs * 0.8}" fill="#fbd38d" pointer-events="none">recommended</text>`);
      }
    }
    // the field (draggable), its channels, the guider annulus and box
    const P = (pts) => pts.map(([e, n]) => `${(-e).toFixed(1)},${(-n).toFixed(1)}`).join(" ");
    out.push(`<g transform="translate(${fx} ${fy})">`);
    const ok = targetInside(), fc = ok ? "#E2A123" : "#f87171", what = js.target.blank ? "position" : "target";
    out.push(`<polygon class="drag-field" data-drag="field" points="${P(fp.common)}" fill="${ok ? "rgba(226,161,35,.13)" : "rgba(248,113,113,.16)"}" stroke="${fc}" stroke-width="1.4" ${NS}><title>${ok ? "drag to move the field" : `the ${what} is off the field (or within the edge margin) here`}</title></polygon>`);
    for (const b of BANDS)
      out.push(`<polygon points="${P(fp.bands[b])}" fill="none" stroke="${BAND_COL[b]}" stroke-width="1" ${NS} opacity=".8" pointer-events="none"/>`);
    for (const r of [gd.r_in, gd.r_out])
      out.push(`<circle cx="0" cy="0" r="${r}" fill="none" stroke="rgba(147,197,253,.5)" stroke-dasharray="3 5" stroke-width="1" ${NS} pointer-events="none"/>`);
    const a = st.pa * D2R, bx = -gd.r_box * Math.sin(a), by = -gd.r_box * Math.cos(a), h = gd.box / 2;
    out.push(`<rect class="drag-guider" data-drag="guider" x="${bx - h}" y="${by - h}" width="${gd.box}" height="${gd.box}" transform="rotate(${-st.pa} ${bx} ${by})" fill="rgba(147,197,253,.08)" stroke="#93c5fd" stroke-width="1.6" ${NS}/>`);
    out.push(`<line x1="0" y1="0" x2="${bx}" y2="${by}" stroke="rgba(147,197,253,.35)" stroke-width="1" ${NS} pointer-events="none"/>`);
    out.push("</g>");
    // the guide star(s) in the box
    const inBox = guideBox(), [gmin, gmax] = (js.sky && js.sky.guide_g) || [8, 14];
    const guide = inBox.find((s) => s.G >= gmin && s.G <= gmax);
    for (const s of inBox.slice(0, 6)) {
      const x = -js.sky.E[s.i], y = -js.sky.N[s.i], best = s === guide;
      out.push(`<circle cx="${x}" cy="${y}" r="${fs * (best ? 0.6 : 0.4)}" fill="none" stroke="#93c5fd" stroke-width="${best ? 1.8 : 1}" ${NS} pointer-events="none"/>`);
      if (best) out.push(`<text x="${x + fs * 0.75}" y="${y + fs * 0.35}" font-size="${fs}" fill="#93c5fd" pointer-events="none">G ${s.G.toFixed(1)}</text>`);
    }
    // not used: grey dashed circles, numbered as in the list (none while the field waits for its answer)
    js.stars.filter((r) => r.role === "" && r.reasons).slice(0, 8).forEach((r, k) => {
      if (st.stale && !inMovedField(r)) return;   // left the field while it is being moved
      const x = -r.dE_target_arcsec, y = -r.dN_target_arcsec;
      out.push(`<circle cx="${x}" cy="${y}" r="${fs * 0.45}" fill="none" stroke="#94a3b8" stroke-dasharray="3 3" stroke-width="1.2" ${NS}><title>${esc(`×${k + 1} · G ${r.G.toFixed(2)} · not used: ${r.reasons}`)}</title></circle>`);
      out.push(`<text x="${x + fs * 0.6}" y="${y + fs * 0.9}" font-size="${fs * 0.75}" fill="#94a3b8" pointer-events="none">×${k + 1}</text>`);
    });
    // polarimetric second images, comparison stars, target (sky frame)
    const tw = fp.polar_split_arcsec;
    const stars = js.stars.filter((r) => r.role === "target" || /^C\d+$/.test(r.label));
    for (const r of stars) {
      const x = -r.dE_target_arcsec, y = -r.dN_target_arcsec, isT = r.role === "target";
      if (st.stale && !isT && !inMovedField(r)) continue;   // left the field while it is being moved
      if (tw) out.push(`<circle cx="${x - tw[0]}" cy="${y - tw[1]}" r="${fs * 0.35}" fill="none" stroke="rgba(255,255,255,.55)" stroke-dasharray="2 2" stroke-width="1" ${NS} pointer-events="none"/>`);
      const used = BANDS.filter((b) => r["used_" + b]).join("");
      const tip = isT ? `target · Gaia DR3 ${r.source_id} · G ${r.G.toFixed(2)}`
        : `${r.label} · Gaia DR3 ${r.source_id} · G ${r.G.toFixed(2)} · V ${r.V == null ? "—" : r.V.toFixed(2)} · used in ${used || "none"}`;
      out.push(`<circle cx="${x}" cy="${y}" r="${fs * (isT ? 0.65 : 0.5)}" fill="none" stroke="${isT ? "#E2A123" : "#ffffff"}" stroke-width="${isT ? 2 : 1.4}" ${NS}><title>${esc(tip)}</title></circle>`);
      out.push(`<text x="${x + fs * 0.75}" y="${y - fs * 0.45}" font-size="${fs}" font-weight="700" fill="${isT ? "#E2A123" : "#ffffff"}" paint-order="stroke" stroke="#0b1424" stroke-width="${fs * 0.12}" pointer-events="none">${isT ? "target" : r.label}</text>`);
    }
    if (js.target.blank) {               // the position itself, no star there
      const c = fs * 0.9;
      out.push(`<g stroke="#E2A123" stroke-width="2" pointer-events="none"><line ${NS} x1="${-c}" y1="0" x2="${-c * 0.3}" y2="0"/><line ${NS} x1="${c * 0.3}" y1="0" x2="${c}" y2="0"/>`
        + `<line ${NS} x1="0" y1="${-c}" x2="0" y2="${-c * 0.3}"/><line ${NS} x1="0" y1="${c * 0.3}" x2="0" y2="${c}"/></g>`
        + `<text x="${c * 0.85}" y="${-c * 0.55}" font-size="${fs}" font-weight="700" fill="#E2A123" paint-order="stroke" stroke="#0b1424" stroke-width="${fs * 0.12}" pointer-events="none">position</text>`);
    }
    if (st.stale && st.slow) out.push(`<text x="${v.x - half * 0.9}" y="${v.y - half * 0.84}" font-size="${fs * 1.05}" font-weight="700" fill="#E2A123" paint-order="stroke" stroke="#0b1424" stroke-width="${fs * 0.15}" pointer-events="none">updating comparisons…</text>`);
    decor(out, v, fs);
    svg.setAttribute("viewBox", `${v.x - half} ${v.y - half} ${2 * half} ${2 * half}`);
    markPreset();
    ensureSky();
    svg.setAttribute("font-family", "Inter, system-ui, sans-serif");
    el("fLay").innerHTML = out.join("");
    // guider readout, below the sky view and in the summary bar
    const angle = ((st.pa - 180) % 360 + 360) % 360;
    if (el("fGuideCard")) el("fGuideCard").innerHTML = `<span class="cd-label">Guider setup angle${st.guiderTouched ? " · by hand" : ""}</span>
      <span class="cd-value">${angle.toFixed(1)}°</span>
      <small>${guide ? `guide star G ${guide.G.toFixed(2)} in the box` : `<span style="color:#fca5a5">no star with G ${gmin}–${gmax} in the box</span>`}</small>`;
    el("fGuide").innerHTML = `Guider Setup angle <b>${angle.toFixed(1)}°</b>${st.guiderTouched ? " (set by hand)" : ""} · `
      + (guide ? `guide star <b>G ${guide.G.toFixed(2)}</b> (Gaia DR3 ${esc(guide.id)}) in the box`
               : `<span style="color:var(--red)">no star with G ${gmin}–${gmax} in the box</span>${inBox.length ? ` (brightest there: G ${inBox[0].G.toFixed(1)})` : ""}`)
      + (js.sky ? "" : " · the helper sent no star list");
    el("fLegend").innerHTML = BANDS.map((b) => `<span><i class="dot" style="background:${BAND_COL[b]}"></i>${b}</span>`).join("")
      + `<span><i class="dot" style="background:#E2A123"></i>field (all channels) · ${js.target.blank ? "position" : "target"}</span>`
      + (js.target.blank ? "" : '<span><i class="dot" style="background:#fff;border:1px solid #94a3bb"></i>comparison</span>')
      + '<span><i class="dot" style="background:#93c5fd"></i>guider</span>'
      + (js.target.blank ? "" : '<span><i class="dot" style="background:none;border:1px dashed #94a3b8"></i>not used (×)</span>')
      + (tw ? "<span>small dashed: second image</span>" : "")
      + (js.target.blank ? "" : "<span>dashed circle: search radius</span>");
  }

  // pointer → SVG coordinates
  function svgPt(ev) {
    const svg = el("fSvg"), pt = svg.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  }
  const perPx = () => (2 * st.v.half) / el("fSvg").getBoundingClientRect().width;

  function onDown(ev) {
    if (!st.js || ev.button > 0) return;
    const kind = ev.target.closest && ev.target.closest("[data-drag]");
    st.drag = { mode: kind ? kind.getAttribute("data-drag") : "pan", x0: ev.clientX, y0: ev.clientY,
                v0: { ...st.v }, fo0: { ...st.fo }, moved: false, tile: kind ? kind.getAttribute("data-tile") : null };
    el("fSvg").setPointerCapture(ev.pointerId);
    if (st.drag.mode === "pan") el("fSvg").classList.add("panning");
    ev.preventDefault();
  }

  function onMove(ev) {
    const d = st.drag;
    if (!d) return;
    const k = perPx(), dx = (ev.clientX - d.x0) * k, dy = (ev.clientY - d.y0) * k;
    if (Math.hypot(ev.clientX - d.x0, ev.clientY - d.y0) > 2) d.moved = true;
    if (d.mode === "pan") { st.v.x = d.v0.x - dx; st.v.y = d.v0.y - dy; }
    else if (d.mode === "mosaic") {     // the whole mosaic moves
      if (!d.moved) return;
      const q = Math.max(0.05, Math.min(1, k));
      st.fo = { e: Math.round((d.fo0.e - dx) / q) * q, n: Math.round((d.fo0.n - dy) / q) * q };
      mos.off = st.fo;
    } else if (d.mode === "field") {
      const gen = st.js.generic, q = gen ? Math.max(0.01, Math.min(0.5, k)) : 0.5;   // fine steps for small FoVs
      if (d.moved && (!gen || st.js.ranked)) setStale(true);   // the stars shown are the old pointing's until the helper answers
      st.fo = { e: Math.round((d.fo0.e - dx) / q) * q, n: Math.round((d.fo0.n - dy) / q) * q };
      const o = el("fOffVal");
      if (o) o.textContent = fmtOff();
      const c = el("fCentreVal");
      if (c) c.textContent = centreText();
    } else if (d.mode === "guider") {
      const p = svgPt(ev), e = -p.x - st.fo.e, n = -p.y - st.fo.n;
      const pa = Math.round(((Math.atan2(e, n) / D2R) % 360 + 360) % 360 * 10) / 10;
      if (st.js.generic) { st.ppa = pa; st.ppaTouched = true; }
      else { st.pa = pa; st.guiderTouched = true; }
    }
    redraw();
  }

  function onUp(ev) {
    const d = st.drag;
    st.drag = null;
    el("fSvg").classList.remove("panning");
    if (d && d.mode === "mosaic") {      // a click changes the tile's status; a drag moved the mosaic
      if (d.moved) mosSave(); else if (d.tile) mosCycle(d.tile);
      return;
    }
    if (!d || !d.moved || (st.js.generic && !st.js.ranked)) return;   // drawn without the helper: nothing to ask
    if (d.mode === "field") {           // score the new pointing
      st.at = { ...st.fo };
      waitingNote();
      el("fReset").hidden = false;
      compute(false);
    } else if (d.mode === "guider") el("fReset").hidden = false;
  }

  function zoom(f, at) {
    if (!st.v) return;
    const [lo, hi] = zoomRange(), half = Math.min(hi, Math.max(lo, st.v.half * f));
    const c = at || { x: st.v.x, y: st.v.y };
    st.v.x = c.x + (st.v.x - c.x) * half / st.v.half;
    st.v.y = c.y + (st.v.y - c.y) * half / st.v.half;
    st.v.half = half;
    redraw();
  }

  function onWheel(ev) {
    if (!st.v) return;
    ev.preventDefault();
    zoom(Math.exp(Math.sign(ev.deltaY) * 0.15), svgPt(ev));
  }

  function backToRecommended() {
    if (st.js && st.js.mosaic) { mosCenter(); return; }
    if (st.js && st.js.generic) {        // a loaded FoV
      st.ppaTouched = false; st.ppa = defaultPPA();
      if (!st.js.ranked) { st.fo = { e: 0, n: 0 }; preset(st.preset); return; }   // no helper: back on the target
      st.at = null;
      el("fReset").hidden = true;
      if (st.rec && st.rec.generic && tkey(st.rec) === tkey(st.js) && st.rec !== st.js) {
        st.js = st.rec; st.key = null;
        drawGeneric(st.rec);
      } else redraw();
      compute(true);
      return;
    }
    st.at = null;
    st.guiderTouched = false;
    el("fReset").hidden = true;
    if (st.rec && st.js && tkey(st.rec) === tkey(st.js) && st.rec !== st.js) {
      st.js = st.rec; st.key = null;
      draw(st.rec, null);
    } else if (st.js) { st.pa = st.js.guider[0] ? st.js.guider[0].pa : st.pa; redraw(); }
    compute(true);                       // brings the shown answer back in step with the settings
  }

  function saveText(text, name, type) {
    const b = new Blob([text], { type }), u = URL.createObjectURL(b), a = document.createElement("a");
    a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(u);
  }
  const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");

  // a loaded FoV: the stars in its field, with their place on the instrument (x, y at PA 0)
  function downloadGenericCSV() {
    const js = st.js, P = placed(), inF = starsIn(js, P, "science"), t = js.target;
    if (!js.sky) return;
    if (js.ranked) {                     // the helper's star list: comparisons, target, every star on the detector(s)
      const cols = Object.keys(js.stars[0] || { source_id: 0 });
      const cell = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
      const p = js.pointing, ascii = (x) => sgn(x).replace("−", "-"), cs = js.settings;
      const head = [
        `# s4field ${js.s4field} star list for ${st.fov.name} (via SkyBound) -- basic mode, band ${cs.band}`,
        t.blank ? `# position ${t.text || ""}: no Gaia star there, no comparison stars` : `# target ${t.text || ""} = Gaia DR3 ${t.source_id}`,
        `# field centre ${p.hms} (RA ${p.ra.toFixed(6)}, Dec ${p.dec.toFixed(6)} deg); field PA ${st.fpa} deg (east of north)`,
        `# offset from the target: RA.cos(dec) ${ascii(p.offset_ra_cosdec_arcsec)}"  DEC ${ascii(p.offset_dec_arcsec)}"${p.fixed ? " (your pointing)" : " (recommended)"}`,
        `# epoch ${t.epoch.toFixed(3)}; edge margin ${cs.edge_margin}"; isolation ${cs.iso_radius}"; max comparisons ${cs.max_comps || "no limit"}${cs.twin ? `; second image at ${cs.twin.map((v) => v.toFixed(1)).join(", ")}"` : ""}`,
      ];
      if (js.catalogue.lite) head.push("# !! " + js.catalogue.caveat);
      saveText(head.join("\n") + "\n" + cols.join(",") + "\n" + js.stars.map((r) => cols.map((c) => cell(r[c])).join(",")).join("\n") + "\n",
               `field_${slug(js._label || t.text || "target")}_${slug(st.fov.instrument || st.fov.name)}.csv`, "text/csv");
      return;
    }
    const ascii = (x) => sgn(x).replace("−", "-");
    const head = [
      `# SkyBound, ${st.fov.name}: Gaia DR3 stars in the field (G < 18, from the s4field helper), brightest first`,
      `# target ${t.text || js._label || ""}${t.blank ? " (no Gaia star there)" : ` = Gaia DR3 ${t.source_id}`}; epoch ${t.epoch.toFixed(3)}`,
      `# field centre ${centreText()}; offset from the target RA.cos(dec) ${ascii(st.fo.e)}"  DEC ${ascii(st.fo.n)}"; field PA ${st.fpa} deg (east of north)`,
      "# dE, dN: arcsec East, North of the target; x, y: arcsec in the FoV file's frame (x East, y North at PA 0) from the field centre",
    ];
    if (js.catalogue && js.catalogue.lite) head.push("# !! " + js.catalogue.caveat);
    const lab = regLabel() || ((r) => r.name);
    const rows = inF.map((s, k) => {
      const [x, y] = FoV.rot(s.E - st.fo.e, s.N - st.fo.n, -st.fpa);
      return [k + 1, s.id, s.G.toFixed(3), s.E.toFixed(1), s.N.toFixed(1), x.toFixed(1), y.toFixed(1), `"${String(lab(s.reg)).replace(/"/g, '""')}"`].join(",");
    });
    saveText(head.join("\n") + "\nn,source_id,G,dE_target_arcsec,dN_target_arcsec,x_arcsec,y_arcsec,region\n" + rows.join("\n") + "\n",
             `field_${slug(js._label || t.text || "target")}_${slug(st.fov.instrument || st.fov.name)}.csv`, "text/csv");
  }

  function downloadCSV() {
    const js = st.js;
    if (js && js.generic) return downloadGenericCSV();
    if (!js || !js.stars.length) return;
    const cols = Object.keys(js.stars[0]);
    const cell = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
    const p = js.pointing, s = js.settings, ascii = (x) => sgn(x).replace("−", "-");
    const head = [
      `# s4field ${js.s4field} star list (via SkyBound)${s.basic ? " -- BASIC (brightness only)" : ""}`,
      js.target.blank ? `# position ${js.target.text || ""}: no Gaia star there, no comparison stars`
                      : `# target ${js.target.text || ""} = Gaia DR3 ${js.target.source_id}`,
      `# field centre ${p.hms} (RA ${p.ra.toFixed(6)}, Dec ${p.dec.toFixed(6)} deg)`,
      `# TCSPD offset from the target: RA.cos(dec) ${ascii(p.offset_ra_cosdec_arcsec)}"  DEC ${ascii(p.offset_dec_arcsec)}"`,
      `# epoch ${js.target.epoch.toFixed(3)}; mode ${s.mode}; read-out ${s.size}`
        + (s.basic ? "" : "; t_exp " + (BANDS.every((b) => s.t_exp[b] != null) ? BANDS.map((b) => `${b}=${s.t_exp[b]}s`).join(", ") : "not given") + (s.texp_estimated ? " (ESTIMATED)" : "") + `; airmass ${s.airmass}`),
      "# pixels: 0-based, raw orientation (N up, E left); x2/y2 = second image (polarimetric)",
    ];
    if (js.catalogue.lite) head.push("# !! " + js.catalogue.caveat);
    const text = head.join("\n") + "\n" + cols.join(",") + "\n"
      + js.stars.map((r) => cols.map((c) => cell(r[c])).join(",")).join("\n") + "\n";
    const name = "field_" + (js._label || js.target.text || "target").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") + ".csv";
    const b = new Blob([text], { type: "text/csv" }), u = URL.createObjectURL(b), a = document.createElement("a");
    a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(u);
  }

  /* ---- filters and the dV window ---------------------------------------------- */
  const DV_MIN = -3, DV_MAX = 12, DV_DEFAULT = [-0.35, 8], MAX_COMPS = 6;
  const ADV_ONLY = ["saturation", "noise", "trails"];
  const dvValues = () => {
    let a = parseFloat(el("fDvMin").value), b = parseFloat(el("fDvMax").value);
    return a <= b ? [a, b] : [b, a];
  };
  function dvShow() {
    const [a, b] = dvValues(), span = DV_MAX - DV_MIN;
    const f = (x, edge, word) => (x === edge ? word : (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(2));
    el("fDvVal").textContent = `${f(a, DV_MIN, "any")} to ${f(b, DV_MAX, "any")} mag`;
    el("fDvFill").style.left = `${(100 * (a - DV_MIN)) / span}%`;
    el("fDvFill").style.width = `${(100 * (b - a)) / span}%`;
  }
  // null = all filters (the helper's default); [] = none; else the names that are on
  function filterList() {
    if (st.tab === "basic") return el("fFiltAll").checked ? null : [];
    const on = [...document.querySelectorAll(".fFilt")].filter((c) => c.checked).map((c) => c.value);
    return on.length === document.querySelectorAll(".fFilt").length ? null : on;
  }
  function settingsChanged() { save(); if (st.computed) compute(true); }
  // most comparison stars: a whole number >= 0 (0 = no limit); anything else falls back to 6
  function maxComps() {
    const n = Number(el("fMaxComps").value);
    if (!Number.isInteger(n) || n < 0) { el("fMaxComps").value = MAX_COMPS; return MAX_COMPS; }
    return n;
  }
  function toggleHelp() {
    const h = el("fFiltHelp");
    h.hidden = !h.hidden;
  }

  /* ---- basic / advanced ---------------------------------------------------- */
  function setTab(tab, quiet) {
    st.tab = tab;
    el("fTabBasic").classList.toggle("on", tab === "basic");
    el("fTabAdv").classList.toggle("on", tab === "advanced");
    el("fAdvPane").hidden = tab !== "advanced";
    el("fIntroBasic").hidden = tab !== "basic";
    el("fBasicFilt").hidden = tab !== "basic";
    if (!quiet) { save(); render(); }      // redraws the planned-time marker; recomputes a shown field
  }

  /* ---- remember the choices (this browser only); the page opens without a target */
  const STORE = "skybound.v1";
  function save() {
    try {
      localStorage.setItem(STORE, JSON.stringify({
        scope: el("scope").value, tab: st.tab, mode: el("fMode").value, view: st.mode,
        dv: dvValues(), maxComps: maxComps(), filtAll: el("fFiltAll").checked,
        filt: [...document.querySelectorAll(".fFilt")].filter((c) => !c.checked).map((c) => c.value) }));
    } catch (e) { /* storage unavailable: nothing to remember */ }
  }
  function restore() {
    let v = null;
    try { v = JSON.parse(localStorage.getItem(STORE) || "null"); } catch (e) { v = null; }
    if (!v) return;
    if (v.scope && v.scope !== "custom" && [...el("scope").options].some((o) => o.value === v.scope)) {
      el("scope").value = v.scope;
      applyScope();
    }
    if (v.mode) el("fMode").value = v.mode;
    if (Array.isArray(v.dv) && v.dv.length === 2) { el("fDvMin").value = v.dv[0]; el("fDvMax").value = v.dv[1]; }
    if (Number.isInteger(v.maxComps) && v.maxComps >= 0) el("fMaxComps").value = v.maxComps;
    if (typeof v.filtAll === "boolean") el("fFiltAll").checked = v.filtAll;
    if (Array.isArray(v.filt)) for (const c of document.querySelectorAll(".fFilt")) c.checked = !v.filt.includes(c.value);
    if (v.tab === "advanced") setTab("advanced", true);
    if (v.view === "mosaic") st.mode = "mosaic";
  }

  /* ---- SkyBound calls this after every render ------------------------------ */
  /* ---- mosaics ------------------------------------------------------------- */
  // cols x rows tiles of the instrument's science area (js/fov.js tiles()), the
  // grid turned with the field PA; the whole mosaic is dragged like the field
  // (st.fo is its centre's offset from the target). Each tile has a status,
  // kept in this browser per target and instrument.
  const MOS_STORE = "skybound.mosaics.v1", MOS_MAX = 30;
  const MOS_ORDER = ["todo", "priority", "observed", "done", "redo"];
  const MOS = {
    todo: { name: "to do", fill: "rgba(226,161,35,.08)", stroke: "#E2A123", doc: "" },
    priority: { name: "priority", fill: "rgba(250,204,21,.38)", stroke: "#facc15", doc: "#ffff00" },
    observed: { name: "observed, not reduced", fill: "rgba(34,211,238,.38)", stroke: "#22d3ee", doc: "#66ffff" },
    done: { name: "observed and reduced", fill: "rgba(74,222,128,.38)", stroke: "#4ade80", doc: "#9be59b" },
    redo: { name: "to redo", fill: "rgba(248,113,113,.40)", stroke: "#f87171", doc: "#f4a3a3" },
  };
  const mos = { key: null, cols: 3, rows: 3, ov: 15, off: { e: 0, n: 0 }, status: {} };
  function mosAll() { try { return JSON.parse(localStorage.getItem(MOS_STORE) || "{}") || {}; } catch (e) { return {}; } }
  function mosLoad(key) {
    const m = mosAll()[key] || {};
    mos.key = key;
    mos.cols = Math.min(MOS_MAX, Math.max(1, m.cols | 0 || 3));
    mos.rows = Math.min(MOS_MAX, Math.max(1, m.rows | 0 || 3));
    mos.ov = isFinite(m.ov) ? Math.min(90, Math.max(-100, +m.ov)) : 15;
    mos.off = m.off && isFinite(m.off.e) && isFinite(m.off.n) ? { e: +m.off.e, n: +m.off.n } : { e: 0, n: 0 };
    mos.status = m.status && typeof m.status === "object" ? { ...m.status } : {};
    st.fo = mos.off;
  }
  function mosSave() {
    if (!mos.key) return;
    const all = mosAll();
    all[mos.key] = { cols: mos.cols, rows: mos.rows, ov: mos.ov, off: mos.off, status: mos.status, saved: new Date().toISOString() };
    try { localStorage.setItem(MOS_STORE, JSON.stringify(all)); } catch (e) { /* storage unavailable */ }
  }
  const mosPA = () => (isS4() ? 0 : st.fpa);
  const mosaicT = () => FoV.tiles(st.fov, mos.cols, mos.rows, mos.ov, mosPA());
  // a tile's centre on the sky [deg]: the target's tangent plane, offset by the mosaic and the tile
  function tileRaDec(t) {
    const tg = st.js.target;
    return FoV.fromTangent(st.fo.e + t.e, st.fo.n + t.n, tg.ra, tg.dec);
  }
  const fmtRA = (ra) => { let s = Math.round(ra / 15 * 360000) / 100; const h = Math.floor(s / 3600); s -= h * 3600; const m = Math.floor(s / 60); return `${pad2(h)} ${pad2(m)} ${(s - m * 60).toFixed(2).padStart(5, "0")}`; };
  const fmtDec = (d) => { const g = d < 0 ? "-" : "+"; let s = Math.round(Math.abs(d) * 360000) / 100; const D = Math.floor(s / 3600); s -= D * 3600; const m = Math.floor(s / 60); return `${g}${pad2(D)} ${pad2(m)} ${(s - m * 60).toFixed(2).padStart(5, "0")}`; };

  function computeMosaic(auto) {
    const target = fieldTarget();
    if (!target) { if (!auto) showError("Give the target (the mosaic's centre) a name or valid RA/Dec at the top of the page."); return; }
    const key = `mos|${st.fov.id}|${target}`;
    if (auto && key === st.key && st.js && st.js.mosaic) return;
    if (mos.key !== key) mosLoad(key);
    const ra = parseAngle(el("ra").value) * 15, dec = parseAngle(el("dec").value);
    st.js = { generic: true, mosaic: true, nohelper: true, ranked: false, _label: (listed() || {}).name || "",
              target: { ra, dec, blank: true, nostar: true, epoch: null, text: target }, sky: null, catalogue: null, warnings: [] };
    st.key = key; st.computed = true; st.lastTarget = target;
    showError("");
    drawMosaic();
  }

  function drawMosaic() {
    const js = st.js, t = js.target, fov = st.fov;
    setStale(false);
    el("fOut").hidden = false;
    el("fOut").classList.add("mos");
    st.fo = mos.off;
    const inList = typeof TARGETS !== "undefined" && TARGETS.some((x) => x.name === js._label);
    const tSel = inList ? `<select class="cd-tsel" id="fTargetSum" aria-label="target: the mosaic's centre" title="another target: its mosaic">${tgtOptions()}</select>` : "";
    const copyBtn = (id) => `<button type="button" class="copy-btn" id="${id}" data-copy="" title="copy">copy</button>`;
    el("fSummary").innerHTML = `<div class="fld-offset">
        <div class="cd-item"><span class="cd-label">Mosaic centre</span>
          <span class="cd-value cd-coord" id="fMosCentre"></span><small>RA, Dec (J2000, as the target) ${copyBtn("fMosCentreCopy")}</small></div>
        <div class="cd-item"><span class="cd-label">Offset from ${tSel ? "" : esc(js._label || "the target")}</span>${tSel}
          <span class="cd-value" id="fOffVal"></span><small>drag the mosaic to move it · Center puts it back</small></div>
        <div class="cd-item"><span class="cd-label">Tiles</span><span class="cd-value" id="fMosN"></span><small id="fMosNNote"></small></div>
        <div class="cd-item"><span class="cd-label">Covers</span><span class="cd-value" id="fMosSize"></span><small id="fMosStep"></small></div>
        <div class="cd-item"><span class="cd-label">Progress</span><span class="cd-value" id="fMosDone"></span><small id="fMosCounts"></small></div>
      </div>`;
    const B = FoV.scienceBox(fov), am = (x) => (x >= 120 ? (x / 60).toFixed(2) + "′" : x.toFixed(1) + "″");
    el("fMeta").innerHTML = `<div class="fld-meta"><b>${esc(fov.name)}</b>: each tile is its science area, ${am(B.w)} × ${am(B.h)}`
      + `${isS4() ? " (the area all four channels share)" : ""}, at PA <span id="fMosPA">${mosPA()}</span>°. Columns A, B, … run from East to West, rows 1, 2, … from`
      + ` North to South; click a tile, here or on the sky, to change its status. Coordinates are J2000, like the target's, on the`
      + ` tangent plane around it.</div>`;
    el("fError").innerHTML = ""; el("fNotes").innerHTML = ""; el("fReport").textContent = "";
    el("fRepWrap").hidden = true; el("fCsv").hidden = true;
    const newTarget = st.imgFor !== tkey(js);
    if (newTarget || !st.v || st.vFor !== "mosaic") { st.vFor = "mosaic"; preset("field", true); }
    if (newTarget) { st.imgFor = tkey(js); st.sky = null; st.imgOK = null; el("fLayImg").innerHTML = ""; }
    mosControls();
    redraw();
  }

  // the size inputs follow the grid (and the grid follows a size typed in arcmin)
  function mosControls() {
    const T = mosaicT();
    el("fMosCols").value = mos.cols; el("fMosRows").value = mos.rows; el("fMosOv").value = mos.ov;
    if (document.activeElement !== el("fMosW")) el("fMosW").value = (T.width / 60).toFixed(1);
    if (document.activeElement !== el("fMosH")) el("fMosH").value = (T.height / 60).toFixed(1);
  }
  function mosFromInputs(src) {
    const n = (id) => parseFloat(el(id).value);
    if (src === "w" || src === "h") {          // only the axis typed; the shown size is rounded to 0.1'
      const B = FoV.scienceBox(st.fov), f = 1 - mos.ov / 100;
      const size = n(src === "w" ? "fMosW" : "fMosH") * 60 - 3;
      if (src === "w" && size > 0) mos.cols = Math.min(MOS_MAX, FoV.tilesFor(size, B.w, B.w * f));
      if (src === "h" && size > 0) mos.rows = Math.min(MOS_MAX, FoV.tilesFor(size, B.h, B.h * f));
    } else {
      const c = Math.round(n("fMosCols")), r = Math.round(n("fMosRows")), o = n("fMosOv");
      if (c >= 1) mos.cols = Math.min(MOS_MAX, c);
      if (r >= 1) mos.rows = Math.min(MOS_MAX, r);
      if (isFinite(o)) mos.ov = Math.min(90, Math.max(-100, o));
    }
    mosSave();
    mosControls();
    if (st.js && st.js.mosaic) preset("field");   // a new size: the view fits it again
  }
  function mosCenter() {
    st.fo = { e: 0, n: 0 }; mos.off = st.fo;
    mosSave();
    if (st.js && st.js.mosaic) preset("field");
  }
  function mosCycle(label) {
    const k = MOS_ORDER.indexOf(mos.status[label] || "todo"), next = MOS_ORDER[(k + 1) % MOS_ORDER.length];
    if (next === "todo") delete mos.status[label]; else mos.status[label] = next;
    mosSave();
    redraw();
  }

  function redrawMosaic() {
    const js = st.js, v = st.v, half = v.half, fs = half * 0.042, svg = el("fSvg");
    const pa = mosPA(), T = mosaicT(), P = FoV.place(st.fov, pa, null);
    const fx = -st.fo.e, fy = -st.fo.n, out = [];
    const pts = (rg) => rg.pts.map(([e, n]) => `${(-e).toFixed(2)},${(-n).toFixed(2)}`).join(" ");
    out.push(`<defs><g id="mosTile">${P.science.map((rg) => (rg.shape === "circle"
      ? `<circle cx="${-rg.c[0]}" cy="${-rg.c[1]}" r="${rg.r}" ${NS}/>` : `<polygon points="${pts(rg)}" ${NS}/>`)).join("")}</g></defs>`);
    out.push(`<g transform="translate(${fx} ${fy})">`);
    for (const t of T.tiles) {
      const s = MOS[mos.status[t.label] || "todo"];
      out.push(`<use href="#mosTile" class="drag-field" data-drag="mosaic" data-tile="${t.label}" transform="translate(${(-t.e).toFixed(2)} ${(-t.n).toFixed(2)})" fill="${s.fill}" stroke="${s.stroke}" stroke-width="1.2"><title>${t.label} · ${s.name} · click to change it, drag to move the mosaic</title></use>`);
    }
    const [bx, by] = FoV.rot(T.box.cx, T.box.cy, pa), tile = Math.min(T.box.w, T.box.h);
    const lf = Math.min(fs * 1.05, tile * 0.16);
    if (tile / (2 * half) > 0.035)                 // labels only while the tiles are big enough to hold them
      for (const t of T.tiles)
        out.push(`<text x="${-(t.e + bx)}" y="${-(t.n + by)}" font-size="${lf}" font-weight="700" fill="#fff" text-anchor="middle" dominant-baseline="central" paint-order="stroke" stroke="#0b1424" stroke-width="${lf * 0.14}" pointer-events="none">${t.label}</text>`);
    const c = fs * 0.5;                              // the mosaic's centre
    out.push(`<g stroke="#E2A123" stroke-width="1.4" pointer-events="none"><line ${NS} x1="${-c}" y1="0" x2="${c}" y2="0"/><line ${NS} x1="0" y1="${-c}" x2="0" y2="${c}"/></g>`);
    out.push("</g>");
    // the target
    const k = fs * 0.9;
    out.push(`<g stroke="#fbd38d" stroke-width="2" pointer-events="none"><line ${NS} x1="${-k}" y1="0" x2="${-k * 0.3}" y2="0"/><line ${NS} x1="${k * 0.3}" y1="0" x2="${k}" y2="0"/>`
      + `<line ${NS} x1="0" y1="${-k}" x2="0" y2="${-k * 0.3}"/><line ${NS} x1="0" y1="${k * 0.3}" x2="0" y2="${k}"/></g>`
      + `<text x="${k * 0.85}" y="${-k * 0.55}" font-size="${fs}" font-weight="700" fill="#fbd38d" paint-order="stroke" stroke="#0b1424" stroke-width="${fs * 0.12}" pointer-events="none">${esc(js._label || "target")}</text>`);
    decor(out, v, fs);
    svg.setAttribute("viewBox", `${v.x - half} ${v.y - half} ${2 * half} ${2 * half}`);
    markPreset();
    ensureSky();
    svg.setAttribute("font-family", "Inter, system-ui, sans-serif");
    el("fLay").innerHTML = out.join("");
    mosReadouts(T);
  }

  function mosReadouts(T) {
    const tg = st.js.target, [ra, dec] = FoV.fromTangent(st.fo.e, st.fo.n, tg.ra, tg.dec);
    const cen = `${fmtRA(ra)} ${fmtDec(dec)}`;
    if (!el("fMosCentre")) return;
    el("fMosCentre").textContent = cen; el("fMosCentreCopy").setAttribute("data-copy", cen);
    el("fOffVal").textContent = fmtOff();
    el("fMosN").textContent = String(T.tiles.length);
    el("fMosNNote").textContent = `${mos.cols} × ${mos.rows} · serpentine from A1`;
    el("fMosSize").textContent = `${(T.width / 60).toFixed(1)}′ × ${(T.height / 60).toFixed(1)}′`;
    el("fMosStep").textContent = `step ${T.sx.toFixed(1)}″ × ${T.sy.toFixed(1)}″ · overlap ${mos.ov}%`;
    if (el("fMosPA")) el("fMosPA").textContent = String(mosPA());
    const cnt = {};
    for (const t of T.tiles) { const s = mos.status[t.label] || "todo"; cnt[s] = (cnt[s] || 0) + 1; }
    el("fMosDone").textContent = `${(cnt.done || 0) + (cnt.observed || 0)} / ${T.tiles.length}`;
    el("fMosCounts").textContent = MOS_ORDER.filter((s) => s !== "todo" && cnt[s]).map((s) => `${cnt[s]} ${MOS[s].name}`).join(" · ") || "observed (reduced or not) / all";
    el("fReset").hidden = !(st.fo.e || st.fo.n);
    el("fReset").textContent = "Center";
    el("fReset").title = "the mosaic back on the target";
    el("fViewGuider").hidden = true;
    el("fGuide").innerHTML = "";
    el("fLegend").innerHTML = MOS_ORDER.map((s) => `<span><i class="dot" style="background:${MOS[s].fill};border:1px solid ${MOS[s].stroke}"></i>${MOS[s].name}</span>`).join("")
      + '<span><i class="dot" style="background:#fbd38d"></i>target · gold cross: mosaic centre</span>';
    el("fHelp").innerHTML = "Drag the sky to pan, scroll or use +/− to zoom, double-click to recentre. Drag a tile to move the whole mosaic; "
      + "<b>click</b> a tile to change its status. <b>Center</b> puts the mosaic back on the target.";
    mosTable(T);
  }

  // the table, as AstroMosaic gives it (and as it goes into the observing log)
  function mosTable(T) {
    let h = `<tr><th></th>${Array.from({ length: mos.cols }, (_, i) => `<th>${FoV.colName(i)}</th>`).join("")}</tr>`;
    for (let j = 0; j < mos.rows; j++) {
      h += `<tr><th>${j + 1}</th>`;
      for (let i = 0; i < mos.cols; i++) {
        const t = T.tiles[j * mos.cols + i], [ra, dec] = tileRaDec(t), s = mos.status[t.label] || "todo";
        h += `<td data-tile="${t.label}" class="mos-${s}" title="${t.label} · ${MOS[s].name} · #${t.order} in the serpentine order · click to change">`
          + `${fmtRA(ra)}<br>${fmtDec(dec)}</td>`;
      }
      h += "</tr>";
    }
    el("fTable").innerHTML = h;
    el("fTable").classList.add("mos-grid");
    if (!el("fMosBar")) {
      el("fMiss").innerHTML = `<div class="fld-bar" id="fMosBar">
          <button class="btn-primary btn-sm" id="fMosCopy" type="button" title="paste into Google Docs or a spreadsheet">Copy table</button>
          <button class="btn-ghost btn-sm" id="fMosCsv" type="button">CSV</button>
          <button class="btn-ghost btn-sm" id="fMosAladin" type="button" title="Aladin: File → Load instrument FoV, then put it at the mosaic centre">Aladin FoV</button>
          <button class="btn-ghost btn-sm" id="fMosDs9" type="button" title="DS9 / Aladin regions, fk5">DS9 regions</button>
          <button class="btn-ghost btn-sm" id="fMosClear" type="button">Clear statuses</button>
          <span class="note" id="fMosMsg"></span></div>`;
    }
  }

  /* ---- mosaic exports ------------------------------------------------------- */
  function mosTiles() {
    const T = mosaicT();
    return { T, rows: T.tiles.map((t) => { const [ra, dec] = tileRaDec(t); return { ...t, ra, dec, status: mos.status[t.label] || "todo" }; }) };
  }
  const mosName = () => `mosaic_${slug(st.js._label || st.js.target.text || "target")}_${slug(st.fov.instrument || st.fov.name)}`;

  async function mosCopy() {
    const { rows } = mosTiles(), cell = "border:1px solid #d9d9d9;padding:4px 8px;text-align:center;vertical-align:middle";
    let html = `<table style="border-collapse:collapse;font-family:'Times New Roman',serif;font-size:11pt"><tr><th style="${cell}"></th>`
      + Array.from({ length: mos.cols }, (_, i) => `<th style="${cell};font-weight:normal">${FoV.colName(i)}</th>`).join("") + "</tr>";
    let tsv = "\t" + Array.from({ length: mos.cols }, (_, i) => FoV.colName(i)).join("\t") + "\n";
    for (let j = 0; j < mos.rows; j++) {
      html += `<tr><td style="${cell}">${j + 1}</td>`;
      tsv += j + 1;
      for (let i = 0; i < mos.cols; i++) {
        const r = rows[j * mos.cols + i], bg = MOS[r.status].doc;
        const mark = (s) => (bg ? `<span style="background:${bg}">${s}</span>` : s);
        html += `<td style="${cell}">${mark(fmtRA(r.ra))}<br>${mark(fmtDec(r.dec))}</td>`;
        tsv += `\t${fmtRA(r.ra)} ${fmtDec(r.dec)}`;
      }
      html += "</tr>";
      tsv += "\n";
    }
    html += "</table>";
    const used = MOS_ORDER.filter((s) => s !== "todo" && rows.some((r) => r.status === s));
    if (used.length)
      html += "<p>" + used.map((s) => `<span style="background:${MOS[s].doc}">&nbsp;${MOS[s].name}&nbsp;</span>`).join(" &nbsp; ") + "</p>";
    try {
      await navigator.clipboard.write([new ClipboardItem({ "text/html": new Blob([html], { type: "text/html" }),
                                                           "text/plain": new Blob([tsv], { type: "text/plain" }) })]);
    } catch (e) {                                    // older browsers, or a page opened from disk: copy a selection
      const box = document.createElement("div");
      box.contentEditable = "true"; box.innerHTML = html;
      box.style.cssText = "position:fixed;left:-9999px;top:0";
      document.body.appendChild(box);
      const range = document.createRange(); range.selectNodeContents(box);
      const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
      try { document.execCommand("copy"); } catch (e2) { /* nothing else to try */ }
      sel.removeAllRanges(); box.remove();
    }
    el("fMosMsg").textContent = "copied: paste it into the log";
    setTimeout(() => { if (el("fMosMsg")) el("fMosMsg").textContent = ""; }, 2500);
  }

  function mosCsv() {
    const { T, rows } = mosTiles(), tg = st.js.target, [cra, cdec] = FoV.fromTangent(st.fo.e, st.fo.n, tg.ra, tg.dec);
    const head = [
      `# SkyBound mosaic: ${st.fov.name}, ${mos.cols} x ${mos.rows} tiles, overlap ${mos.ov}%, PA ${mosPA()} deg (east of north)`,
      `# centre ${fmtRA(cra)} ${fmtDec(cdec)} (J2000); offset from ${st.js._label || "the target"} RA.cos(dec) ${sgn(st.fo.e).replace("−", "-")}" DEC ${sgn(st.fo.n).replace("−", "-")}"`,
      `# tile = science area ${T.box.w.toFixed(1)}" x ${T.box.h.toFixed(1)}"; step ${T.sx.toFixed(1)}" x ${T.sy.toFixed(1)}"; columns A.. East to West, rows 1.. North to South`,
      "# dE/dN_centre: the tile's pointing from the mosaic centre; dE/dN_target: from the target (TCSPD offset after pointing at it) [arcsec]",
    ];
    const lines = rows.slice().sort((a, b) => a.order - b.order).map((r) => [r.order, r.label, FoV.colName(r.col), r.row + 1,
      r.ra.toFixed(6), r.dec.toFixed(6), fmtRA(r.ra), fmtDec(r.dec), r.e.toFixed(1), r.n.toFixed(1),
      (st.fo.e + r.e).toFixed(1), (st.fo.n + r.n).toFixed(1), r.status].join(","));
    saveText(head.join("\n") + "\norder,tile,column,row,ra_deg,dec_deg,ra_hms,dec_dms,dE_centre,dN_centre,dE_target,dN_target,status\n"
      + lines.join("\n") + "\n", mosName() + ".csv", "text/csv");
  }

  // the whole mosaic as one Aladin instrument FoV: every tile's science regions,
  // in arcsec from the mosaic centre (x East, y North, as Aladin reads them)
  function mosAladin() {
    const { T, rows } = mosTiles(), pa = mosPA(), P = FoV.place(st.fov, pa, null), tg = st.js.target;
    const [cra, cdec] = FoV.fromTangent(st.fo.e, st.fo.n, tg.ra, tg.dec), x = (v) => v.toFixed(2);
    const res = [];
    for (const r of rows)
      P.science.forEach((rg, k) => {
        const id = `${r.label}${P.science.length > 1 ? "_" + (k + 1) : ""}`;
        if (rg.shape === "circle") {
          res.push(`  <RESOURCE ID="${id}" name="${id}"><TABLE utype="dal:footprint.geom.segment">
    <PARAM datatype="char" name="Shape" arraysize="*" value="Circle" utype="dal:footprint.geom.segment.shape"/>
    <PARAM datatype="double" unit="arcsec" name="cxPtPosition" utype="stc:AstroCoordArea.Circle.Center.C1" value="${x(r.e + rg.c[0])}"/>
    <PARAM datatype="double" unit="arcsec" name="cyPtPosition" utype="stc:AstroCoordArea.Circle.Center.C2" value="${x(r.n + rg.c[1])}"/>
    <PARAM datatype="double" unit="arcsec" name="radius" utype="stc:AstroCoordArea.Circle.radius" value="${x(rg.r)}"/>
  </TABLE></RESOURCE>`);
        } else {
          res.push(`  <RESOURCE ID="${id}" name="${id}"><TABLE utype="dal:footprint.geom.segment">
    <PARAM datatype="char" name="Shape" arraysize="*" value="Polygon" utype="dal:footprint.geom.segment.shape"/>
    <FIELD unit="arcsec" datatype="double" name="xPtPosition" utype="stc:AstroCoordArea.Polygon.Vertex.Position.C1"/>
    <FIELD unit="arcsec" datatype="double" name="yPtPosition" utype="stc:AstroCoordArea.Polygon.Vertex.Position.C2"/>
    <DATA><TABLEDATA>
${rg.pts.map(([a, b]) => `      <TR><TD>${x(r.e + a)}</TD><TD>${x(r.n + b)}</TD></TR>`).join("\n")}
    </TABLEDATA></DATA>
  </TABLE></RESOURCE>`);
        }
      });
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<!-- SkyBound mosaic: ${esc(st.fov.name)}, ${mos.cols} x ${mos.rows} tiles, overlap ${mos.ov}%, PA ${pa} deg.
     Aladin: File > Load instrument FoV, then put the FoV's centre at ${fmtRA(cra)} ${fmtDec(cdec)} (J2000), roll 0.
     The PA is already in the tiles. -->
<VOTABLE xmlns="http://www.ivoa.net/xml/VOTable/v1.1" version="1.1">
<RESOURCE ID="skybound_mosaic" utype="dal:footprint.geom">
  <PARAM datatype="char" arraysize="*" name="TelescopeName" value="${esc(st.fov.telescope || "")}"/>
  <PARAM datatype="char" arraysize="*" name="InstrumentName" value="${esc((st.fov.instrument || st.fov.name) + ` mosaic ${mos.cols}x${mos.rows}`)}"/>
  <PARAM datatype="char" arraysize="*" name="InstrumentDescription" value="centre ${fmtRA(cra)} ${fmtDec(cdec)} J2000; overlap ${mos.ov}%; PA ${pa}"/>
  <PARAM datatype="char" arraysize="*" name="Origin" value="SkyBound"/>
  <PARAM datatype="char" arraysize="*" utype="stc:AstroCoordSystem.CoordFrame.CARTESIAN" name="reference-frame" value="*"/>
  <PARAM name="projection" utype="stc:AstroCoordSystem.CoordFrame.Cart2DRefFrame.projection" datatype="char" arraysize="*" value="TAN"/>
  <PARAM name="Rollable" value="false"/>
${res.join("\n")}
</RESOURCE>
</VOTABLE>
`;
    saveText(xml, mosName() + "_aladin.xml", "application/xml");
  }

  // DS9 regions (fk5, degrees): every tile's science regions, labelled; Aladin reads them too
  function mosDs9() {
    const { rows } = mosTiles(), P = FoV.place(st.fov, mosPA(), null), tg = st.js.target, out = [];
    const sky = (e, n) => FoV.fromTangent(st.fo.e + e, st.fo.n + n, tg.ra, tg.dec);
    const [bx, by] = FoV.rot(FoV.scienceBox(st.fov).cx, FoV.scienceBox(st.fov).cy, mosPA());
    for (const r of rows) {
      const colr = { todo: "yellow", priority: "orange", observed: "cyan", done: "green", redo: "red" }[r.status];
      for (const rg of P.science) {
        if (rg.shape === "circle") {
          const [ra, dec] = sky(r.e + rg.c[0], r.n + rg.c[1]);
          out.push(`circle(${ra.toFixed(6)},${dec.toFixed(6)},${rg.r.toFixed(2)}") # color=${colr}`);
        } else {
          out.push(`polygon(${rg.pts.map(([a, b]) => sky(r.e + a, r.n + b).map((v) => v.toFixed(6)).join(",")).join(",")}) # color=${colr}`);
        }
      }
      const [lra, ldec] = sky(r.e + bx, r.n + by);
      out.push(`# text(${lra.toFixed(6)},${ldec.toFixed(6)}) text={${r.label}} color=${colr}`);
    }
    saveText(`# Region file format: DS9 version 4.1\n# SkyBound mosaic: ${st.fov.name}, ${mos.cols} x ${mos.rows}, overlap ${mos.ov}%\nglobal width=1 font="helvetica 10 bold"\nfk5\n`
      + out.join("\n") + "\n", mosName() + ".reg", "text/plain");
  }

  /* ---- the instrument: SPARC4 or a loaded FoV (js/fov.js) ------------------- */
  function fovOptions() {
    const sel = el("fovSel");
    sel.innerHTML = FoV.list().map((f) => `<option value="${esc(f.id)}">${esc(f.name)}${f.builtin ? " (built in)" : ""}</option>`).join("");
    sel.value = st.fov.id;
  }
  function fovMsg(kind, html) { el("fovMsg").className = "fov-msg" + (kind ? " " + kind : ""); el("fovMsg").innerHTML = html; }

  // the controls that only mean something for SPARC4 (s4field) are hidden for a loaded FoV
  function applyFovUI() {
    const s4 = isS4(), mosaic = st.mode === "mosaic";
    el("fMField").classList.toggle("on", !mosaic); el("fMMosaic").classList.toggle("on", mosaic);
    el("fMosRow").hidden = !mosaic; el("fMosIntro").hidden = !mosaic;
    el("fBandWrap").hidden = mosaic;
    for (const id of ["fTabs", "fModeLab", "fModeTipWrap"]) el(id).hidden = !s4 || mosaic;
    el("fPaWrap").hidden = s4;
    el("fGenIntro").hidden = s4 || mosaic;
    el("fBand").value = FoV.settingsOf(st.fov).band;
    if (mosaic) {
      for (const id of ["fDvRow", "fMaxRow", "fIntroBasic", "fBasicFilt", "fAdvPane", "fFiltHelp"]) el(id).hidden = true;
      el("fGo").textContent = goLabel();
      el("fCardTitle").firstChild.nodeValue = "Mosaic";
      el("fovInfoBtn").textContent = s4 ? "About…" : "Regions…";
      fovPanel();
      return;
    }
    if (s4) setTab(st.tab, true);
    else {                               // basic mode only: the dV window, the limit and the filters stay
      el("fIntroBasic").hidden = true; el("fAdvPane").hidden = true;
      el("fBasicFilt").hidden = false; el("fDvRow").hidden = false; el("fMaxRow").hidden = false;
    }
    el("fGo").textContent = goLabel();
    el("fCardTitle").firstChild.nodeValue = s4 ? "Comparison field" : "Field";
    el("fovInfoBtn").textContent = s4 ? "About…" : "Regions…";
    fovPanel();
  }

  function fovPanel() {
    const f = st.fov, p = el("fovPanel");
    if (p.hidden) return;
    const A = FoV.analyse(f), cnt = {};
    for (const r of f.regions) cnt[r.role] = (cnt[r.role] || 0) + 1;
    const sum = FoV.ROLES.filter((r) => cnt[r]).map((r) => `${cnt[r]} ${r}`).join(" · ");
    const facts = [
      f.telescope && `telescope <b>${esc(f.telescope)}</b>`, f.instrument && `instrument <b>${esc(f.instrument)}</b>`,
      f.description && esc(f.description), f.origin && `origin: ${esc(f.origin)}`,
      f.file && `file <span class="file-tag">${esc(f.file)}</span>${f.loaded ? ` (loaded ${esc(f.loaded)})` : ""}`,
    ].filter(Boolean).join(" · ");
    const size = `science area out to ${A.extent >= 120 ? (A.extent / 60).toFixed(1) + "′" : A.extent.toFixed(1) + "″"} from the centre`
      + (A.patrol ? `; patrol annulus ${A.patrol.rin.toFixed(1)}–${A.patrol.rout.toFixed(1)}″` : "")
      + (A.probe ? `; the guider is a probe that turns around the centre (at PA ${A.probePA0.toFixed(1)}° in the file)` : "");
    let h = `<div class="fov-facts"><b>${esc(f.name)}</b> · ${sum}<br>${facts}<br>${size}.</div>`;
    if (f.builtin) {
      h += `<p class="fov-note">Built in: the area common to the four channels measured from the 2026 pipeline WCS, the
        Savart second image and the guider of <code>sparc4_fov.xml</code>. Comparison stars, offsets and the guider angle
        come from the s4field helper. Its position angle is fixed (no rotator on the 1.6 m).</p>`;
    } else {
      const G = FoV.groups(f);
      h += `<table class="fov-roles"><tr><th>Region</th><th>Shape</th><th>How many</th><th>Role</th></tr>`
        + G.map((g, k) => `<tr><td>${esc(g.label)}</td><td>${g.shape}</td><td>${g.idx.length}</td><td><select data-g="${k}">`
          + FoV.ROLES.map((r) => `<option${r === g.role ? " selected" : ""}>${r}</option>`).join("") + "</select></td></tr>").join("")
        + "</table>";
      h += `<p class="fov-note">Roles were guessed from the shapes (a polygon inside two centred circles is a guide probe that turns
        around the centre; the rest is the detector) and the names. Change them if they are wrong; <b>science</b> is what counts
        as "in the field". The file's x is taken as East and y as North at position angle 0; the field PA turns it east of north.`
        + (f.rollable ? "" : " The file does not mark this FoV as rollable; SkyBound lets you turn it anyway.") + "</p>";
      const c = FoV.settingsOf(f), v = (x) => (x == null ? "" : String(x));
      h += `<div class="fov-set"><b>Comparison stars</b> <span class="fov-note">(ranked by s4field in the band chosen beside Field PA)</span><br>
        <label>edge margin <input type="number" data-set="margin" min="0" step="1" value="${v(c.margin)}">″</label>
        <label>isolation radius <input type="number" data-set="iso" min="0.5" step="0.5" value="${v(c.iso)}">″</label>
        <label>second image (polarimeter) <input type="number" data-set="sep" min="0" step="0.1" placeholder="none" value="${v(c.split && c.split.sep)}">″
          at PA <input type="number" data-set="pa" step="1" placeholder="0" value="${v(c.split && c.split.pa)}">°</label>
        <label>guide stars G <input type="number" data-set="g0" step="0.5" placeholder="any" value="${v(c.guide && c.guide[0])}">
          to <input type="number" data-set="g1" step="0.5" placeholder="any" value="${v(c.guide && c.guide[1])}"></label>
        <span class="fov-note">The edge margin keeps the aperture and sky annulus on a detector (and off chip gaps); a neighbour
          brighter than G+5 within the isolation radius rejects a star. The second image's PA is at field PA 0, east of north.</span></div>`;
      if (f.warnings && f.warnings.length) h += `<div class="warn fld-warn">${f.warnings.map(esc).join("<br>")}</div>`;
      h += `<div class="fld-bar"><button class="btn-ghost btn-sm" type="button" id="fovExport">Download as SkyBound JSON</button>
        <button class="btn-ghost btn-sm" type="button" id="fovRemove">Remove from this browser</button></div>`;
    }
    p.innerHTML = h;
  }

  /* ---- "New FoV": a rectangular detector from its numbers ---------------------- */
  const DETECTORS = [
    { name: "Andor iKon-L 936", nx: 2048, ny: 2048, um: 13.5 },
    { name: "Andor iKon-M 934", nx: 1024, ny: 1024, um: 13 },
    { name: "Andor iXon Ultra 888", nx: 1024, ny: 1024, um: 13 },
    { name: "Andor iXon Ultra 897", nx: 512, ny: 512, um: 16 },
    { name: "Apogee Alta U16M", nx: 4096, ny: 4096, um: 9 },
  ];
  const TELESCOPES = [
    { name: "OPD 1.6 m Perkin-Elmer · f/10 · 15 752 mm", short: "OPD 1.6 m Perkin-Elmer", focal: 15752 },
    { name: "OPD 0.6 m Boller & Chivens · f/13.5 · 8100 mm", short: "OPD 0.6 m Boller & Chivens", focal: 8100 },
    { name: "OPD 0.6 m Zeiss · f/12.5 · 7500 mm", short: "OPD 0.6 m Zeiss", focal: 7500 },
  ];
  function newFovForm(open) {
    const box = el("fovNew");
    box.hidden = !open;
    if (!open) return;
    const opt = (arr) => '<option value="">choose…</option>' + arr.map((d, k) => `<option value="${k}">${esc(d.name)}</option>`).join("");
    box.innerHTML = `<div class="fov-form">
      <div class="ff-title"><b>New FoV</b> · a rectangular detector, from its numbers</div>
      <div class="ff-row">
        <label>Instrument <input id="nfInst" placeholder="e.g. iKon-L"></label>
        <label>Telescope <input id="nfTel" placeholder="e.g. OPD 0.6 m Zeiss"></label>
      </div>
      <div class="ff-row">
        <label>Detector <select id="nfDet">${opt(DETECTORS)}</select></label>
        <label>Pixels <span class="ff-pair"><input id="nfNx" type="number" min="1" step="1"> × <input id="nfNy" type="number" min="1" step="1"></span></label>
        <label>Pixel size <span class="ff-pair"><input id="nfPix" type="number" min="0" step="0.1"> µm</span></label>
      </div>
      <div class="ff-row">
        <label>Telescope <select id="nfScope">${opt(TELESCOPES)}</select></label>
        <label>Focal length <span class="ff-pair"><input id="nfFocal" type="number" min="0" step="1"> mm</span></label>
        <label>or plate scale <span class="ff-pair"><input id="nfScale" type="number" min="0" step="0.001" placeholder="from the above"> ″/px</span></label>
      </div>
      <div class="ff-row">
        <label>Detector centre from the pointing <span class="ff-pair">x <input id="nfDx" type="number" step="1" value="0">″ (E) &nbsp; y <input id="nfDy" type="number" step="1" value="0">″ (N)</span></label>
        <label>Guider <select id="nfGuide"><option value="none">none</option><option value="fixed">a fixed box</option><option value="probe">a probe in a patrol annulus</option></select></label>
      </div>
      <div class="ff-row" id="nfG_fixed" hidden>
        <label>Box side <span class="ff-pair"><input id="nfGs" type="number" min="1" step="1">″</span></label>
        <label>Box centre <span class="ff-pair">x <input id="nfGx" type="number" step="1" value="0">″ y <input id="nfGy" type="number" step="1" value="0">″</span></label>
      </div>
      <div class="ff-row" id="nfG_probe" hidden>
        <label>Patrol annulus <span class="ff-pair"><input id="nfRin" type="number" min="1" step="1"> to <input id="nfRout" type="number" min="1" step="1">″</span></label>
        <label>Probe box side <span class="ff-pair"><input id="nfPs" type="number" min="1" step="1">″</span></label>
      </div>
      <div class="ff-out" id="nfOut"></div>
      <div class="fld-bar"><button class="btn-primary btn-sm" id="nfMake" type="button">Create FoV</button>
        <button class="btn-ghost btn-sm" id="nfCancel" type="button">Cancel</button></div>
      <p class="fov-note">The scale comes from the nominal focal length: check it against a solved image (a focal reducer
        changes it). The field is drawn north up at PA 0, with the detector's x axis to the East; set the real PA with Field PA.</p>
    </div>`;
    newFovPreview();
  }
  function newFovSpec() {
    const v = (id) => el(id).value.trim(), g = v("nfGuide");
    return { instrument: v("nfInst"), telescope: v("nfTel"), nx: v("nfNx"), ny: v("nfNy"), pix_um: v("nfPix"),
             focal_mm: v("nfFocal"), scale: v("nfScale"), dx: v("nfDx"), dy: v("nfDy"),
             guider: g === "fixed" ? { kind: g, size: v("nfGs"), x: v("nfGx"), y: v("nfGy") }
                   : g === "probe" ? { kind: g, rin: v("nfRin"), rout: v("nfRout"), size: v("nfPs") } : { kind: "none" } };
  }
  function newFovPreview() {
    if (el("fovNew").hidden || !el("nfOut")) return;
    try {
      const f = FoV.build(newFovSpec());
      el("nfOut").className = "ff-out"; el("nfOut").textContent = `→ ${f.name}: ${f.description}`;
    } catch (e) { el("nfOut").className = "ff-out bad"; el("nfOut").textContent = e.message; }
  }
  function makeNewFov() {
    let f;
    try { f = FoV.build(newFovSpec()); } catch (e) { el("nfOut").className = "ff-out bad"; el("nfOut").textContent = e.message; return; }
    const r = FoV.add(f);
    fovOptions();
    newFovForm(false);
    useFov(r.fov.id, true);
    el("fovPanel").hidden = false; el("fovInfoBtn").setAttribute("aria-expanded", "true");
    fovPanel();
    fovMsg("", `${r.again ? "Already made" : "Made"}: <b>${esc(r.fov.name)}</b>, ${esc(r.fov.description)}.`);
    render();
    if (sb()) compute(false);
  }

  function useFov(id, quiet) {
    st.fov = FoV.get(id) || FoV.SPARC4;
    FoV.select(st.fov.id);
    st.fpa = 0; el("fPa").value = "0";
    st.ppaTouched = false; st.foFor = null; st.key = null; st.fovRev++;
    if (el("fovSel").value !== st.fov.id) fovOptions();
    applyFovUI();
    if (quiet) return;
    render();                            // the heading
    if (st.computed) compute(false);
  }

  async function loadFovFile(file) {
    let text;
    try { text = await file.text(); } catch (e) { fovMsg("bad", "Could not read that file."); return; }
    let fov;
    try { fov = FoV.load(text, file.name); }
    catch (e) { fovMsg("bad", `${esc(file.name)}: ${esc(e.message)}`); return; }
    const r = FoV.add(fov);
    fovOptions();
    useFov(r.fov.id, true);
    el("fovPanel").hidden = false; el("fovInfoBtn").setAttribute("aria-expanded", "true");
    fovPanel();
    const A = FoV.analyse(r.fov);
    fovMsg(r.stored ? "" : "warn", `${r.again ? "Already loaded" : "Loaded"}: <b>${esc(r.fov.name)}</b>, ${r.fov.regions.length} region${r.fov.regions.length === 1 ? "" : "s"}`
      + `${A.gd.length ? (A.probe ? ", with a guide probe" : ", with a guider") : ""}. Check the roles below.`
      + (r.stored ? "" : " It is too large to keep in this browser: load it again next time."));
    render();
    if (sb()) compute(false);
  }

  function onRender() {
    const s = sb();
    if (!s) {                            // no target (yet): nothing to show for a field
      el("fHead").textContent = st.fov.name;
      el("fOut").hidden = true; st.computed = false; st.js = null; showError("");
      return;
    }
    const key = `${s.cw.base}|${s.ctx.ra.toFixed(5)}|${s.ctx.dec.toFixed(5)}`;
    if (key !== st.plannedKey || !el("fTime").value) {       // new night or target
      st.plannedKey = key;
      el("fTime").value = hmL(bestTime(s.ctx, s.cw));
    }
    whenNote();
    const lab = targetLabel();
    el("fHead").textContent = st.fov.name + (lab ? " · " + lab : "");
    if (st.mode === "mosaic") { compute(true); return; }   // a mosaic only needs the target
    if (!st.computed) return;
    const targetChanged = fieldTarget() !== st.lastTarget;
    if (targetChanged) { st.at = null; st.guiderTouched = false; }
    if (!isS4() || st.tab === "basic" || el("fAuto").checked || targetChanged) compute(true);
  }

  function markerMs() {
    const s = sb();
    if (!s || st.tab !== "advanced" || el("fWhen").value !== "planned") return null;
    return plannedMs(s.cw);
  }

  /* ---- wiring -------------------------------------------------------------- */
  function init() {
    el("tFind").addEventListener("click", find);
    el("tName").addEventListener("keydown", (e) => { if (e.key === "Enter") find(); });
    for (const id of ["ra", "dec"]) el(id).addEventListener("change", coordsEdited);
    el("scope").addEventListener("change", save);
    el("fGo").addEventListener("click", () => {
      if (typeof tgtCommit === "function" && typeof ACTIVE !== "undefined" && ACTIVE < 0 && tgtCommit()) render();
      compute(false);
    });
    el("fTarget").addEventListener("change", () => {     // another target of the list: its field
      const i = parseInt(el("fTarget").value, 10);
      if (!(i >= 0) || typeof tgtUse !== "function") return;
      tgtUse(i);
      compute(false);
    });
    el("fTabBasic").addEventListener("click", () => setTab("basic"));
    el("fTabAdv").addEventListener("click", () => setTab("advanced"));
    el("fMode").addEventListener("change", () => { save(); if (st.computed) compute(true); });
    el("fCsv").addEventListener("click", downloadCSV);
    // mode tooltip: hover / focus in CSS; a tap (touch screens) pins it, a tap elsewhere or Esc closes it
    const tip = el("fModeTipWrap"), tipOpen = (on) => { tip.classList.toggle("open", on); el("fModeBtn").setAttribute("aria-expanded", String(on)); };
    el("fModeBtn").addEventListener("click", (e) => { e.stopPropagation(); tipOpen(!tip.classList.contains("open")); });
    document.addEventListener("click", (e) => { if (!tip.contains(e.target)) tipOpen(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { tipOpen(false); if (tip.contains(document.activeElement)) document.activeElement.blur(); } });
    el("fWhen").addEventListener("change", () => render());
    el("fTime").addEventListener("change", () => { el("fWhen").value = "planned"; render(); });
    el("fMoon").addEventListener("change", whenNote);
    el("fViewField").addEventListener("click", () => st.js && preset("field"));
    el("fViewGuider").addEventListener("click", () => st.js && preset("guider"));
    el("fZoomIn").addEventListener("click", () => zoom(1 / 1.4));
    el("fZoomOut").addEventListener("click", () => zoom(1.4));
    el("fReset").addEventListener("click", backToRecommended);
    el("fSummary").addEventListener("change", (ev) => {     // the target list in the summary bar
      if (ev.target.id !== "fTargetSum") return;
      const i = parseInt(ev.target.value, 10);
      if (!(i >= 0)) return;
      tgtUse(i);
      compute(false);
    });
    el("fSummary").addEventListener("click", async (ev) => {
      const c = ev.target.closest(".copy-btn");
      if (c) {
        const txt = c.getAttribute("data-copy");
        try { await navigator.clipboard.writeText(txt); }
        catch (e) { const ta = document.createElement("textarea"); ta.value = txt; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); } catch (e2) {} ta.remove(); }
        c.textContent = "copied"; setTimeout(() => { c.textContent = "copy"; }, 1200);
        return;
      }
      if (ev.target.closest("#fEtaBtn")) {
        const h = el("fEtaHelp"), open = h.hidden;
        h.hidden = !open; el("fEtaBtn").setAttribute("aria-expanded", String(open));
        st.etaHelp = open;
      }
    });
    const svg = el("fSvg");
    svg.addEventListener("pointerdown", onDown);
    svg.addEventListener("pointermove", onMove);
    svg.addEventListener("pointerup", onUp);
    svg.addEventListener("pointercancel", onUp);
    svg.addEventListener("wheel", onWheel, { passive: false });
    svg.addEventListener("dblclick", () => st.js && preset(st.preset));
    el("toggleField").addEventListener("click", () => {
      const b = el("fieldBody"), hidden = b.style.display === "none";
      b.style.display = hidden ? "" : "none";
      el("toggleField").textContent = hidden ? "Hide" : "Show";
    });
    for (const id of ["fDvMin", "fDvMax"]) {
      el(id).addEventListener("input", dvShow);
      el(id).addEventListener("change", () => { dvShow(); settingsChanged(); });
    }
    el("fDvReset").addEventListener("click", () => {
      el("fDvMin").value = DV_DEFAULT[0]; el("fDvMax").value = DV_DEFAULT[1]; dvShow(); settingsChanged();
    });
    el("fFiltAll").addEventListener("change", settingsChanged);
    el("fMaxComps").addEventListener("change", () => { maxComps(); settingsChanged(); });
    el("fMaxReset").addEventListener("click", () => { el("fMaxComps").value = MAX_COMPS; settingsChanged(); });
    for (const c of document.querySelectorAll(".fFilt")) c.addEventListener("change", settingsChanged);
    el("fFiltOn").addEventListener("click", (e) => { e.preventDefault(); for (const c of document.querySelectorAll(".fFilt")) c.checked = true; settingsChanged(); });
    el("fFiltOff").addEventListener("click", (e) => { e.preventDefault(); for (const c of document.querySelectorAll(".fFilt")) c.checked = false; settingsChanged(); });
    el("fFiltBtn").addEventListener("click", toggleHelp);
    el("fFiltBtn2").addEventListener("click", toggleHelp);
    // the instrument
    el("fovSel").addEventListener("change", () => { fovMsg("", ""); useFov(el("fovSel").value); });
    el("fovLoad").addEventListener("click", () => el("fovFile").click());
    el("fovFile").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) loadFovFile(f); });
    el("fovInfoBtn").addEventListener("click", () => {
      const p = el("fovPanel"); p.hidden = !p.hidden;
      el("fovInfoBtn").setAttribute("aria-expanded", String(!p.hidden));
      fovPanel();
    });
    el("fovPanel").addEventListener("change", (ev) => {
      if (ev.target.matches("[data-set]")) {       // the FoV's comparison settings
        const q = (k) => { const x = el("fovPanel").querySelector(`[data-set="${k}"]`); return x ? x.value.trim() : ""; };
        FoV.setSettings(st.fov.id, {
          margin: q("margin"), iso: q("iso"),
          split: q("sep") !== "" && +q("sep") > 0 ? { sep: q("sep"), pa: q("pa") || 0 } : null,
          guide: q("g0") !== "" && q("g1") !== "" ? [q("g0"), q("g1")] : null,
        });
        fovPanel();
        if (st.js && st.js.generic) redraw();
        if (st.computed) compute(true);
        return;
      }
      const s = ev.target.closest("select[data-g]");
      if (!s) return;
      const g = FoV.groups(st.fov)[+s.getAttribute("data-g")];
      if (!g) return;
      FoV.setRole(st.fov.id, g.idx, s.value);
      st.fovRev++; st.ppaTouched = false;
      fovPanel();
      if (st.js && st.js.generic) { st.ppa = defaultPPA(); preset(st.preset); }
    });
    el("fovPanel").addEventListener("click", (ev) => {
      if (ev.target.id === "fovExport") saveText(FoV.toJSON(st.fov), `${slug(st.fov.name)}.skybound-fov.json`, "application/json");
      if (ev.target.id === "fovRemove") {
        const name = st.fov.name;
        FoV.remove(st.fov.id);
        fovOptions(); useFov(FoV.SPARC4.id);
        fovMsg("", `Removed <b>${esc(name)}</b> from this browser.`);
      }
    });
    const setPA = (v, typing) => {
      v = parseFloat(v);
      st.fpa = isFinite(v) ? Math.round((((v % 360) + 360) % 360) * 100) / 100 : 0;
      if (!typing) el("fPa").value = String(st.fpa);
      if (!st.ppaTouched) st.ppa = defaultPPA();
      if (st.js && st.js.generic) {
        redraw();
        clearTimeout(st.paTimer);          // the turned footprint goes to the helper once typing stops
        if (!st.js.nohelper) st.paTimer = setTimeout(() => compute(true), 450);
      }
    };
    el("fPa").addEventListener("change", () => setPA(el("fPa").value));
    el("fPa").addEventListener("input", () => { if (el("fPa").value !== "" && isFinite(+el("fPa").value)) setPA(el("fPa").value, true); });
    el("fPaZero").addEventListener("click", () => setPA(0));
    el("fBand").addEventListener("change", () => {
      FoV.setSettings(st.fov.id, { band: el("fBand").value });
      fovPanel();
      if (st.computed) compute(true);
    });
    el("fovNewBtn").addEventListener("click", () => newFovForm(true));
    // Field | Mosaic
    const setMode = (m) => {
      if (st.mode === m) return;
      st.mode = m; st.key = null; st.v = null; st.foFor = null;
      if (m === "field") st.fo = { e: 0, n: 0 };
      save(); applyFovUI(); render();
      if (fieldTarget()) compute(false); else el("fOut").hidden = true;
    };
    el("fMField").addEventListener("click", () => setMode("field"));
    el("fMMosaic").addEventListener("click", () => setMode("mosaic"));
    for (const id of ["fMosCols", "fMosRows", "fMosOv"]) el(id).addEventListener("change", () => mosFromInputs("grid"));
    el("fMosW").addEventListener("change", () => mosFromInputs("w"));
    el("fMosH").addEventListener("change", () => mosFromInputs("h"));
    el("fMosCenter").addEventListener("click", mosCenter);
    el("fMiss").addEventListener("click", (ev) => {
      const id = ev.target.id;
      if (id === "fMosCopy") mosCopy();
      else if (id === "fMosCsv") mosCsv();
      else if (id === "fMosAladin") mosAladin();
      else if (id === "fMosDs9") mosDs9();
      else if (id === "fMosClear") { mos.status = {}; mosSave(); redraw(); }
    });
    el("fTable").addEventListener("click", (ev) => {
      const td = ev.target.closest("td[data-tile]");
      if (td && st.js && st.js.mosaic) mosCycle(td.getAttribute("data-tile"));
    });
    el("fovNew").addEventListener("input", newFovPreview);
    el("fovNew").addEventListener("change", (ev) => {
      if (ev.target.id === "nfDet") {
        const d = DETECTORS[+ev.target.value];
        if (d) { el("nfNx").value = d.nx; el("nfNy").value = d.ny; el("nfPix").value = d.um; if (!el("nfInst").value.trim()) el("nfInst").value = d.name; }
      }
      if (ev.target.id === "nfScope") {
        const t = TELESCOPES[+ev.target.value];
        if (t) { el("nfFocal").value = t.focal; el("nfScale").value = ""; if (!el("nfTel").value.trim()) el("nfTel").value = t.short; }
      }
      if (ev.target.id === "nfGuide") for (const k of ["fixed", "probe"]) el("nfG_" + k).hidden = ev.target.value !== k;
      newFovPreview();
    });
    el("fovNew").addEventListener("click", (ev) => {
      if (ev.target.id === "nfCancel") newFovForm(false);
      if (ev.target.id === "nfMake") makeNewFov();
    });
    el("fStatus").addEventListener("click", statusClick);
    // a target dropdown is as wide as the name it shows (its label is spaced capitals)
    new MutationObserver(() => document.querySelectorAll("#fSummary .cd-tsel").forEach(fitSelect))
      .observe(el("fSummary"), { childList: true, subtree: true });
    document.addEventListener("change", (ev) => { if (ev.target.matches && ev.target.matches(".cd-tsel")) fitSelect(ev.target); });
    st.fov = FoV.selected();
    fovOptions();
    restore();
    applyFovUI();
    dvShow();
    ping();
    render();                            // runs onRender and draws the planned-time marker
  }

  // after `Field` exists: render() calls back into it
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else setTimeout(init, 0);

  return { onRender, markerMs, compute, ping, find };
})();
