#!/usr/bin/env python3
"""Build SkyBound's offline name index (js/targets.js) from reference tables.

SkyBound looks a target name up with the s4field helper, else online from the
browser, else in this index. The index comes from the same catalogue CSVs that
toi_transits.py keeps in its reference_tables/ folder:

    python3 tools/build_targets.py ~/path/to/reference_tables
    python3 tools/build_targets.py a.csv b.csv -o js/targets.js

Recognised tables (by their columns; anything else is skipped and reported):
    ExoFOP TOI list                 TOI, TIC ID, RA, Dec (sexagesimal or degrees)
    NASA Exoplanet Archive PS/PSCompPars  pl_name, hostname, ra, dec (+ hd_name, hip_name, tic_id, gaia ids)
    NASA TOI list                   toi, tid, ra, dec
    exoplanet.eu                    name, star_name, ra, dec (+ alternate names)

When the same name is in several tables the first source in that order wins,
as in toi_transits.py (ExoFOP > NASA composite > TOI > exoplanet.eu). Planets
are folded into their host star: SkyBound strips " b" / ".01" before looking up.
Standard library only; no network. By Cleber Silva · UFC/MALTED
"""
import argparse
import csv
import datetime as dt
import json
import math
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "..", "js", "targets.js")

PLANET = re.compile(r"^(.*\S)\s+[A-C]?[b-k]$")       # "AU Mic b", "CoRoT-2 Ab" (same rule as js/lookup.js)


def key(name):
    """Lookup key, identical to js/lookup.js: lower case, letters and digits only."""
    s = unicodedata.normalize("NFKD", str(name).lower())
    return re.sub(r"[^a-z0-9]", "", s)


def host_of(planet):
    m = PLANET.match(planet.strip())
    return m.group(1) if m else planet.strip()


def num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def angle(v, hours):
    """Decimal degrees from '311.29' or '20:45:09.53' / '20 45 09.53' (hours if `hours`)."""
    v = str(v or "").strip()
    if not v:
        return None
    if re.fullmatch(r"[+-]?\d+(\.\d*)?", v):
        return float(v)
    parts = re.split(r"[\s:hmsd°'\"]+", v.lstrip("+-"))
    parts = [p for p in parts if p]
    try:
        a = float(parts[0]) + (float(parts[1]) if len(parts) > 1 else 0) / 60 + (float(parts[2]) if len(parts) > 2 else 0) / 3600
    except (ValueError, IndexError):
        return None
    a = -a if v.startswith("-") else a
    return a * 15 if hours else a


def read_csv(path):
    """Header (lower-cased) and rows, skipping NASA's '#' comment lines."""
    csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        lines = (ln for ln in f if not ln.startswith("#") and ln.strip())
        rd = csv.reader(lines)
        try:
            head = [h.strip().lower() for h in next(rd)]
        except StopIteration:
            return [], []
        return head, [dict(zip(head, r)) for r in rd]


# ---- one reader per table type: yields (host name, [aliases], ra_deg, dec_deg) --------
def rows_exofop(rows):
    for r in rows:
        toi, tic = num(r.get("toi")), str(r.get("tic id", "")).strip()
        ra, dec = angle(r.get("ra"), hours=True), angle(r.get("dec"), hours=False)   # decimal = degrees
        if toi is None or ra is None or dec is None:
            continue
        host = f"TOI-{int(toi)}"
        yield host, [host] + ([f"TIC {tic}"] if tic else []), ra, dec


def rows_nasa_ps(rows):
    for r in rows:
        host, ra, dec = str(r.get("hostname", "")).strip(), num(r.get("ra")), num(r.get("dec"))
        if not host or ra is None or dec is None:
            continue
        al = [host]
        for c in ("hd_name", "hip_name", "tic_id", "gaia_id", "gaia_dr2_id", "gaia_dr3_id"):
            v = str(r.get(c, "") or "").strip()
            if v:
                al.append(v)
        pl = str(r.get("pl_name", "")).strip()
        if pl and key(host_of(pl)) != key(host):
            al.append(pl)
        yield host, al, ra, dec


def rows_nasa_toi(rows):
    for r in rows:
        toi, tid, ra, dec = num(r.get("toi")), num(r.get("tid")), num(r.get("ra")), num(r.get("dec"))
        if toi is None or ra is None or dec is None:
            continue
        host = f"TOI-{int(toi)}"
        yield host, [host] + ([f"TIC {int(tid)}"] if tid is not None else []), ra, dec


def rows_eu(rows):
    for r in rows:
        star, ra, dec = str(r.get("star_name", "")).strip(), num(r.get("ra")), num(r.get("dec"))
        planet = str(r.get("name", "")).strip()
        host = star or host_of(planet)
        if not host or ra is None or dec is None:
            continue
        al = [host] + [a.strip() for a in str(r.get("star_alternate_names", "") or "").split(",") if a.strip()]
        for p in [planet] + str(r.get("alternate_names", "") or "").split(","):
            if p.strip():
                al.append(host_of(p))
        yield host, al, ra, dec


# (label, required columns, reader), in priority order
TYPES = [
    ("ExoFOP TOI list", {"toi", "tic id", "ra", "dec"}, rows_exofop),
    ("NASA Exoplanet Archive", {"pl_name", "hostname", "ra", "dec"}, rows_nasa_ps),
    ("NASA TOI list", {"toi", "tid", "ra", "dec"}, rows_nasa_toi),
    ("exoplanet.eu", {"name", "star_name", "ra", "dec"}, rows_eu),
]


def classify(head):
    cols = set(head)
    for i, (label, need, reader) in enumerate(TYPES):
        if need <= cols:
            return i, label, reader
    return None


def build(paths, out, quiet=False):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(".csv"))
        else:
            files.append(p)
    say = (lambda *a: None) if quiet else print

    # read every table into per-source host groups
    groups = []                                    # (priority, label, file, {host key: [host, aliases, ra, dec]})
    for path in files:
        head, rows = read_csv(path)
        kind = classify(head)
        if not kind:
            say(f"  skipped  {os.path.basename(path)}: not a table type this script knows")
            continue
        prio, label, reader = kind
        hosts = {}
        for host, al, ra, dec in reader(rows):
            g = hosts.setdefault(key(host), [host, [], ra % 360, dec])
            g[1] += al
        groups.append((prio, label, os.path.basename(path), hosts))
        say(f"  {label:<24} {os.path.basename(path)}: {len(rows)} rows, {len(hosts)} stars")
    if not groups:
        raise SystemExit("No usable table found: nothing written.")

    # first source to claim a name keeps it
    groups.sort(key=lambda g: g[0])
    labels, taken, out_rows = [], set(), []
    for prio, label, fname, hosts in groups:
        if label not in labels:
            labels.append(label)
        si = labels.index(label)
        for host, al, ra, dec in hosts.values():
            mine, seen = [], set()
            for a in [host] + al:
                k = key(a)
                if k and k not in taken and k not in seen:
                    seen.add(k)
                    mine.append(a)
            if mine:
                taken.update(seen)
                out_rows.append([round(ra, 5), round(dec, 5), si, "|".join(mine)])

    payload = {"built": dt.date.today().isoformat(), "sources": labels,
               "files": [g[2] for g in groups], "rows": out_rows}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("/* SkyBound offline name index: generated by tools/build_targets.py, do not edit.\n"
                f"   {payload['built']} from {', '.join(payload['files'])} */\n")
        f.write("window.SB_TARGETS = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    say(f"wrote {out}: {len(out_rows)} stars, {len(taken)} names, {os.path.getsize(out) / 1024:.0f} KB")
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", help="reference_tables folder(s) and/or .csv files")
    ap.add_argument("-o", "--out", default=DEFAULT_OUT, help="output file (default: js/targets.js)")
    a = ap.parse_args(argv)
    build(a.paths, a.out)


if __name__ == "__main__":
    main()
