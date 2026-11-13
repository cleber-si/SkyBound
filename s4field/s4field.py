#!/usr/bin/env python3
"""
s4field.py -- SPARC4 comparison-star field finder (OPD 1.6 m).

Given a Gaia DR3 cone around a target, find the telescope pointing that gives
the best differential photometry in g, r, i, z, and report:

    * the field centre and the TCSPD "Precise offset" from the target
      (RA.cos(dec), Dec, in arcsec), plus where the target must land on the
      detector so the sign convention can be checked on the first try;
    * the comparison stars, per band, with the reason any near-match failed;
    * a guide star and the guider angle to type into Guider Setup.

Give just the target -- a name, coordinates or a Gaia DR3 source_id. The
script resolves it (Sesame, CfA mirror first, or the NASA Exoplanet
Archive), downloads the Gaia DR3 cone from the ESA Gaia Archive (or, if
that is down, GAVO's reduced copy: --source), and keeps both in ./cones/, so
the next run for that target needs no network at all. --gaia uses a CSV you
downloaded yourself instead; --offline forbids any network access. --serve
runs the same pipeline as a local HTTP helper for SkyBound. All plotting functions return matplotlib Figure objects and never
show or save them. Documentation: S4FIELD.md.

Numbers and where they come from
--------------------------------
Footprint      measured 4-channel WCS, pipeline stacks of 2026-08-01 (TOI-2631).
Guider         annulus/box from sparc4_fov.xml; PA_box = 180 deg + angle,
               confirmed with Gaia DR3 6560736409211951616 on 2026-08-01.
Noise model    sparc4.py / official ETC spreadsheet (F_CALIB, extinction,
               sky, read noise, gain), with the flux scale measured on
               2026-08-01 (EMPIRICAL_FLUX_SCALE, see below).
Peak pixel     Gaussian PSF; checked against fitted stars to within 7 %.
Scintillation  Young (1967) with the x1.5 correction of Osborn et al. (2015).

By Cleber Silva · UFC/MALTED
"""

from __future__ import annotations

__version__ = "0.13.0"

import argparse
import math
import os
import re as _re
import time as _time
import sys
from dataclasses import dataclass, field

import numpy as np

# ============================================================================
# GEOMETRY -- measured
# ============================================================================

BANDS = ("g", "r", "i", "z")

# Corner offsets (arcsec East, arcsec North) of each channel's read-out area,
# relative to the mean of the four channel centres. From the full TAN-SIP
# pipeline WCS of the 2026-08-01 TOI-2631 stacks (photometric mode).
# Corner order = pixel (lo,lo), (hi,lo), (hi,hi), (lo,hi).
FOOTPRINT = {
    "LARGE": {
        "g": [(168.34, -176.42), (-174.91, -169.24), (-166.83, 174.79), (176.42, 167.60)],
        "r": [(168.12, -177.51), (-176.88, -169.64), (-168.73, 175.99), (176.26, 168.12)],
        "i": [(164.78, -176.35), (-179.73, -162.68), (-167.42, 179.44), (177.09, 165.78)],
        "z": [(171.93, -173.95), (-172.88, -171.03), (-170.23, 173.86), (174.58, 170.94)],
    },
    "MEDIUM": {
        "g": [(84.55, -88.62), (-87.08, -85.03), (-83.04, 86.99), (88.59, 83.39)],
        "r": [(83.91, -89.13), (-88.59, -85.20), (-84.52, 87.62), (87.98, 83.68)],
        "i": [(81.73, -87.40), (-90.53, -80.57), (-84.37, 90.49), (87.88, 83.66)],
        "z": [(86.39, -86.99), (-86.01, -85.53), (-84.69, 86.91), (87.71, 85.45)],
    },
    "SMALL": {
        "g": [(42.65, -44.72), (-43.16, -42.92), (-41.14, 43.09), (44.67, 41.29)],
        "r": [(41.80, -44.95), (-44.45, -42.98), (-42.41, 43.43), (43.84, 41.46)],
        "i": [(40.20, -42.93), (-45.92, -39.51), (-42.85, 46.02), (43.28, 42.60)],
        "z": [(43.62, -43.52), (-42.58, -42.79), (-41.92, 43.43), (44.28, 42.70)],
    },
}
# 0-based pixel coordinates of the same corners (needed to predict where the
# target lands on the detector).
CORNER_PIX = {
    "LARGE": [(-0.5, -0.5), (1023.5, -0.5), (1023.5, 1023.5), (-0.5, 1023.5)],
    "MEDIUM": [(255.5, 255.5), (767.5, 255.5), (767.5, 767.5), (255.5, 767.5)],
    "SMALL": [(383.5, 383.5), (639.5, 383.5), (639.5, 639.5), (383.5, 639.5)],
}
# Per-channel plate scale from the same WCS ["/px] -- used for the peak pixel.
PLATE_SCALE = {"g": 0.3357, "r": 0.3373, "i": 0.3355, "z": 0.3368}

# Polarimetric mode: second (Savart) image of every star, measured on
# 2026-07-13: 14.0" towards PA 29 deg.
# Measured 2026-06-04 (AU Mic, L4): the pipeline WCS puts the "S" image on the
# Gaia position; the "N" image is 13.8" away towards PA 208.4 deg (14 stars).
# (2026-07-13, L2: 14.0" between the images, same axis.)
POL_SPLIT_ARCSEC, POL_SPLIT_PA = 13.8, 208.4

# Readout smear. The CCDs shift charge row by row with the shutter open
# (header VSHIFT = 4.33 us/row), so every star leaves a faint trail along the
# shift direction across the whole frame. Measured 2026-06-04 on AU Mic (i):
# trail = star flux x 0.004-0.005 ms per row, i.e. VSHIFT. Trail direction in
# the reduced frames: r along columns (N-S), i and z along rows (E-W);
# g not yet measured -> both directions, to be safe.
T_VSHIFT = 4.33e-6
SMEAR_AXIS = {"g": "both", "r": "col", "i": "row", "z": "row"}

# Guider (sparc4_fov.xml). Box centre at the mid-radius of the patrol annulus.
GUIDER_R_IN, GUIDER_R_OUT = 569.68, 758.68
GUIDER_R_MID = 0.5 * (GUIDER_R_IN + GUIDER_R_OUT)
GUIDER_BOX = 189.0

# ============================================================================
# DETECTOR + ETC -- sparc4.py / official ETC spreadsheet / Operation Modes
# ============================================================================

ACQ = {  # name -> (rate MHz, preamp, saturation ADU)
    "SLOW BRIGHT": (0.1, 1, 30000), "SLOW FAINT": (0.1, 2, 60000),
    "NORMAL BRIGHT": (1.0, 1, 30000), "NORMAL FAINT": (1.0, 2, 60000),
}
RN_GAIN = {  # (rate, preamp) -> band -> (read noise e-, gain e-/ADU)
    (1.0, 1): {"g": (6.66, 3.37), "r": (6.57, 3.30), "i": (6.67, 3.37), "z": (6.55, 3.36)},
    (1.0, 2): {"g": (4.82, 0.80), "r": (4.84, 0.79), "i": (4.76, 0.80), "z": (4.65, 0.80)},
    (0.1, 1): {"g": (8.87, 3.35), "r": (8.70, 3.32), "i": (8.78, 3.35), "z": (8.43, 3.37)},
    (0.1, 2): {"g": (3.47, 0.80), "r": (3.40, 0.79), "i": (3.46, 0.80), "z": (3.21, 0.80)},
}
F_CALIB = {"g": 9.0e8, "r": 1.5e9, "i": 9.0e8, "z": 5.0e8}       # ETC, e-/s at m=0
# Measured / ETC electrons, 2026-08-01, 4-5 stars per channel, scatter
# <= 0.019 mag (aperture photometry on the pipeline stacks, BUNIT = electron,
# mean stack). The spreadsheet under-predicts the counts by this factor.
EMPIRICAL_FLUX_SCALE = {"g": 4.97, "r": 6.51, "i": 4.37, "z": 4.49}
EXTINCTION = {
    "photometric": {"g": 0.195, "r": 0.113, "i": 0.030, "z": 0.010},
    "good": {"g": 0.277, "r": 0.1655, "i": 0.0975, "z": 0.0665},
    "regular": {"g": 0.359, "r": 0.218, "i": 0.165, "z": 0.123},
}
M_SKY = {
    "new": {"g": 20.9, "r": 20.5, "i": 20.7, "z": 20.3},
    "quarter": {"g": 19.8, "r": 20.1, "i": 20.4, "z": 20.1},
    "full": {"g": 17.7, "r": 18.7, "i": 19.7, "z": 19.6},
}
PLATE_SCALE_ETC = 0.336          # the ETC's own value (aperture pixel count)
DIGITISATION = 0.289
TEL_DIAMETER_CM, SITE_ALT_M = 160.0, 1864.0


# ============================================================================
# SETTINGS -- the agreed default rules
# ============================================================================

@dataclass
class Settings:
    t_exp: dict | None = None        # band -> exposure time [s], set by you;
                                     # None -> estimated from the target (estimate_texp)
    texp_peak: float = 0.50          # estimate: target peak at this x saturation ...
    texp_max: float = 60.0           # ... never longer than this [s]
    texp_estimated: bool = False     # set by run() when the times were estimated
    basic: bool = False              # brightness-only ranking (see BASIC below)
    acq: str = "NORMAL FAINT"
    size: str = "LARGE"
    mode: str = "phot"               # 'phot' or 'polar'
    airmass: float = 1.2
    moon: str = "new"
    sky: str = "photometric"
    aperture_arcsec: float = 1.5     # ETC convention
    fwhm_plan: float = 2.5           # typical seeing ["], sets isolation radius
    fwhm_best: float = 1.2           # best expected seeing ["], saturation check
    flux_scale: dict = field(default_factory=lambda: dict(EMPIRICAL_FLUX_SCALE))
    # rules
    edge_margin: float = 12.0        # ["] from the edge (both images): aperture + sky annulus
    search_radius: float = 420.0     # ["] comparison candidates within 7' of the target
    noise_ratio_max: float = 10.0    # comp noise per exposure <= this x the target's
                                     # (beyond, a star adds < 1 % of a target-like weight)
    dmag_bright: float | None = None # optional extra limits on m_comp - m_target
    dmag_faint: float | None = None  # (None = only saturation and noise_ratio_max)
    sat_fraction: float = 0.70       # peak must stay below this x saturation
    dcol_good: float = 0.3           # |d(BP-RP)| <= this: weight 1
    dcol_max: float = 0.5            # |d(BP-RP)| <= this: weight dcol_weight
    dcol_weight: float = 0.5
    dcol_far_weight: float = 0.25    # beyond dcol_max: still used, at this weight
    smear: bool = True               # reject comps on another star's readout trail
    smear_frac: float = 0.01         # ... if the trail adds more than this to its flux
    iso_fwhm: float = 3.0            # isolation radius in units of fwhm_plan
    iso_dmag: float = 5.0            # neighbours brighter than m+5 contaminate (>1 %)
    ruwe_max: float = 1.4
    filters: frozenset = None        # rules switched on (None = all of FILTERS)
    dv_min: float | None = None      # window in V_comp - V_target [mag]; None = no limit
    dv_max: float | None = None      # (basic mode: BASIC_DV when both are None)
    max_comps: int | None = 6        # at most this many comparison stars, the ones that
                                     # add most weight (None or 0: every usable star)
    # search
    pointing_unc: float = 0.0        # ["] radius over which the score must hold
                                     # (0: the pointing is refined at the telescope)
    grid_step: float = 4.0           # ["]
    # guider
    guide_gmin: float = 8.0          # placeholder range -- confirm with OPD staff
    guide_gmax: float = 14.0
    guide_radial_margin: float = 15.0
    bands: tuple = BANDS
    # any other instrument (run(..., geometry=FovGeometry), SkyBound's loaded FoVs)
    twin: tuple | None = None        # its second image (dE, dN) ["]: a polarimeter's beam split
    iso_radius: float | None = None  # isolation radius ["]; None: iso_fwhm x fwhm_plan


# ============================================================================
# Catalogue
# ============================================================================

GAIA_EPOCH = 2016.0

ADQL = """SELECT g.source_id, g.ra, g.dec, g.pmra, g.pmdec, g.phot_g_mean_mag, g.bp_rp,
       g.ruwe, g.phot_variable_flag, g.non_single_star,
       s.g_sdss_mag, s.r_sdss_mag, s.i_sdss_mag, s.z_sdss_mag, s.v_jkc_mag
FROM gaiadr3.gaia_source AS g
LEFT JOIN gaiadr3.synthetic_photometry_gspc AS s ON g.source_id = s.source_id
WHERE 1 = CONTAINS(POINT('ICRS', g.ra, g.dec),
                   CIRCLE('ICRS', {ra:.5f}, {dec:.5f}, {radius:.4f}))
  AND g.phot_g_mean_mag < 18"""

# The same cone from GAVO's reduced copy of Gaia DR3 (gaia.dr3lite), used when
# the ESA archive cannot be reached. It has positions, proper motions, G,
# BP, RP and RUWE, but no synthetic photometry and no variability or
# non-single-star flags (see read_gaia and S4FIELD.md, "Reduced catalogue").
ADQL_LITE = """SELECT source_id, ra, dec, pmra, pmdec, phot_g_mean_mag,
       phot_bp_mean_mag - phot_rp_mean_mag AS bp_rp, ruwe
FROM gaia.dr3lite
WHERE 1 = CONTAINS(POINT('ICRS', ra, dec),
                   CIRCLE('ICRS', {ra:.5f}, {dec:.5f}, {radius:.4f}))
  AND phot_g_mean_mag < 18"""

CONE_RADIUS = 0.27      # [deg] SPARC4: the field, the guider annulus and room for offsets
CONE_RADIUS_MAX = 1.0   # [deg] the largest cone downloaded for a loaded FoV

LITE_CAVEAT = ("reduced catalogue (Gaia DR3 lite, GAVO): g,r,i,z from BP-RP colour relations "
               "(~0.015 mag); no Gaia variability or non-single-star flags, so check the "
               "comparison stars for variability yourself")


# Johnson V from Gaia G and BP-RP: Riello et al. (2021), Gaia EDR3 documentation
# 5.5.1, Table 5.7 (scatter 0.030 mag), valid for -0.5 < BP-RP < 5.0.
GV_POLY = (-0.02704, 0.01424, -0.2156, 0.01426)
GV_RANGE = (-0.5, 5.0)

# G - X versus BP-RP for Gaia's synthetic SDSS magnitudes X = g, r, i, z
# (cubic, coefficients from the constant term up). Used only for a catalogue
# without synthetic photometry -- the reduced "lite" cone from GAVO. Fitted
# 2026-11-06 on the AU Mic and TOI-2631 cones (892 stars, 0.12 < BP-RP <
# 3.08). Fitted on one field and tested on the other: bias <= 0.013 mag,
# core scatter 0.005-0.016 mag (1.48 x MAD), rms with outliers 0.035-0.058.
# Every bright (G < 15) star off by more than 0.1 mag had RUWE > 1.4 or no
# RUWE, so the RUWE rule removes them. Outside the fitted colour range the
# relation continues as a straight line (tangent at the edge).
SDSS_POLY = {
    "g": (0.09067, -0.22652, -0.54739, 0.12473),
    "r": (-0.19895, 0.37240, -0.10102, -0.03598),
    "i": (-0.26171, 0.55908, -0.03044, -0.01520),
    "z": (-0.37957, 0.72563, 0.01425, -0.01195),
}
SDSS_POLY_RANGE = (0.12, 3.08)


def sdss_from_bprp(G, x, band):
    """Synthetic-SDSS magnitude estimated from G and BP-RP (SDSS_POLY)."""
    c = SDSS_POLY[band]
    lo, hi = SDSS_POLY_RANGE
    xc = np.clip(x, lo, hi)
    val = sum(ck * xc ** k for k, ck in enumerate(c))
    slope = sum(k * ck * xc ** (k - 1) for k, ck in enumerate(c) if k)
    return np.asarray(G, float) - (val + slope * (np.asarray(x, float) - xc))


def johnson_v(t):
    """V for every star: Gaia's synthetic Johnson-Kron-Cousins V (v_jkc_mag)
    where the CSV has it, otherwise V = G - (G - V)(BP-RP) from the Gaia
    relation. Returns (V, source) with source 'gaia_synthetic', 'G_bprp' or ''."""
    G = np.asarray(t["phot_g_mean_mag"], float)
    x = np.asarray(t["bp_rp"], float)
    gv = sum(c * x ** k for k, c in enumerate(GV_POLY))
    ok = np.isfinite(gv) & (x > GV_RANGE[0]) & (x < GV_RANGE[1])
    V = np.where(ok, G - gv, np.nan)
    src = np.where(ok, "G_bprp", "")
    if "v_jkc_mag" in t.colnames:
        vs = np.asarray(np.ma.filled(t["v_jkc_mag"].astype(float), np.nan))
        has = np.isfinite(vs)
        V = np.where(has, vs, V)
        src = np.where(has, "gaia_synthetic", src)
    return V, src.astype(str)


_READ_MEMO = {}                        # (path, mtime, size) -> Table: the helper reads a cone once


def read_gaia(path):
    """read_gaia_file(), remembered while the file is unchanged (dragging the
    field asks for the same cone again and again). Returns a copy."""
    st = os.stat(path)
    k = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    if k not in _READ_MEMO:
        if len(_READ_MEMO) >= 8:
            _READ_MEMO.pop(next(iter(_READ_MEMO)))
        _READ_MEMO[k] = read_gaia_file(path)
    return _READ_MEMO[k].copy()


def read_gaia_file(path):
    """Read the Gaia CSV (columns as in ADQL above).

    Stars without Gaia synthetic SDSS photometry (G > ~17.65, or no XP
    spectra) get g,r,i,z estimated from a cubic in BP-RP fitted to the stars
    in the same file that have it; those are flagged in `mag_est`.

    A reduced cone (ADQL_LITE: no synthetic photometry, no variability or
    non-single-star columns) is accepted too: every star then gets g,r,i,z
    from the fixed relations SDSS_POLY, the missing flags read as "not
    flagged", and t.meta["lite"] is True so the report says so.
    """
    from astropy.table import Table
    t = Table.read(path, format="ascii.csv")
    n = len(t)
    lite = not all(c in t.colnames for c in ("g_sdss_mag", "phot_variable_flag", "non_single_star"))
    if "bp_rp" not in t.colnames:
        t["bp_rp"] = (np.asarray(np.ma.filled(t["phot_bp_mean_mag"].astype(float), np.nan))
                      - np.asarray(np.ma.filled(t["phot_rp_mean_mag"].astype(float), np.nan)))
    for c in ("g_sdss_mag", "r_sdss_mag", "i_sdss_mag", "z_sdss_mag"):
        if c not in t.colnames:
            t[c] = np.full(n, np.nan)
    if "phot_variable_flag" not in t.colnames:
        t["phot_variable_flag"] = ["NOT_AVAILABLE"] * n
    if "non_single_star" not in t.colnames:
        t["non_single_star"] = np.zeros(n, int)
    for c in ("ruwe", "bp_rp", "g_sdss_mag", "r_sdss_mag", "i_sdss_mag", "z_sdss_mag"):
        t[c] = np.asarray(np.ma.filled(t[c].astype(float), np.nan))
    t["non_single_star"] = np.asarray(np.ma.filled(t["non_single_star"].astype(float), 0.0))
    t["mag_est"] = np.zeros(n, bool)
    G, col = np.asarray(t["phot_g_mean_mag"], float), np.asarray(t["bp_rp"], float)
    for b in BANDS:
        m = np.asarray(t[f"{b}_sdss_mag"], float)
        ok = np.isfinite(m) & np.isfinite(col)
        miss = ~np.isfinite(m) & np.isfinite(col)
        if miss.any():
            if ok.sum() >= 10:
                p = np.polyfit(col[ok], (m - G)[ok], 3)
                m[miss] = G[miss] + np.polyval(p, col[miss])
            else:
                m[miss] = sdss_from_bprp(G[miss], col[miss], b)
            t["mag_est"] |= miss
        t[f"{b}_sdss_mag"] = m
    t.meta["lite"] = lite
    t["phot_variable_flag"] = [str(v) for v in t["phot_variable_flag"]]
    t["V"], t["V_source"] = johnson_v(t)
    # proper motions (mas/yr). Missing columns (older CSVs) or missing values
    # (2-parameter solutions) -> 0, i.e. the star is held at its 2016.0 position.
    t.meta["has_pm"] = "pmra" in t.colnames and "pmdec" in t.colnames
    for c in ("pmra", "pmdec"):
        v = (np.asarray(np.ma.filled(t[c].astype(float), np.nan)) if c in t.colnames
             else np.zeros(len(t)))
        t[c] = np.where(np.isfinite(v), v, 0.0)
    return t


def current_epoch():
    """Today as a decimal year."""
    from astropy.time import Time
    return float(Time.now().decimalyear)


def propagate(tab, epoch):
    """(ra, dec) arrays of every star at `epoch` (decimal year), linear in the
    proper motion from Gaia's 2016.0. Good to << 0.1" over decades except
    within a few degrees of the poles."""
    dt = epoch - GAIA_EPOCH
    dec = np.asarray(tab["dec"], float) + np.asarray(tab["pmdec"]) * dt / 3.6e6
    ra = (np.asarray(tab["ra"], float)
          + np.asarray(tab["pmra"]) * dt / 3.6e6 / np.cos(np.radians(tab["dec"]))) % 360
    return ra, dec


def tangent_offsets(ra, dec, ra0, dec0):
    """Gnomonic offsets (arcsec East, arcsec North) of (ra,dec) about (ra0,dec0)."""
    a, d = np.radians(ra), np.radians(dec)
    a0, d0 = math.radians(ra0), math.radians(dec0)
    cosc = np.sin(d0) * np.sin(d) + np.cos(d0) * np.cos(d) * np.cos(a - a0)
    xi = np.cos(d) * np.sin(a - a0) / cosc
    eta = (np.cos(d0) * np.sin(d) - np.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc
    k = 180 / math.pi * 3600
    return xi * k, eta * k


def from_tangent(e, n, ra0, dec0):
    """Inverse gnomonic: (E,N arcsec) about (ra0,dec0) -> (ra, dec) deg."""
    k = 180 / math.pi * 3600
    xi, eta = e / k, n / k
    a0, d0 = math.radians(ra0), math.radians(dec0)
    rho = math.hypot(xi, eta)
    c = math.atan(rho)
    if rho == 0:
        return ra0, dec0
    dec = math.asin(math.cos(c) * math.sin(d0) + eta * math.sin(c) * math.cos(d0) / rho)
    ra = a0 + math.atan2(xi * math.sin(c),
                         rho * math.cos(d0) * math.cos(c) - eta * math.sin(d0) * math.sin(c))
    return math.degrees(ra) % 360, math.degrees(dec)


# ============================================================================
# Convex polygons
# ============================================================================

def _ccw(poly):
    p = np.asarray(poly, float)
    area = 0.5 * np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1])
    return p if area > 0 else p[::-1]


def clip(subject, clipper):
    """Sutherland-Hodgman intersection of two convex polygons."""
    out = list(map(tuple, _ccw(subject)))
    c = _ccw(clipper)
    for k in range(len(c)):
        a, b = c[k], c[(k + 1) % len(c)]
        inside = lambda p: (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= 0
        inp, out = out, []
        for j in range(len(inp)):
            p, q = inp[j], inp[(j + 1) % len(inp)]
            if inside(q):
                if not inside(p):
                    out.append(_isect(p, q, a, b))
                out.append(q)
            elif inside(p):
                out.append(_isect(p, q, a, b))
    return np.array(out)


def _isect(p, q, a, b):
    x1, y1, x2, y2 = *p, *q
    x3, y3, x4, y4 = *a, *b
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def poly_area(poly):
    p = _ccw(poly)
    return 0.5 * np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1])


def edge_distance(poly, x, y):
    """Signed distance of points to the nearest edge (>0 inside), convex poly.
    x, y broadcast together; returns array of the same shape."""
    p = _ccw(poly)
    d = None
    for k in range(len(p)):
        a, b = p[k], p[(k + 1) % len(p)]
        t = b - a
        nx, ny = -t[1] / np.hypot(*t), t[0] / np.hypot(*t)       # inward normal
        dk = nx * (x - a[0]) + ny * (y - a[1])
        d = dk if d is None else np.minimum(d, dk)
    return d


# ============================================================================
# Any instrument's footprint (SkyBound's loaded FoVs)
# ============================================================================
#
# SkyBound sends the science regions of a field of view -- polygons and
# circles in arcsec East, North of the field centre, already turned to the
# position angle observed (an Aladin FoV file, or one it made itself). A star
# counts as on the detector when it lies inside a region; it can be a
# comparison (or the target) when it is at least the edge margin inside that
# region's edge, so a chip gap is avoided like the field edge. The regions are
# rasterised once per margin (about 2000 cells across), so testing thousands of
# stars at ten thousand pointings costs index arithmetic, whatever the number
# of chips or vertices.

def _rdp(p, tol):
    """Ramer-Douglas-Peucker simplification of an open polyline."""
    keep = np.zeros(len(p), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(p) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg, rel = p[b] - p[a], p[a + 1:b] - p[a]
        L = math.hypot(*seg)
        d = (np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / L if L > 0
             else np.hypot(rel[:, 0], rel[:, 1]))
        k = int(np.argmax(d))
        if d[k] > tol:
            keep[a + 1 + k] = True
            stack += [(a, a + 1 + k), (a + 1 + k, b)]
    return p[keep]


def _in_polygon(pts, X, Y):
    """Ray casting, vectorised over the points X, Y (any shape)."""
    inside = np.zeros(np.shape(X), bool)
    n = len(pts)
    with np.errstate(divide="ignore", invalid="ignore"):
        for k in range(n):
            (xi, yi), (xj, yj) = pts[k], pts[k - 1]
            cross = (yi > Y) != (yj > Y)
            inside ^= cross & (X < (xj - xi) * (Y - yi) / (yj - yi) + xi)
    return inside


def _segment_distance(pts, X, Y):
    """Distance of points X, Y to the nearest edge of a closed polygon."""
    d = np.full(np.shape(X), np.inf)
    for k in range(len(pts)):
        (x0, y0), (x1, y1) = pts[k - 1], pts[k]
        dx, dy = x1 - x0, y1 - y0
        L2 = dx * dx + dy * dy or 1.0
        t = np.clip(((X - x0) * dx + (Y - y0) * dy) / L2, 0, 1)
        d = np.minimum(d, np.hypot(X - x0 - t * dx, Y - y0 - t * dy))
    return d


class FovMask:
    """Where a point (arcsec E, N of the field centre) is on a region of the
    footprint and at least `margin` inside its edge, on a raster."""

    def __init__(self, geom, margin):
        self.margin, self.res = float(margin), geom.res
        x0, y0, x1, y1 = geom.box
        self.x0, self.y0 = x0, y0
        nx = int(math.ceil((x1 - x0) / self.res)) + 1
        ny = int(math.ceil((y1 - y0) / self.res)) + 1
        self.mask = np.zeros((ny, nx), bool)
        self.half = geom.half
        for rg in geom.regions:
            if rg["shape"] == "circle":
                (cx, cy), r = rg["c"], rg["r"]
                bx = (cx - r, cy - r, cx + r, cy + r)
            else:
                pts = rg["pts"]
                bx = (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())
            i0 = max(0, int(math.floor((bx[0] + margin - x0) / self.res)))
            i1 = min(nx - 1, int(math.ceil((bx[2] - margin - x0) / self.res)))
            j0 = max(0, int(math.floor((bx[1] + margin - y0) / self.res)))
            j1 = min(ny - 1, int(math.ceil((bx[3] - margin - y0) / self.res)))
            if i1 < i0 or j1 < j0:
                continue                         # smaller than twice the margin
            X, Y = np.meshgrid(x0 + np.arange(i0, i1 + 1) * self.res,
                               y0 + np.arange(j0, j1 + 1) * self.res)
            if rg["shape"] == "circle":
                ok = np.hypot(X - cx, Y - cy) <= r - margin
            else:
                ok = _in_polygon(pts, X, Y)
                if margin > 0 and ok.any():
                    ok[ok] = _segment_distance(pts, X[ok], Y[ok]) >= margin
            self.mask[j0:j1 + 1, i0:i1 + 1] |= ok
        self.area = float(self.mask.sum()) * self.res ** 2      # [arcsec^2]

    def inside(self, x, y):
        x, y = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float))
        i = np.rint((x - self.x0) / self.res)
        j = np.rint((y - self.y0) / self.res)
        ny, nx = self.mask.shape
        ok = np.isfinite(i) & np.isfinite(j) & (i >= 0) & (i < nx) & (j >= 0) & (j < ny)
        out = np.zeros(x.shape, bool)
        out[ok] = self.mask[j[ok].astype(int), i[ok].astype(int)]
        return out


class FovGeometry:
    """Any instrument's science regions (SkyBound's loaded FoVs), from a list of
    {"shape": "polygon", "pts": [[e, n], ...]} or {"shape": "circle",
    "c": [e, n], "r": r} [arcsec E, N of the field centre, at the position
    angle observed]. mask(margin) is cached."""

    CELLS = 2000           # raster cells across the footprint
    GRID = 100             # pointings across the search, at most
    SEARCH_MAX = 600.0     # ["] offsets searched, at most (a large field has many comparisons anyway)
    MAX_REGIONS, MAX_POINTS = 5000, 200000

    def __init__(self, regions):
        if not isinstance(regions, (list, tuple)) or not regions:
            raise ValueError("a FoV needs at least one science region")
        if len(regions) > self.MAX_REGIONS:
            raise ValueError(f"too many regions ({len(regions)} > {self.MAX_REGIONS})")
        out, npts = [], 0
        for k, rg in enumerate(regions):
            shape = str(rg.get("shape", "polygon")).lower()
            if shape == "circle":
                c, r = [float(v) for v in rg["c"]], float(rg["r"])
                if not (r > 0 and all(map(math.isfinite, c))):
                    raise ValueError(f"region {k + 1}: a circle needs a centre and a radius > 0")
                out.append({"shape": "circle", "c": c, "r": r})
            elif shape == "polygon":
                pts = np.asarray(rg["pts"], float)
                if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 3 or not np.isfinite(pts).all():
                    raise ValueError(f"region {k + 1}: a polygon needs three or more [x, y] vertices")
                npts += len(pts)
                out.append({"shape": "polygon", "pts": pts})
            else:
                raise ValueError(f"region {k + 1}: unknown shape {shape!r} (polygon or circle)")
        if npts > self.MAX_POINTS:
            raise ValueError(f"too many vertices ({npts} > {self.MAX_POINTS})")
        lo = np.min([self._box(r)[:2] for r in out], axis=0)
        hi = np.max([self._box(r)[2:] for r in out], axis=0)
        self.extent = float(max(self._far(r) for r in out))       # farthest point from the centre
        self.half = float(max(np.abs(lo).max(), np.abs(hi).max()))
        self.res = max(2 * self.extent / self.CELLS, 0.02)
        for r in out:                                            # curved chip edges: fewer vertices
            if r["shape"] == "polygon" and len(r["pts"]) > 8:
                closed = _rdp(np.vstack([r["pts"], r["pts"][:1]]), self.res / 2)[:-1]
                r["pts"] = closed if len(closed) >= 3 else r["pts"]
        pad = 2 * self.res
        self.box = (float(lo[0]) - pad, float(lo[1]) - pad, float(hi[0]) + pad, float(hi[1]) + pad)
        self.regions = out
        self._masks = {}

    @staticmethod
    def _box(r):
        if r["shape"] == "circle":
            (cx, cy), rr = r["c"], r["r"]
            return (cx - rr, cy - rr, cx + rr, cy + rr)
        p = r["pts"]
        return (p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max())

    @staticmethod
    def _far(r):
        if r["shape"] == "circle":
            return math.hypot(*r["c"]) + r["r"]
        return float(np.hypot(r["pts"][:, 0], r["pts"][:, 1]).max())

    def mask(self, margin):
        key = round(float(margin), 3)
        if key not in self._masks:
            self._masks[key] = FovMask(self, key)
        return self._masks[key]

    def reach(self):
        """How far the field centre is moved from the target in the search ["]."""
        return min(self.half, self.SEARCH_MAX)

    def need(self):
        """The Gaia cone [deg] around the target that holds every star the search
        can put on the detector: the field centre moves at most reach (and the
        target stays on the detector, so at most extent) from the target, a star
        is at most extent from the centre, plus 20" for neighbours (isolation)."""
        return (min(self.reach() * math.sqrt(2), self.extent) + self.extent + 20.0) / 3600.0

    def cone_radius(self):
        """The cone to download [deg]: need(), rounded up; never smaller than
        SPARC4's (one cone serves both), at most CONE_RADIUS_MAX."""
        return min(CONE_RADIUS_MAX, max(CONE_RADIUS, math.ceil(self.need() * 100) / 100))

    def covers(self, radius_deg):
        """Is a cone of this radius enough? 5% short is accepted: only stars at
        the far corner of an extreme pointing are missed (SPARC4's 0.27 deg
        cones serve 12' fields)."""
        return radius_deg >= self.need() * 0.95


def _inside(fp, x, y, margin):
    """On the footprint and `margin` inside its edge: SPARC4's polygons (exact),
    or a loaded FoV's raster (FovMask, whose margin is built in)."""
    if isinstance(fp, FovMask):
        return fp.inside(x, y)
    return edge_distance(fp, x, y) >= margin



def footprints(size):
    """Per-band polygons and the common 4-channel polygon (arcsec E, N)."""
    per = {b: _ccw(FOOTPRINT[size][b]) for b in BANDS}
    common = per["g"]
    for b in BANDS[1:]:
        common = clip(common, per[b])
    return per, common


def pixel_affine(size, band):
    """Affine map (E,N) -> pixel (x,y), fitted to the 4 measured corners."""
    en = np.asarray(FOOTPRINT[size][band], float)
    xy = np.asarray(CORNER_PIX[size], float)
    A = np.c_[en, np.ones(4)]
    coef, *_ = np.linalg.lstsq(A, xy, rcond=None)

    def f(e, n):
        out = np.c_[np.atleast_1d(e), np.atleast_1d(n), np.ones(np.size(e))] @ coef
        return out[:, 0], out[:, 1]
    return f


# ============================================================================
# Noise model
# ============================================================================

def _rates(band, mag, s: Settings):
    atten = 10 ** (-0.4 * EXTINCTION[s.sky][band] * s.airmass)
    f = F_CALIB[band] * s.flux_scale.get(band, 1.0) * atten
    src = f * 10 ** (-0.4 * np.asarray(mag, float))                       # e-/s
    sky = f * PLATE_SCALE[band] ** 2 * 10 ** (-0.4 * M_SKY[s.moon][band])  # e-/s/pix
    if s.mode == "polar":
        src = 0.5 * src                                                     # per beam
        sky = 0.5 * sky
    return src, sky


def peak_adu(band, mag, s: Settings, fwhm=None):
    """Peak pixel [ADU] of one exposure, Gaussian PSF, sky included."""
    rate, pre, _ = ACQ[s.acq]
    _, gain = RN_GAIN[(rate, pre)][band]
    src, sky = _rates(band, mag, s)
    sig = (s.fwhm_best if fwhm is None else fwhm) / 2.3548
    frac = PLATE_SCALE[band] ** 2 / (2 * math.pi * sig * sig)
    return (src * frac + sky) * s.t_exp[band] / gain


NICE_TEXP = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 1, 1.5, 2, 3, 5, 7, 10, 15,
             20, 30, 45, 60, 90, 120, 180, 300)


def estimate_texp(mags, s: Settings):
    """Exposure times when none are given: per band, the longest 'round'
    value at which the target's peak pixel stays at or below
    s.texp_peak x saturation at the best seeing (s.fwhm_best), capped at
    s.texp_max. Returns ({band: t}, {band: exact t before rounding})."""
    from dataclasses import replace
    sat = ACQ[s.acq][2]
    one = replace(s, t_exp={b: 1.0 for b in BANDS})
    est, exact = {}, {}
    for b in BANDS:
        t = s.texp_peak * sat / float(peak_adu(b, mags[b], one))
        exact[b] = t
        t = min(t, s.texp_max)
        fits = [v for v in NICE_TEXP if v <= t]
        est[b] = fits[-1] if fits else round(t, 4)
    return est, exact


def scintillation(band, s: Settings):
    t = s.t_exp[band]
    return (1.5 * 0.09 * TEL_DIAMETER_CM ** (-2 / 3) * s.airmass ** 1.75
            * math.exp(-SITE_ALT_M / 8000) / math.sqrt(2 * t))


def rel_error(band, mag, s: Settings):
    """Relative flux error of one exposure: ETC CCD equation + scintillation."""
    rate, pre, _ = ACQ[s.acq]
    rn, gain = RN_GAIN[(rate, pre)][band]
    src, sky = _rates(band, mag, s)
    t = s.t_exp[band]
    S = src * t
    npix = 2 * math.pi * (s.aperture_arcsec / PLATE_SCALE_ETC) ** 2   # ETC convention
    var = S + npix * (sky * t + rn ** 2 + (DIGITISATION * gain) ** 2)
    return np.sqrt(var / S ** 2 + scintillation(band, s) ** 2)


# ============================================================================
# Field finder
# ============================================================================

@dataclass
class Result:
    settings: Settings
    target: dict
    stars: object                 # astropy Table, with E, N offsets from target
    per: dict
    common: np.ndarray
    grid_e: np.ndarray
    grid_n: np.ndarray
    score: np.ndarray             # robust worst-band eta on the grid (nan = target off)
    best: dict
    comps: dict                   # band -> indices used at best pointing
    reasons: dict                 # star index -> list of rule failures
    guider: dict
    compared: list


def _twin(s=None):
    """The second image's offset (dE, dN) ["]: SPARC4's Savart split, or the
    beam split of another instrument (Settings.twin)."""
    if s is not None and s.twin is not None:
        return float(s.twin[0]), float(s.twin[1])
    pa = math.radians(POL_SPLIT_PA)
    return POL_SPLIT_ARCSEC * math.sin(pa), POL_SPLIT_ARCSEC * math.cos(pa)


# BASIC mode: the field is chosen by brightness alone, with no exposure, sky,
# airmass or detector model -- "the stars closest in brightness to the target".
#   * each star's noise is pure photon noise, so sigma_i / sigma_T =
#     10**(0.2 (m_i - m_T)) and eta = sqrt(1 + F_T / sum(w F_i)) per band;
#   * a comparison may be at most BASIC_DMAG_BRIGHT brighter than the target:
#     with the target at 50 % of saturation (the exposure estimate's rule) a
#     star 0.37 mag brighter reaches the 70 % limit;
#   * at most BASIC_DMAG_FAINT (8 mag) fainter: the faintest comparison the
#     full model has used in the validation fields (AU Mic C8, +8.0 in z) --
#     for a bright target that model is scintillation-limited and keeps such
#     stars, so the basic mode must not drop them. Fainter stars change eta
#     by < 0.1 % of the target's flux each.
# Quality, colour weights, isolation and edges are unchanged. Read-out trails
# are not modelled: their strength depends on the exposure time, which the
# basic mode does not use (the Advanced mode / full model checks them).

BASIC_DMAG_FAINT = 8.0
BASIC_DV = (-0.35, BASIC_DMAG_FAINT)   # basic mode's default V window (0.35 ~ 0.37 rounded down)

# The rules a comparison star must pass, each of which can be switched off
# (Settings.filters; SkyBound's checkboxes). With none left, only the V window
# and the magnitudes themselves decide.
FILTERS = {
    "ruwe": "Gaia RUWE below 1.4 (well-behaved single-star astrometry)",
    "variable": "not flagged VARIABLE by Gaia",
    "binary": "not a Gaia non-single star (astrometric, spectroscopic or eclipsing binary)",
    "isolation": "no neighbour brighter than G+5 within 3x the typical seeing (both images in polarimetric mode)",
    "radius": "within the search radius (7') of the target",
    "edge": "at least 12\" inside each channel's edge (else: anywhere on the detector)",
    "colour": "weight by colour difference from the target (else every star weighs 1)",
    "saturation": "Advanced: peak below 70% of saturation at the best seeing",
    "noise": "Advanced: noise per exposure at most 10x the target's",
    "trails": "Advanced: not on another star's readout trail (>1% of its flux)",
}
BASIC_FILTERS = ("ruwe", "variable", "binary", "isolation", "radius", "edge", "colour")


def active_filters(s: Settings):
    f = set(FILTERS) if s.filters is None else set(s.filters)
    return f & set(BASIC_FILTERS) if s.basic else f


def dv_window(s: Settings):
    if s.dv_min is None and s.dv_max is None:
        return BASIC_DV if s.basic else (-np.inf, np.inf)
    return (-np.inf if s.dv_min is None else s.dv_min, np.inf if s.dv_max is None else s.dv_max)


def basic_dmag_bright(s: Settings):
    return 2.5 * math.log10(s.sat_fraction / s.texp_peak)


def _eligibility(tab, it, s: Settings):
    """Position-independent rules. Returns (ok[band] bool arrays, weight,
    sigma[band], reasons dict)."""
    n = len(tab)
    G = np.asarray(tab["phot_g_mean_mag"], float)
    col = np.asarray(tab["bp_rp"], float)
    E, N = np.asarray(tab["E"]), np.asarray(tab["N"])
    reasons = {i: [] for i in range(n)}
    on = active_filters(s)

    # quality
    ruwe = np.asarray(tab["ruwe"], float)
    bad_ruwe = ~(ruwe < s.ruwe_max) if "ruwe" in on else np.zeros(n, bool)
    var = (np.array([v.strip().upper() == "VARIABLE" for v in tab["phot_variable_flag"]])
           if "variable" in on else np.zeros(n, bool))
    nss = (np.asarray(tab["non_single_star"], float) != 0) if "binary" in on else np.zeros(n, bool)
    q_bad = bad_ruwe | var | nss
    for i in np.where(q_bad)[0]:
        if bad_ruwe[i]:
            reasons[i].append(f"RUWE {ruwe[i]:.2f}" if np.isfinite(ruwe[i]) else "no RUWE")
        if var[i]:
            reasons[i].append("Gaia VARIABLE")
        if nss[i]:
            reasons[i].append("Gaia non-single star")

    # colour: a weight, not a cut (a missing colour is the only rejection)
    dcol = np.abs(col - col[it])
    if "colour" in on:
        c_bad = ~np.isfinite(dcol)
        weight = np.where(dcol <= s.dcol_good, 1.0,
                          np.where(dcol <= s.dcol_max, s.dcol_weight, s.dcol_far_weight))
    else:
        c_bad = np.zeros(n, bool)
        weight = np.ones(n)
    for i in np.where(c_bad)[0]:
        reasons[i].append("no BP-RP")

    # brightness window in V (all bands)
    V = np.asarray(tab["V"], float)
    lo_v, hi_v = dv_window(s)
    dV = V - V[it]
    v_bad = np.zeros(n, bool)
    if np.isfinite(lo_v) or np.isfinite(hi_v):
        v_bad = ~((dV >= lo_v) & (dV <= hi_v))
        for i in np.where(v_bad)[0]:
            reasons[i].append(f"dV {dV[i]:+.2f} outside {lo_v:+.2f}..{hi_v:+.2f}" if np.isfinite(dV[i])
                              else "no V")

    # distance from the target
    far = (np.hypot(E, N) > s.search_radius) if "radius" in on else np.zeros(n, bool)
    for i in np.where(far)[0]:
        reasons[i].append(f"beyond {s.search_radius / 60:g}'")

    # isolation (G band, both images in polarimetric mode)
    r_iso = s.iso_radius if s.iso_radius else s.iso_fwhm * s.fwhm_plan
    pi, pj = (_contaminations(E, N, G, r_iso, s.iso_dmag, s.mode == "polar", _twin(s))
              if "isolation" in on else (np.array([], int), np.array([], int)))
    i_bad = np.zeros(n, bool)
    i_bad[pi] = True
    for i in np.where(i_bad)[0]:
        j = pj[pi == i]
        k = j[np.argmin(G[j])]
        d0 = math.hypot(E[i] - E[k], N[i] - N[k])
        if d0 < r_iso:
            reasons[i].append(f"neighbour G={G[k]:.1f} at {d0:.1f}\"")
        else:                                   # only a second image is too close
            ve, vn = _twin(s)
            de, dn = E[i] - E[k], N[i] - N[k]
            dm, dp = math.hypot(de - ve, dn - vn), math.hypot(de + ve, dn + vn)
            whose = "its second image" if dp < dm else "the neighbour's second image"
            reasons[i].append(f"neighbour G={G[k]:.1f} at {d0:.1f}\", {whose} "
                              f"{min(dm, dp):.1f}\" away (< {r_iso:.1f}\")")

    ok, sigma, peaks, smear = {}, {}, {}, {}
    _, _, sat = ACQ[s.acq]
    for b in s.bands:
        m = np.asarray(tab[mag_col(b)], float)
        dm = m - m[it]
        pk = peak_adu(b, m, s) if b in BANDS and s.t_exp else np.full(n, np.nan)
        peaks[b] = pk
        sigma[b] = 1e-3 * 10 ** (0.2 * dm) if s.basic else rel_error(b, m, s)
        lo = -np.inf if s.dmag_bright is None else -s.dmag_bright
        hi = np.inf if s.dmag_faint is None else s.dmag_faint
        m_bad = ~((dm >= lo) & (dm <= hi))
        # basic: the V window does the brightness work; advanced: saturation and noise
        n_bad = (~(sigma[b] <= s.noise_ratio_max * sigma[b][it]) & ~m_bad
                 if "noise" in on else np.zeros(n, bool))
        s_bad = ((pk > s.sat_fraction * sat) & ~m_bad
                 if "saturation" in on else np.zeros(n, bool))
        ok[b] = ~(q_bad | c_bad | i_bad | far | v_bad | m_bad | n_bad | s_bad) & np.isfinite(m)
        ok[b][it] = False
        for i in np.where(m_bad)[0]:
            reasons[i].append(f"d{b} {dm[i]:+.2f}" if np.isfinite(dm[i]) else f"no {b}")
        for i in np.where(n_bad)[0]:
            reasons[i].append(f"too faint in {b} (noise {sigma[b][i] / sigma[b][it]:.1f}x target)")
        for i in np.where(s_bad)[0]:
            reasons[i].append(f"saturates in {b} ({pk[i] / sat:.0%} at {s.fwhm_best}\")")
        smear[b] = _smear_pairs(b, m, E, N, ok[b], it, s)
    return ok, weight, sigma, peaks, reasons, bool(np.any(pi == it)), smear


def mag_col(b):
    """The cone's column for a band: V (Johnson), G (Gaia) or g, r, i, z (synthetic SDSS)."""
    return {"V": "V", "G": "phot_g_mean_mag"}.get(b, f"{b}_sdss_mag")


GENERIC_BANDS = ("V", "G", "g", "r", "i", "z")


def _contaminations(E, N, G, r, dmag, polar, twin=None):
    """Pairs (i, j): star j spoils star i -- j is less than `dmag` fainter than i
    (or brighter) and closer than r arcsec, counting in polarimetric mode also
    either image of one against the other (offsets +/- the twin vector).
    Only nearby pairs are examined (a sweep in E), not all n^2 of them: in a
    6000-star cone that is ~1e6 candidates instead of 4e7, and ~30x faster."""
    n = len(E)
    if n < 2:
        return np.array([], int), np.array([], int)
    ve, vn = (twin or _twin()) if polar else (0.0, 0.0)
    reach = r + math.hypot(ve, vn)               # no closer pair can matter
    order = np.argsort(E, kind="stable")
    Es = E[order]
    lo = np.searchsorted(Es, Es - reach, "left")
    hi = np.searchsorted(Es, Es + reach, "right")
    cnt = hi - lo
    a = np.repeat(np.arange(n), cnt)             # positions in the sorted order
    b = np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt) + np.repeat(lo, cnt)
    i, j = order[a], order[b]
    keep = (i != j) & (np.abs(N[i] - N[j]) < reach) & ((G[j] - G[i]) < dmag)
    i, j = i[keep], j[keep]
    de, dn = E[i] - E[j], N[i] - N[j]
    close = np.hypot(de, dn) < r
    if polar:
        close |= np.hypot(de - ve, dn - vn) < r
        close |= np.hypot(de + ve, dn + vn) < r
    return i[close], j[close]


def _smear_pairs(b, m, E, N, ok_b, it, s: Settings):
    """Which stars' readout trails would spoil which comparison stars in band b.

    Star j's trail runs along the readout direction through both of its
    images. A comparison i whose image lies within the isolation radius of
    that line picks up  F_j * T_VSHIFT * D  electrons, D = aperture diameter
    in pixels (radius = fwhm_plan), against its own F_i * t_exp. Pairs above
    smear_frac are returned as (sources, matrix[source, comp]) -- they only
    matter when the source is actually on the detector, decided per pointing.
    """
    idx = np.where(ok_b)[0]
    if not s.smear or idx.size == 0:
        return np.array([], int), np.zeros((0, idx.size), bool), idx
    D = 2 * s.fwhm_plan / PLATE_SCALE[b]
    frac = 10 ** (-0.4 * (m[:, None] - m[None, idx])) * T_VSHIFT * D / s.t_exp[b]
    frac = np.where(np.isfinite(frac), frac, 0)
    w = s.iso_fwhm * s.fwhm_plan
    offs = [(0.0, 0.0)]
    if s.mode == "polar":
        ve, vn = _twin(s)
        offs += [(ve, vn), (-ve, -vn)]
    hit = np.zeros(frac.shape, bool)
    axis = SMEAR_AXIS[b]
    for oe, on in offs:
        if axis in ("row", "both"):
            hit |= np.abs(N[:, None] + on - N[None, idx]) < w
        if axis in ("col", "both"):
            hit |= np.abs(E[:, None] + oe - E[None, idx]) < w
    H = hit & (frac > s.smear_frac)
    H[idx, np.arange(idx.size)] = False                 # a star does not spoil itself
    src = np.where(H.any(axis=1))[0]
    return src, H[src], idx


def _usable(b, dE, dN, tab, ok, smear, per, s):
    """Boolean (pointings x comps of band b): inside the channel with margin,
    and not on the trail of a star that is on the detector."""
    E, N = np.asarray(tab["E"]), np.asarray(tab["N"])
    dE, dN = np.asarray(dE, float).reshape(-1, 1), np.asarray(dN, float).reshape(-1, 1)
    src, H, idx = smear[b]
    x, y = E[idx][None, :] - dE, N[idx][None, :] - dN
    inside = _inside(per[b], x, y, s.edge_margin)
    if s.mode == "polar":
        ve, vn = _twin(s)
        inside &= _inside(per[b], x + ve, y + vn, s.edge_margin)
    if src.size:
        on = edge_distance(per[b], E[src][None, :] - dE, N[src][None, :] - dN) > 0
        inside &= ~((on.astype(float) @ H.astype(float)) > 0)
    return idx, inside


def _choose(dE, dN, tab, it, ok, weight, sigma, per, s, smear):
    """The comparison stars at field centres (dE, dN): per band, (idx, use)
    with use[pointing, k] True if star idx[k] is a comparison there.

    Without a limit: every usable star (_usable). With s.max_comps = N: the N
    stars that add most weight, counted over all bands together -- ranked once
    by sum_b weight / sigma_b^2 x sigma_b,target^2 (a star as good as the
    target in every band scores the number of bands) -- among those usable in
    at least one band. So at most N distinct stars, the same set in every band.
    """
    U = {b: _usable(b, dE, dN, tab, ok, smear, per, s) for b in s.bands}
    n = s.max_comps
    if not n:
        return U
    allidx = np.unique(np.concatenate([U[b][0] for b in s.bands]))
    if allidx.size <= n:
        return U
    P = U[s.bands[0]][1].shape[0]
    pos, score = {}, np.zeros(allidx.size)
    for b in s.bands:
        idx = U[b][0]
        pos[b] = np.searchsorted(allidx, idx)
        score[pos[b]] += weight[idx] / sigma[b][idx] ** 2 * sigma[b][it] ** 2
    order = np.argsort(-score, kind="stable")
    keep = np.zeros((P, allidx.size), bool)
    for a in range(0, P, 2048):                  # in chunks: P x stars can be large
        anyin = np.zeros((min(P, a + 2048) - a, allidx.size), bool)
        for b in s.bands:
            anyin[:, pos[b]] |= U[b][1][a:a + 2048]
        srt = anyin[:, order]
        take = srt & (np.cumsum(srt, axis=1, dtype=np.int32) <= n)
        keep[a:a + 2048, order] = take
    return {b: (U[b][0], U[b][1] & keep[:, pos[b]]) for b in s.bands}


def _eta(dE, dN, tab, it, ok, weight, sigma, per, common, s, smear, per_band=False):
    """Worst-band eta = sigma_diff / sigma_target at field centres (dE, dN)
    [arcsec of the field centre East/North of the target]. Shapes broadcast.
    per_band: {band: eta} instead of the worst."""
    dE, dN = np.asarray(dE, float), np.asarray(dN, float)
    # target position relative to field centre = (-dE, -dN)
    t_in = _inside(common, -dE, -dN, s.edge_margin)
    if s.mode == "polar":
        ve, vn = _twin(s)
        t_in &= _inside(common, -dE + ve, -dN + vn, s.edge_margin)
    worst, bands = np.ones(dE.shape), {}
    chosen = _choose(dE, dN, tab, it, ok, weight, sigma, per, s, smear)
    for b in s.bands:
        idx, use = chosen[b]
        inv = use @ (weight[idx] / sigma[b][idx] ** 2)
        var_e = np.where(inv > 0, 1 / np.where(inv > 0, inv, 1), np.inf)
        eta = np.sqrt(1 + var_e / sigma[b][it] ** 2).reshape(dE.shape)
        bands[b] = np.where(t_in, eta, np.nan)
        worst = np.maximum(worst, eta)
    return bands if per_band else np.where(t_in, worst, np.nan)


def _robust(dE, dN, *args):
    """Worst eta over the nominal pointing and 8 points on the uncertainty
    circle. NaN (target off the field) anywhere -> NaN."""
    s = args[-2]
    out = _eta(dE, dN, *args)
    if s.pointing_unc > 0:
        for a in np.radians(np.arange(0, 360, 45)):
            e = _eta(dE + s.pointing_unc * math.cos(a), dN + s.pointing_unc * math.sin(a), *args)
            out = np.where(np.isnan(e) | np.isnan(out), np.nan, np.maximum(out, e))
    return out


def find_guider(tab, ce, cn, s: Settings):
    """Guide-star candidates for a field centre at (ce, cn) [arcsec from target].
    Returns list of dicts sorted by brightness, with the guider angle."""
    G = np.asarray(tab["phot_g_mean_mag"], float)
    e = np.asarray(tab["E"]) - ce
    n = np.asarray(tab["N"]) - cn
    r = np.hypot(e, n)
    pa = np.degrees(np.arctan2(e, n)) % 360
    ok = ((G >= s.guide_gmin) & (G <= s.guide_gmax)
          & (r >= GUIDER_R_IN + s.guide_radial_margin)
          & (r <= GUIDER_R_OUT - s.guide_radial_margin))
    out = []
    for i in np.where(ok)[0][np.argsort(G[ok])]:
        out.append({"index": int(i), "source_id": int(tab["source_id"][i]), "G": float(G[i]),
                    "r": float(r[i]), "pa": float(pa[i]),
                    "angle": float((pa[i] - 180) % 360)})
    return out


def run(tab, target_ra, target_dec, s: Settings, compare=(), match_radius=2.0,
        resolution=None, epoch=None, at=None, allow_blank=False, geometry=None):
    """Main entry point. `compare`: list of (label, ra, dec) pointings to score.
    `at`: (dE, dN) [arcsec], the field centre's offset from the target -- score
    that one pointing instead of searching (SkyBound's drag-the-field).

    The target is the Gaia star that comes within `match_radius` arcsec of
    (target_ra, target_dec) at epoch 2000.0 (catalogue/SIMBAD positions),
    2016.0 (Gaia) or the observing epoch -- so a high proper motion star is
    found whichever epoch its coordinates refer to. All positions are then
    moved to the observing `epoch` (default: today) before the field is built.

    No star there: a ValueError naming the nearest stars, or, with
    `allow_blank`, the field at that sky position without a target star
    (run_blank: no comparison stars, no eta; the guider and the star list).

    `geometry`: another instrument's footprint (FovGeometry) instead of
    SPARC4's channels -- basic mode only, in the band(s) of s.bands (V, G, g,
    r, i, z), with no guider (SkyBound places a guider itself).
    """
    if geometry is not None and not s.basic:
        raise ValueError("a loaded FoV is ranked in basic mode only (no exposure model)")
    tab = tab.copy()
    epoch = current_epoch() if epoch is None else float(epoch)
    sep, sep_ep = None, {}
    for ep in (2000.0, GAIA_EPOCH, epoch):
        ra, dec = propagate(tab, ep)
        d = np.hypot(*tangent_offsets(ra, dec, target_ra, target_dec))
        sep_ep[ep] = d
        sep = d if sep is None else np.minimum(sep, d)
    it = int(np.argmin(sep))
    if sep[it] > match_radius:
        near = np.argsort(sep)[:3]
        lst = ", ".join(f"G={tab['phot_g_mean_mag'][i]:.2f} at {sep[i]:.1f}\"" for i in near)
        hint = ("" if tab.meta.get("has_pm") else
                " This CSV has no proper motions: re-download it with the query from --adql,"
                " or use the Gaia DR3 source_id.")
        if allow_blank:
            return run_blank(tab, target_ra, target_dec, s, compare, resolution, epoch, at,
                             match_radius=match_radius, nearest=lst, pm_hint=hint,
                             sep_min=float(sep[it]), geometry=geometry)
        raise ValueError(f"No Gaia star within {match_radius}\" of the target position "
                         f"(nearest: {lst}).{hint} Or raise --match-radius, or use --blank "
                         f"for a field at an empty position.")
    match_epoch = min(sep_ep, key=lambda ep: sep_ep[ep][it])
    tab["ra"], tab["dec"] = propagate(tab, epoch)
    ra0, dec0 = float(tab["ra"][it]), float(tab["dec"][it])
    tab["E"], tab["N"] = tangent_offsets(np.asarray(tab["ra"]), np.asarray(tab["dec"]), ra0, dec0)

    texp_exact = None
    on = active_filters(s)
    if s.smear and (s.basic or "trails" not in on):          # trails need exposure times
        from dataclasses import replace
        s = replace(s, smear=False)
    if "edge" not in on and s.edge_margin != 0:              # anywhere on the detector
        from dataclasses import replace
        s = replace(s, edge_margin=0.0)
    if s.t_exp is None and geometry is None:
        from dataclasses import replace
        est, texp_exact = estimate_texp({b: float(tab[f"{b}_sdss_mag"][it]) for b in BANDS}, s)
        s = replace(s, t_exp=est, texp_estimated=True)

    if geometry is None:
        per, common = footprints(s.size)
    else:                                       # one footprint, the same for every band
        common = geometry.mask(s.edge_margin)
        per = {b: common for b in s.bands}
    ok, weight, sigma, peaks, reasons, t_contam, smear = _eligibility(tab, it, s)
    args = (tab, it, ok, weight, sigma, per, common, s, smear)

    if at is not None:                          # one given pointing, no search
        ge, gn = np.array([[float(at[0])]]), np.array([[float(at[1])]])
        score = _robust(ge, gn, *args)
        if np.all(np.isnan(score)):
            score = np.array([[np.inf]])          # target off the field: still report it
    elif geometry is None:
        half = float(np.max(np.abs(common))) + s.pointing_unc
        g = np.arange(-half, half + 1e-9, s.grid_step)
        ge, gn = np.meshgrid(g, g)
    else:                                       # a large field: offsets within its reach, ~100 x 100
        half = geometry.reach() + s.pointing_unc
        g = np.arange(-half, half + 1e-9, max(s.grid_step, 2 * half / FovGeometry.GRID))
        ge, gn = np.meshgrid(g, g)
    if at is None:
        score = _robust(ge, gn, *args)
    if np.all(np.isnan(score)):
        if geometry is None:
            raise ValueError("The target cannot be placed inside the field with this margin.")
        # a loaded FoV smaller than its margins (an IFU), or the target never on a
        # detector: the field centred on the target, with no comparison stars
        ge, gn, score = np.array([[0.0]]), np.array([[0.0]]), np.array([[np.inf]])

    # best: lowest robust score. Usually a plateau of equal scores (the same
    # stars are in the field); take its deepest point -- farthest from any
    # worse pointing -- so extra pointing error costs nothing. Remaining ties
    # -> target nearest the field centre.
    smin = np.nanmin(score)
    good = score <= smin * 1.005
    ce_c, cn_c = ge[good], gn[good]
    be, bn = ge[~good], gn[~good]
    depth = np.array([np.min(np.hypot(be - x, bn - y)) if be.size else 0.0
                      for x, y in zip(ce_c, cn_c)])
    deep = depth >= depth.max() - 1e-6
    k = np.argmin(np.hypot(ce_c[deep], cn_c[deep]))
    ce, cn = float(ce_c[deep][k]), float(cn_c[deep][k])

    comps, smeared, usable = {}, {}, set()
    chosen = _choose(ce, cn, tab, it, ok, weight, sigma, per, s, smear)
    for b in s.bands:
        idx, use = _usable(b, ce, cn, tab, ok, smear, per, s)
        usable |= set(idx[use[0]].tolist())
        comps[b] = chosen[b][0][chosen[b][1][0]]
        no_smear = Settings(**{**s.__dict__, "smear": False})
        idx0, use0 = _usable(b, ce, cn, tab, ok, {b: (np.array([], int), None, idx)}, per, no_smear)
        smeared[b] = idx0[use0[0] & ~use[0]]       # inside, but on a trail

    cra, cdec = from_tangent(ce, cn, ra0, dec0)
    t_pix = {b: pixel_affine(s.size, b)(-ce, -cn) for b in BANDS} if geometry is None else {}
    t_in = _inside(common, np.array(-ce), np.array(-cn), s.edge_margin)
    if s.mode == "polar":
        ve, vn = _twin(s)
        t_in = t_in & _inside(common, np.array(-ce + ve), np.array(-cn + vn), s.edge_margin)
    best = {"dE": ce, "dN": cn, "ra": cra, "dec": cdec, "target_inside": bool(np.all(t_in)),
            "eta_robust": float(smin), "plateau_depth": float(depth.max()),
            "eta_nominal": float(_eta(np.array(ce), np.array(cn), *args)),
            "eta_bands": _eta_bands(ce, cn, args),
            "target_pixel": {b: (float(t_pix[b][0][0]), float(t_pix[b][1][0])) for b in t_pix}}

    guider = {"candidates": find_guider(tab, ce, cn, s) if geometry is None else []}

    compared = []
    for lab, ra, dec in compare:
        e, n = tangent_offsets(np.array([ra]), np.array([dec]), ra0, dec0)
        compared.append({"label": lab, "dE": float(e[0]), "dN": float(n[0]),
                         "eta_nominal": float(_eta(e, n, *args)[0]),
                         "eta_robust": float(_robust(e, n, *args)[0]),
                         "eta_bands": _eta_bands(float(e[0]), float(n[0]), args)})

    tgt = {"index": it, "source_id": int(tab["source_id"][it]), "ra": ra0, "dec": dec0,
           "G": float(tab["phot_g_mean_mag"][it]), "bp_rp": float(tab["bp_rp"][it]),
           "mags": {b: float(tab[f"{b}_sdss_mag"][it]) for b in BANDS},
           "V": float(tab["V"][it]),
           "peak": {b: float(peaks[b][it]) for b in s.bands},
           "sigma": {b: float(sigma[b][it]) for b in s.bands},
           "contaminated": bool(t_contam), "match_sep": float(sep[it]),
           "resolution": resolution, "match_epoch": match_epoch, "epoch": epoch,
           "texp_exact": texp_exact,
           "has_pm": bool(tab.meta.get("has_pm")),
           "pm": (float(tab["pmra"][it]), float(tab["pmdec"][it]))}
    res = Result(s, tgt, tab, per, common, ge, gn, score, best, comps, reasons, guider, compared)
    res._sigma, res._peaks, res._weight, res._smeared = sigma, peaks, weight, smeared
    res._fixed = at is not None
    res._capped = usable - set().union(*[set(v.tolist()) for v in comps.values()])
    res._geometry = geometry
    return res


def blank_note(t):
    """The warning for a field at a position with no Gaia star (run_blank)."""
    return (f"No Gaia star within {t['match_radius']:g}\" of this position (nearest: {t['nearest']})."
            f"{t.get('pm_hint', '')} The field is centred on the position itself; with no target star there is no brightness "
            f"to compare with, so no comparison stars are chosen and there is no eta. "
            f"If you meant a star, check its coordinates.")


def run_blank(tab, ra0, dec0, s: Settings, compare=(), resolution=None, epoch=None, at=None,
              match_radius=2.0, nearest="", pm_hint="", sep_min=float("nan"), geometry=None):
    """The field at a sky position with no Gaia star on it (pointing tests,
    empty fields): centred on the position, or at `at` = (dE, dN) from it.
    No target star, so no comparison stars and no eta; the guider, the
    footprints and the star list are as usual. Peaks and noise per star are
    computed when exposure times are given (Advanced), else left empty.
    Result.target['blank'] is True and its 'index' None."""
    from dataclasses import replace
    tab = tab.copy()
    epoch = current_epoch() if epoch is None else float(epoch)
    tab["ra"], tab["dec"] = propagate(tab, epoch)
    ra0, dec0 = float(ra0), float(dec0)              # a fixed sky position: no proper motion
    tab["E"], tab["N"] = tangent_offsets(np.asarray(tab["ra"]), np.asarray(tab["dec"]), ra0, dec0)
    on = active_filters(s)
    s = replace(s, smear=False)                      # trails only matter for comparison stars
    if "edge" not in on and s.edge_margin != 0:
        s = replace(s, edge_margin=0.0)
    has_texp = s.t_exp is not None
    if not has_texp:                                 # nothing to estimate them from
        s = replace(s, t_exp={b: float("nan") for b in BANDS})

    n, nan = len(tab), float("nan")
    if geometry is None:
        per, common = footprints(s.size)
    else:
        common = geometry.mask(s.edge_margin)
        per = {b: common for b in s.bands}
    ce, cn = (float(at[0]), float(at[1])) if at is not None else (0.0, 0.0)
    peaks, sigma = {}, {}
    for b in s.bands:
        m = np.asarray(tab[mag_col(b)], float)
        peaks[b] = peak_adu(b, m, s) if has_texp and b in BANDS else np.full(n, nan)
        sigma[b] = rel_error(b, m, s) if has_texp and not s.basic and b in BANDS else np.full(n, nan)
    empty = np.array([], int)
    comps = {b: empty for b in s.bands}
    smeared = {b: empty for b in s.bands}

    cra, cdec = from_tangent(ce, cn, ra0, dec0)
    t_pix = {b: pixel_affine(s.size, b)(-ce, -cn) for b in BANDS} if geometry is None else {}
    t_in = _inside(common, np.array(-ce), np.array(-cn), s.edge_margin)
    if s.mode == "polar":
        ve, vn = _twin(s)
        t_in = t_in & _inside(common, np.array(-ce + ve), np.array(-cn + vn), s.edge_margin)
    best = {"dE": ce, "dN": cn, "ra": cra, "dec": cdec, "target_inside": bool(np.all(t_in)),
            "eta_robust": nan, "plateau_depth": 0.0, "eta_nominal": nan,
            "eta_bands": {b: nan for b in s.bands},
            "target_pixel": {b: (float(t_pix[b][0][0]), float(t_pix[b][1][0])) for b in t_pix}}
    guider = {"candidates": find_guider(tab, ce, cn, s) if geometry is None else []}
    compared = []
    for lab, ra, dec in compare:
        e, nn = tangent_offsets(np.array([ra]), np.array([dec]), ra0, dec0)
        compared.append({"label": lab, "dE": float(e[0]), "dN": float(nn[0]), "eta_nominal": nan,
                         "eta_robust": nan, "eta_bands": {b: nan for b in s.bands}})
    tgt = {"index": None, "source_id": None, "ra": ra0, "dec": dec0, "G": nan, "bp_rp": nan,
           "mags": {b: nan for b in BANDS}, "peak": {b: nan for b in s.bands},
           "sigma": {b: nan for b in s.bands}, "contaminated": False, "match_sep": sep_min,
           "resolution": resolution, "match_epoch": None, "epoch": epoch, "texp_exact": None,
           "has_pm": bool(tab.meta.get("has_pm")), "pm": (0.0, 0.0),
           "blank": True, "match_radius": match_radius, "nearest": nearest, "pm_hint": pm_hint,
           "has_texp": has_texp}
    res = Result(s, tgt, tab, per, common, np.array([[ce]]), np.array([[cn]]), np.array([[nan]]),
                 best, comps, {i: [] for i in range(n)}, guider, compared)
    res._sigma, res._peaks, res._weight, res._smeared = sigma, peaks, np.ones(n), smeared
    res._fixed = at is not None
    res._geometry = geometry
    return res


def _eta_bands(ce, cn, args):
    """eta per band at one pointing, with the same comparison stars as the field."""
    per_b = _eta(np.array(ce), np.array(cn), *args, per_band=True)
    return {b: float(v) for b, v in per_b.items()}


# ============================================================================
# Report
# ============================================================================

def _hms(ra, dec):
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    return SkyCoord(ra * u.deg, dec * u.deg).to_string("hmsdms", sep=":", precision=2)


def _texp_warning(res: Result):
    s, t = res.settings, res.target
    ex = t["texp_exact"]
    rate, pre, sat = ACQ[s.acq]
    full_e = sat * RN_GAIN[(rate, pre)]["r"][1]
    L = ["!! EXPOSURE TIMES ESTIMATED -- you did not give --texp.",
         f"   {', '.join(f'{b}={s.t_exp[b]:g}s' for b in BANDS)}: the longest round values that keep",
         f"   the target's peak at <= {s.texp_peak:.0%} of saturation at {s.fwhm_best}\" seeing"
         f" ({s.acq}), capped at {s.texp_max:g} s."]
    capped = [b for b in BANDS if ex[b] > s.texp_max]
    if capped:
        L.append(f"   Capped in {', '.join(capped)}: the target alone would allow "
                 + ", ".join(f"{ex[b]:.0f} s" for b in capped) + ".")
    L.append("   They decide which comparison stars are too bright, so the field depends on them.")
    L.append(f"   At {s.fwhm_plan}\" seeing they could be ~{(s.fwhm_plan / s.fwhm_best) ** 2:.1f}x longer"
             f" (set --fwhm-best to the best seeing you expect, or the defocused FWHM).")
    if min(s.t_exp.values()) < 1.0:
        better = [(n, a[2] * RN_GAIN[(a[0], a[1])]["r"][1] / full_e) for n, a in ACQ.items()]
        n, f = max(better, key=lambda x: x[1])
        if f > 1.05:
            L.append(f"   Sub-second exposures: {n} holds {f:.1f}x more electrons before saturating.")
        L.append(f"   Scintillation per exposure: "
                 + ", ".join(f"{b} {1e3 * scintillation(b, s):.1f} ppt" for b in BANDS) + ".")
    L.append("   Give --texp G R I Z to use your own.")
    L.append("")
    return L


def _report_blank(res: Result) -> str:
    """report() for a field at a position with no Gaia star (run_blank)."""
    s, t, b0, tab = res.settings, res.target, res.best, res.stars
    L = ["=" * 74, f"  SPARC4 field finder  (s4field {__version__})", "=" * 74]
    rz = t.get("resolution")
    if rz is not None:
        L.append(f"Input   '{rz.text}' -> {_hms(rz.ra, rz.dec)}  via {rz.source}"
                 + (f" ({rz.note})" if rz.note else ""))
        if rz.catalogue:
            L.append(f"Gaia    {rz.catalogue}")
    if tab.meta.get("lite"):
        L.append(f"!! {LITE_CAVEAT}.")
    L.append(f"Target  NONE: no Gaia star within {t['match_radius']:g}\" of {_hms(t['ra'], t['dec'])}")
    L.append(f"        (nearest: {t['nearest']}){t.get('pm_hint', '')}")
    texp = (", ".join(f"{b}={s.t_exp[b]:g}s" for b in s.bands) if t["has_texp"] else "not given")
    L.append(f"Setup   {s.mode}, {s.size}" + ("" if s.basic else f", {s.acq}; t_exp {texp}"))
    L.append("")
    L.append("!! EMPTY POSITION: the field is centred on the position itself. With no target star there")
    L.append("   is no brightness to compare with, so no comparison stars are chosen and there is no eta.")
    L.append("")
    L.append("POINTING" + ("  (moved by hand)" if getattr(res, "_fixed", False) else ""))
    L.append(f"  field centre   {_hms(b0['ra'], b0['dec'])}")
    L.append(f"  TCSPD Precise offset (from the position):  RA.cos(dec) = {b0['dE']:+.1f}\"   "
             f"DEC = {b0['dN']:+.1f}\"")
    px, py = b0["target_pixel"]["r"]
    L.append(f"  the position falls near x={px:.0f}, y={py:.0f} (channel 2, 0-based, raw orientation)")
    L.append("")
    L.append("BRIGHTEST STARS IN THE FIELD (all four channels)")
    ce, cn = b0["dE"], b0["dN"]
    inside = edge_distance(res.common, np.asarray(tab["E"]) - ce, np.asarray(tab["N"]) - cn)
    G = np.asarray(tab["phot_g_mean_mag"], float)
    ids = [i for i in np.argsort(G) if inside[i] > 0][:10]
    for i in ids:
        pk = ""
        if t["has_texp"]:
            sat = ACQ[s.acq][2]
            pk = "  peak " + " ".join(f"{b} {res._peaks[b][i] / sat:4.0%}" for b in s.bands)
        L.append(f"     {tab['source_id'][i]:>19d} G={G[i]:5.2f}  V={tab['V'][i]:5.2f}  "
                 f"{math.hypot(tab['E'][i], tab['N'][i]):4.0f}\" from the position{pk}")
    if not ids:
        L.append("     none brighter than the catalogue limit (G < 18)")
    L.append("")
    L.append("GUIDER")
    gc = res.guider["candidates"]
    if gc:
        g = gc[0]
        L.append(f"  Gaia DR3 {g['source_id']}  G={g['G']:.2f}  r={g['r']:.0f}\"  PA={g['pa']:.1f} deg")
        L.append(f"  -> Guider Setup angle {g['angle']:.1f} deg   (PA_box = 180 + angle)")
        for g in gc[1:4]:
            L.append(f"     alternative: G={g['G']:.2f} at angle {g['angle']:.1f} deg")
    else:
        L.append(f"  no star with {s.guide_gmin} <= G <= {s.guide_gmax} in the patrol annulus")
    L.append(f"  (guide-star range G {s.guide_gmin}-{s.guide_gmax} is a placeholder)")
    L.append("")
    L.append(f"Notes: star positions moved to epoch {t['epoch']:.2f} with Gaia proper motions; "
             "the position itself is held fixed." if t["has_pm"] else
             "!! This CSV has no pmra/pmdec: star positions are Gaia's 2016.0.")
    return "\n".join(L)


def report(res: Result) -> str:
    s, t, b0 = res.settings, res.target, res.best
    if t.get("blank"):
        return _report_blank(res)
    sat = ACQ[s.acq][2]
    L = []
    L.append("=" * 74)
    L.append(f"  SPARC4 field finder  (s4field {__version__})")
    L.append("=" * 74)
    rz = t.get("resolution")
    if rz is not None:
        L.append(f"Input   '{rz.text}' -> {_hms(rz.ra, rz.dec)}  via {rz.source}"
                 + (f" ({rz.note})" if rz.note else "")
                 + f"; matched to Gaia at {t['match_sep']:.2f}\" (epoch {t['match_epoch']:.1f})")
        if rz.catalogue:
            L.append(f"Gaia    {rz.catalogue}")
    if res.stars.meta.get("lite"):
        L.append(f"!! {LITE_CAVEAT}.")
    L.append(f"Target  Gaia DR3 {t['source_id']}  G={t['G']:.2f}  BP-RP={t['bp_rp']:.2f}  "
             f"griz={' '.join(f'{t['mags'][b]:.2f}' for b in BANDS)}")
    if s.basic:
        L.append(f"Setup   BASIC: {s.mode}, {s.size}; comparison stars ranked by brightness alone "
                 f"(photon noise; no exposure, sky or airmass model)")
    else:
        L.append(f"Setup   {s.mode}, {s.acq}, {s.size}; t_exp " +
                 ", ".join(f"{b}={s.t_exp[b]:g}s" for b in s.bands) +
                 f"; X={s.airmass}, moon {s.moon}, sky {s.sky}")
    win = ""
    if s.dmag_bright is not None or s.dmag_faint is not None:
        win = (f"{-(s.dmag_bright if s.dmag_bright is not None else math.inf):+.1f}<=dm<="
               f"{(s.dmag_faint if s.dmag_faint is not None else math.inf):+.1f}, ")
    on = active_filters(s)
    lo_v, hi_v = dv_window(s)
    parts = []
    if np.isfinite(lo_v) or np.isfinite(hi_v):
        parts.append(f"dV {lo_v:+.2f}..{hi_v:+.2f}".replace("+inf", "any").replace("-inf", "any"))
    if "noise" in on and not s.basic:
        parts.append(f"noise<={s.noise_ratio_max:g}x target")
    if "saturation" in on and not s.basic:
        parts.append(f"peak<{s.sat_fraction:.0%} sat at {s.fwhm_best}\"")
    off = [k for k in (BASIC_FILTERS if s.basic else FILTERS) if k not in on]
    if off:
        parts.append("OFF: " + ", ".join(off))
    bright_rule = ", ".join(parts) if parts else "magnitudes only"
    r1 = ([f"within {s.search_radius / 60:g}' of target"] if "radius" in on else []) + [win + bright_rule]
    if "colour" in on:
        r1.append(f"colour weight 1/{s.dcol_weight}/{s.dcol_far_weight} at |d(BP-RP)|<={s.dcol_good}/<={s.dcol_max}/beyond")
    r2 = []
    if "isolation" in on:
        r2.append(f"isolation {s.iso_fwhm:g}x{s.fwhm_plan}\" & dG<{s.iso_dmag:g}")
    if s.smear:
        r2.append(f"readout smear >{s.smear_frac:.0%}")
    if "ruwe" in on:
        r2.append(f"RUWE<{s.ruwe_max}")
    if "variable" in on:
        r2.append("not Gaia VARIABLE")
    if "binary" in on:
        r2.append("not Gaia non-single")
    r2.append(f"edge>={s.edge_margin:g}\"" if s.edge_margin > 0 else "anywhere on the detector")
    r2.append(f"pointing +/-{s.pointing_unc}\"")
    if s.max_comps:
        r2.append(f"at most {s.max_comps} comparisons (those adding most weight)")
    L.append("Rules   " + ", ".join(r1) + ",")
    L.append("        " + ", ".join(r2))
    L.append("")
    if s.texp_estimated and not s.basic:
        L += _texp_warning(res)
    for b in ([] if s.basic else s.bands):
        frac = t["peak"][b] / sat
        flag = "  <-- TARGET SATURATES" if frac > 1 else ("  <-- target above limit" if frac > s.sat_fraction else "")
        L.append(f"  target {b}: peak {t['peak'][b]:7.0f} ADU at {s.fwhm_best}\" ({frac:.0%} of sat), "
                 f"sigma/exp {1e3 * t['sigma'][b]:.2f} ppt{flag}")
    if t["contaminated"]:
        L.append("  !! the target itself has a neighbour inside the isolation radius")
    L.append("")
    if not np.isfinite(b0["eta_robust"]):
        dead = [b for b, v in b0["eta_bands"].items() if not np.isfinite(v)]
        L.append(f"!! NO USABLE FIELD: no pointing gives a comparison star in {', '.join(dead)} "
                 f"under these rules. See NEAR MISSES; relax the rules, shorten the exposures, "
                 f"or use a larger read-out area.")
        L.append("")
    L.append("RECOMMENDED POINTING")
    L.append(f"  field centre   {_hms(b0['ra'], b0['dec'])}")
    L.append(f"  TCSPD Precise offset (from the target):  RA.cos(dec) = {b0['dE']:+.1f}\"   "
             f"DEC = {b0['dN']:+.1f}\"")
    L.append(f"      i.e. move the field centre {abs(b0['dE']):.0f}\" {'E' if b0['dE'] >= 0 else 'W'} "
             f"and {abs(b0['dN']):.0f}\" {'N' if b0['dN'] >= 0 else 'S'} of the target")
    px, py = b0["target_pixel"]["r"]
    L.append(f"  sign check: after GO the target should sit near x={px:.0f}, y={py:.0f} "
             f"(channel 2, 0-based, raw orientation).")
    L.append(f"      near x={1023 - px:.0f} instead -> flip the RA sign; "
             f"near y={1023 - py:.0f} instead -> flip the DEC sign.")
    if s.pointing_unc > 0:
        tol = (f"(holds to {b0['eta_robust']:.3f} anywhere within {s.pointing_unc:g}\"; "
               f"same result for a further {b0['plateau_depth']:.0f}\" of pointing error)")
    else:
        tol = (f"(unchanged for {b0['plateau_depth']:.0f}\" of pointing error"
               + ("; refine the pointing at the telescope)" if b0["plateau_depth"] < 10 else ")"))
    L.append(f"  noise penalty eta = sigma_diff/sigma_target: worst band {b0['eta_nominal']:.3f} {tol}")
    L.append("    per band: " + "  ".join(f"{b} {v:.3f}" for b, v in b0["eta_bands"].items()))
    L.append("")
    L.append("COMPARISON STARS at the recommended pointing")
    used = sorted(set().union(*[set(v.tolist()) for v in res.comps.values()]),
                  key=lambda i: res.stars["phot_g_mean_mag"][i])
    L.append(f"  {'':3} {'Gaia DR3':>19} {'G':>6} {'V':>6} {'dBP-RP':>7} {'w':>4} {'dist':>6}  "
             + "  ".join(f"{'d' + b:>5} {'pk%':>4}" for b in s.bands) + "  bands")
    tab = res.stars
    for k, i in enumerate(used, 1):
        row = f"  {'C' + str(k):3} {tab['source_id'][i]:>19d} {tab['phot_g_mean_mag'][i]:6.2f} " \
              f"{tab['V'][i]:6.2f} " \
              f"{tab['bp_rp'][i] - t['bp_rp']:+7.2f} {res._weight[i]:4.2f} " \
              f"{math.hypot(tab['E'][i], tab['N'][i]):5.0f}\"  "
        cells, bands = [], ""
        for b in s.bands:
            dm = tab[f"{b}_sdss_mag"][i] - t["mags"][b]
            cells.append(f"{dm:+5.2f} {res._peaks[b][i] / sat:4.0%}")
            bands += b if i in res.comps[b] else ("~" if i in res._smeared[b] else ".")
        L.append(row + "  ".join(cells) + "  " + bands + ("  (mag est.)" if tab["mag_est"][i] else ""))
    if not used:
        L.append("  none -- relax the rules or change the exposure times")
    if any(i in res._smeared[b] for i in used for b in s.bands):
        L.append("  ~ in bands: on another star's readout trail in that band, so not used there.")
    if any(w < 1 for w in res._weight[used]) if used else False:
        L.append("  w < 1: colour differs from the target's -- detrend the light curve against airmass"
                 " (mostly in g).")
    capped = getattr(res, "_capped", set())
    if capped:
        L.append(f"  + {len(capped)} more stars qualify here; the limit keeps the {s.max_comps} that add most "
                 f"weight (--max-comps N, 0 = all)")
    L.append("")
    L.append("NEAR MISSES: the brightest stars inside the field that are not used, and why")
    ce, cn = b0["dE"], b0["dN"]
    inside = edge_distance(res.common, tab["E"] - ce, tab["N"] - cn)
    G = np.asarray(tab["phot_g_mean_mag"])
    miss = [i for i in np.argsort(G) if i != t["index"] and i not in used
            and inside[i] > -s.edge_margin][:10]
    for i in miss:
        why = list(res.reasons[i])
        sm = [b for b in s.bands if i in res._smeared[b]]
        if sm:
            why.append(f"on a readout trail in {''.join(sm)}")
        if i in getattr(res, "_capped", ()):
            why.append(f"not among the {s.max_comps} best (comparison limit)")
        elif not why or inside[i] < s.edge_margin:
            why += ([f"outside the field by {-inside[i]:.0f}\" (edge margin {s.edge_margin:g}\")"
                     if inside[i] < 0 else
                     f"{inside[i]:.0f}\" inside the edge (needs {s.edge_margin:g}\")"]
                    if inside[i] < s.edge_margin else ["outside a channel"])
        L.append(f"     {tab['source_id'][i]:>19d} G={G[i]:5.2f}: " + "; ".join(why))
    if not miss:
        L.append("     none")
    L.append("")
    L.append("GUIDER")
    gc = res.guider["candidates"]
    if gc:
        g = gc[0]
        L.append(f"  Gaia DR3 {g['source_id']}  G={g['G']:.2f}  r={g['r']:.0f}\"  PA={g['pa']:.1f} deg")
        L.append(f"  -> Guider Setup angle {g['angle']:.1f} deg   (PA_box = 180 + angle)")
        for g in gc[1:4]:
            L.append(f"     alternative: G={g['G']:.2f} at angle {g['angle']:.1f} deg")
    else:
        L.append(f"  no star with {s.guide_gmin} <= G <= {s.guide_gmax} in the patrol annulus")
    L.append(f"  (guide-star range G {s.guide_gmin}-{s.guide_gmax} is a placeholder)")
    if res.compared:
        L.append("")
        L.append("OTHER POINTINGS, same rules")
        for c in res.compared:
            L.append(f"  {c['label']:<12} offset ({c['dE']:+6.1f}\", {c['dN']:+6.1f}\")  "
                     f"eta {c['eta_nominal']:.3f}  robust {c['eta_robust']:.3f}   "
                     + "  ".join(f"{b} {v:.3f}" for b, v in c["eta_bands"].items()))
    L.append("")
    L.append("Notes: eta = 1 would mean the comparison ensemble adds no noise.")
    if t["has_pm"]:
        L.append(f"Positions moved from Gaia's 2016.0 to epoch {t['epoch']:.2f} with Gaia proper "
                 f"motions (target: {t['pm'][0]:+.1f}, {t['pm'][1]:+.1f} mas/yr).")
    else:
        L.append("!! This CSV has no pmra/pmdec: positions are Gaia's 2016.0. Re-download it")
        L.append("   with the query from --adql to include proper motions.")
    L.append("Counts use the flux scale measured on 2026-08-01 (" +
             ", ".join(f"{b} x{v}" for b, v in s.flux_scale.items()) + " vs the ETC).")
    return "\n".join(L)


# ============================================================================
# Star list of the chosen field
# ============================================================================

def comp_labels(res: Result):
    """{star index: 'C1', 'C2', ...}: comparison stars numbered by brightness
    (G), the same numbering as the report table and the finder chart."""
    used = sorted(set().union(*[set(v.tolist()) for v in res.comps.values()]),
                  key=lambda i: res.stars["phot_g_mean_mag"][i])
    return {int(i): f"C{k}" for k, i in enumerate(used, 1)}


def field_table(res: Result):
    """Every Gaia star whose image falls on at least one channel at the
    recommended pointing, brightest first, as an astropy Table.

    Per star: identity, position at the observing epoch, offsets from the
    target and from the field centre, Gaia photometry and quality. Per band
    (g, r, i, z): predicted pixel on that channel (0-based, raw orientation;
    plus the second image in polarimetric mode), whether it is on the
    channel, predicted peak [ADU] at fwhm_best and noise per exposure [ppt],
    whether it is used as a comparison, and whether it lies on a readout
    trail. Reasons lists every rule the star failed.
    """
    from astropy.table import Table
    s, b0, tab, t = res.settings, res.best, res.stars, res.target
    ce, cn = b0["dE"], b0["dN"]
    E, N = np.asarray(tab["E"]), np.asarray(tab["N"])
    fe, fn = E - ce, N - cn
    on_any = np.zeros(len(tab), bool)
    for b in BANDS:
        on_any |= edge_distance(res.per[b], fe, fn) > 0
    idx = np.where(on_any)[0]
    idx = idx[np.argsort(np.asarray(tab["phot_g_mean_mag"])[idx])]

    out = Table()
    out["source_id"] = np.asarray(tab["source_id"])[idx]
    out["label"] = [comp_labels(res).get(int(i), "target" if i == t["index"] else "")
                    for i in idx]
    out["role"] = ["target" if i == t["index"] else
                   ("comparison" if any(i in res.comps.get(b, ()) for b in s.bands) else "")
                   for i in idx]
    out["ra_deg"] = np.round(np.asarray(tab["ra"], float)[idx], 7)
    out["dec_deg"] = np.round(np.asarray(tab["dec"], float)[idx], 7)
    out["dE_target_arcsec"] = np.round(E[idx], 2)
    out["dN_target_arcsec"] = np.round(N[idx], 2)
    out["dE_field_arcsec"] = np.round(fe[idx], 2)
    out["dN_field_arcsec"] = np.round(fn[idx], 2)
    out["G"] = np.round(np.asarray(tab["phot_g_mean_mag"], float)[idx], 3)
    out["bp_rp"] = np.round(np.asarray(tab["bp_rp"], float)[idx], 3)
    out["V"] = np.round(np.asarray(tab["V"], float)[idx], 3)
    out["V_source"] = np.asarray(tab["V_source"])[idx]
    for b in BANDS:
        out[f"{b}_mag"] = np.round(np.asarray(tab[f"{b}_sdss_mag"], float)[idx], 3)
    out["mags_estimated"] = np.asarray(tab["mag_est"])[idx]
    out["ruwe"] = np.round(np.asarray(tab["ruwe"], float)[idx], 3)
    if tab.meta.get("lite"):                      # the reduced catalogue has no flag
        out["gaia_variable"] = ["unknown"] * len(idx)
    else:
        out["gaia_variable"] = [str(v).strip().upper() == "VARIABLE"
                                for v in np.asarray(tab["phot_variable_flag"])[idx]]
    out["colour_weight"] = np.round(res._weight[idx], 2)
    ve, vn = _twin(s)
    for b in BANDS:
        f = pixel_affine(s.size, b)
        x, y = f(fe[idx], fn[idx])
        out[f"x_{b}"], out[f"y_{b}"] = np.round(x, 1), np.round(y, 1)
        if s.mode == "polar":
            x2, y2 = f(fe[idx] + ve, fn[idx] + vn)
            out[f"x2_{b}"], out[f"y2_{b}"] = np.round(x2, 1), np.round(y2, 1)
        out[f"on_{b}"] = edge_distance(res.per[b], fe[idx], fn[idx]) > 0
        if b in s.bands:
            out[f"peak_adu_{b}"] = np.round(res._peaks[b][idx], 0)
            out[f"noise_ppt_{b}"] = np.round(1e3 * res._sigma[b][idx], 3)
            out[f"used_{b}"] = np.isin(idx, res.comps[b])
            out[f"trail_{b}"] = np.isin(idx, res._smeared[b])
    reasons = []
    d_common = edge_distance(res.common, fe, fn)
    used_any = set().union(*[set(np.asarray(v).tolist()) for v in res.comps.values()])
    for i in idx:
        why = list(res.reasons[i])
        sm = [b for b in s.bands if i in res._smeared[b]]
        if sm:
            why.append(f"on a readout trail in {''.join(sm)}")
        if i in getattr(res, "_capped", ()):
            why.append(f"not among the {s.max_comps} best (comparison limit)")
        elif (not t.get("blank") and i != t["index"] and i not in used_any
                and (not why or d_common[i] < s.edge_margin)):
            why.append(f"{d_common[i]:.0f}\" from the field edge (needs {s.edge_margin:g}\")"
                       if d_common[i] >= 0 else "outside the area all four channels share")
            if s.mode == "polar" and d_common[i] >= s.edge_margin:
                why[-1] = "its second image is too close to the field edge"
        reasons.append("; ".join(why))
    out["reasons"] = reasons
    out.meta.update({"s4field": __version__, "field_ra": b0["ra"], "field_dec": b0["dec"],
                     "offset_ra_cosdec_arcsec": ce, "offset_dec_arcsec": cn,
                     "epoch": t["epoch"], "mode": s.mode, "size": s.size,
                     "t_exp": dict(s.t_exp), "t_exp_estimated": s.texp_estimated,
                     "fwhm_best": s.fwhm_best, "lite": bool(tab.meta.get("lite"))})
    return out


def write_field_csv(res: Result, path):
    """Write field_table(res) as a plain CSV. A sidecar is not needed: the
    first lines are '#' comments with the pointing and setup."""
    out = field_table(res)
    m = out.meta
    head = [f"# s4field {m['s4field']} star list",
            f"# field centre {_hms(m['field_ra'], m['field_dec'])}"
            f" (RA {m['field_ra']:.6f}, Dec {m['field_dec']:.6f} deg)",
            f"# TCSPD offset from the target: RA.cos(dec) {m['offset_ra_cosdec_arcsec']:+.1f}\""
            f"  DEC {m['offset_dec_arcsec']:+.1f}\"",
            f"# epoch {m['epoch']:.3f}; mode {m['mode']}; read-out {m['size']}; t_exp "
            + (", ".join(f"{b}={v:g}s" for b, v in m["t_exp"].items())
               if all(math.isfinite(v) for v in m["t_exp"].values()) else "not given")
            + (" (ESTIMATED)" if m["t_exp_estimated"] else "")
            + f"; peaks at {m['fwhm_best']}\" seeing",
            "# pixels: 0-based, raw orientation (N up, E left); x2/y2 = second image (polarimetric)"]
    if m["lite"]:
        head.append(f"# !! {LITE_CAVEAT}")
    import io
    buf = io.StringIO()
    out.meta = {}
    out.write(buf, format="ascii.csv")
    with open(path, "w") as f:
        f.write("\n".join(head) + "\n" + buf.getvalue())
    return len(out)


# ============================================================================
# Figures (return Figure objects only)
# ============================================================================

def finder_chart(res: Result, title=None):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MPoly, Circle
    s, b0, tab = res.settings, res.best, res.stars
    ce, cn = b0["dE"], b0["dN"]
    E, N = np.asarray(tab["E"]) - ce, np.asarray(tab["N"]) - cn
    G = np.asarray(tab["phot_g_mean_mag"])
    fig, axes = plt.subplots(1, 2, figsize=(13, 6.4), gridspec_kw={"width_ratios": [1.15, 1]})

    for ax, lim, wide in ((axes[0], 230, False), (axes[1], GUIDER_R_OUT + 60, True)):
        sel = (np.abs(E) < lim) & (np.abs(N) < lim)
        ax.scatter(E[sel], N[sel], s=np.clip(3 * 10 ** (0.25 * (17.5 - G[sel])), 2, 400),
                   c="0.25", lw=0, zorder=2)
        for b in BANDS:
            ax.add_patch(MPoly(res.per[b], closed=True, fill=False, lw=0.6, ls=":", ec="0.5"))
        ax.add_patch(MPoly(res.common, closed=True, fill=False, lw=1.6, ec="tab:blue", zorder=3))
        ax.plot(-ce, -cn, "+", ms=16, mew=2, color="tab:red", zorder=5)
        ax.set_aspect("equal")
        ax.set_xlim(lim, -lim)                      # East to the left
        ax.set_ylim(-lim, lim)
        ax.set_xlabel("East of field centre [arcsec]")
        ax.set_ylabel("North of field centre [arcsec]")
        if wide:
            for r in (GUIDER_R_IN, GUIDER_R_OUT):
                ax.add_patch(Circle((0, 0), r, fill=False, lw=0.8, ec="tab:orange"))
            gc = res.guider["candidates"]
            if gc:
                g = gc[0]
                a = math.radians(g["pa"])
                ux, uy = math.sin(a), math.cos(a)          # radial unit vector
                vx, vy = uy, -ux
                h = GUIDER_BOX / 2
                cx, cy = GUIDER_R_MID * ux, GUIDER_R_MID * uy
                box = [(cx + p * h * ux + q * h * vx, cy + p * h * uy + q * h * vy)
                       for p, q in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
                ax.add_patch(MPoly(box, closed=True, fill=False, lw=1.4, ec="tab:orange"))
                i = g["index"]
                ax.plot(E[i], N[i], "o", mfc="none", mec="tab:orange", ms=14, mew=1.5)
                ax.text(E[i], N[i] - 60, f"guide G={g['G']:.1f}\nangle {g['angle']:.1f}°",
                        ha="center", va="top", fontsize=8, color="tab:orange")
            ax.set_title("Guider patrol annulus", fontsize=10)
        else:
            inset = []
            used = sorted(set().union(*[set(v.tolist()) for v in res.comps.values()]),
                          key=lambda i: G[i])
            for k, i in enumerate(used, 1):
                ax.add_patch(Circle((E[i], N[i]), 9, fill=False, ec="tab:green", lw=1.5, zorder=4))
                bands = "".join(b for b in s.bands if i in res.comps[b])
                ax.text(E[i] - 11, N[i] + 11, f"C{k} {bands}", fontsize=8, color="tab:green")
            ax.text(-ce - 11, -cn + 11, "position" if res.target.get("blank") else "target",
                    fontsize=8, color="tab:red")
            # the brightest unused stars inside the field, with the first reason
            # (same list as NEAR MISSES in the report)
            inside = edge_distance(res.common, E, N) > -s.edge_margin
            miss = [] if res.target.get("blank") else [   # no comparisons chosen: no near misses
                i for i in np.argsort(G) if inside[i] and i != res.target["index"] and i not in used][:8]
            for i in miss:
                sm = "".join(b for b in s.bands if i in res._smeared[b])
                why = (res.reasons[i][0] if res.reasons[i] else
                       f"trail in {sm}" if sm else
                       "limit" if i in getattr(res, "_capped", ()) else "edge")
                ax.add_patch(Circle((E[i], N[i]), 9, fill=False, ec="0.55", lw=1, ls="--", zorder=4))
                ax.text(E[i] - 11, N[i] - 11, why, fontsize=7, color="0.45", va="top")
            ax.plot([lim - 20, lim - 80], [-lim + 20] * 2, "k-", lw=1.5)
            ax.text(lim - 50, -lim + 26, "1'", ha="center", fontsize=8)
            ax.set_title("Science field (blue: all 4 channels; dotted: each channel)", fontsize=10)
        c0, tip = 0.62 * lim, 0.84 * lim            # upper-left corner on screen
        ax.annotate("", xy=(c0, tip), xytext=(c0, c0), arrowprops=dict(arrowstyle="->", lw=1))
        ax.annotate("", xy=(tip, c0), xytext=(c0, c0), arrowprops=dict(arrowstyle="->", lw=1))
        ax.text(c0, tip + 0.03 * lim, "N", ha="center", va="bottom", fontsize=8)
        ax.text(tip + 0.03 * lim, c0, "E", ha="right", va="center", fontsize=8)
    fig.suptitle(title or f"Field centre {_hms(b0['ra'], b0['dec'])}  |  offset "
                 f"RA.cosδ {b0['dE']:+.1f}\"  DEC {b0['dN']:+.1f}\"", fontsize=11)
    fig.tight_layout()
    return fig


def score_map(res: Result, vmax=3.0):
    import matplotlib.pyplot as plt
    s, b0 = res.settings, res.best
    fig, ax = plt.subplots(figsize=(6.6, 5.6))
    g = res.grid_e[0]
    im = ax.imshow(np.clip(res.score, 1, vmax), origin="lower", cmap="viridis_r",
                   extent=[g[0], g[-1], g[0], g[-1]], vmin=1, vmax=vmax, aspect="equal")
    ax.set_xlim(g[-1], g[0])
    cb = fig.colorbar(im, ax=ax, shrink=0.85)
    cb.set_label(f"worst-band η, robust to ±{s.pointing_unc:g}\"  (1 = no added noise; "
                 f"top = ≥{vmax:g} or no comparison in a band)", fontsize=8)
    ax.plot(b0["dE"], b0["dN"], "*", ms=16, mfc="white", mec="k", label="recommended")
    for c, mk in zip(res.compared, "osD^v"):
        ax.plot(c["dE"], c["dN"], mk, ms=9, mfc="none", mec="tab:red", mew=1.5, label=c["label"])
    ax.plot(0, 0, "+", ms=12, color="k", label="field centred on target")
    ax.set_xlabel("field centre East of target [arcsec]")
    ax.set_ylabel("field centre North of target [arcsec]")
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()
    return fig


# ============================================================================
# Target resolution
# ============================================================================
#
# Order, stopping at the first that answers:
#   1. coordinates                          offline
#   2. Gaia DR3 source_id, looked up in the CSV   offline
#   3. Sesame name resolver, CfA mirror first, Strasbourg second   network
#   4. NASA Exoplanet Archive (TOI, TIC, planet host names)        network
# The network is only touched for names, and never with --offline.

SESAME_MIRRORS = [
    "http://vizier.cfa.harvard.edu/viz-bin/nph-sesame/",   # CfA (unaffected by the CDS incident)
    "https://cds.unistra.fr/cgi-bin/nph-sesame/",          # Strasbourg
]
EXOPLANET_TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
NETWORK_TIMEOUT = 10.0           # s, per server
SESAME_RETRY_AFTER = 1800.0      # s: after Sesame times out, skip it this long (helper)
_SESAME_DOWN_UNTIL = 0.0


_GAIA_RE = _re.compile(r"^\s*(?:gaia\s*(?:dr3)?\s*)?(\d{15,20})\s*$", _re.I)
_TOI_RE = _re.compile(r"^\s*toi[\s\-_]*(\d+)(?:\.(\d+))?\s*$", _re.I)
_TIC_RE = _re.compile(r"^\s*tic[\s\-_]*(\d+)\s*$", _re.I)


class ResolveError(ValueError):
    pass


@dataclass
class Resolution:
    text: str          # what the user typed
    ra: float          # deg (ICRS)
    dec: float
    source: str        # who answered
    note: str = ""
    catalogue: str = ""  # where the Gaia cone came from


def parse_coordinates(text):
    """'hh:mm:ss.s -dd:mm:ss', 'hh mm ss -dd mm ss' or 'ra dec' (deg).
    Returns (ra, dec) in deg, or None if the text is not coordinates."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    t = text.replace(",", " ").split()
    if len(t) not in (2, 6) or not all(_re.fullmatch(r"[+\-]?[\d.:]+", w) for w in t):
        return None
    unit = (u.hourangle, u.deg) if ":" in text or len(t) == 6 else (u.deg, u.deg)
    try:
        c = SkyCoord(" ".join(t), unit=unit)
    except ValueError:
        return None
    return float(c.ra.deg), float(c.dec.deg)


def resolve_gaia_id(text, tab):
    """Gaia DR3 source_id -> (ra, dec) from the CSV. None if not an ID."""
    m = _GAIA_RE.match(text)
    if not m:
        return None
    sid = int(m.group(1))
    if tab is None:
        raise ResolveError(f"Gaia DR3 {sid}: give --gaia so it can be looked up offline.")
    hit = np.where(np.asarray(tab["source_id"]) == sid)[0]
    if hit.size == 0:
        raise ResolveError(f"Gaia DR3 {sid} is not in this CSV -- is the cone centred on it?")
    return float(tab["ra"][hit[0]]), float(tab["dec"][hit[0]])


def resolve_sesame(name, mirrors=SESAME_MIRRORS, timeout=NETWORK_TIMEOUT):
    """Name -> (ra, dec, server) via Sesame, trying mirrors in order."""
    from astropy.coordinates import SkyCoord, name_resolve
    from astropy.utils.data import conf as data_conf
    errors = []
    for url in mirrors:
        try:
            with name_resolve.sesame_url.set([url]), \
                    data_conf.set_temp("remote_timeout", timeout):
                c = SkyCoord.from_name(name, parse=False, cache=False)
            return float(c.ra.deg), float(c.dec.deg), url
        except Exception as e:                     # NameResolveError, timeouts, DNS
            errors.append(f"{url}: {type(e).__name__}")
    raise ResolveError("Sesame: " + "; ".join(errors))


def _tap_csv(query, timeout=NETWORK_TIMEOUT):
    import csv, io, urllib.parse, urllib.request
    url = EXOPLANET_TAP + "?" + urllib.parse.urlencode({"query": query, "format": "csv"})
    req = urllib.request.Request(url, headers={"User-Agent": "s4field"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
    return list(csv.DictReader(io.StringIO(text)))


def exoplanet_query(name):
    """ADQL for the NASA Exoplanet Archive, or None if the name has no form we know.
    TOI-2631 / TOI-2631.01 / TIC 123 -> 'toi' table; anything else -> host name
    in 'pscomppars'."""
    m = _TOI_RE.match(name)
    if m:
        n = int(m.group(1))
        if m.group(2):
            cond = f"toi = {n}.{m.group(2)}"
        else:
            cond = f"toi >= {n} and toi < {n + 1}"
        return f"select toi, tid, ra, dec from toi where {cond} order by toi"
    m = _TIC_RE.match(name)
    if m:
        return f"select toi, tid, ra, dec from toi where tid = {int(m.group(1))} order by toi"
    host = name.strip().replace("'", "''")
    return (f"select hostname, ra, dec from pscomppars "
            f"where lower(hostname) = lower('{host}')")


def resolve_exoplanet_archive(name, timeout=NETWORK_TIMEOUT):
    rows = _tap_csv(exoplanet_query(name), timeout=timeout)
    rows = [r for r in rows if r.get("ra") not in (None, "") and r.get("dec") not in (None, "")]
    if not rows:
        raise ResolveError("NASA Exoplanet Archive: no match")
    r = rows[0]
    label = (f"TOI {r['toi']} / TIC {r['tid']}" if "toi" in r else f"host {r['hostname']}")
    return float(r["ra"]), float(r["dec"]), label


def resolve_target(text, tab=None, offline=False):
    """Turn whatever the user typed into a Resolution (see the order above)."""
    xy = parse_coordinates(text)
    if xy:
        return Resolution(text, *xy, "coordinates")
    m = _GAIA_RE.match(text)
    if m and tab is None and not offline:                 # no CSV yet: ask the archive
        sid = int(m.group(1))
        try:
            rows = _csv_rows(gaia_tap(f"SELECT ra, dec FROM gaiadr3.gaia_source "
                                      f"WHERE source_id = {sid}"))
            where = "Gaia Archive"
        except GaiaArchiveError:                          # ESA down: GAVO's copy
            rows = _csv_rows(gavo_tap(f"SELECT ra, dec FROM gaia.dr3lite "
                                      f"WHERE source_id = {sid}"))
            where = "GAVO"
        if not rows:
            raise ResolveError(f"Gaia DR3 {sid} does not exist in the {where} catalogue.")
        return Resolution(text, float(rows[0]["ra"]), float(rows[0]["dec"]),
                          f"Gaia DR3 source_id ({where})")
    xy = resolve_gaia_id(text, tab)
    if xy:
        return Resolution(text, *xy, "Gaia DR3 source_id (CSV, offline)")
    if offline:
        raise ResolveError(f"'{text}' is a name; --offline allows only coordinates or a Gaia DR3 ID.")
    global _SESAME_DOWN_UNTIL
    tried = []
    if _time.time() < _SESAME_DOWN_UNTIL:          # failed by timing out a moment ago
        tried.append("Sesame: skipped (timed out recently)")
    else:
        t0 = _time.monotonic()
        try:
            ra, dec, url = resolve_sesame(text)
            where = "CfA mirror" if "harvard" in url else "Strasbourg"
            return Resolution(text, ra, dec, f"Sesame ({where})")
        except ResolveError as e:
            tried.append(str(e))
            if _time.monotonic() - t0 >= 0.9 * NETWORK_TIMEOUT:   # timeouts, not "unknown name"
                _SESAME_DOWN_UNTIL = _time.time() + SESAME_RETRY_AFTER
    try:
        ra, dec, label = resolve_exoplanet_archive(text)
        return Resolution(text, ra, dec, "NASA Exoplanet Archive", label)
    except ResolveError as e:
        tried.append(str(e))
    except Exception as e:                         # network errors
        tried.append(f"NASA Exoplanet Archive: {e}")
    raise ResolveError(f"Could not resolve '{text}'.\n  " + "\n  ".join(tried) +
                       "\n  Give coordinates or a Gaia DR3 source_id instead.")


# ============================================================================
# Gaia Archive download and local cache
# ============================================================================
#
# With no --gaia file, the cone is fetched from the ESA Gaia Archive (TAP,
# asynchronous job: no small row cap, unlike quick synchronous queries) and
# saved as  <cache-dir>/cone_<target>.csv  plus a .json with the resolved
# position, the query and the date. The next run for the same target reads
# both from disk -- no name lookup, no download, works with --offline.

GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap"
GAIA_TIMEOUT = 30.0             # s per HTTP request
GAIA_MAX_WAIT = 300.0           # s for the job to finish
GAIA_POLL = 2.0                 # s between status checks


class GaiaArchiveError(RuntimeError):
    pass


def _http(url, data=None, timeout=GAIA_TIMEOUT):
    """GET (data None) or POST form data. Returns (final URL, text)."""
    import urllib.parse, urllib.request
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": f"s4field/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl(), r.read().decode("utf-8", "replace")


def _csv_rows(text):
    import csv, io
    return list(csv.DictReader(io.StringIO(text)))


def gaia_tap(adql, base=GAIA_TAP, max_wait=GAIA_MAX_WAIT, poll=GAIA_POLL, sleep=None):
    """Run an ADQL query as an asynchronous job on the Gaia Archive; return CSV text."""
    import time
    sleep = time.sleep if sleep is None else sleep
    try:
        url, body = _http(base + "/async", {"REQUEST": "doQuery", "LANG": "ADQL",
                                            "FORMAT": "csv", "PHASE": "RUN", "QUERY": adql})
    except Exception as e:
        raise GaiaArchiveError(f"Gaia Archive unreachable ({base}): {e}") from e
    job = url.rstrip("/")
    if job.endswith("/async"):                       # no redirect followed: read the job id
        jid = _re.search(r"<uws:jobId>\s*([^<\s]+)\s*</uws:jobId>", body)
        if not jid:
            raise GaiaArchiveError("Gaia Archive: could not read the job id.")
        job = f"{base}/async/{jid.group(1)}"
    waited = 0.0
    while True:
        phase = _http(job + "/phase")[1].strip().upper()
        if phase == "COMPLETED":
            break
        if phase in ("ERROR", "ABORTED"):
            msg = _re.search(r"<uws:message>(.*?)</uws:message>", _http(job)[1], _re.S)
            raise GaiaArchiveError(f"Gaia Archive job {phase}: "
                                   + (msg.group(1).strip() if msg else "no message"))
        if waited >= max_wait:
            raise GaiaArchiveError(f"Gaia Archive job still {phase} after {max_wait:.0f} s ({job}).")
        sleep(poll)
        waited += poll
    return _http(job + "/results/result")[1]


GAVO_TAP = "https://dc.g-vo.org/tap"
GAVO_MAXREC = 100000


def gavo_tap(adql, base=GAVO_TAP, maxrec=GAVO_MAXREC):
    """Run an ADQL query synchronously on the GAVO Data Center; return CSV text.
    (A cone of a few thousand stars comes back in ~2 s; GAVO's async jobs
    were seen to sit queued for minutes, so sync is the better route.)"""
    try:
        _, text = _http(base + "/sync", {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                         "MAXREC": str(maxrec), "QUERY": adql})
    except Exception as e:
        raise GaiaArchiveError(f"GAVO unreachable ({base}): {e}") from e
    if text.lstrip().startswith("<"):                 # a VOTable error document
        msg = _re.search(r"<INFO[^>]*name=\"QUERY_STATUS\"[^>]*>(.*?)</INFO>", text, _re.S)
        raise GaiaArchiveError("GAVO query failed: "
                               + (msg.group(1).strip() if msg else text[:200]))
    if max(0, len(text.strip().splitlines()) - 1) >= maxrec:
        raise GaiaArchiveError(f"GAVO returned {maxrec} rows, its limit: the cone is truncated.")
    return text


def cache_slug(text):
    """File-name stem for a target: 'AU Mic' -> 'au_mic'; coordinates are
    written out in degrees so the sign of the declination survives."""
    xy = parse_coordinates(text)
    if xy:
        return f"ra{xy[0]:.4f}_dec{xy[1]:+.4f}".replace("+", "p").replace("-", "m")
    m = _GAIA_RE.match(text)
    if m:
        return f"gaia_dr3_{m.group(1)}"
    return _re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "target"


SOURCES = ("auto", "esa", "gavo")


def _cache_paths(cache_dir, text, lite):
    import os
    stem = os.path.join(cache_dir, "cone_" + cache_slug(text) + ("_lite" if lite else ""))
    return stem + ".csv", stem + ".json"


REUSE_RADIUS = 60.0       # ["] a cached cone centred this close serves typed coordinates


def _read_cached(text, cache_dir, lite, need=0.0):
    """(table, Resolution) from the cache, or None if this cone is not cached
    (or is smaller than `need` [deg]: a loaded FoV larger than SPARC4's).
    Coordinates also match a cone cached under another name (a star looked
    up by name, then typed as RA/Dec) if its centre is within REUSE_RADIUS."""
    import glob, json, os
    csv_path, meta_path = _cache_paths(cache_dir, text, lite)
    xy = parse_coordinates(text)
    if not (os.path.exists(csv_path) and os.path.exists(meta_path)) and xy:
        best = None
        for mp in glob.glob(os.path.join(cache_dir, "cone_*.json")):
            if mp.endswith("_lite.json") != lite:
                continue
            try:
                with open(mp) as f:
                    m = json.load(f)
                d = float(np.hypot(*tangent_offsets(np.array([m["ra"]]), np.array([m["dec"]]), *xy))[0])
            except (OSError, ValueError, KeyError):
                continue
            if d <= REUSE_RADIUS and os.path.exists(mp[:-5] + ".csv") and (best is None or d < best[0]):
                best = (d, mp[:-5] + ".csv", mp)
        if best:
            csv_path, meta_path = best[1], best[2]
    if not (os.path.exists(csv_path) and os.path.exists(meta_path)):
        return None
    with open(meta_path) as f:
        meta = json.load(f)
    have = float(meta.get("radius_deg", CONE_RADIUS))
    if have < need:
        return None
    tab = read_gaia(csv_path)
    if xy:                                   # the position asked for, not the cone's centre
        rz = Resolution(text, *xy, "coordinates")
    else:
        rz = Resolution(text, meta["ra"], meta["dec"], f"cache ({meta['source']})", meta.get("note", ""))
    rz.catalogue = (f"{csv_path}: {len(tab)} stars"
                    + (" (reduced, GAVO)" if lite else "")
                    + (f", radius {have:g} deg" if have != CONE_RADIUS else "")
                    + f", downloaded {meta['downloaded'][:10]} (--refresh to download again)")
    rz.radius_deg = have
    return tab, rz


def cached_resolution(text, cache_dir="cones"):
    """The position saved with a cached cone for `text`, or None (no network)."""
    import json, os
    for lite in (False, True):
        meta_path = _cache_paths(cache_dir, text, lite)[1]
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            return Resolution(text, meta["ra"], meta["dec"], f"cache ({meta['source']})",
                              meta.get("note", ""))
    return None


def _download(text, rz, cache_dir, lite, radius=CONE_RADIUS):
    import json, os
    from datetime import datetime, timezone
    csv_path, meta_path = _cache_paths(cache_dir, text, lite)
    where = "GAVO (reduced Gaia DR3)" if lite else "the Gaia Archive"
    adql = (ADQL_LITE if lite else ADQL).format(ra=rz.ra, dec=rz.dec, radius=radius)
    print(f"Downloading the Gaia DR3 cone for '{text}' (radius {radius:g} deg) from {where} ...",
          file=sys.stderr, flush=True)
    text_csv = gavo_tap(adql) if lite else gaia_tap(adql)
    n = max(0, len(text_csv.strip().splitlines()) - 1)
    if n == 0:
        raise GaiaArchiveError(f"{where} returned no stars for this cone.")
    os.makedirs(cache_dir, exist_ok=True)
    for path, content in ((csv_path, text_csv),
                          (meta_path, json.dumps({
                              "target": text, "ra": rz.ra, "dec": rz.dec, "source": rz.source,
                              "note": rz.note, "rows": n, "s4field": __version__,
                              "catalogue": "lite" if lite else "full", "radius_deg": radius,
                              "service": GAVO_TAP if lite else GAIA_TAP,
                              "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "adql": adql}, indent=1))):
        tmp = path + ".part"
        with open(tmp, "w") as f:
            f.write(content)
        os.replace(tmp, path)                       # never leave a half-written file
    tab = read_gaia(csv_path)
    rz.catalogue = (f"{csv_path}: {n} stars" + (f", radius {radius:g} deg" if radius != CONE_RADIUS else "")
                    + f", downloaded now from {where}")
    rz.radius_deg = radius
    return tab, rz


def obtain_field(text, cache_dir="cones", offline=False, refresh=False, source="auto",
                 radius=CONE_RADIUS, need=0.0):
    """Catalogue and target position for `text`, from the cache or the network.
    Returns (table, Resolution).

    source "esa": the full cone from the ESA Gaia Archive. "gavo": the reduced
    cone from GAVO. "auto" (default): the full cone if it is cached or ESA
    answers; otherwise the reduced one (cached, or downloaded from GAVO). A
    cached reduced cone is upgraded as soon as ESA answers again.

    radius [deg]: the cone to download; a cached cone serves if it is at least
    `need` [deg] (a larger FoV than SPARC4's). If none is large enough and the
    download fails, the largest cached one is used (its note says so).
    """
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    order = {"esa": [False], "gavo": [True], "auto": [False, True]}[source]
    if not refresh:
        hit = _read_cached(text, cache_dir, lite=False, need=need) if source != "gavo" else None
        if hit:
            return hit
        if offline or source == "gavo":
            hit = _read_cached(text, cache_dir, lite=True, need=need) if source != "esa" else None
            if hit:
                return hit
    if offline:
        small = _smaller_cached(text, cache_dir, source, need)
        if small:
            return small
        raise ResolveError(f"No cached cone for '{text}' in {cache_dir}/. "
                           f"Run once without --offline, or give --gaia.")
    # a larger cone for a target already cached: its saved position, no new lookup
    rz = cached_resolution(text, cache_dir) if need else None
    if rz is not None:
        src = rz.source[7:-1] if rz.source.startswith("cache (") else rz.source
        rz = Resolution(text, rz.ra, rz.dec, src, rz.note)
    else:
        try:
            rz = resolve_target(text, None, offline=False)
        except ResolveError:
            small = _smaller_cached(text, cache_dir, source, need)
            if small:
                return small
            raise
    errors = []
    for lite in order:
        try:
            tab, rz2 = _download(text, rz, cache_dir, lite, radius)
        except GaiaArchiveError as e:
            errors.append(str(e))
            if lite is False and source == "auto":
                hit = None if refresh else _read_cached(text, cache_dir, lite=True, need=need)
                if hit:
                    print(f"{e}\nUsing the cached reduced cone.", file=sys.stderr)
                    return hit
                print(f"{e}\nTrying GAVO's reduced Gaia DR3 instead.", file=sys.stderr)
            continue
        if errors:
            rz2.catalogue += f" -- ESA archive failed: {errors[0]}"
        return tab, rz2
    small = None if refresh else _smaller_cached(text, cache_dir, source, need)
    if small:
        return small
    raise GaiaArchiveError("No Gaia cone could be downloaded.\n  " + "\n  ".join(errors))


def _smaller_cached(text, cache_dir, source, need):
    """A cached cone smaller than asked for (no larger one could be had):
    better than none; its note says how far it reaches."""
    if not need:
        return None
    for lite in ([False] if source == "esa" else [True] if source == "gavo" else [False, True]):
        hit = _read_cached(text, cache_dir, lite)
        if hit:
            tab, rz = hit
            rz.catalogue += (f" -- SMALLER than this FoV needs ({rz.radius_deg:g} of {need:.2f} deg): "
                             f"stars near its edge may be missing")
            return tab, rz
    return None


# ============================================================================
# Local helper for SkyBound (--serve)
# ============================================================================
#
# A browser page cannot read the ESA Gaia Archive, the NASA Exoplanet Archive
# or MAST (no CORS headers; tested 2026-11-05 from file:// and localhost).
# `s4field.py --serve` runs this script as a small HTTP API on 127.0.0.1 so
# SkyBound can use the full pipeline -- same catalogue, cache and rules as the
# command line. Read-only GET endpoints, JSON answers, CORS open to any page:
#
#   /api/status                     version, cache directory, cached cones
#   /api/resolve?target=AU+Mic      name/coordinates -> RA, Dec
#   /api/cone?target=AU+Mic         the Gaia cone (cached after the first call)
#   /api/field?target=AU+Mic&...    the recommended pointing, comparison stars,
#                                   footprints, star list and the text report
#   POST /api/fov                   another instrument's footprint (JSON
#                                   {"science": [regions], "name": ...}) -> {"fov": id}
#   /api/field?target=...&fov=id    that instrument's field: basic mode, in
#                                   band=V|G|g|r|i|z, margin, iso, twin=dE,dN
#
# /api/field takes the command-line options as parameters: texp=g,r,i,z
# (omit -> estimated), mode, size, acq, airmass, moon, sky, fwhm, fwhm_best,
# texp_max, margin, search_radius [arcmin], noise_ratio, pointing_unc,
# bands, smear=0, etc_flux=1, epoch, source, refresh=1.
# Gaia source_ids are sent as strings: they do not fit in a JavaScript number.

SERVE_PORT = 8417


def _jsonable(x):
    """Plain JSON types; NaN/inf -> null (JSON.parse rejects NaN)."""
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_jsonable(v) for v in (x.tolist() if isinstance(x, np.ndarray) else x)]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(x) else None
    if isinstance(x, (str, np.str_)):
        return str(x)
    if x is None or x is np.ma.masked:
        return None
    return str(x)


def _rows(table, id_cols=("source_id",)):
    cols = table.colnames
    out = []
    for r in table:
        d = {}
        for c in cols:
            v = r[c]
            d[c] = str(int(v)) if c in id_cols else _jsonable(v.item() if hasattr(v, "item") else v)
        out.append(d)
    return out


def cone_json(tab, rz):
    """The Gaia cone as JSON (for SkyBound to draw or score by itself)."""
    from astropy.table import Table
    keep = ["source_id", "ra", "dec", "pmra", "pmdec", "phot_g_mean_mag", "bp_rp", "ruwe",
            "phot_variable_flag", "non_single_star", "g_sdss_mag", "r_sdss_mag",
            "i_sdss_mag", "z_sdss_mag", "mag_est", "V", "V_source"]
    t = Table({c: tab[c] for c in keep if c in tab.colnames})
    lite = bool(tab.meta.get("lite"))
    return {"s4field": __version__, "epoch_positions": GAIA_EPOCH,
            "target": {"text": rz.text, "ra": rz.ra, "dec": rz.dec, "source": rz.source,
                       "note": rz.note},
            "catalogue": {"note": rz.catalogue, "lite": lite,
                          "caveat": LITE_CAVEAT if lite else ""},
            "stars": _rows(t)}


def result_json(res: Result):
    """Everything SkyBound needs to show a field: the pointing, the offsets,
    eta, the comparison stars, the footprints, the guide star, the star list
    (field_table) and the text report."""
    s, t, b = res.settings, res.target, res.best
    rz = t.get("resolution")
    lite = bool(res.stars.meta.get("lite"))
    labels = comp_labels(res)
    warnings = []
    blank = bool(t.get("blank"))
    if blank:
        warnings.append(blank_note(t))
    if s.texp_estimated and not s.basic:
        warnings.append("\n".join(_texp_warning(res)).strip())
    if lite:
        warnings.append(LITE_CAVEAT)
    if t["contaminated"]:
        warnings.append("The target itself lies on a readout trail of a brighter star.")
    if blank:
        pass                                     # no target star: nothing more to say
    elif not b.get("target_inside", True):
        warnings.append(f"At this pointing the target is outside the field, or closer than "
                        f"{s.edge_margin:g}\" to its edge: no light curve, so no eta.")
    else:
        dead = [k for k, v in b["eta_bands"].items() if not np.isfinite(v)]
        if dead:
            warnings.append(f"No usable comparison star in {', '.join(dead)} at this pointing, so no "
                            f"eta there. The star list says why each star in the field was not used.")
    out = {
        "s4field": __version__,
        "target": {"text": rz.text if rz else "",
                   "source_id": None if blank else str(t["source_id"]), "blank": blank,
                   "ra": t["ra"], "dec": t["dec"], "G": t["G"], "bp_rp": t["bp_rp"],
                   "mags": t["mags"], "epoch": t["epoch"], "match_sep_arcsec": t["match_sep"],
                   "resolved_by": rz.source if rz else "", "note": rz.note if rz else ""},
        "catalogue": {"note": rz.catalogue if rz else "", "lite": lite,
                      "caveat": LITE_CAVEAT if lite else ""},
        "settings": {"basic": s.basic, "filters": sorted(active_filters(s)), "max_comps": s.max_comps,
                     "dv_window": [None if not np.isfinite(v) else v for v in dv_window(s)],
                     "t_exp": dict(s.t_exp), "texp_estimated": s.texp_estimated,
                     "mode": s.mode, "size": s.size, "acq": s.acq, "airmass": s.airmass,
                     "moon": s.moon, "sky": s.sky, "fwhm": s.fwhm_plan,
                     "fwhm_best": s.fwhm_best, "edge_margin": s.edge_margin,
                     "search_radius_arcmin": s.search_radius / 60.0,
                     "noise_ratio": s.noise_ratio_max, "bands": "".join(s.bands)},
        "pointing": {"ra": b["ra"], "dec": b["dec"], "hms": _hms(b["ra"], b["dec"]),
                     "offset_ra_cosdec_arcsec": b["dE"], "offset_dec_arcsec": b["dN"],
                     "eta": b["eta_nominal"], "eta_robust": b["eta_robust"],
                     "eta_bands": b["eta_bands"], "target_pixel": b["target_pixel"],
                     "target_inside": b.get("target_inside", True),
                     "fixed": bool(getattr(res, "_fixed", False)),
                     "left_out": len(getattr(res, "_capped", ()))},   # qualify, but over max_comps
        "comparisons": {band: sorted((labels[int(i)] for i in res.comps[band]),
                                     key=lambda c: int(c[1:]))
                        for band in s.bands},
        "compared": res.compared,
        "guider": [{**g, "source_id": str(g["source_id"])} for g in res.guider["candidates"][:5]],
        "footprint": {"frame": "arcsec East, North of the field centre",
                      "bands": {band: np.asarray(res.per[band]).tolist() for band in BANDS},
                      "common": np.asarray(res.common).tolist(),
                      "polar_split_arcsec": list(_twin(s)) if s.mode == "polar" else None,
                      "guider": {"r_in": GUIDER_R_IN, "r_out": GUIDER_R_OUT,
                                 "r_box": GUIDER_R_MID, "box": GUIDER_BOX}},
        "warnings": warnings,
        "stars": _rows(field_table(res)),
        "sky": {"frame": "arcsec East, North of the target at the epoch",
                "E": np.round(np.asarray(res.stars["E"], float), 1).tolist(),
                "N": np.round(np.asarray(res.stars["N"], float), 1).tolist(),
                "G": np.round(np.asarray(res.stars["phot_g_mean_mag"], float), 2).tolist(),
                "id": [str(int(v)) for v in res.stars["source_id"]],
                "guide_g": [s.guide_gmin, s.guide_gmax]},
        "report": report(res),
    }
    return _jsonable(out)


def fov_table(res: Result):
    """A loaded FoV's star list: every Gaia star on the detector(s) at the
    recommended pointing, brightest first (list of dicts)."""
    s, b0, tab, t = res.settings, res.best, res.stars, res.target
    band = s.bands[0]
    ce, cn = b0["dE"], b0["dN"]
    E, N = np.asarray(tab["E"]), np.asarray(tab["N"])
    fe, fn = E - ce, N - cn
    geom = res._geometry
    on = geom.mask(0.0).inside(fe, fn)
    idx = np.where(on)[0]
    G = np.asarray(tab["phot_g_mean_mag"], float)
    idx = idx[np.argsort(G[idx])]
    labels = comp_labels(res)
    m = np.asarray(tab[mag_col(band)], float)
    mt = m[t["index"]] if not t.get("blank") else np.nan
    used = set(np.asarray(res.comps[band]).tolist())
    inner = res.common.inside(fe, fn)
    rows = []
    for i in idx:
        role = "target" if i == t["index"] else ("comparison" if i in used else "")
        why = list(res.reasons[i]) if role == "" else []
        if role == "" and not t.get("blank"):
            if i in getattr(res, "_capped", ()):
                why.append(f"not among the {s.max_comps} best (comparison limit)")
            elif not why and not inner[i]:
                why.append(f"closer than {s.edge_margin:g}\" to a detector edge")
        rows.append({"source_id": str(int(tab["source_id"][i])), "label": labels.get(int(i), "target" if role == "target" else ""),
                     "role": role, "ra_deg": float(tab["ra"][i]), "dec_deg": float(tab["dec"][i]),
                     "dE_target_arcsec": round(float(E[i]), 2), "dN_target_arcsec": round(float(N[i]), 2),
                     "dE_field_arcsec": round(float(fe[i]), 2), "dN_field_arcsec": round(float(fn[i]), 2),
                     "G": round(float(G[i]), 3), "bp_rp": round(float(tab["bp_rp"][i]), 3),
                     "V": round(float(tab["V"][i]), 3), "band": band, "mag": round(float(m[i]), 3),
                     "dmag": round(float(m[i] - mt), 3), "ruwe": round(float(tab["ruwe"][i]), 3),
                     "colour_weight": round(float(res._weight[i]), 2), "reasons": "; ".join(why)})
    return rows


def report_fov(res: Result, name=""):
    """The text report for a loaded FoV (basic mode, one band)."""
    s, b, t = res.settings, res.best, res.target
    band = s.bands[0]
    L = ["=" * 74, f"  Field finder for {name or 'a loaded FoV'}  (s4field {__version__}, basic, {band})", "=" * 74]
    rz = t.get("resolution")
    if t.get("blank"):
        L.append(f"  Position {rz.text if rz else ''}: no Gaia star there -- no comparison stars.")
    else:
        L.append(f"  Target   Gaia DR3 {t['source_id']}  G {t['G']:.2f}  BP-RP {t['bp_rp']:.2f}")
    L.append(f"  Field    {_hms(b['ra'], b['dec'])}   offset RA.cos(dec) {b['dE']:+.1f}\"  DEC {b['dN']:+.1f}\""
             + ("  (your pointing)" if getattr(res, "_fixed", False) else "  (recommended)"))
    if not t.get("blank"):
        eta = b["eta_bands"].get(band, float("nan"))
        L.append(f"  eta ({band}, photon noise) {eta:.3f}" if np.isfinite(eta) else f"  eta ({band}): no comparison")
        L.append(f"  Rules: dV window {dv_window(s)[0]:+.2f}..{dv_window(s)[1]:+.2f}; edge margin {s.edge_margin:g}\"; "
                 f"isolation {s.iso_radius or s.iso_fwhm * s.fwhm_plan:g}\"; filters {', '.join(sorted(active_filters(s) - {'radius'})) or 'none'}"
                 + (f"; beam split {math.hypot(*_twin(s)):.1f}\"" if s.mode == "polar" else ""))
        rows = [r for r in fov_table(res) if r["role"]]
        L.append("")
        L.append(f"  {'':8s} {'G':>6s} {band:>6s} {'d' + band:>7s} {'BP-RP':>6s} {'dE':>7s} {'dN':>7s}  Gaia DR3")
        for r in sorted(rows, key=lambda r: (r["role"] != "target", int(r["label"][1:]) if r["label"].startswith("C") else 0)):
            L.append(f"  {r['label']:8s} {r['G']:6.2f} {r['mag']:6.2f} {r['dmag']:+7.2f} {r['bp_rp']:6.2f} "
                     f"{r['dE_target_arcsec']:+7.1f} {r['dN_target_arcsec']:+7.1f}  {r['source_id']}")
    return "\n".join(L)


def result_json_fov(res: Result, name=""):
    """What SkyBound needs to show a loaded FoV's field: the pointing, eta in
    the band, the comparison stars, the star list and a short report."""
    s, t, b = res.settings, res.target, res.best
    band = s.bands[0]
    rz = t.get("resolution")
    lite = bool(res.stars.meta.get("lite"))
    blank = bool(t.get("blank"))
    labels = comp_labels(res)
    warnings = []
    if blank:
        warnings.append(blank_note(t))
    if lite:
        warnings.append(LITE_CAVEAT)
    if rz and "SMALLER than this FoV needs" in rz.catalogue:
        warnings.append("The Gaia cone is smaller than this FoV needs: stars near the edge of the search may be "
                        "missing. A larger one is being downloaded in the background; the field is computed "
                        "again when it arrives.")
    if res.common.area == 0:
        warnings.append(f"This FoV is too small for the {s.edge_margin:g}\" edge margin: no star can be that far "
                        f"inside it, so there are no comparison stars. Lower the margin under Regions... if you need them.")
    elif not blank and not b.get("target_inside", True):
        warnings.append(f"At this pointing the target is not on a detector, or closer than {s.edge_margin:g}\" "
                        f"to an edge: no light curve, so no eta.")
    elif not blank and not np.isfinite(b["eta_bands"].get(band, np.nan)):
        warnings.append("No usable comparison star at this pointing, so no eta. The star list says why each "
                        "star in the field was not used.")
    tm = (lambda c: None if blank else float(res.stars[c][t["index"]]))
    out = {
        "s4field": __version__, "generic": True,
        "target": {"text": rz.text if rz else "", "source_id": None if blank else str(t["source_id"]),
                   "blank": blank, "ra": t["ra"], "dec": t["dec"], "G": t["G"], "bp_rp": t["bp_rp"],
                   "V": tm("V"), "mag": tm(mag_col(band)), "epoch": t["epoch"],
                   "match_sep_arcsec": t["match_sep"], "resolved_by": rz.source if rz else ""},
        "catalogue": {"note": rz.catalogue if rz else "", "lite": lite, "caveat": LITE_CAVEAT if lite else "",
                      "radius_deg": getattr(rz, "radius_deg", CONE_RADIUS)},
        "settings": {"basic": True, "band": band, "filters": sorted(active_filters(s)),
                     "max_comps": s.max_comps, "edge_margin": s.edge_margin,
                     "iso_radius": s.iso_radius or s.iso_fwhm * s.fwhm_plan,
                     "dv_window": [None if not np.isfinite(v) else v for v in dv_window(s)],
                     "twin": list(_twin(s)) if s.mode == "polar" else None},
        "pointing": {"ra": b["ra"], "dec": b["dec"], "hms": _hms(b["ra"], b["dec"]),
                     "offset_ra_cosdec_arcsec": b["dE"], "offset_dec_arcsec": b["dN"],
                     "eta": b["eta_nominal"], "eta_bands": b["eta_bands"],
                     "target_inside": b.get("target_inside", True),
                     "fixed": bool(getattr(res, "_fixed", False)),
                     "left_out": len(getattr(res, "_capped", ()))},
        "comparisons": sorted((labels[int(i)] for i in res.comps[band]), key=lambda c: int(c[1:])),
        "warnings": warnings,
        "stars": fov_table(res),
        "sky": {"frame": "arcsec East, North of the target at the epoch",
                "E": np.round(np.asarray(res.stars["E"], float), 1).tolist(),
                "N": np.round(np.asarray(res.stars["N"], float), 1).tolist(),
                "G": np.round(np.asarray(res.stars["phot_g_mean_mag"], float), 2).tolist(),
                "id": [str(int(v)) for v in res.stars["source_id"]]},
        "report": report_fov(res, name),
    }
    return _jsonable(out)


def fov_settings_from_query(q):
    """Settings for a loaded FoV: the basic-mode options of settings_from_query,
    plus band (V, G, g, r, i, z), iso [arcsec] and twin = dE,dN [arcsec]."""
    from dataclasses import replace
    q = {k: v for k, v in q.items() if k not in ("bands", "mode", "texp")}
    s = settings_from_query({**q, "basic": "1"})
    band = q.get("band", "V")
    if band not in GENERIC_BANDS:
        raise ValueError(f"band must be one of {', '.join(GENERIC_BANDS)}")
    kw = {"bands": (band,), "search_radius": 1e9, "smear": False}
    if q.get("iso", "") != "":
        kw["iso_radius"] = float(q["iso"])
        if not kw["iso_radius"] > 0:
            raise ValueError("iso must be > 0 [arcsec]")
    if q.get("twin"):
        v = [float(x) for x in q["twin"].split(",")]
        if len(v) != 2 or not all(map(math.isfinite, v)):
            raise ValueError("twin needs dE,dN [arcsec]")
        kw["twin"], kw["mode"] = tuple(v), "polar"
    return replace(s, **kw)


_FLOAT_PARAMS = {"airmass": "airmass", "fwhm": "fwhm_plan", "fwhm_best": "fwhm_best",
                 "texp_max": "texp_max", "margin": "edge_margin",
                 "noise_ratio": "noise_ratio_max", "pointing_unc": "pointing_unc",
                 "dmag_bright": "dmag_bright", "dmag_faint": "dmag_faint"}
_CHOICE_PARAMS = {"mode": ("mode", ("phot", "polar")), "size": ("size", tuple(FOOTPRINT)),
                  "acq": ("acq", tuple(ACQ)), "moon": ("moon", tuple(M_SKY)),
                  "sky": ("sky", tuple(EXTINCTION))}


def settings_from_query(q):
    """Settings from URL parameters (dict of str). Unknown values -> ValueError."""
    kw = {}
    if q.get("texp"):
        v = [float(x) for x in q["texp"].split(",")]
        if len(v) != 4:
            raise ValueError("texp needs 4 values: g,r,i,z")
        kw["t_exp"] = dict(zip(BANDS, v))
    for p, f in _FLOAT_PARAMS.items():
        if q.get(p, "") != "":
            kw[f] = float(q[p])
    for p, (f, ok) in _CHOICE_PARAMS.items():
        if q.get(p):
            v = q[p].upper() if p in ("size", "acq") else q[p].lower()
            if v not in ok:
                raise ValueError(f"{p} must be one of {', '.join(ok)}")
            kw[f] = v
    if q.get("search_radius", "") != "":
        kw["search_radius"] = 60.0 * float(q["search_radius"])
    if q.get("basic") in ("1", "true", "yes"):
        kw["basic"] = True
    if "filters" in q:
        f = [x for x in q["filters"].split(",") if x and x != "none"]
        bad = [x for x in f if x not in FILTERS]
        if bad:
            raise ValueError(f"unknown filter(s) {', '.join(bad)}; known: {', '.join(FILTERS)}")
        kw["filters"] = frozenset(f)
    if q.get("dv"):
        v = q["dv"].split(",")
        if len(v) != 2:
            raise ValueError("dv needs two values: min,max (empty = no limit)")
        kw["dv_min"] = float(v[0]) if v[0].strip() else None
        kw["dv_max"] = float(v[1]) if v[1].strip() else None
    if q.get("max_comps", "") != "":
        n = int(q["max_comps"])
        if n < 0:
            raise ValueError("max_comps must be 0 (no limit) or more")
        kw["max_comps"] = n or None
    if q.get("smear") in ("0", "false", "no"):
        kw["smear"] = False
    if q.get("bands"):
        if not set(q["bands"]) <= set(BANDS):
            raise ValueError("bands: letters from griz")
        kw["bands"] = tuple(q["bands"])
    s = Settings(**kw)
    if q.get("etc_flux") in ("1", "true", "yes"):
        s.flux_scale = {b: 1.0 for b in BANDS}
    return s


UPGRADE_EVERY = 900                     # s: the helper retries ESA for a reduced cone at most this often


def cached_cone(text, cache_dir, source="auto", need=0.0):
    """(table, Resolution, lite) from the cache alone -- no network -- or None.
    The full cone first (unless source is "gavo"), then the reduced one
    (unless source is "esa"); at least `need` [deg] in radius."""
    for lite in ([False] if source == "esa" else [True] if source == "gavo" else [False, True]):
        hit = _read_cached(text, cache_dir, lite, need)
        if hit:
            return (*hit, lite)
    return None


def make_handler(cache_dir="cones", source="auto"):
    import json, threading, urllib.parse
    from http.server import BaseHTTPRequestHandler
    lock = threading.Lock()                     # one foreground download at a time
    state = threading.Lock()
    upgrading, last_try = set(), {}
    fovs = {}                                   # id -> (FovGeometry, name), the most recent 64

    def upgrade_later(text, radius=CONE_RADIUS):
        """Only the reduced (GAVO) cone is cached: fetch the full one from ESA in
        the background, at most every UPGRADE_EVERY s per target, so no request
        waits for ESA (it can take minutes, or fail). The next request after it
        arrives uses it."""
        key = text.strip().lower()
        with state:
            if key in upgrading or _time.time() - last_try.get(key, 0) < UPGRADE_EVERY:
                return
            upgrading.add(key)
            last_try[key] = _time.time()

        def work():
            try:
                rz = _read_cached(text, cache_dir, True)[1]
                src = rz.source[7:-1] if rz.source.startswith("cache (") else rz.source
                rz = Resolution(text, rz.ra, rz.dec, src, rz.note)
                _download(text, rz, cache_dir, False, radius)
                print(f"[s4field] full Gaia cone for '{text}' cached in the background: "
                      f"the next request uses it", file=sys.stderr, flush=True)
            except Exception as e:                   # ESA down or slow: keep the reduced cone
                first = str(e).strip().splitlines()[0][:160] if str(e).strip() else type(e).__name__
                print(f"[s4field] full Gaia cone for '{text}' not available ({first}); using the "
                      f"reduced cone, next try in {UPGRADE_EVERY // 60} min", file=sys.stderr, flush=True)
            finally:
                with state:
                    upgrading.discard(key)
        threading.Thread(target=work, daemon=True).start()

    def enlarge_later(text, radius, src):
        """A loaded FoV needs a larger cone than the cached one: answer from the
        cached one now and download the larger one in the background (same
        throttle as upgrade_later), so no request waits for the archive."""
        key = "larger|" + text.strip().lower()
        with state:
            if key in upgrading or _time.time() - last_try.get(key, 0) < UPGRADE_EVERY:
                return
            upgrading.add(key)
            last_try[key] = _time.time()

        def work():
            try:
                rz = cached_resolution(text, cache_dir)
                rz = Resolution(text, rz.ra, rz.dec,
                                rz.source[7:-1] if rz.source.startswith("cache (") else rz.source, rz.note)
                errors = []
                for lite in ([False] if src == "esa" else [True] if src == "gavo" else [False, True]):
                    try:
                        _download(text, rz, cache_dir, lite, radius)
                        print(f"[s4field] larger Gaia cone ({radius:g} deg) for '{text}' cached in the "
                              f"background: the next request uses it", file=sys.stderr, flush=True)
                        return
                    except GaiaArchiveError as e:
                        errors.append(str(e).strip().splitlines()[0][:120] if str(e).strip() else "failed")
                raise GaiaArchiveError("; ".join(errors))
            except Exception as e:
                print(f"[s4field] larger Gaia cone for '{text}' not available ({str(e)[:200]}); using the "
                      f"cached one, next try in {UPGRADE_EVERY // 60} min", file=sys.stderr, flush=True)
            finally:
                with state:
                    upgrading.discard(key)
        threading.Thread(target=work, daemon=True).start()

    def cone(q, geom=None):
        src, text = q.get("source", source), q["target"]
        refresh, offline = q.get("refresh") == "1", q.get("offline") == "1"
        radius = geom.cone_radius() if geom else CONE_RADIUS
        need = geom.need() * 0.95 if geom else 0.0
        if not refresh:                              # the cache: no network, no waiting for others
            hit = cached_cone(text, cache_dir, src, need)
            if hit:
                tab, rz, lite = hit
                if lite and src == "auto" and not offline:
                    upgrade_later(text, radius)
                return tab, rz
            small = _smaller_cached(text, cache_dir, src, need) if need else None
            if small:                                # too small for this FoV: use it now, enlarge later
                if not offline:
                    enlarge_later(text, radius, src)
                return small
        with lock:
            return obtain_field(text, cache_dir, refresh=refresh, source=src, offline=offline,
                                radius=radius, need=need)

    class Handler(BaseHTTPRequestHandler):
        server_version = f"s4field/{__version__}"

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            # Chrome's checks for public pages calling a local server
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", "no-store")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_POST(self):
            u = urllib.parse.urlsplit(self.path)
            if u.path != "/api/fov":
                return self._send(404, {"error": f"unknown endpoint {u.path}"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > 20_000_000:
                    raise ValueError("send the FoV as JSON, under 20 MB")
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                regions = body.get("science") if isinstance(body, dict) else None
                geom = FovGeometry(regions)
                import hashlib
                fid = hashlib.sha1(json.dumps(regions, sort_keys=True).encode()).hexdigest()[:16]
                with state:
                    fovs.pop(fid, None)
                    fovs[fid] = (geom, str(body.get("name", ""))[:120])
                    while len(fovs) > 64:
                        fovs.pop(next(iter(fovs)))
                return self._send(200, {"fov": fid, "regions": len(geom.regions),
                                        "extent_arcsec": geom.extent,
                                        "cone_radius_deg": geom.cone_radius()})
            except (ValueError, KeyError, TypeError) as e:
                return self._send(400, {"error": f"FoV: {e}"})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
            try:
                if u.path in ("/api/status", "/api", "/"):
                    import glob, os
                    paths = glob.glob(os.path.join(cache_dir, "cone_*.csv"))
                    cached = sorted(os.path.basename(p)[5:-4] for p in paths)
                    updated = max((os.path.getmtime(p) for p in paths), default=0.0)
                    return self._send(200, {"s4field": __version__, "cache_dir":
                                            os.path.abspath(cache_dir), "source": source,
                                            "cached": cached, "updated": updated})
                if u.path not in ("/api/resolve", "/api/cone", "/api/field"):
                    return self._send(404, {"error": f"unknown endpoint {u.path}"})
                if not q.get("target"):
                    return self._send(400, {"error": "parameter 'target' is required"})
                if u.path == "/api/resolve":
                    rz = (cached_resolution(q["target"], cache_dir)
                          or resolve_target(q["target"], None, offline=q.get("offline") == "1"))
                    return self._send(200, _jsonable({"text": rz.text, "ra": rz.ra,
                                                      "dec": rz.dec, "hms": _hms(rz.ra, rz.dec),
                                                      "source": rz.source, "note": rz.note}))
                if u.path == "/api/cone":
                    tab, rz = cone(q)
                    return self._send(200, cone_json(tab, rz))
                geom, name = None, ""
                if q.get("fov"):                         # another instrument (POST /api/fov first)
                    with state:
                        geom, name = fovs.get(q["fov"], (None, ""))
                    if geom is None:
                        return self._send(404, {"error": "unknown fov: send it again (POST /api/fov)",
                                                "code": "unknown_fov"})
                    s = fov_settings_from_query(q)
                else:
                    s = settings_from_query(q)
                tab, rz = cone(q, geom)
                epoch = float(q["epoch"]) if q.get("epoch") else None
                at = None
                if q.get("at"):
                    at = tuple(float(v) for v in q["at"].split(","))
                    if len(at) != 2:
                        raise ValueError("at needs two numbers: dE,dN [arcsec]")
                res = run(tab, rz.ra, rz.dec, s, resolution=rz, epoch=epoch,
                          match_radius=float(q.get("match_radius", 2.0)), at=at,
                          allow_blank=q.get("blank", "1") not in ("0", "false", "no"), geometry=geom)
                return self._send(200, result_json_fov(res, name) if geom else result_json(res))
            except (ResolveError, ValueError) as e:
                return self._send(400, {"error": str(e)})
            except GaiaArchiveError as e:
                return self._send(502, {"error": str(e)})
            except Exception as e:                       # keep serving
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def log_message(self, fmt, *args):
            print(f"[s4field] {self.address_string()} {fmt % args}", file=sys.stderr)

    return Handler


def serve(port=SERVE_PORT, cache_dir="cones", source="auto", ready=None):
    """Run the helper until Ctrl-C. Listens on 127.0.0.1 only."""
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cache_dir, source))
    print(f"s4field {__version__} helper on http://127.0.0.1:{httpd.server_port}/api/status "
          f"(cache: {cache_dir}/, catalogue: {source}). Ctrl-C to stop.", file=sys.stderr, flush=True)
    if ready is not None:
        ready(httpd)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


# ============================================================================
# CLI
# ============================================================================

def _coord(text):
    xy = parse_coordinates(text)
    if xy is None:
        raise ValueError(f"not coordinates: {text!r}")
    return xy


def main(argv=None):
    ap = argparse.ArgumentParser(description="SPARC4 comparison-star field finder (see S4FIELD.md)")
    ap.add_argument("--version", action="version", version=f"s4field {__version__}")
    ap.add_argument("--gaia", help="use this Gaia DR3 CSV instead of downloading (see --adql)")
    ap.add_argument("--cache-dir", default="cones",
                    help="where downloaded cones are kept (default: ./cones)")
    ap.add_argument("--refresh", action="store_true",
                    help="download the cone again even if it is cached")
    ap.add_argument("--source", default="auto", choices=SOURCES,
                    help="where to download the Gaia cone: esa (full), gavo (reduced: magnitudes "
                         "from colour relations, no variability flags) or auto (default: esa, "
                         "gavo if the ESA archive cannot be reached)")
    ap.add_argument("--serve", action="store_true",
                    help="run the local helper for SkyBound (an HTTP API on 127.0.0.1) "
                         "instead of a single field")
    ap.add_argument("--port", type=int, default=SERVE_PORT, help="port for --serve")
    ap.add_argument("--target",
                    help='coordinates ("hh:mm:ss.s -dd:mm:ss" or "ra dec" deg), a Gaia DR3 '
                         'source_id, or a name (TOI-2631, TIC 123, HD 1234, WASP-18)')
    ap.add_argument("--offline", action="store_true",
                    help="never use the network (names are then refused)")
    ap.add_argument("--epoch", type=float, default=None,
                    help="observing epoch as a decimal year (default: today); "
                         "positions are moved there with Gaia proper motions")
    ap.add_argument("--match-radius", type=float, default=2.0,
                    help="max distance [\"] between the target position and its Gaia star")
    ap.add_argument("--blank", action="store_true",
                    help="no Gaia star at the position (a pointing test, an empty field): show the "
                         "field there anyway, without comparison stars")
    ap.add_argument("--texp", nargs=4, type=float, metavar=("G", "R", "I", "Z"),
                    help="exposure times [s] in g r i z (your choice). If omitted, they are "
                         "estimated from the target's brightness and the report says so")
    ap.add_argument("--texp-max", type=float, default=60.0,
                    help="longest estimated exposure [s] (only without --texp)")
    ap.add_argument("--acq", default="NORMAL FAINT", choices=list(ACQ))
    ap.add_argument("--size", default="LARGE", choices=list(FOOTPRINT))
    ap.add_argument("--mode", default="phot", choices=["phot", "polar"])
    ap.add_argument("--airmass", type=float, default=1.2)
    ap.add_argument("--moon", default="new", choices=list(M_SKY))
    ap.add_argument("--sky", default="photometric", choices=list(EXTINCTION))
    ap.add_argument("--fwhm", type=float, default=2.5, help="typical seeing [\"]")
    ap.add_argument("--fwhm-best", type=float, default=1.2, help="best seeing for saturation [\"]")
    ap.add_argument("--pointing-unc", type=float, default=0.0,
                    help="pointing error [\"] the choice must survive (default 0: you refine the pointing at the telescope)")
    ap.add_argument("--margin", type=float, default=12.0, help="edge margin [\"], both images")
    ap.add_argument("--search-radius", type=float, default=7.0,
                    help="comparison candidates within this many arcmin of the target")
    ap.add_argument("--noise-ratio", type=float, default=10.0,
                    help="a comparison's noise per exposure may be at most this x the target's")
    ap.add_argument("--dmag-bright", type=float, default=None,
                    help="optional: comparison at most this many mag brighter (default: "
                         "saturation decides)")
    ap.add_argument("--dmag-faint", type=float, default=None,
                    help="optional: comparison at most this many mag fainter (default: "
                         "--noise-ratio decides)")
    ap.add_argument("--filters", default=None,
                    help="comma-separated rules to apply (default: all): " + ", ".join(FILTERS)
                         + "; 'none' = magnitudes only")
    ap.add_argument("--dv", nargs=2, type=float, default=None, metavar=("MIN", "MAX"),
                    help="window in V_comp - V_target [mag] for comparison stars")
    ap.add_argument("--max-comps", type=int, default=6, metavar="N",
                    help="at most N comparison stars, those adding most weight (default 6; 0 = all)")
    ap.add_argument("--basic", action="store_true",
                    help="rank comparison stars by brightness alone (photon noise; no "
                         "exposure, sky or airmass model): the stars closest in brightness win")
    ap.add_argument("--no-smear", action="store_true",
                    help="ignore readout-smear trails")
    ap.add_argument("--bands", default="griz")
    ap.add_argument("--etc-flux", action="store_true",
                    help="use the ETC spreadsheet flux scale instead of the measured one")
    ap.add_argument("--compare", nargs=2, action="append", default=[],
                    metavar=("LABEL", "COORD"), help='score another pointing, e.g. planned "22:00:16 -48:41:54"')
    ap.add_argument("--out", default=None, help="prefix for PNG figures")
    ap.add_argument("--adql", action="store_true", help="print the ADQL query for --target and exit")
    a = ap.parse_args(argv)
    if a.serve:
        return serve(a.port, a.cache_dir, a.source)
    if not a.target:
        ap.error("--target is required (or --serve)")

    try:
        if a.gaia:
            tab = read_gaia(a.gaia)
            rz = resolve_target(a.target, tab, offline=a.offline)
            rz.catalogue = f"{a.gaia}: {len(tab)} stars (given with --gaia)"
        elif a.adql:
            tab, rz = None, resolve_target(a.target, None, offline=a.offline)
        else:
            tab, rz = obtain_field(a.target, a.cache_dir, offline=a.offline, refresh=a.refresh,
                                   source=a.source)
    except (ResolveError, GaiaArchiveError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.adql:
        print(f"-- {a.target}: {_hms(rz.ra, rz.dec)} via {rz.source}"
              + (f" ({rz.note})" if rz.note else ""))
        print((ADQL_LITE if a.source == "gavo" else ADQL).format(ra=rz.ra, dec=rz.dec, radius=CONE_RADIUS))
        return 0
    s = Settings(t_exp=dict(zip(BANDS, a.texp)) if a.texp else None, texp_max=a.texp_max,
                 acq=a.acq, size=a.size, mode=a.mode,
                 airmass=a.airmass, moon=a.moon, sky=a.sky, fwhm_plan=a.fwhm,
                 fwhm_best=a.fwhm_best, pointing_unc=a.pointing_unc, edge_margin=a.margin,
                 dmag_bright=a.dmag_bright, dmag_faint=a.dmag_faint,
                 search_radius=60 * a.search_radius, noise_ratio_max=a.noise_ratio,
                 smear=not a.no_smear, bands=tuple(a.bands), basic=a.basic,
                 filters=(None if a.filters is None else
                          frozenset(x for x in a.filters.split(",") if x and x != "none")),
                 dv_min=a.dv[0] if a.dv else None, dv_max=a.dv[1] if a.dv else None,
                 max_comps=a.max_comps if a.max_comps and a.max_comps > 0 else None)
    if a.etc_flux:
        s.flux_scale = {b: 1.0 for b in BANDS}
    compare = [(lab, *_coord(c)) for lab, c in a.compare]
    try:
        res = run(tab, rz.ra, rz.dec, s, compare, match_radius=a.match_radius,
                  resolution=rz, epoch=a.epoch, allow_blank=a.blank)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(report(res))
    if a.out:
        finder_chart(res).savefig(f"{a.out}_finder.png", dpi=130)
        figs = [f"{a.out}_finder.png"]
        if not res.target.get("blank"):          # no eta to map at an empty position
            score_map(res).savefig(f"{a.out}_score.png", dpi=130)
            figs.append(f"{a.out}_score.png")
        n = write_field_csv(res, f"{a.out}_field.csv")
        print(f"\nfigures: {', '.join(figs)}")
        print(f"star list: {a.out}_field.csv ({n} stars on the detector)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
