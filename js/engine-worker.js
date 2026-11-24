"use strict";
/* SkyBound · the in-browser comparison-star engine (a Web Worker).

   Runs s4field.py itself, unchanged, in Python compiled to WebAssembly
   (Pyodide), so a page with no helper on the computer gets the same answers.
   The page sends requests shaped like the helper's HTTP API (GET path + query,
   POST path + body) and gets back (status, JSON) as the helper would answer.

   - Python and numpy come from Pyodide's CDN (indexURL), cached by the browser.
   - s4field.py comes from this site (s4field/s4field.py next to index.html).
   - Network: s4field's urllib calls go through synchronous XMLHttpRequest (allowed
     in a worker), so its ESA / GAVO / Sesame code runs as it is; the archives must
     allow web pages to read them (CORS).
   - The Gaia star lists (s4field's cones/) live in the browser (IndexedDB), so a
     target used once works offline later.
   By Cleber Silva · UFC/MALTED */

const post = (m) => self.postMessage(m);
let py = null;
let chain = Promise.resolve();          // one request at a time, in order

const GLUE = String.raw`
import sys, json, io, urllib.error, urllib.parse
sys.path.insert(0, "/home/pyodide")
import s4field as F
import js
from js import XMLHttpRequest


class _Answer:
    """What urllib's urlopen gives back, as far as s4field uses it."""
    def __init__(self, url, data, status):
        self._url, self._data, self.status = url, data, status

    def read(self):
        return self._data

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_HTTPS_PAGE = str(js.location.protocol) == "https:"


def _xhr(req, timeout):
    url, data = req.full_url, req.data
    if _HTTPS_PAGE and url.startswith("http://"):   # an https page may not ask http (mixed content)
        url = "https://" + url[7:]
    host = urllib.parse.urlsplit(url).netloc
    x = XMLHttpRequest.new()
    x.open("POST" if data is not None else "GET", url, False)
    if data is not None:
        x.setRequestHeader("Content-Type", "application/x-www-form-urlencoded")
    try:
        x.timeout = int(timeout * 1000)
    except Exception:
        pass
    try:
        x.send(data.decode("utf-8") if data is not None else None)
    except Exception as e:
        raise urllib.error.URLError(f"{host} did not answer this web page (offline, or it does not allow "
                                    f"web pages to read it: CORS)") from None
    if x.status == 0:
        raise urllib.error.URLError(f"{host} did not answer this web page (offline, or CORS)")
    body = str(x.responseText).encode("utf-8")
    if x.status >= 400:
        raise urllib.error.HTTPError(url, x.status, str(x.statusText), None, io.BytesIO(body))
    return _Answer(str(x.responseURL) or url, body, x.status)


F._urlopen = _xhr
api = F.Api("/cones", "auto", background="queue")


def handle(method, path, query, body):
    q = json.loads(query) if query else {}
    if method == "POST":
        code, obj = api.post(path, body.encode("utf-8"))
    else:
        code, obj = api.get(path, {str(k): str(v) for k, v in q.items()})
    return json.dumps([code, obj])


def background():
    return api.run_queue()


def status():
    return json.dumps(api.status())
`;

function syncfs(populate) {
  return new Promise((res, rej) => py.FS.syncfs(populate, (err) => (err ? rej(err) : res())));
}

async function boot(m) {
  post({ type: "progress", step: 1, text: "downloading Python" });
  importScripts(m.indexURL + "pyodide.js");
  // eslint-disable-next-line no-undef
  py = await loadPyodide({ indexURL: m.indexURL, stdout: () => {}, stderr: (t) => post({ type: "log", text: t }) });
  post({ type: "progress", step: 2, text: "downloading numpy" });
  await py.loadPackage("numpy", { messageCallback: () => {}, errorCallback: (t) => post({ type: "log", text: t }) });
  post({ type: "progress", step: 3, text: "loading s4field" });
  const r = await fetch(m.site + "s4field/s4field.py", { cache: "no-cache" });
  if (!r.ok) throw new Error(`s4field/s4field.py: HTTP ${r.status}`);
  py.FS.mkdirTree("/home/pyodide");
  py.FS.writeFile("/home/pyodide/s4field.py", await r.text());
  py.FS.mkdirTree("/cones");
  try {                                   // the star lists kept in this browser
    py.FS.mount(py.FS.filesystems.IDBFS, {}, "/cones");
    await syncfs(true);
  } catch (e) { post({ type: "log", text: `no browser storage for star lists (${e}); kept for this visit only` }); }
  py.runPython(GLUE);
  return JSON.parse(py.globals.get("status")());
}

async function request(m) {
  const out = JSON.parse(py.globals.get("handle")(m.method, m.path, JSON.stringify(m.query || {}), m.body || ""));
  post({ type: "response", id: m.id, status: out[0], body: out[1] });
  await syncfs(false).catch(() => {});
  post({ type: "status", status: JSON.parse(py.globals.get("status")()) });
  // the downloads s4field leaves for later (full cone after a reduced one, a larger
  // cone for a loaded FoV): now, after the answer, then tell the page
  try {
    const n = py.globals.get("background")();
    if (n) {
      await syncfs(false).catch(() => {});
      post({ type: "updated", status: JSON.parse(py.globals.get("status")()) });
    }
  } catch (e) { post({ type: "log", text: `background download: ${e}` }); }
}

let booted = null;
self.onmessage = (ev) => {
  const m = ev.data || {};
  if (m.type === "boot") {
    if (!booted) {
      booted = boot(m).then((status) => { post({ type: "ready", status }); return true; },
                            (e) => { post({ type: "failed", error: String((e && e.message) || e) }); return false; });
    }
    return;
  }
  if (m.type === "request") {
    chain = chain.then(async () => {
      if (!(await booted)) { post({ type: "response", id: m.id, status: 503, body: { error: "the engine did not start" } }); return; }
      try { await request(m); }
      catch (e) { post({ type: "response", id: m.id, status: 500, body: { error: `engine: ${String((e && e.message) || e).slice(0, 400)}` } }); }
    });
  }
};
