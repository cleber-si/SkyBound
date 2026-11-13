"use strict";
/* SkyBound · instrument fields of view (FoV).
   A FoV is a set of regions in the instrument's focal plane, in arcsec East
   and North of the pointing centre at position angle 0 (north up):
     science  the detector(s): what counts as "in the field"
     guider   a guide camera or probe; if it sits inside a patrol annulus
              (two circles on the centre), it can be turned around it
     patrol   the circles bounding where a guide probe can go
     other    drawn, nothing else
   SPARC4 is built in (measured four-channel footprint, s4field does the
   comparison stars). Any other FoV comes from an Aladin "Load instrument FoV"
   file (XML/VOTable), in either of its two dialects:
     - dal:footprint.geom: one TABLE per shape, PARAM Shape = Polygon (FIELDs
       xPtPosition, yPtPosition) or Circle (PARAMs cxPtPosition, cyPtPosition,
       radius), usually one RESOURCE per shape;
     - ivoa:characterization (the older ESO/CDS note): TABLEs with PARAM
       Region = Polygon and FIELDs VertexRAOffset, VertexDecOffset.
   x is taken as East (standard coordinate xi), y as North. Loaded FoVs are
   kept in this browser (localStorage) and can be exported as SkyBound JSON.
   By Cleber Silva · UFC/MALTED */

const FoV = (() => {
  const STORE = "skybound.fovs.v1", SEL = "skybound.fov.sel.v1";
  const ROLES = ["science", "guider", "patrol", "other"];
  // how a loaded FoV's comparison stars are chosen (s4field basic mode); kept with the FoV
  const BANDS = ["V", "G", "g", "r", "i", "z"];
  const DEFAULTS = { band: "V", margin: 12, iso: 7.5, split: null, guide: null };   // split {sep, pa}; guide [Gmin, Gmax]
  const settingsOf = (fov) => ({ ...DEFAULTS, ...((fov && fov.settings) || {}) });
  function cleanSettings(o) {
    const out = {}, num = (v) => (v === "" || v == null ? NaN : Number(v));
    if (o && BANDS.includes(o.band)) out.band = o.band;
    if (o && num(o.margin) >= 0) out.margin = num(o.margin);
    if (o && num(o.iso) > 0) out.iso = num(o.iso);
    if (o && o.split && num(o.split.sep) > 0 && isFinite(num(o.split.pa))) out.split = { sep: num(o.split.sep), pa: num(o.split.pa) };
    else if (o && "split" in o) out.split = null;
    if (o && Array.isArray(o.guide) && o.guide.length === 2 && o.guide.every((v) => isFinite(num(v))))
      out.guide = [Math.min(num(o.guide[0]), num(o.guide[1])), Math.max(num(o.guide[0]), num(o.guide[1]))];
    else if (o && "guide" in o) out.guide = null;
    return out;
  }
  const D2R_ = Math.PI / 180;
  const r2 = (x) => Math.round(x * 1000) / 1000;   // 1 mas: IFU fibres are ~1″

  /* ---- SPARC4, built in ---------------------------------------------------- */
  // s4field footprints("LARGE"): the area common to the four channels, and the
  // channels themselves (2026 pipeline WCS); the guider of sparc4_fov.xml
  const SPARC4 = {
    id: "builtin:sparc4", builtin: true, engine: "s4field",
    name: "SPARC4 · OPD 1.6 m", telescope: "OPD 1.6 m (Perkin-Elmer)", instrument: "SPARC4",
    description: "four simultaneous channels g r i z; comparison stars by s4field",
    origin: "measured from the 2026 pipeline WCS (s4field); guider from sparc4_fov.xml",
    rollable: false,
    guide: { label: "Guider setup angle", zero: 180, g: [8, 14] },
    regions: [
      { name: "common area (g r i z)", role: "science", shape: "polygon",
        pts: [[174.5, 165.9], [80.5, 169.6], [-90.0, 173.2], [-166.9, 173.8], [-171.9, -39.9],
              [-172.8, -163.0], [85.9, -173.2], [164.9, -173.9], [173.8, 75.7]] },
      { name: "patrol annulus", role: "patrol", shape: "circle", c: [0, 0], r: 569.68 },
      { name: "patrol annulus", role: "patrol", shape: "circle", c: [0, 0], r: 758.68 },
      { name: "guider", role: "guider", shape: "polygon",
        pts: [[-94.5, -569.68], [94.5, -569.68], [94.5, -758.68], [-94.5, -758.68]] },
    ],
  };

  /* ---- geometry ------------------------------------------------------------ */
  // turn (E, N) by a position angle [deg] east of north: a vector at PA p goes to PA p + pa
  function rot(e, n, pa) {
    const a = pa * D2R_, c = Math.cos(a), s = Math.sin(a);
    return [e * c + n * s, -e * s + n * c];
  }
  const paOf = (e, n) => ((Math.atan2(e, n) / D2R_) % 360 + 360) % 360;

  function centroid(rg) {
    if (rg.shape === "circle") return rg.c;
    let e = 0, n = 0;
    for (const [x, y] of rg.pts) { e += x; n += y; }
    return [e / rg.pts.length, n / rg.pts.length];
  }
  function farthest(rg) {              // largest distance of the region from the centre
    if (rg.shape === "circle") return Math.hypot(rg.c[0], rg.c[1]) + rg.r;
    return Math.max(...rg.pts.map(([x, y]) => Math.hypot(x, y)));
  }
  function nearest(rg) {               // smallest distance of a vertex (or of the circle) from the centre
    if (rg.shape === "circle") return Math.max(0, Math.hypot(rg.c[0], rg.c[1]) - rg.r);
    return Math.min(...rg.pts.map(([x, y]) => Math.hypot(x, y)));
  }
  function inPoly(pts, x, y) {
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i], [xj, yj] = pts[j];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function contains(rg, x, y) {
    if (rg.shape === "circle") return Math.hypot(x - rg.c[0], y - rg.c[1]) <= rg.r;
    const b = rg.bb;
    if (b && (x < b[0] || x > b[2] || y < b[1] || y > b[3])) return false;
    return inPoly(rg.pts, x, y);
  }
  // distance from (x, y) to the edge of a region (for the edge margin)
  function edgeDist(rg, x, y) {
    if (rg.shape === "circle") return Math.abs(rg.r - Math.hypot(x - rg.c[0], y - rg.c[1]));
    let d = Infinity;
    const p = rg.pts;
    for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
      const [xi, yi] = p[i], [xj, yj] = p[j], dx = xj - xi, dy = yj - yi;
      const t = Math.max(0, Math.min(1, ((x - xi) * dx + (y - yi) * dy) / (dx * dx + dy * dy || 1)));
      d = Math.min(d, Math.hypot(x - xi - t * dx, y - yi - t * dy));
    }
    return d;
  }
  function withBox(rg) {
    if (rg.shape === "circle") return { ...rg, bb: [rg.c[0] - rg.r, rg.c[1] - rg.r, rg.c[0] + rg.r, rg.c[1] + rg.r] };
    let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
    for (const [x, y] of rg.pts) { a = Math.min(a, x); b = Math.min(b, y); c = Math.max(c, x); d = Math.max(d, y); }
    return { ...rg, bb: [a, b, c, d] };
  }

  /* ---- what a FoV has: science area, guider, patrol annulus ---------------- */
  function analyse(fov) {
    const sci = fov.regions.filter((r) => r.role === "science");
    const gd = fov.regions.filter((r) => r.role === "guider");
    const pc = fov.regions.filter((r) => r.role === "patrol" && r.shape === "circle");
    const radii = pc.map((r) => r.r).sort((a, b) => a - b);
    const patrol = radii.length ? { rin: radii.length > 1 ? radii[0] : 0, rout: radii[radii.length - 1] } : null;
    // a guider that lies within the patrol annulus turns around the centre (a probe)
    const probe = !!(patrol && gd.length && gd.every((r) => nearest(r) >= patrol.rin * 0.98 - 1
                                                         && farthest(r) <= patrol.rout * 1.02 + 1));
    let probePA0 = null;
    if (gd.length) {
      let e = 0, n = 0;
      for (const r of gd) { const c = centroid(r); e += c[0]; n += c[1]; }
      probePA0 = paOf(e / gd.length, n / gd.length);
    }
    const extent = sci.length ? Math.max(...sci.map(farthest)) : Math.max(1, ...fov.regions.map(farthest));
    const outer = Math.max(extent, ...fov.regions.filter((r) => r.role !== "other").map(farthest));
    return { sci, gd, patrol, probe, probePA0, extent, outer };
  }

  // the regions on the sky for a field at position angle pa; a probe guider at probe PA ppa
  function place(fov, pa, ppa) {
    const A = analyse(fov);
    const turn = (rg, by) => withBox(rg.shape === "circle"
      ? { ...rg, c: rot(rg.c[0], rg.c[1], by) }
      : { ...rg, pts: rg.pts.map(([x, y]) => rot(x, y, by)) });
    const out = { science: [], guider: [], patrol: [], other: [], A };
    for (const rg of fov.regions) {
      let by = pa || 0;
      if (rg.role === "guider" && A.probe && ppa != null) by = ppa - A.probePA0;   // the probe: its own angle
      if (rg.role === "patrol" && rg.shape === "circle" && Math.hypot(rg.c[0], rg.c[1]) < 1) by = 0;
      out[rg.role in out ? rg.role : "other"].push(turn(rg, by));
    }
    // each role's bounding box: most stars are rejected by it at once
    out.box = {};
    for (const k of ROLES) {
      const bs = out[k].map((r) => r.bb);
      out.box[k] = bs.length ? [Math.min(...bs.map((b) => b[0])), Math.min(...bs.map((b) => b[1])),
                                Math.max(...bs.map((b) => b[2])), Math.max(...bs.map((b) => b[3]))] : null;
    }
    return out;
  }

  // the best guide star for a probe guider: stars {x, y, G} [arcsec E, N of the
  // field centre]; the brightest one, in the G range rng [Gmin, Gmax] if given,
  // that the probe can hold at least `margin` inside its box (as s4field: 15″).
  // The probe is turned so that its box's centre lies on the star's position
  // angle. Returns { ppa, star } or null.
  function bestProbe(fov, stars, rng, margin) {
    const A = analyse(fov);
    if (!A.probe || !A.gd.length) return null;
    const gd = A.gd.map(withBox), lo = A.patrol.rin, hi = A.patrol.rout;
    const small = Math.min(...gd.map((r) => Math.min(r.bb[2] - r.bb[0], r.bb[3] - r.bb[1])));
    const m = margin != null ? margin : Math.min(15, small / 4);
    const cand = stars.filter((s) => {
      const r = Math.hypot(s.x, s.y);
      return r >= lo && r <= hi && isFinite(s.G) && (!rng || (s.G >= rng[0] && s.G <= rng[1]));
    }).sort((a, b) => a.G - b.G);
    for (const s of cand) {
      const ppa = Math.round(paOf(s.x, s.y) * 10) / 10;
      const [x, y] = rot(s.x, s.y, -(ppa - A.probePA0));       // the star in the file's frame
      if (gd.some((r) => contains(r, x, y) && edgeDist(r, x, y) >= m)) return { ppa, star: s };
    }
    return null;
  }

  /* ---- Aladin FoV files ---------------------------------------------------- */
  const kids = (node, tag) => [...node.children].filter((c) => c.localName === tag);
  const below = (node, tag) => [...node.getElementsByTagNameNS("*", tag)];
  const unitScale = (u) => {
    u = (u || "arcsec").trim().toLowerCase();
    if (/^(arcsec|asec|"|''|arcseconds?)$/.test(u)) return 1;
    if (/^(arcmin|amin|'|arcminutes?)$/.test(u)) return 60;
    if (/^(deg|degrees?|°)$/.test(u)) return 3600;
    if (/^(mas|milliarcsec)$/.test(u)) return 0.001;
    return NaN;
  };
  const num = (s) => parseFloat(String(s).replace(/[−–]/g, "-"));

  function parse(text, file) {
    const doc = new DOMParser().parseFromString(text.replace(/^﻿/, ""), "application/xml");
    if (doc.getElementsByTagName("parsererror").length) throw new Error("This file is not valid XML.");
    const root = doc.documentElement;
    if (!root || root.localName !== "VOTABLE")
      throw new Error("This is XML, but not a VOTable (an Aladin FoV file starts with <VOTABLE>).");
    const meta = {}, flags = {};
    for (const p of below(root, "PARAM")) {
      const name = (p.getAttribute("name") || p.getAttribute("ID") || "").trim();
      const v = (p.getAttribute("value") || "").trim();
      if (/^(TelescopeName|InstrumentName|InstrumentDescription|Origin)$/i.test(name) && !meta[name.toLowerCase()])
        meta[name.toLowerCase()] = v;
      if (/^(Rollable|Movable)$/i.test(name)) flags[name.toLowerCase()] = /^true$/i.test(v);
    }
    const regions = [], warnings = [];
    const tables = below(root, "TABLE");
    tables.forEach((tb, k) => {
      const res = tb.parentElement && tb.parentElement.localName === "RESOURCE" ? tb.parentElement : null;
      const lone = res && kids(res, "TABLE").length === 1;
      const label = (lone ? (res.getAttribute("name") || res.getAttribute("ID")) : null)
        || tb.getAttribute("name") || tb.getAttribute("ID")
        || (res && (res.getAttribute("name") || res.getAttribute("ID"))) || `shape ${k + 1}`;
      const id = tb.getAttribute("ID") || (lone && res.getAttribute("ID")) || "";
      const params = {};
      for (const p of kids(tb, "PARAM")) {
        const key = (p.getAttribute("name") || p.getAttribute("ID") || "").trim();
        params[key.toLowerCase()] = { v: (p.getAttribute("value") || "").trim(), unit: p.getAttribute("unit"),
                                      utype: (p.getAttribute("utype") || "").toLowerCase() };
      }
      const shapeWord = ((params.shape || params.region || {}).v || "").toLowerCase();
      const pByUtype = (re) => Object.values(params).find((p) => re.test(p.utype));
      const fields = kids(tb, "FIELD");
      const rows = below(tb, "TR");
      const isCircle = shapeWord === "circle" || (!shapeWord && !rows.length && pByUtype(/circle/));
      if (isCircle) {
        const cx = params.cxptposition || pByUtype(/circle\.center\.c1/);
        const cy = params.cyptposition || pByUtype(/circle\.center\.c2/);
        const rr = params.radius || pByUtype(/circle\.radius/);
        const sc = unitScale((rr && rr.unit) || (cx && cx.unit)), sx = unitScale((cx && cx.unit) || (rr && rr.unit));
        const c = [num(cx ? cx.v : 0) * sx, num(cy ? cy.v : 0) * sx], r = num(rr ? rr.v : NaN) * sc;
        if (!(r > 0) || !isFinite(c[0]) || !isFinite(c[1])) { warnings.push(`${label}: a circle without a usable centre or radius, skipped`); return; }
        regions.push({ name: label, id, shape: "circle", c: c.map(r2), r: r2(r) });
        return;
      }
      if (shapeWord && shapeWord !== "polygon") { warnings.push(`${label}: shape "${shapeWord}" is not supported yet, skipped`); return; }
      if (!rows.length) { warnings.push(`${label}: no vertices, skipped`); return; }
      // which columns are x and y: by utype (C1/C2, Position[1]/[2]), by name, else the first two
      const fi = (re) => fields.findIndex((f) => re.test((f.getAttribute("utype") || "") + " " + (f.getAttribute("name") || "")));
      let ix = fi(/(\.C1\b|Position\[1\]|xPtPosition|RAOffset)/i), iy = fi(/(\.C2\b|Position\[2\]|yPtPosition|DecOffset)/i);
      if (ix < 0 || iy < 0) { ix = 0; iy = 1; }
      const ux = unitScale(fields[ix] && fields[ix].getAttribute("unit")), uy = unitScale(fields[iy] && fields[iy].getAttribute("unit"));
      if (!isFinite(ux) || !isFinite(uy)) { warnings.push(`${label}: unknown unit "${fields[ix].getAttribute("unit")}", skipped`); return; }
      const pts = [];
      for (const tr of rows) {
        const td = kids(tr, "TD").map((t) => num(t.textContent));
        if (isFinite(td[ix]) && isFinite(td[iy])) pts.push([r2(td[ix] * ux), r2(td[iy] * uy)]);
      }
      if (pts.length > 2 && pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1]) pts.pop();
      if (pts.length < 3) { warnings.push(`${label}: fewer than three vertices, skipped`); return; }
      regions.push({ name: label, id, shape: "polygon", pts });
    });
    if (!regions.length) throw new Error("No shape found in this file" + (warnings.length ? `: ${warnings[0]}` : "."));
    guessRoles(regions);
    const name = [meta.instrumentname, meta.telescopename].filter(Boolean).join(" · ")
      || (file || "loaded FoV").replace(/\.(xml|vot|json)$/i, "");
    return {
      id: "", name, telescope: meta.telescopename || "", instrument: meta.instrumentname || "",
      description: meta.instrumentdescription || "", origin: meta.origin || "",
      rollable: !!flags.rollable, movable: !!flags.movable, file: file || "",
      regions, warnings, loaded: new Date().toISOString().slice(0, 10),
    };
  }

  // roles from geometry first, names second: a region named "Guider" that holds the
  // centre is the science field (it happens: sparc4_fov.xml)
  function guessRoles(regions) {
    const centred = regions.filter((r) => r.shape === "circle" && Math.hypot(r.c[0], r.c[1]) <= Math.max(1, 0.005 * r.r));
    const radii = [...new Set(centred.map((r) => r.r))].sort((a, b) => a - b);
    const holdsCentre = (r) => (r.shape === "circle" ? Math.hypot(r.c[0], r.c[1]) <= r.r : inPoly(r.pts, 0, 0));
    // a polygon in the annulus: all its corners there, and not around the centre (a
    // wide detector's corners can all lie in a guider annulus, as ROBO43's do)
    const inRing = (r, a, b) => r.shape === "polygon" && !holdsCentre(r) && nearest(r) >= a * 0.98 - 1 && farthest(r) <= b * 1.02 + 1;
    let rin = null, rout = null;
    if (radii.length >= 2) {           // a patrol annulus holds at least one polygon
      for (let i = 0; i < radii.length && rin == null; i++)
        for (let j = radii.length - 1; j > i; j--) {
          const a = radii[i], b = radii[j];
          if (b / a < 1.05) continue;
          const held = regions.filter((r) => inRing(r, a, b));
          if (held.length) { rin = a; rout = b; break; }
        }
    }
    for (const r of regions) {
      if (rin != null && centred.includes(r) && (r.r === rin || r.r === rout)) r.role = "patrol";
      else if (rin != null && inRing(r, rin, rout)) r.role = "guider";
      else if (!holdsCentre(r) && /guid|probe|\bwfs\b|acq|tip.?tilt|\bag\b/i.test(r.name)) r.role = "guider";
      else if (!holdsCentre(r) && /patrol|annul/i.test(r.name)) r.role = "other";
      else r.role = "science";
    }
    if (!regions.some((r) => r.role === "science"))
      for (const r of regions) if (r.role !== "patrol") r.role = "science";
  }

  // regions that look alike, to give roles to all of them at once ("ccd0" ... "ccd103")
  const stem = (s) => String(s).replace(/[\s_\-.#]*\(?\d+\)?$/, "").trim() || String(s);
  // (up to four alike are listed one by one, so each can get its own role)
  function groups(fov) {
    const g = new Map();
    fov.regions.forEach((r, i) => {
      const key = `${stem(r.name)}|${r.shape}|${r.role0 || r.role}`;
      if (!g.has(key)) g.set(key, { key, label: stem(r.name), shape: r.shape, role: r.role, idx: [] });
      g.get(key).idx.push(i);
    });
    const out = [];
    for (const x of g.values()) {
      if (x.idx.length > 4) { out.push(x); continue; }
      for (const i of x.idx) {
        const r = fov.regions[i];
        out.push({ key: `${x.key}|${i}`, label: r.name + (r.id && r.id !== r.name ? ` (${r.id})` : ""), shape: r.shape, role: r.role, idx: [i] });
      }
    }
    return out;
  }

  /* ---- SkyBound JSON (export / import) ------------------------------------- */
  function toJSON(fov) {
    return JSON.stringify({
      skybound_fov: 1, name: fov.name, telescope: fov.telescope, instrument: fov.instrument,
      description: fov.description, origin: fov.origin, file: fov.file, rollable: fov.rollable,
      settings: settingsOf(fov),
      frame: "arcsec East (x), North (y) of the pointing centre at position angle 0",
      regions: fov.regions.map((r) => (r.shape === "circle"
        ? { name: r.name, role: r.role, shape: "circle", c: r.c, r: r.r }
        : { name: r.name, role: r.role, shape: "polygon", pts: r.pts })),
    }, null, 1);
  }
  function fromJSON(text, file) {
    let o;
    try { o = JSON.parse(text); } catch (e) { throw new Error("This JSON file could not be read."); }
    if (!o || !o.skybound_fov || !Array.isArray(o.regions)) throw new Error("This JSON file is not a SkyBound FoV (no \"skybound_fov\" key).");
    const regions = o.regions.filter((r) => (r.shape === "circle" ? r.r > 0 && Array.isArray(r.c) : Array.isArray(r.pts) && r.pts.length > 2))
      .map((r) => ({ name: String(r.name || "shape"), role: ROLES.includes(r.role) ? r.role : "science", shape: r.shape === "circle" ? "circle" : "polygon",
                     ...(r.shape === "circle" ? { c: r.c.map(Number), r: Number(r.r) } : { pts: r.pts.map(([x, y]) => [Number(x), Number(y)]) }) }));
    if (!regions.length) throw new Error("This SkyBound FoV has no usable region.");
    return { id: "", name: String(o.name || file || "loaded FoV"), telescope: o.telescope || "", instrument: o.instrument || "",
             description: o.description || "", origin: o.origin || "", file: file || o.file || "", rollable: !!o.rollable,
             settings: cleanSettings(o.settings), regions, warnings: [], loaded: new Date().toISOString().slice(0, 10) };
  }
  function load(text, file) {
    const t = text.trim();
    return t.startsWith("{") ? fromJSON(t, file) : parse(t, file);
  }

  /* ---- the library (this browser) ------------------------------------------ */
  let LIB = null;
  function lib() {
    if (LIB) return LIB;
    try { LIB = JSON.parse(localStorage.getItem(STORE) || "[]"); } catch (e) { LIB = []; }
    if (!Array.isArray(LIB)) LIB = [];
    return LIB;
  }
  function persist() {
    try { localStorage.setItem(STORE, JSON.stringify(lib())); return true; }
    catch (e) { return false; }      // full (a very large file) or unavailable: kept for this session only
  }
  const list = () => [SPARC4, ...lib()];
  const get = (id) => list().find((f) => f.id === id) || null;
  function add(fov) {
    let base = "user:" + (fov.name || "fov").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""), id = base, k = 2;
    const same = lib().find((f) => f.name === fov.name && toJSON(f) === toJSON(fov));
    if (same) return { fov: same, stored: true, again: true };
    while (get(id)) id = `${base}-${k++}`;
    fov.id = id;
    lib().push(fov);
    return { fov, stored: persist() };
  }
  function remove(id) {
    const i = lib().findIndex((f) => f.id === id);
    if (i >= 0) { lib().splice(i, 1); persist(); }
  }
  function setRole(id, idx, role) {
    const f = get(id);
    if (!f || f.builtin || !ROLES.includes(role)) return;
    for (const i of idx) if (f.regions[i]) { f.regions[i].role0 = f.regions[i].role0 || f.regions[i].role; f.regions[i].role = role; }
    persist();
  }
  function setSettings(id, patch) {
    const f = get(id);
    if (!f || f.builtin) return;
    f.settings = { ...settingsOf(f), ...cleanSettings(patch) };
    persist();
  }

  /* ---- a FoV from numbers (the "New FoV" form) ----------------------------- */
  // a rectangular detector of nx x ny pixels: scale ["/px] given, or from the
  // pixel size [um] and the focal length [mm]; its centre (dx, dy) from the
  // pointing; optionally a fixed guider box, or a probe in a patrol annulus
  function build(sp) {
    const num = (v) => Number(v);
    const scale = num(sp.scale) > 0 ? num(sp.scale) : 206264.806 * num(sp.pix_um) * 1e-3 / num(sp.focal_mm);
    if (!(scale > 0) || !(num(sp.nx) > 0) || !(num(sp.ny) > 0)) throw new Error("Give the pixels across and the plate scale (or the pixel size and the focal length).");
    const w = num(sp.nx) * scale, h = num(sp.ny) * scale, dx = num(sp.dx) || 0, dy = num(sp.dy) || 0;
    const box = (cx, cy, a, b) => [[cx - a / 2, cy - b / 2], [cx + a / 2, cy - b / 2], [cx + a / 2, cy + b / 2], [cx - a / 2, cy + b / 2]].map((p) => p.map(r2));
    const regions = [{ name: sp.instrument || "detector", role: "science", shape: "polygon", pts: box(dx, dy, w, h) }];
    const g = sp.guider || {};
    if (g.kind === "fixed" && num(g.size) > 0)
      regions.push({ name: "guider", role: "guider", shape: "polygon", pts: box(num(g.x) || 0, num(g.y) || 0, num(g.size), num(g.size)) });
    if (g.kind === "probe") {
      const rin = num(g.rin), rout = num(g.rout), size = num(g.size);
      if (!(rout > rin && rin > 0 && size > 0)) throw new Error("A guide probe needs its patrol annulus (inner < outer radius) and its box size.");
      regions.push({ name: "patrol annulus", role: "patrol", shape: "circle", c: [0, 0], r: r2(rin) },
                   { name: "patrol annulus", role: "patrol", shape: "circle", c: [0, 0], r: r2(rout) },
                   { name: "guide probe", role: "guider", shape: "polygon", pts: box(0, -(rin + rout) / 2, Math.min(size, rout - rin), Math.min(size, rout - rin)) });
    }
    const am = (x) => (x / 60).toFixed(1) + "′";
    return {
      id: "", name: sp.name || [sp.instrument, sp.telescope].filter(Boolean).join(" · ") || "new FoV",
      telescope: sp.telescope || "", instrument: sp.instrument || "",
      description: `${num(sp.nx)} × ${num(sp.ny)} px at ${scale.toFixed(4)}″/px: ${am(w)} × ${am(h)}`
        + (num(sp.focal_mm) > 0 && !(num(sp.scale) > 0) ? ` (${num(sp.pix_um)} µm pixels, focal length ${num(sp.focal_mm)} mm)` : ""),
      origin: "SkyBound New FoV form: nominal, check it against a solved image",
      file: "", rollable: true, settings: cleanSettings(sp.settings || {}),
      regions, warnings: [], loaded: new Date().toISOString().slice(0, 10),
    };
  }

  /* ---- mosaics ------------------------------------------------------------- */
  // gnomonic offsets [arcsec E, N] from (ra0, dec0) [deg] -> RA, Dec [deg]
  function fromTangent(e, n, ra0, dec0) {
    const x = e / 206264.806, y = n / 206264.806, d0 = dec0 * D2R_;
    const den = Math.cos(d0) - y * Math.sin(d0);
    const ra = ra0 + Math.atan2(x, den) / D2R_;
    const dec = Math.atan2(Math.sin(d0) + y * Math.cos(d0), Math.hypot(x, den)) / D2R_;
    return [((ra % 360) + 360) % 360, dec];
  }
  // column names: A ... Z, AA, AB ...
  const colName = (i) => (i < 26 ? String.fromCharCode(65 + i) : colName(Math.floor(i / 26) - 1) + String.fromCharCode(65 + (i % 26)));

  // the science area's bounding box at PA 0 [arcsec]: its size and its centre's offset from the pointing
  function scienceBox(fov) {
    let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
    for (const r of fov.regions.filter((q) => q.role === "science")) {
      const pts = r.shape === "circle" ? [[r.c[0] - r.r, r.c[1] - r.r], [r.c[0] + r.r, r.c[1] + r.r]] : r.pts;
      for (const [x, y] of pts) { a = Math.min(a, x); b = Math.min(b, y); c = Math.max(c, x); d = Math.max(d, y); }
    }
    if (!isFinite(a)) return { w: 0, h: 0, cx: 0, cy: 0 };
    return { w: c - a, h: d - b, cx: (a + c) / 2, cy: (b + d) / 2 };
  }

  // A mosaic of cols x rows tiles: the science areas side by side with `ov` [%]
  // overlap, the grid turned by `pa` with the field. Columns A, B, ... run from
  // East to West, rows 1, 2, ... from North to South (as AstroMosaic). Each
  // tile: its pointing [arcsec E, N of the mosaic centre], label and the
  // serpentine order (row 1 east to west, row 2 back, ...).
  function tiles(fov, cols, rows, ov, pa) {
    const B = scienceBox(fov), f = 1 - ov / 100, sx = B.w * f, sy = B.h * f, out = [];
    for (let j = 0; j < rows; j++)
      for (let i = 0; i < cols; i++) {
        const x = ((cols - 1) / 2 - i) * sx, y = ((rows - 1) / 2 - j) * sy;       // the science area's centre
        const [e, n] = rot(x - B.cx, y - B.cy, pa || 0);                           // ... and the pointing
        const order = j * cols + (j % 2 ? cols - 1 - i : i) + 1;
        out.push({ label: colName(i) + (j + 1), col: i, row: j, e, n, order });
      }
    return { tiles: out, box: B, sx, sy, width: (cols - 1) * sx + B.w, height: (rows - 1) * sy + B.h };
  }
  // how many tiles cover a size [arcsec] along one axis
  const tilesFor = (size, tile, step) => (size <= tile || !(step > 0) ? 1 : Math.ceil((size - tile) / step - 1e-9) + 1);

  function selected() {
    let id = null;
    try { id = localStorage.getItem(SEL); } catch (e) { id = null; }
    return get(id) || SPARC4;
  }
  function select(id) { try { localStorage.setItem(SEL, id); } catch (e) { /* nothing to remember */ } }

  return { SPARC4, ROLES, BANDS, rot, paOf, analyse, place, contains, edgeDist, bestProbe, parse, load, toJSON, groups,
           list, get, add, remove, setRole, settingsOf, setSettings, build, selected, select,
           fromTangent, colName, scienceBox, tiles, tilesFor };
})();
