"use strict";
/* SkyBound · sky images from HiPS tiles (Hierarchical Progressive Surveys).
   The survey's static tiles (512 x 512 JPEG/PNG, HEALPix NESTED) are fetched
   straight from the HiPS server and drawn onto a canvas in SkyBound's own
   projection (the target's tangent plane), so they line up with the Gaia
   stars. No server-side cutout (hips2fits) is involved: tiles are plain files,
   cached by the browser and by the server's CDN.

   HEALPix: Gorski et al. 2005; face coordinates as in healpix_base (x, y in
   [0, 1] across a base face, South corner (0, 0), East (1, 0), West (0, 1),
   North (1, 1)). HiPS tiles (Fernique et al. 2015): in the JPEG/PNG image,
   the column runs along y and the row (from the top) along x, so the image's
   top-left corner is the tile's South corner, top-right West, bottom-left
   East, bottom-right North (as the hips package and Aladin draw them).
   By Cleber Silva · UFC/MALTED */

const Hips = (() => {
  const JRLL = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4], JPLL = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7];
  const D2R = Math.PI / 180, HALFPI = Math.PI / 2;

  // a position on a base face (x, y in [0, 1]) -> [ra, dec] in degrees
  function xyf2radec(x, y, face) {
    const jr = JRLL[face] - x - y;
    let nr, z;
    if (jr < 1) { nr = jr; z = 1 - nr * nr / 3; }
    else if (jr > 3) { nr = 4 - jr; z = nr * nr / 3 - 1; }
    else { nr = 1; z = (2 - jr) * 2 / 3; }
    let t = JPLL[face] * nr + x - y;
    if (t < 0) t += 8;
    if (t >= 8) t -= 8;
    const phi = nr < 1e-15 ? 0 : (0.5 * HALFPI * t) / nr;
    return [phi / D2R, Math.asin(Math.max(-1, Math.min(1, z))) / D2R];
  }

  // spread the bits of v (< 2^15) to the even bit positions
  function spread(v) {
    let r = 0;
    for (let b = 0; b < 15; b++) if (v & (1 << b)) r += 2 ** (2 * b);
    return r;
  }
  function compact(v) {               // the inverse: even bits of v -> an integer
    let r = 0;
    for (let b = 0; b < 15; b++) if (Math.floor(v / 2 ** (2 * b)) % 2) r |= 1 << b;
    return r;
  }

  // [ra, dec] deg -> NESTED pixel index at this order (nside = 2^order, order <= 14)
  function radec2pix(order, ra, dec) {
    const nside = 2 ** order, z = Math.sin(dec * D2R), za = Math.abs(z);
    let tt = ((ra % 360) + 360) % 360 / 90;
    if (tt >= 4) tt -= 4;
    let face, ix, iy;
    if (za <= 2 / 3) {
      const t1 = nside * (0.5 + tt), t2 = nside * z * 0.75;
      const jp = Math.floor(t1 - t2), jm = Math.floor(t1 + t2);
      const ifp = Math.floor(jp / nside), ifm = Math.floor(jm / nside);
      face = ifp === ifm ? (ifp | 4) : ifp < ifm ? ifp : ifm + 8;
      ix = ((jm % nside) + nside) % nside;
      iy = nside - (((jp % nside) + nside) % nside) - 1;
    } else {
      const ntt = Math.min(3, Math.floor(tt)), tp = tt - ntt;
      const tmp = nside * Math.sqrt(3 * (1 - za));
      const jp = Math.min(nside - 1, Math.floor(tp * tmp)), jm = Math.min(nside - 1, Math.floor((1 - tp) * tmp));
      if (z >= 0) { face = ntt; ix = nside - jm - 1; iy = nside - jp - 1; }
      else { face = ntt + 8; ix = jp; iy = jm; }
    }
    return face * nside * nside + spread(ix) + 2 * spread(iy);
  }
  // a NESTED pixel -> { face, ix, iy } at its order
  function pix2xyf(order, ipix) {
    const npf = 4 ** order, face = Math.floor(ipix / npf), p = ipix - face * npf;
    return { face, ix: compact(p), iy: compact(Math.floor(p / 2)) };
  }

  /* ---- survey properties (CDS MocServer), kept in this browser ------------- */
  const STORE = "skybound.hips.v2", MAXAGE = 30 * 86400e3;
  const MOCSERVERS = ["https://alasky.cds.unistra.fr/MocServer/query", "https://alaskybis.cds.unistra.fr/MocServer/query"];
  function cacheGet(id) {
    try {
      const all = JSON.parse(localStorage.getItem(STORE) || "{}"), e = all[id];
      return e && Date.now() - e.t < MAXAGE ? e.p : null;
    } catch (e) { return null; }
  }
  function cachePut(id, p) {
    try {
      const all = JSON.parse(localStorage.getItem(STORE) || "{}");
      all[id] = { t: Date.now(), p };
      localStorage.setItem(STORE, JSON.stringify(all));
    } catch (e) { /* kept for this visit */ }
  }
  // where CDS keeps some surveys: their own properties file (next to the tiles)
  // gives the order and the tile format, with no MocServer query
  const KNOWN = {
    "CDS/P/DSS2/red": "https://alasky.cds.unistra.fr/DSS/DSS2Merged",
    "CDS/P/DSS2/blue": "https://alasky.cds.unistra.fr/DSS/DSS2-blue-XJ-S",
    "CDS/P/DSS2/color": "https://alasky.cds.unistra.fr/DSS/DSSColor",
    "CDS/P/2MASS/color": "https://alasky.cds.unistra.fr/2MASS/Color",
    "CDS/P/allWISE/color": "https://alasky.cds.unistra.fr/AllWISE/RGB-W4-W2-W1",
    "CDS/P/PanSTARRS/DR1/color-z-zg-g": "https://alasky.cds.unistra.fr/Pan-STARRS/DR1/color-z-zg-g",
    "CDS/P/Skymapper/DR4/color": "https://alasky.cds.unistra.fr/Skymapper/DR4/CDS_P_Skymapper_DR4_color",
  };
  // CDS's tile servers under their current names, CDS first, each once; CDS's
  // own servers get their mirror (alaskybis) as the second choice
  function serverList(list) {
    const out = [];
    for (let u of list) {
      u = String(u).trim().replace(/\/+$/, "").replace(/^http:/, "https:")
        .replace("//alasky.u-strasbg.fr/", "//alasky.cds.unistra.fr/").replace("//alaskybis.u-strasbg.fr/", "//alaskybis.cds.unistra.fr/");
      if (!/^https:\/\//.test(u)) continue;
      if (!out.includes(u)) out.push(u);
      const bis = u.replace("//alasky.cds.unistra.fr/", "//alaskybis.cds.unistra.fr/");
      if (bis !== u && !out.includes(bis)) out.push(bis);
    }
    const cds = (u) => /\/\/alasky(bis)?\.cds\.unistra\.fr\//.test(u) ? 0 : 1;
    return out.sort((a, b) => cds(a) - cds(b));
  }
  const parseProps = (txt) => {
    const rec = {};
    for (const line of txt.split(/\r?\n/)) {
      const m = line.match(/^\s*([\w.]+)\s*=\s*(.*?)\s*$/);
      if (m && !(m[1] in rec)) rec[m[1]] = m[2];
    }
    return rec;
  };
  async function getJSON(url, ms, text) {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), ms);
    try {
      const r = await fetch(url, { signal: ctl.signal });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return text ? await r.text() : await r.json();
    } finally { clearTimeout(timer); }
  }
  const mem = new Map();
  // { urls: [base, mirrors...], order, ext, width, frame }
  async function survey(id) {
    if (mem.has(id)) return mem.get(id);
    const hit = cacheGet(id);
    if (hit) { mem.set(id, hit); return hit; }
    const job = (async () => {
      let rec = null, err = null;
      if (KNOWN[id]) {                             // the survey's own properties file
        for (const base of serverList([KNOWN[id]])) {
          try {
            rec = { ...parseProps(await getJSON(`${base}/properties`, 10000, true)), hips_service_url: KNOWN[id] };
            break;
          } catch (e) { err = e; }
        }
      }
      for (const ms of rec ? [] : MOCSERVERS) {
        try {
          const js = await getJSON(`${ms}?ID=${encodeURIComponent(id)}&get=record&fmt=json`, 12000);
          rec = Array.isArray(js) ? js[0] : js;
          if (rec) break;
        } catch (e) { err = e; }
      }
      if (!rec) throw new Error(`no properties for ${id}${err ? ` (${err.message})` : ""}`);
      const one = (v) => (Array.isArray(v) ? v[0] : v);
      const urls = serverList([...(KNOWN[id] ? [KNOWN[id]] : []),
        ...Object.keys(rec).filter((k) => /^hips_service_url(_\d+)?$/.test(k)).sort().map((k) => one(rec[k]))]);
      const fmt = String(one(rec.hips_tile_format) || "jpeg").toLowerCase();
      const p = { urls, order: parseInt(one(rec.hips_order), 10) || 9, width: parseInt(one(rec.hips_tile_width), 10) || 512,
                  ext: /jpe?g/.test(fmt) ? "jpg" : /png/.test(fmt) ? "png" : "jpg", frame: String(one(rec.hips_frame) || "equatorial") };
      if (!p.urls.length) throw new Error(`${id} has no tile server`);
      cachePut(id, p);
      return p;
    })();
    mem.set(id, job);
    job.catch(() => mem.delete(id));
    return job;
  }
  // for tests: properties given by hand
  const setSurvey = (id, p) => mem.set(id, p);

  /* ---- drawing ------------------------------------------------------------- */
  const tileUrl = (base, order, ipix, ext) => `${base}/Norder${order}/Dir${Math.floor(ipix / 10000) * 10000}/Npix${ipix}.${ext}`;
  const imgCache = new Map();        // url -> Promise<HTMLImageElement>, the most recent ~300
  function loadImg(urls, timeout) {
    const key = urls[0];
    if (imgCache.has(key)) return imgCache.get(key);
    const job = new Promise((resolve, reject) => {
      let k = 0, timer = null;
      const next = () => {
        if (k >= urls.length) { reject(new Error("tile")); return; }
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.decoding = "async";
        clearTimeout(timer);
        timer = setTimeout(() => { img.src = ""; k++; next(); }, timeout);
        img.onload = () => { clearTimeout(timer); resolve(img); };
        img.onerror = () => { clearTimeout(timer); k++; next(); };
        img.src = urls[k];
      };
      next();
    });
    imgCache.set(key, job);
    job.catch(() => imgCache.delete(key));
    if (imgCache.size > 300) imgCache.delete(imgCache.keys().next().value);
    return job;
  }

  // one triangle of a tile image (image points s, canvas points d), by an affine map
  function triangle(ctx, img, s, d) {
    const [[u0, v0], [u1, v1], [u2, v2]] = s, [[x0, y0], [x1, y1], [x2, y2]] = d;
    const den = (u1 - u0) * (v2 - v0) - (u2 - u0) * (v1 - v0);
    if (!den) return;
    const a = ((x1 - x0) * (v2 - v0) - (x2 - x0) * (v1 - v0)) / den;
    const b = ((y1 - y0) * (v2 - v0) - (y2 - y0) * (v1 - v0)) / den;
    const c = ((x2 - x0) * (u1 - u0) - (x1 - x0) * (u2 - u0)) / den;
    const e = ((y2 - y0) * (u1 - u0) - (y1 - y0) * (u2 - u0)) / den;
    const f = x0 - a * u0 - c * v0, g = y0 - b * u0 - e * v0;
    // clip slightly outside the triangle: no hairline seams between triangles
    const cx = (x0 + x1 + x2) / 3, cy = (y0 + y1 + y2) / 3, grow = (x, y) => {
      const dx = x - cx, dy = y - cy, l = Math.hypot(dx, dy) || 1;
      return [x + dx / l * 0.7, y + dy / l * 0.7];
    };
    const p0 = grow(x0, y0), p1 = grow(x1, y1), p2 = grow(x2, y2);
    ctx.save();
    ctx.beginPath(); ctx.moveTo(p0[0], p0[1]); ctx.lineTo(p1[0], p1[1]); ctx.lineTo(p2[0], p2[1]); ctx.closePath();
    ctx.clip();
    ctx.transform(a, b, c, e, f, g);
    ctx.drawImage(img, 0, 0);
    ctx.restore();
  }

  // Draw survey `id` on a P x P canvas. toRaDec(u, v) and toPx(ra, dec) map canvas
  // pixels to the sky and back; scale: arcsec per canvas pixel. onUpdate(canvas,
  // done) is called as tiles arrive (throttled) and at the end. Returns
  // { cancel } at once; drawing goes on asynchronously.
  function draw(id, P, scale, toRaDec, toPx, onUpdate, onFail) {
    let stop = false;
    (async () => {
      let sv;
      try { sv = await survey(id); } catch (e) { if (!stop) onFail(e.message); return; }
      if (stop) return;
      if (!/equatorial|icrs/i.test(sv.frame)) { onFail(`${id} is not in equatorial coordinates`); return; }
      // the order whose tile pixels are about the canvas pixel size
      const S = sv.width, want = Math.log2(Math.sqrt(Math.PI / 3) * 206264.806 / (S * scale));
      const order = Math.max(3, Math.min(sv.order, Math.ceil(want - 0.25)));
      // the tiles under the canvas: a fine grid of samples
      const tiles = new Set(), n = 96;
      for (let i = 0; i <= n; i++) for (let j = 0; j <= n; j++) {
        const [ra, dec] = toRaDec(i * P / n, j * P / n);
        tiles.add(radec2pix(order, ra, dec));
      }
      const cv = document.createElement("canvas");
      cv.width = cv.height = P;
      const ctx = cv.getContext("2d");
      // transparent where no tile has arrived (yet): SkyBound's Gaia stars show through
      ctx.imageSmoothingEnabled = true;
      const nside = 2 ** order;
      let drawn = 0, failed = 0, pending = tiles.size, timer = null, last = 0;
      const update = (done) => {
        if (stop) return;
        clearTimeout(timer);
        const now = Date.now();
        if (!done && now - last < 400) { timer = setTimeout(() => update(false), 400 - (now - last)); return; }
        last = now;
        if (!drawn) { if (done) diagnose(); return; }
        onUpdate(cv, done);
      };
      const first = tileUrl(sv.urls[0], order, [...tiles][0], sv.ext);
      // nothing could be drawn: ask the first tile directly, to say why
      const diagnose = async () => {
        let why = "tile server unreachable or blocked";
        try {
          const r = await getJSON(first, 10000, true).then(() => ({ ok: true }), (e) => ({ ok: false, msg: e.message }));
          if (r.ok) why = "tiles arrive but cannot be drawn here";
          else if (/HTTP 404/.test(r.msg)) why = "no tiles here: outside the survey's sky coverage";
          else if (/HTTP/.test(r.msg)) why = `tile server answered ${r.msg}`;
        } catch (e) { /* keep the first guess */ }
        if (typeof console !== "undefined") console.warn(`SkyBound: ${id}: ${why} (${first})`);
        if (!stop) onFail(why, first);
      };
      for (const ipix of tiles) {
        const urls = sv.urls.map((b) => tileUrl(b, order, ipix, sv.ext));
        loadImg(urls, 8000).then((img) => {
          if (stop) return;
          const { face, ix, iy } = pix2xyf(order, ipix);
          // corners of a grid of cells over the tile, on the canvas
          const sz = img.naturalWidth || S;
          const c0 = toPx(...xyf2radec((ix + 0.5) / nside, (iy + 0.5) / nside, face));
          const c1 = toPx(...xyf2radec((ix + 1) / nside, (iy + 1) / nside, face));
          const across = Math.hypot(c1[0] - c0[0], c1[1] - c0[1]) * 2.83;   // the tile's size on the canvas
          const M = Math.max(2, Math.min(16, Math.ceil(across / 48)));
          const pt = [], im = [];
          for (let a = 0; a <= M; a++) {
            pt.push([]); im.push([]);
            for (let b = 0; b <= M; b++) {
              pt[a].push(toPx(...xyf2radec((ix + a / M) / nside, (iy + b / M) / nside, face)));
              im[a].push([b / M * sz, a / M * sz]);        // column along y, row along x
            }
          }
          for (let a = 0; a < M; a++) for (let b = 0; b < M; b++) {
            triangle(ctx, img, [im[a][b], im[a + 1][b], im[a][b + 1]], [pt[a][b], pt[a + 1][b], pt[a][b + 1]]);
            triangle(ctx, img, [im[a + 1][b + 1], im[a][b + 1], im[a + 1][b]], [pt[a + 1][b + 1], pt[a][b + 1], pt[a + 1][b]]);
          }
          drawn++;
        }).catch(() => { failed++; }).finally(() => {
          pending--;
          update(pending === 0);
        });
      }
    })();
    return { cancel: () => { stop = true; } };
  }

  return { xyf2radec, radec2pix, pix2xyf, survey, setSurvey, draw, tileUrl };
})();
