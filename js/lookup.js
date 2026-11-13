"use strict";
/* SkyBound · target name lookup without the s4field helper.

   Tried in this order (field.js asks the helper first when it is running):
     1. online, straight from the browser
        - a Gaia DR3 source ID → GAVO's gaia.dr3lite (the one Gaia service a web
          page may read: tools/cors_test2.html), moved from epoch 2016.0 to 2000.0;
        - any other name → CDS Sesame (SIMBAD, then VizieR), Strasbourg, then the
          CfA mirror;
     2. offline, the reference tables: js/targets.js, built from the
        toi_transits.py reference_tables/ folder by tools/build_targets.py and
        loaded only when needed.
   A name the online services don't know (many TOIs) also falls through to 2.

   Lookup.resolve(name) → { ra, dec (J2000, degrees), source, offline, note }
   or throws an Error whose message is ready to show.
   By Cleber Silva · UFC/MALTED */

const Lookup = (() => {
  const SESAME = ["https://cds.unistra.fr/cgi-bin/nph-sesame/-ox/SNV?",
                  "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-ox/SNV?"];
  const GAVO = "https://dc.g-vo.org/tap/sync";
  const ONLINE_MS = 8000;               // per service: past this, try the next layer
  const SELF = (document.currentScript && document.currentScript.src) || "";
  const INDEX_URL = SELF ? SELF.replace(/lookup\.js(\?.*)?$/, "targets.js") : "js/targets.js";

  class Offline extends Error {}        // no answer at all: network down, blocked or timed out

  async function getText(url, ms = ONLINE_MS) {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), ms);
    let r;
    try { r = await fetch(url, { signal: ctl.signal, cache: "no-store" }); }
    catch (e) { throw new Offline(e.name === "AbortError" ? "timeout" : "unreachable"); }
    finally { clearTimeout(timer); }
    if (!r.ok) throw new Offline(`HTTP ${r.status}`);
    return r.text();
  }

  /* ---- names ---------------------------------------------------------------- */
  // the same key as tools/build_targets.py: lower case, letters and digits only
  const key = (s) => String(s).toLowerCase().normalize("NFKD").replace(/[^a-z0-9]/g, "");
  function variants(name) {             // the name, then its host star
    const n = name.trim().replace(/\s+/g, " "), out = [n];
    const planet = n.match(/^(.*\S)\s+[A-C]?[b-k]$/);          // "AU Mic b", "CoRoT-2 Ab"
    if (planet) out.push(planet[1]);
    const toi = n.match(/^(TOI)[-\s]?(\d+)\.\d{1,2}$/i);       // "TOI-2631.01"
    if (toi) out.push(`TOI-${toi[2]}`);
    const tic = n.match(/^TIC[-\s]?(\d+)$/i);                  // "TIC-123" = "TIC 123"
    if (tic) out.push(`TIC ${tic[1]}`);
    return [...new Set(out)];
  }
  const gaiaId = (name) => {
    const m = name.trim().match(/^Gaia\s*DR3\s*(\d{6,20})$/i);
    return m ? m[1] : null;
  };

  /* ---- 1a. Gaia DR3 ID → GAVO ------------------------------------------------- */
  async function fromGaia(id) {
    const adql = `SELECT ra, dec, pmra, pmdec FROM gaia.dr3lite WHERE source_id = ${id}`;
    const csv = await getText(`${GAVO}?REQUEST=doQuery&LANG=ADQL&FORMAT=csv&QUERY=${encodeURIComponent(adql)}`);
    const lines = csv.trim().split(/\r?\n/);
    if (lines.length < 2) return null;
    const head = lines[0].split(","), row = lines[1].split(",");
    const v = (c) => parseFloat(row[head.indexOf(c)]);
    let ra = v("ra"), dec = v("dec");
    const pmra = v("pmra"), pmdec = v("pmdec");
    if (!isFinite(ra) || !isFinite(dec)) return null;
    if (isFinite(pmra) && isFinite(pmdec)) {     // 2016.0 → 2000.0 (pmra includes cos dec)
      dec -= (16 * pmdec) / 3.6e6;
      ra -= (16 * pmra) / 3.6e6 / Math.cos((dec * Math.PI) / 180);
    }
    return { ra: ((ra % 360) + 360) % 360, dec, source: "Gaia DR3 via GAVO", note: "position moved to epoch 2000 with its proper motion" };
  }

  /* ---- 1b. Sesame ------------------------------------------------------------ */
  // XML answer: one <Resolver> per database tried; the first with <jradeg> wins
  function parseSesame(xml) {
    const doc = new DOMParser().parseFromString(xml, "text/xml");
    for (const r of doc.getElementsByTagName("Resolver")) {
      const ra = parseFloat(r.getElementsByTagName("jradeg")[0]?.textContent);
      const dec = parseFloat(r.getElementsByTagName("jdedeg")[0]?.textContent);
      if (isFinite(ra) && isFinite(dec)) {
        const db = (r.getAttribute("name") || "").replace(/^\w=/, "").split(/[ (]/)[0] || "Sesame";
        const oname = r.getElementsByTagName("oname")[0]?.textContent?.trim();
        return { ra, dec, source: `Sesame · ${db}`, note: oname ? `as ${oname}` : "" };
      }
    }
    return null;                         // answered, but the name is unknown
  }
  async function fromSesame(name) {
    let why = "";
    for (const base of SESAME) {
      try { return parseSesame(await getText(base + encodeURIComponent(name))); }
      catch (e) { if (!(e instanceof Offline)) throw e; why = e.message; }  // try the mirror
    }
    throw new Offline(why);
  }

  /* ---- 2. reference tables ---------------------------------------------------- */
  let index = null, loading = null;
  function loadIndex() {
    if (index) return Promise.resolve(index);
    if (!loading) loading = new Promise((resolve) => {
      const done = () => {
        const t = window.SB_TARGETS;
        if (!t || !Array.isArray(t.rows)) { loading = null; resolve(null); return; }
        const map = new Map();               // rows are in priority order: first one wins
        t.rows.forEach((row, i) => {
          for (const a of row[3].split("|")) { const k = key(a); if (k && !map.has(k)) map.set(k, i); }
        });
        index = { t, map };
        resolve(index);
      };
      if (window.SB_TARGETS) { done(); return; }
      const s = document.createElement("script");
      s.src = INDEX_URL; s.async = true;
      s.onload = done;
      s.onerror = () => { s.remove(); loading = null; resolve(null); };
      document.head.appendChild(s);
    });
    return loading;
  }
  async function fromTables(name) {
    const ix = await loadIndex();
    if (!ix) return { missing: true };
    for (const v of variants(name)) {
      const i = ix.map.get(key(v));
      if (i == null) continue;
      const [ra, dec, si, names] = ix.t.rows[i];
      const first = names.split("|")[0];
      return { ra, dec, source: `${ix.t.sources[si]} (reference table)`, offline: true,
               note: key(first) === key(name) ? "" : `as ${first}` };
    }
    return null;
  }

  /* ---- the chain -------------------------------------------------------------- */
  async function resolve(name, opts = {}) {
    const online = opts.online !== false;
    let netWhy = null;                   // set when no online service answered at all
    if (online) {
      try {
        const id = gaiaId(name);
        const hit = id ? await fromGaia(id) : await fromSesame(name);
        if (hit) return hit;
      } catch (e) { if (e instanceof Offline) netWhy = e.message; else throw e; }
    }
    const hit = await fromTables(name);
    if (hit && !hit.missing) {
      if (netWhy) hit.note = [hit.note, "the online services did not answer"].filter(Boolean).join(" · ");
      return hit;
    }
    const tables = hit && hit.missing
      ? "no reference index yet (<code>python3 tools/build_targets.py &lt;reference_tables folder&gt;</code>)"
      : "not in the reference tables";
    const net = !online ? "" : netWhy ? "the online services did not answer (no internet, or they are down)" : "unknown online";
    throw new Error(`<b>${esc(name)}</b>: ${[net, tables].filter(Boolean).join("; ")}. Type RA/Dec below instead.`);
  }

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // the online layer alone (tools/lookup_test.html): a hit, null (unknown), or throws "Offline: …"
  async function online(name) {
    const id = gaiaId(name);
    try { return id ? await fromGaia(id) : await fromSesame(name); }
    catch (e) { throw new Error(e instanceof Offline ? `no answer (${e.message})` : e.message); }
  }

  return { resolve, online, fromTables, loadIndex, variants, key, parseSesame };
})();
