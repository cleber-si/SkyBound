# s4field — SPARC4 comparison-star field finder

`s4field.py` chooses where to point the OPD 1.6 m telescope so that SPARC4 gets the best comparison stars for differential photometry in g, r, i and z at the same time. It replaces the manual procedure of moving an Aladin window around by eye.

For one target it reports:

- the field centre and the **TCSPD Precise offset** to apply after pointing at the target (RA·cos δ and DEC, in arcsec);
- the pixel where the target should land, so the offset's sign convention can be checked on the first try;
- the comparison stars in each band, and why near-matches were rejected;
- a guide star and the **Guider Setup angle**;
- a finder chart and a map of how good every possible pointing is.

Since 0.13 it also ranks comparison stars for **any other instrument** whose field of view SkyBound has loaded (an Aladin FoV file or its New FoV form), in basic mode and one band: section 12b.

Version 0.13.1 · By Cleber Silva · UFC/MALTED

---

## Contents

1. [Requirements](#1-requirements)
2. [Quick start](#2-quick-start)
3. [Giving the target](#3-giving-the-target)
4. [Getting the Gaia catalogue](#4-getting-the-gaia-catalogue)
5. [Command-line options](#5-command-line-options)
6. [Reading the output](#6-reading-the-output)
7. [At the telescope](#7-at-the-telescope)
8. [How the field is chosen](#8-how-the-field-is-chosen)
9. [Where the numbers come from](#9-where-the-numbers-come-from)
10. [Validation](#10-validation)
11. [Limitations and open items](#11-limitations-and-open-items)
12. [Using it from Python](#12-using-it-from-python)
12a. [The local helper for SkyBound](#12a-the-local-helper-for-skybound)
12b. [Any instrument's field of view](#12b-any-instruments-field-of-view)
13. [Tests](#13-tests)
14. [Version history](#14-version-history)

---

## 1. Requirements

- Python 3.12 or later
- `numpy`, `astropy`, `matplotlib`

```bash
pip install numpy astropy matplotlib
```

The script is a single file with no other dependencies.

## 2. Quick start

The target is all you need to give:

```bash
python3 s4field.py --target "AU Mic" --out aumic
```

Without `--texp`, the exposure times are estimated from the target's brightness, and the report says so in a warning block (section 8, *Estimated exposure times*). When you know the exposure times you will use, give them, because they decide which comparison stars are too bright:

```bash
python3 s4field.py --target "AU Mic" --texp 5 1 1 1 --out aumic
```

On the first run for a target, the script:

1. resolves the name to coordinates (section 3);
2. downloads the Gaia DR3 cone around it from the ESA Gaia Archive (section 4);
3. saves both in `./cones/` (`cone_au_mic.csv` and `cone_au_mic.json`);
4. prints the report and writes `aumic_finder.png` and `aumic_score.png`.

Later runs for the same target read `./cones/`, so they need no network and work with `--offline`, for example at the telescope without internet. Use `--refresh` to download again.

## 3. Giving the target

`--target` accepts any of the following:

| You type | Example | Resolved by | Network |
|---|---|---|---|
| Sexagesimal coordinates | `"22:00:24.76 -48:42:37.69"` or `"22 00 24.76 -48 42 37.69"` | parsed directly | no |
| Decimal degrees | `"330.1032 -48.7105"` | parsed directly | no |
| Gaia DR3 source_id | `"Gaia DR3 6560684113691404160"` or just the number | the `--gaia` CSV if given, otherwise the Gaia Archive | only without `--gaia` |
| Any name Sesame knows | `"HD 1234"`, `"WASP-18"`, `"TOI-2631"` | Sesame (CDS's name resolver): **CfA mirror first**, Strasbourg second | yes |
| TOI, TIC, planet host | `"TOI-2631"`, `"TOI-2631.01"`, `"TIC 123456"`, `"WASP-18"` | NASA Exoplanet Archive, if Sesame fails | yes |

The tool works down this list and stops at the first answer. The report states which resolver answered and what position it returned.

**Network use.** The tool goes online only on the first run for a target: to resolve a name and to download the cone. Once both are cached, nothing leaves your machine. With `--offline`, it uses the cache (or `--gaia`) and never touches the network.

**Why the CfA mirror comes first.** The Strasbourg servers can be slow or down, as during the CDS security incident of October 2026. Trying Strasbourg first would then cost a timeout of up to 10 s on every run.

**Matching to Gaia.** The resolved position must lie within `--match-radius` (default 2″) of a Gaia star in your CSV; that star becomes the target. This catches a wrong identification.

**No star there (`--blank`).** A position with no Gaia star, such as a pointing test or an empty field, is refused unless you add `--blank` (the SkyBound helper always allows it). The field is then centred on the position itself, or at `at=dE,dN` from it, and you get the footprints, the guider and the star list. With no target star there is no brightness to compare with, so no comparison stars are chosen and there is no η. Star peaks and noise are listed only when you give `--texp`. The report and the warning name the nearest Gaia stars, so a mistyped star still stands out.

Coordinates from SIMBAD, Sesame or the Exoplanet Archive are usually for epoch 2000.0, Gaia's are for 2016.0, and you observe today. For a fast-moving star such as AU Mic these differ by 7″ or more. So the tool tests the match at all three epochs (2000.0, 2016.0 and the observing epoch), moving each Gaia star with its own proper motion. The report states which epoch matched.

## 3a. Proper motion

After the match, every star is moved from Gaia's 2016.0 position to the observing epoch (`--epoch`, default today), so the footprint, comparison stars and guider all use where the stars are now.

This needs the `pmra` and `pmdec` columns, which the query includes since version 0.2.0. An older CSV without them still works: the stars are then held at 2016.0, and the report warns you.

## 4. Getting the Gaia catalogue

### Automatic (default)

Without `--gaia`, the script downloads the cone itself from the ESA Gaia Archive (<https://gea.esac.esa.int/tap-server/tap>).

- **Asynchronous query.** It submits the query as an asynchronous job and checks every 2 s until it finishes, for up to 5 minutes. Asynchronous jobs have no small row limit, so a crowded field near the Galactic plane comes back complete.
- **Where files go.** The result is saved as `<cache-dir>/cone_<target>.csv`. Beside it, `cone_<target>.json` records:
  - the resolved position and which resolver gave it;
  - the number of stars and the download date;
  - the s4field version;
  - the exact query.
  
  The default cache directory is `./cones`; change it with `--cache-dir`.
- **File names** are built from what you typed, ignoring case and punctuation: `"AU Mic"`, `"au mic"` and `"AU_Mic"` all use `cone_au_mic.csv`. Coordinates are written in degrees, keeping the sign of the declination (`cone_ra330.1032_decm48.7105.csv`).
- **Safe saving.** Files are written to a temporary name first and renamed when complete, so an interrupted download never leaves a broken file. An empty result is not cached.
- **When to refresh.** The cache does not expire. Use `--refresh` after a new Gaia data release, or if you change the query.

### Manual

`--gaia my.csv` uses a CSV you downloaded yourself and skips the download and the cache. `--adql` prints the query, centred on your target, to paste into the archive's web page (*Search → Advanced (ADQL)*):

```sql
SELECT g.source_id, g.ra, g.dec, g.pmra, g.pmdec, g.phot_g_mean_mag, g.bp_rp,
       g.ruwe, g.phot_variable_flag, g.non_single_star,
       s.g_sdss_mag, s.r_sdss_mag, s.i_sdss_mag, s.z_sdss_mag, s.v_jkc_mag
FROM gaiadr3.gaia_source AS g
LEFT JOIN gaiadr3.synthetic_photometry_gspc AS s ON g.source_id = s.source_id
WHERE 1 = CONTAINS(POINT('ICRS', g.ra, g.dec),
                   CIRCLE('ICRS', <ra>, <dec>, 0.27))
  AND g.phot_g_mean_mag < 18
```

- **Radius 0.27°** covers three things: the guider patrol annulus (outer box corner 765″ from the field centre), any pointing offset (up to about 190″), and a margin.
- **Synthetic SDSS photometry** (`synthetic_photometry_gspc`) gives g, r, i, z on the SDSS system, which matches SPARC4's natural system. Stars without it (G ≳ 17.65, or no XP spectra) get g, r, i, z estimated from a cubic in BP−RP, fitted to the other stars in the same file. They are marked "(mag est.)" in the report.
- The Gaia Archive is hosted at ESAC, not at CDS, so it is not affected by CDS outages.
- **Each target needs its own cone**, because the cone is centred on it. The automatic download takes care of this; if you download by hand, name the files after the targets.

### When the ESA archive is down: the reduced catalogue (GAVO)

`--source` chooses where the cone comes from:

- `auto` (default): the full cone from ESA. If ESA cannot be reached, the script says so and uses the reduced cone from the GAVO Data Center (<https://dc.g-vo.org/tap>, table `gaia.dr3lite`) instead. It is cached as `cone_<target>_lite.csv`. A cached full cone always wins, and as long as only the reduced cone is cached, each command-line run tries ESA first, so the full cone takes over as soon as ESA answers again. The SkyBound helper never makes a request wait for ESA: with a cached cone (full or reduced) it answers from the cache at once, and for a reduced one it tries ESA in the background, at most every 15 minutes per target; the request after the full cone arrives uses it.
- `esa`: the full cone only; fail if ESA is down.
- `gavo`: the reduced cone only.

`gaia.dr3lite` is Gaia DR3 with fewer columns: positions, proper motions, G, BP, RP and RUWE are there; the synthetic photometry and the `phot_variable_flag` and `non_single_star` columns are not. The script then:

- estimates g, r, i, z for every star from G and BP−RP with fixed cubic relations (`SDSS_POLY`), fitted on Gaia's synthetic photometry of the AU Mic and TOI-2631 cones (892 stars, 0.12 < BP−RP < 3.08; a straight-line continuation outside that range);
- takes V from the Riello et al. (2021) relation;
- cannot reject stars Gaia flags as variable or non-single, so the report, the star list (`gaia_variable` = `unknown`) and the CSV header carry a warning.

**Tested on 2026-11-06.** The relations were fitted on one field and tested on the other: bias ≤ 0.013 mag, core scatter 0.005–0.016 mag (1.48 × MAD), rms including outliers 0.035–0.058 mag. Every bright (G < 15) star off by more than 0.1 mag had RUWE > 1.4 or no RUWE, so the RUWE rule already rejects it. Rerunning both fields with the reduced catalogue (with the given and with estimated exposure times):

| Field | Pointing shift | η full / reduced | Comparison stars |
|---|---|---|---|
| AU Mic (polarimetric) | 0.0″ | 2.735 / 2.668 | same, plus one G = 14.0 star Gaia flags VARIABLE (g, i) |
| TOI-2631 | 0.0″ | 1.324 / 1.323 | identical in all four bands |

The pointing chosen from the reduced cone, scored with the full cone, has exactly the full-cone η. The one real loss is the variability flag. The GAVO cone of AU Mic has 673 stars against ESA's 670 with the same cuts.

GAVO is the only Gaia service tested that a web page can query directly (section 12a), which is why it is the fallback.

## 5. Command-line options

| Option | Default | Meaning |
|---|---|---|
| `--target` | (required) | Coordinates, Gaia DR3 source_id, or a name (section 3); not needed with `--serve` |
| `--gaia` | — | Use this Gaia CSV instead of downloading (section 4) |
| `--source` | auto | Where to download the cone: `esa` (full), `gavo` (reduced), `auto` (ESA, GAVO if ESA is down); section 4 |
| `--basic` | off | Rank comparison stars by brightness alone, with no exposure, sky or airmass model (section 8, "Basic mode") |
| `--max-comps` | 6 | At most N comparison stars, the ones that add most weight; the field is chosen for them (section 8, "Search and choice"). 0 = every star that qualifies |
| `--serve` | off | Run the local helper for SkyBound instead of one field (section 12a) |
| `--port` | 8417 | Port of the local helper |
| `--cache-dir` | `cones` | Where downloaded cones are kept |
| `--refresh` | off | Download the cone again even if cached |
| `--texp G R I Z` | estimated | Exposure times [s] in g, r, i, z. When given, the tool never changes them; when omitted, they are estimated (section 8) |
| `--texp-max` | 60 | Longest estimated exposure [s]; only used without `--texp` |
| `--adql` | off | Print the Gaia query for the target and exit |
| `--offline` | off | Never use the network: use the cache or `--gaia` only |
| `--match-radius` | 2.0 | Maximum distance [″] between the target position and its Gaia star |
| `--blank` | off | No Gaia star at the position: show the field there anyway, without comparison stars (section 3) |
| `--epoch` | today | Observing epoch (decimal year); positions are moved there with Gaia proper motions |
| `--acq` | NORMAL FAINT | Acquisition mode: sets read noise, gain and saturation level |
| `--size` | LARGE | Read-out area: LARGE (full frame), MEDIUM (512² px), SMALL (256² px) |
| `--mode` | phot | `polar` adds the second Savart image of every star |
| `--airmass` | 1.2 | Airmass used for extinction and scintillation |
| `--moon` | new | Sky brightness: new / quarter / full |
| `--sky` | photometric | Extinction: photometric / good / regular |
| `--fwhm` | 2.5 | Typical seeing [″]; sets the isolation radius |
| `--fwhm-best` | 1.2 | Best seeing you expect [″]; saturation is checked at this value |
| `--search-radius` | 7 | Comparison candidates within this many arcmin of the target |
| `--noise-ratio` | 10 | A comparison star's noise per exposure may be at most this many times the target's |
| `--dmag-bright` | off | Optional: at most this many magnitudes brighter than the target (otherwise saturation decides) |
| `--dmag-faint` | off | Optional: at most this many magnitudes fainter (otherwise `--noise-ratio` decides) |
| `--no-smear` | off | Ignore readout-smear trails |
| `--margin` | 12 | Minimum distance [″] from the edge of the field, for both images in polarimetric mode |
| `--pointing-unc` | 0 | Pointing error [″] the recommendation must tolerate. 0 assumes you refine the pointing at the telescope |
| `--bands` | griz | Bands that must have comparison stars |
| `--etc-flux` | off | Use the ETC spreadsheet's flux scale instead of the measured one (section 9) |
| `--compare LABEL COORD` | — | Score another pointing with the same rules; can be repeated |
| `--out PREFIX` | — | Write `PREFIX_finder.png`, `PREFIX_score.png` and the star list `PREFIX_field.csv` |
| `--version` | — | Print the version |

## 6. Reading the output

### The report

- **Input** shows what you typed, the position it resolved to and which resolver answered, and how far that position is from the Gaia star used.
- **Target** gives the Gaia identification and magnitudes. For each band it also shows the predicted peak pixel at the best seeing, with a warning if the target itself would saturate, and the noise of one exposure.
- **RECOMMENDED POINTING**
  - The field centre coordinates, and the TCSPD offset written two ways: as numbers to type in, and in words ("move the field centre 110″ W and 42″ S").
  - **sign check:** the pixel where the target should appear after the offset.
  - **η (noise penalty):** how much the comparison stars add to the noise of the differential light curve, worst band and per band (section 8). η = 1 means they add nothing.
  - "unchanged for …″ of pointing error" says how far the telescope may land from the recommended pointing before the set of usable stars changes. When it is under 10″, the report adds "refine the pointing at the telescope".
- **COMPARISON STARS** lists, for each star (labelled C1, C2, … by brightness, as in the finder chart and the star list): Gaia ID, G, V, colour difference from the target, colour weight `w`, distance from the target, magnitude difference in each band, and peak pixel at the best seeing as a percentage of saturation. The last column shows which bands use the star: a letter means used, `.` means not usable in that band, `~` means it lies on another star's readout trail in that band.
- **NEAR MISSES** lists the ten brightest stars inside the field that are not used, with every rule each one failed. This is where to look if the field looks poor.
- **GUIDER** gives the brightest star in the guide range inside the patrol annulus, and the angle to enter in Guider Setup, plus alternatives.
- **OTHER POINTINGS** shows the scores of any `--compare` pointings under the same rules.

### The figures

- **Finder chart** (`_finder.png`)
  - Left panel: the science field. The blue outline is the area all four channels share; the dotted outlines are the individual channels. The target is the red cross, comparison stars are green circles labelled with their bands, and the brightest unused stars are grey dashed circles with the first reason. The C1, C2, ... labels are the tool's own numbering, by brightness.
  - Right panel: the guider patrol annulus with the guider box and guide star.
  - North is up and East is left, as on the detector.
- **Star list** (`_field.csv`): one row per Gaia star on at least one channel at the recommended pointing, brightest first (see below).
- **Score map** (`_score.png`) shows the worst-band η for every possible field centre (offset from the target), already made robust to the pointing error. The white star is the recommendation; `--compare` pointings appear as red markers.

### The star list

`--out PREFIX` also writes `PREFIX_field.csv`. It opens with `#` comment lines giving the field centre, the TCSPD offset, the epoch, the mode and the exposure times (marked if estimated). Most readers skip these lines; with astropy, use `ascii.read(path, format="csv", comment="#")`.

Each row is one star:

| Columns | Content |
|---|---|
| `source_id`, `label`, `role` | Gaia DR3 ID; `C1`, `C2`, … for comparison stars (numbered by G, as in the report and the finder chart) or `target`; `target`, `comparison` (used in at least one band) or empty |
| `ra_deg`, `dec_deg` | Position at the observing epoch (proper motion applied) |
| `dE_target_arcsec`, `dN_target_arcsec` | Offset from the target, East and North |
| `dE_field_arcsec`, `dN_field_arcsec` | Offset from the field centre |
| `G`, `bp_rp`, `g_mag` … `z_mag` | Gaia G and BP−RP; Gaia synthetic SDSS g, r, i, z |
| `V`, `V_source` | Johnson V (below); `gaia_synthetic`, `G_bprp`, or empty when it cannot be computed |
| `mags_estimated` | True if g, r, i, z were estimated from BP−RP (no Gaia synthetic photometry) |
| `ruwe`, `gaia_variable`, `colour_weight` | Gaia quality and the colour weight used in η |
| `x_<band>`, `y_<band>` | Predicted pixel on that channel, 0-based, raw orientation |
| `x2_<band>`, `y2_<band>` | Polarimetric mode only: the second image |
| `on_<band>` | The star falls on that channel |
| `peak_adu_<band>`, `noise_ppt_<band>` | Predicted peak at the best seeing, and noise per exposure |
| `used_<band>`, `trail_<band>` | Used as a comparison in that band; lies on a readout trail in that band |
| `reasons` | Every rule the star failed |

**Johnson V.** V is reported only; the selection works in g, r, i, z. It comes from Gaia's synthetic Johnson–Kron–Cousins V (`v_jkc_mag`, in the query since version 0.7.0) where it exists. Otherwise it uses the Gaia relation of Riello et al. (2021):

```
G − V = −0.02704 + 0.01424 x − 0.2156 x² + 0.01426 x³,   x = BP − RP
```

The relation has a scatter of 0.030 mag and holds for −0.5 < BP−RP < 5.0; outside that range V is left empty. Cones downloaded before 0.7.0 lack `v_jkc_mag`, so they use the relation for every star until refreshed with `--refresh`. Check: AU Mic (G = 7.843, BP−RP = 2.111) gives V = 8.67; its catalogue V is about 8.6.

The predicted pixels come from the measured footprint. At the observed AU Mic pointing (2026-06-04) they agree with the real channel 3 WCS to a median of 3.5 px (1.2″), at most 4.8 px, for the 13 stars with G < 15. That is close enough to match each row to the pipeline's sources.

## 7. At the telescope

1. **Point at the target** with TCSPD as usual.
2. In *Setup, Pointing & Guiding → HANDSET → PRECISE OFFSET*, enter **RA·cos(δ)** and **DEC** from the report, in arcsec, and press **GO**.
3. **Check the sign** on the first image: the target should be near the pixel given in the report (channel 2, raw orientation, no flips in DS9). If it is mirrored in x, flip the sign of the RA offset; if mirrored in y, flip the DEC sign. Once confirmed, the convention holds for every target.
4. In **Guider Setup**, enter the guider angle from the report.

The sign convention of the TCSPD Precise offset has not yet been confirmed on the sky. Step 3 settles it the first time it is used; please record the result.

## 8. How the field is chosen

### Rules for a comparison star

A star must pass every rule in a band to be used in that band. These are the rules of version 0.5.0, calibrated so that the tool reproduces the fields chosen by hand for TOI-2631 and AU Mic (section 10).

| # | Rule | Default | Why |
|---|---|---|---|
| 1 | Within `--search-radius` of the target | 7′ | The region the field can reach while keeping the target on it |
| 2 | Inside that channel's footprint, at least `--margin` from the edge | 12″ | Room for the aperture and sky annulus (about 5 × FWHM at 2.5″) |
| 3 | Peak pixel below 70% of saturation at the best seeing | 70%, 1.2″ | Stays linear and unsaturated even on the best night. This is the only limit on the bright side |
| 4 | Noise per exposure at most `--noise-ratio` × the target's | 10× | A star beyond this adds less than 1% of the weight of a target-like star. There is no fixed magnitude window: η weighs each star by its noise |
| 5 | No Gaia neighbour within 3 × FWHM brighter than the star + 5 mag | 7.5″ at 2.5″ seeing | Neighbour contributes less than 1% of the flux |
| 6 | Not on the readout trail of a star on the detector, if the trail adds more than 1% to its flux | 1% | Readout smear (below). The trail follows the trail-maker's variability, such as AU Mic's flares |
| 7 | Gaia quality: RUWE < 1.4, not flagged VARIABLE, `non_single_star` = 0 | | Removes likely binaries and known variables |

**Colour is a weight, not a rule.** Comparison stars with |Δ(BP−RP)| ≤ 0.3 count fully. Up to 0.5 they count half, and beyond that a quarter. The report flags them, because colour mismatch shows up as a trend with airmass (mostly in g) that should be detrended. A hard colour cut made every field around a red target empty: AU Mic is an M dwarf with BP−RP = 2.11.

**Optional magnitude window.** `--dmag-bright` and `--dmag-faint` restore a fixed window, like the ±1 mag of versions 0.1–0.4.

**Polarimetric mode.** Rules 2, 5 and 6 also apply to the star's second image. The pipeline puts the "S" image on the Gaia position, and the "N" image lies 13.8″ away at PA 208° (south-west). The flux per image is half.

### Readout smear

The CCDs shift charge row by row with the shutter open, at `VSHIFT` = 4.33 µs per row. So each star leaves a faint trail across the whole frame along the shift direction, and in polarimetric mode one trail per image.

- **Strength.** A comparison star whose aperture lies on the trail of star j picks up

  ```
  F_j × 4.33 µs × D      (D = aperture diameter in pixels, radius = 1 FWHM)
  ```

  against its own F × t_exp. With short exposures this is large: a star 6 mag brighter adds about 5% to a comparison exposed for 0.3 s.
- **Direction.** In the reduced frames the trail runs along columns (N–S) in r, and along rows (E–W) in i and z. For g it has not been measured, so both directions are checked.
- **Measured on AU Mic** (2026-06-04, i): the trail carries 0.004–0.005 ms of exposure per row, matching `VSHIFT`. On the comparison star C5 it adds 3.6% of its flux, against 4.2% predicted.
- **Scope.** Only stars that are on the detector at a given pointing leave trails, so this rule depends on the pointing and is checked for every candidate field.

The target itself must sit at least `--margin` inside the area all four channels share.

### The score: noise penalty η

For each star, the relative error of one exposure is the ETC's CCD equation plus scintillation:

```
σ² = [S + n_pix (B·t + RN² + (0.289·gain)²)] / S²  +  σ_scint²
```

The comparison stars in band b are combined with inverse-variance weights (colour weight w = 1, 0.5 or 0.25):

```
1/σ_E² = Σ w_i / σ_i²
```

The noise of the differential light curve, relative to the target's own noise, is the **noise penalty**:

```
η_b = √(1 + σ_E² / σ_T²)
```

- η = 1: the comparison stars add no noise.
- η = 1.41: they add as much noise as the target has.
- η = ∞: no comparison star in that band.

The pointing score is the worst η over the bands in `--bands`. Lower is better.

**Why η rewards "as many bright stars as possible".** With short exposures, every star has about the same scintillation noise. So each comparison star bright enough to be scintillation-limited counts almost as much as a perfectly matched one, and η falls as √(1 + 1/N) with N such stars. Fainter stars still help, in proportion to 1/σ². This is the formal version of choosing the field by eye to include as many bright stars as possible.

### Search and choice

- The tool tries every field centre on a 4″ grid within about 190″ of the target.
- With `--pointing-unc` > 0, each centre is scored at its nominal position and at 8 points on a circle of that radius, and the worst of the nine is kept. The default is 0, because the pointing is refined at the telescope; the hand-made fields use stars right up to the field edges, which a 15″ tolerance would forbid.
- **At most `--max-comps` comparison stars** (default 6). In a rich field dozens of stars can pass every rule, which makes the list unwieldy and adds little: η falls as 1/√(number of stars) at best. So at each trial pointing only the N stars that add most weight count. Each usable star is ranked once by Σ over bands of w/σ_b² × σ_b,target² (w = colour weight, σ = noise per exposure; a star as good as the target in all four bands scores 4). The N best of those usable in at least one band form the comparison set, the same stars in every band, and η is computed from them alone. The search therefore picks the pointing that is best *for N stars*, not the one with the most stars. The other stars that qualify are listed with "not among the N best (comparison limit)", and the report says how many there were. `--max-comps 0` restores the old behaviour.
- Many neighbouring pointings usually score the same, because they contain the same stars. The tool picks the point of that region farthest from any worse pointing; among equals, the one with the target nearest the field centre. The report states how much further error this leaves room for.

### Estimated exposure times

When `--texp` is not given, each band gets the longest exposure that keeps the **target's** peak pixel at or below **50% of saturation** at the best expected seeing (`--fwhm-best`). The value is then rounded down to a round number (0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 1, 1.5, 2, 3, 5, 7, 10, 15, 20, 30, 45, 60 s …) and capped at `--texp-max` (60 s).

- **Why 50%.** It leaves headroom below the 70% limit used for comparison stars. So comparison stars slightly brighter than the target can still pass, and the target stays linear if the seeing turns out better than planned.
- **Why it matters.** The exposure times set which comparison stars are too bright, so they change the field. That is why the report starts with a warning block when they were estimated.
- **What the warning block shows:**
  - the estimated times and the rule used;
  - any band where the 60 s cap applied;
  - how much longer the exposures could be at typical seeing;
  - for sub-second exposures, the acquisition mode that holds more electrons, and the scintillation per exposure.
- **Example.** For TOI-2631 the estimate is 20 / 5 / 10 / 15 s, against the 15 / 3 / 5 / 10 s used on 2026-08-01; the recommended field is the same.
- **Bright targets.** If you defocus, set `--fwhm-best` to the defocused FWHM. Otherwise the estimate assumes a sharp 1.2″ image and gives exposures several times shorter than you would use.

The estimate only applies to the field search. Choosing the actual exposure times, NEXP and cadence is still the job of S4CALC.

### Switching rules off (`--filters`) and the V window (`--dv`)

Every rule that rejects a comparison star can be switched off. `--filters` lists the ones to keep (default: all); `--filters none` keeps none, and then only the magnitudes, and the V window if set, decide.

| Name | Rejects a star when… |
|---|---|
| `ruwe` | Gaia RUWE ≥ 1.4 |
| `variable` | Gaia flags it VARIABLE |
| `binary` | Gaia lists it as a non-single star |
| `isolation` | a neighbour brighter than its G + 5 lies within 3 × the typical seeing (either image in polarimetric mode) |
| `radius` | it is more than 7′ from the target |
| `edge` | it is closer than 12″ to a channel edge (off: anywhere on the detector; applies to the target too) |
| `colour` | never: weights 1/0.5/0.25 by colour difference (off: all 1) |
| `saturation` | full model only: peak over 70% of saturation at the best seeing |
| `noise` | full model only: noise per exposure over 10 × the target's |
| `trails` | full model only: on another star's readout trail (> 1%) |

`--dv MIN MAX` keeps only stars with MIN ≤ V_comp − V_target ≤ MAX (Johnson V; section 6). It applies in every band and in both modes. The helper takes `filters=ruwe,edge,…` (or `none`) and `dv=min,max` (either side empty for no limit). SkyBound's slider and checkboxes set these.

### Basic mode (`--basic`)

The basic mode chooses the field by brightness alone: the comparison stars that are **closest in brightness to the target** win. It needs no exposure times, time of night or sky conditions, and is what SkyBound's *Basic* tab uses.

- **Noise:** each star's noise is pure photon noise, so σ_i/σ_T = 10^(0.2 (m_i − m_T)) and, per band, η = √(1 + F_T / Σ w_i F_i).
- **Brightness:** a window in V, by default −0.35 to +8 mag (`BASIC_DV`), the same for every band. 0.35 mag is 2.5 log10(0.70/0.50) = 0.37 rounded down: with the target at 50% of saturation (the exposure estimate's rule), such a star stays under the 70% limit.
- **Faintest allowed:** 8 mag fainter (`BASIC_DMAG_FAINT`). That is the faintest comparison the full model used in the validation fields (AU Mic C8, +8.0 in z): for a bright target the full model is scintillation-limited and keeps such stars, so the basic mode must not drop them. (Version 0.9.0 used 5 mag, which left bright targets in sparse fields with no comparison at all.)
- **Not modelled:** readout trails, because their strength depends on the exposure time.
- **Rules applied:** `ruwe`, `variable`, `binary`, `isolation`, `radius`, `edge`, `colour` (each can be switched off), and the guide star.

Because scintillation and read noise are left out, η is larger than the full model's for bright targets (AU Mic: 6.7 against 2.1), and faint comparison stars count for less. The pointings agree with the hand-chosen and full-model fields:

| Field | Basic pointing | Hand-chosen (observed) | Full model |
|---|---|---|---|
| AU Mic | +121.5″, +33.5″ | +122.4″, +29.7″ | +125.5″, +33.5″ (estimated exposures) |
| TOI-2631 | −118.5″, +37.5″ | −118.3″, +20.2″ | −122.5″, +41.5″ |

### Guide star

- The guider box centre sits 664.2″ from the field centre, at position angle PA = 180° + guider angle (east of north).
- The tool lists stars with `guide_gmin` ≤ G ≤ `guide_gmax` (default 8–14, a placeholder) between 584.7″ and 743.7″ from the field centre, which is the patrol annulus minus 15″ on each side. It recommends the brightest.

## 9. Where the numbers come from

| Quantity | Value | Source |
|---|---|---|
| Footprint, per channel and read-out size | corner offsets in the script | Full TAN-SIP pipeline WCS of the 2026-08-01 TOI-2631 stacks |
| Area common to all four channels | 32.26 arcmin² (98% of one channel) | same |
| Orientation | North up, East left, no channel mirrored; PA of +y between 0.45° and 2.07° | same |
| Plate scale | g 0.3357, r 0.3373, i 0.3355, z 0.3368 ″/px | same |
| Polarimetric second image | 13.8″ at PA 208.4° from the Gaia position | 2026-06-04 AU Mic stacks (i), 14 stars matched to Gaia; 2026-07-13 WD1620-391: 14.0″ between the images |
| Readout smear | 4.33 µs per row (`VSHIFT`); r along columns, i and z along rows, g unknown | 2026-06-04 AU Mic stacks: trail 0.004–0.005 ms per row; 3.6% measured vs 4.2% predicted on C5 |
| Guider annulus and box | radius 569.68–758.68″; box 189″ | `sparc4_fov.xml` |
| Guider angle convention | PA_box = 180° + angle | Confirmed 2026-08-01 with Gaia DR3 6560736409211951616 (G = 10.72): implied 135.7°, logged 134° |
| Read noise, gain, sky, extinction, F_CALIB | ETC spreadsheet values | `sparc4.py` |
| Saturation | 60 000 ADU (FAINT), 30 000 (BRIGHT) | Operation Modes spreadsheet |
| **Flux scale** | measured / ETC = g 4.97, r 6.51, i 4.37, z 4.49 | Aperture photometry of 4–5 stars per channel on 2026-08-01, scatter ≤ 0.019 mag |
| Peak pixel | Gaussian PSF | Reproduces measured peaks within 0.85–1.26 |
| Scintillation | Young (1967) × 1.5 (Osborn et al. 2015), D = 1.6 m, altitude 1864 m | |

**Flux scale.** The ETC spreadsheet predicts 4–6 times fewer electrons than SPARC4 recorded on 2026-08-01. The pipeline stacks are means of frames, in electrons. Because the measured values are used by default, the saturation check is realistic; with `--etc-flux` it would let stars through that actually saturate. The same discrepancy affects `sparc4.py` and S4CALC, which reproduce the spreadsheet. This is from one night and needs confirming on a second before changing those tools.

## 10. Validation

### Calibration checks

The TOI-2631 checks use the 2026-08-01 data: four pipeline stacks and a Gaia DR3 cone.

| Check | Result |
|---|---|
| Area common to all four channels, computed by the script vs an independent calculation (shapely) | 32.26 vs 32.26 arcmin² |
| Predicted vs real pixel of the target at the observed pointing (channels 2 and 4) | 0.3 px |
| Peak pixel, predicted at the measured seeing vs measured (5 stars × 4 bands) | median ratio 0.92–0.98 per band |
| Guider angle predicted at the observed pointing vs night log | 135.6° vs 134° |
| η in z, worked out by hand vs script | 1.650 vs 1.650 |
| Gaia ID input vs coordinate input | identical pointing (unit test) |
| Star-list pixels vs the real channel 3 WCS, AU Mic 2026-06-04 (13 stars, G < 15) | median 3.5 px, maximum 4.8 px |

### Reproducing fields chosen by hand

**AU Mic, 2026-06-04** (polarimetric, quarter-wave plate). Inputs:
- exposures: i = 0.30 s and z = 0.23 s from the headers; g = 2 s and r = 0.2 s estimated;
- seeing 2.49″, measured;
- observed pointing from the channel 3 WCS: **+122.4″ E, +29.7″ N** of AU Mic.

| | Version 0.4 rules | Version 0.5 rules |
|---|---|---|
| Result | NO USABLE FIELD | Best pointing **+113.5″, +25.5″: 10″ from the observed one** |
| Worst-band η, best pointing vs observed | — | 2.735 vs 2.735 |
| Observed field better than | — | 99.8% of all pointings |
| Hand-picked C1 (G 11.28), C3 (G 12.29) | rejected (±1 mag, colour) | used in griz |
| C2 (G 12.23) | rejected | used in g, r, z |
| C5 (G 13.21) | rejected | used in g, r; on AU Mic's readout trail in i and z (3.6% measured) |
| C4 (G 13.99) | rejected | rejected: it is a pair with a G = 13.11 star 4.8″ away |

Rule by rule, here is what made the difference:
- **Magnitude window:** removing the fixed window (comparison stars are 3–7 mag fainter than AU Mic).
- **Colour:** turning the cut into a weight (AU Mic's BP−RP is 0.85–1.25 redder than every candidate).
- **Edge margin:** 12″ instead of 20″ (C1's second image is 16″ from the edge in the observed field).
- **Pointing tolerance:** 0″ instead of 15″. With a 15″ tolerance and a 12″ margin, the observed field ranks in the middle (better than about 50% of pointings).

The exposure estimate also checks out here: at 2.49″ it gives i = z = 0.3 s, against 0.30 and 0.23 s used.

**TOI-2631, 2026-08-01** (exposures 15 / 3 / 5 / 10 s):

| | Version 0.4 rules | Version 0.5 rules |
|---|---|---|
| Best pointing | −109.5″, −41.5″ | −122.5″, +41.5″ |
| Worst-band η, best vs observed (−118″, +21″) | 1.65 vs 1.65 | 1.32 vs 1.33 |
| Comparison stars | 2 (G 13.48, 13.90) | 14 (G 13.48–17.87) |

The two bright stars picked by eye are still rejected at these exposures (208% and 87% of saturation in g at 1.2″ seeing). The observed field nevertheless scores within 1% of the best pointing.

## 11. Limitations and open items

**Placeholders that need data:**
- The guide-star magnitude range (G 8–14).
- The pointing uncertainty: the default is 0″ (refine at the telescope). Measure the real offset error the first time the tool is used, if you want a field that survives a blind offset.
- The readout-smear direction in g.
- The TCSPD offset sign convention (see section 7).

**One night only:**
- The flux scale and footprint come from 2026-08-01. Re-measure after any intervention on the instrument; the channels were realigned between 2023 and 2026.

**Approximations:**
- The footprint corners are exact, but the edges between them are straight lines; the distortion (under 5″) is covered by the edge margin.
- In polarimetric mode, each image is assumed to get exactly half the flux, and the analyser efficiency is ignored.
- The saturation check ignores the bias level of the raw frames. The 70% limit leaves room for it.
- The aperture follows the ETC's convention (radius 1.5″, n_pix = 2πr²), so that values match the spreadsheet.

**Readout smear:** the trail strength is modelled for one aperture size (radius = 1 FWHM) and calibrated on one night. Smear from stars just outside the detector edge is ignored.

**Catalogue limits:**
- Gaia is incomplete for pairs closer than about 1″, so the isolation rule cannot see them.
- Proper motion is applied linearly. Parallax and radial velocity are ignored, which is well below 0.1″ over decades for any field star.
- Stars with only a 2-parameter Gaia solution have no proper motion and stay at their 2016.0 position.

**Reduced catalogue:** with the GAVO fallback the variability and non-single-star rules are off and g, r, i, z come from colour relations (section 4).

**Not done yet:** no background image (DSS) under the finder chart; SkyBound's field panel (it will use the helper, section 12a) is not written yet.

## 12. Using it from Python

```python
import s4field as F

tab, rz = F.obtain_field("TOI-2631")              # resolve + download, or read ./cones/
# or, with your own file:
# tab = F.read_gaia("cone.csv"); rz = F.resolve_target("TOI-2631", tab)
s   = F.Settings(t_exp={"g": 15, "r": 3, "i": 5, "z": 10}, airmass=1.2)
res = F.run(tab, rz.ra, rz.dec, s, resolution=rz,
            compare=[("planned", 330.069, -48.699)])

print(F.report(res))
stars = F.field_table(res)        # astropy Table of the stars on the detector
F.write_field_csv(res, "toi2631_field.csv")
fig  = F.finder_chart(res)        # matplotlib Figure, not shown or saved
fig2 = F.score_map(res)
```

- `res.best` holds the recommended pointing: `dE`, `dN` (the offset), `ra`, `dec`, `eta_bands`, `target_pixel`.
- `res.comps[band]` holds the indices of the comparison stars in `res.stars`.
- `res.guider["candidates"]` lists the guide stars.
- Every rule threshold is a field of `Settings`.

## 12a. The local helper for SkyBound

A web page cannot read most astronomy services: the browser lets a page read another site's answer only if that site allows it (CORS). Tested on 2026-11-05 in Firefox, from a file opened from disk and from `http://localhost`:

| Service | From a web page |
|---|---|
| ESA Gaia Archive, AIP and ARI Heidelberg Gaia mirrors | blocked |
| GAVO `gaia.dr3lite` (quick query) | works (asynchronous jobs did not finish in 3 min) |
| NASA Exoplanet Archive, MAST (names, TIC, Gaia), ExoFOP | blocked |
| Sesame (Strasbourg, CfA) | down (CDS incident) |
| STScI DSS image | works, pixels readable |
| SkyView DSS2 image | displays; pixels not readable |

So SkyBound gets the full pipeline from this script running on your laptop:

```bash
python3 s4field.py --serve            # http://127.0.0.1:8417, Ctrl-C to stop
```

It listens on `127.0.0.1` only (not reachable from other computers), answers GET requests with JSON, allows any page to read them, and shares the `./cones/` cache with the command line.

| Endpoint | Answer |
|---|---|
| `/api/status` | version, cache directory, cached cones |
| `/api/resolve?target=AU+Mic` | RA, Dec and who resolved the name |
| `/api/cone?target=AU+Mic` | the Gaia cone (downloaded once, then cached) |
| `/api/field?target=AU+Mic&...` | the recommended pointing and TCSPD offset, η per band, comparison labels per band, footprints (″ E, N of the field centre), guide-star candidates, warnings, the star list of section 6 and the text report |
| `POST /api/fov` | another instrument's footprint (section 12b): answers its id |
| `/api/field?target=...&fov=id&...` | that instrument's field (section 12b) |

`/api/resolve` answers from the cache when the name has a cached cone (no network). After Sesame times out, the helper skips it for 30 minutes, so name lookups during an outage do not wait 20 s each. Typed coordinates reuse any cached cone centred within 1′ (for example a star first looked up by name).

`/api/field?...&at=dE,dN` scores that one pointing (the field centre's offset from the target, in arcsec, as typed into TCSPD) instead of searching; SkyBound uses it when you drag the field. If the target is then off the field or within the edge margin, η is `null` and a warning says so. A position with no Gaia star gives the field at that position (`target.blank` is `true`, `target.source_id` and η are `null`, no comparisons, a warning first); `blank=0` refuses it instead, as the command line does. Every answer also carries `sky`: all the cone's stars as offsets from the target at the epoch, with G, for the interactive guider.

`/api/field` takes the command-line options as parameters: `basic=1`, `at=dE,dN`, `texp=g,r,i,z` (omit to estimate), `mode`, `size`, `acq`, `airmass`, `moon`, `sky`, `fwhm`, `fwhm_best`, `texp_max`, `margin`, `search_radius` (arcmin), `noise_ratio`, `pointing_unc`, `dmag_bright`, `dmag_faint`, `bands`, `max_comps` (0 = no limit), `smear=0`, `etc_flux=1`, `epoch`, `source`, `refresh=1`, `offline=1`. Errors come back as `{"error": "..."}` with HTTP 400 (bad input), 502 (catalogue service down) or 500. Gaia source_ids are strings, because they do not fit in a JavaScript number; missing values are `null`.

`helper_test.html` (in the SkyBound test folder) is a one-page check: open it with the helper running, and it shows whether the page can reach the helper and the field for a target. It worked in Chromium from `file://` and from `http://localhost`, giving the same pointing and η as the command line. Still to check: from SkyBound's GitHub Pages address, where Chrome may ask once for permission to reach "devices on your local network".

## 12b. Any instrument's field of view

SkyBound can load any instrument's field of view (an Aladin "Load instrument FoV" file, or a rectangular detector from its New FoV form). The helper then ranks comparison stars for it with the **basic-mode** rules of section 8, in one band, on that footprint:

- **Footprint.** SkyBound sends the science regions, already turned to the field position angle, as polygons and circles in arcsec East, North of the field centre: `POST /api/fov` with `{"name": ..., "science": [{"shape": "polygon", "pts": [[x, y], ...]}, {"shape": "circle", "c": [x, y], "r": r}, ...]}`. The answer is `{"fov": id, ...}`; the helper keeps the 64 most recent (after a restart, SkyBound sends them again on the 404 `unknown_fov`). In Python: `FovGeometry(regions)`.
- **On the detector.** A star is a comparison candidate (and the target is on the field) only at least the edge margin inside the edge of **its own region**, so chip gaps are avoided like the field edge. The regions are rasterised once per margin, about 2000 cells across the footprint (0.2″ for a 6′ field, 3″ for HSC); polygons with many vertices (curved chip edges) are simplified to half a cell first. A footprint smaller than twice the margin (an IFU) has no comparison stars, and says so.
- **Band.** `band=V` (default; Gaia's synthetic Johnson V, else from G and BP−RP), `G`, or Gaia's synthetic `g`, `r`, `i`, `z`. η is the basic mode's photon-noise η in that band; the colour weights use BP−RP as for SPARC4.
- **Rules.** As basic mode: RUWE, VARIABLE, non-single star, isolation (`iso`, the radius in arcsec, default 7.5″ = 3 × 2.5″), the edge margin (`margin`, default 12″), colour weights, the V window `dv` and `max_comps`; `filters` as usual. There is no 7′ search radius (the whole field counts) and no readout-trail, saturation or noise check (no exposure model).
- **Second image.** `twin=dE,dN` (arcsec, on the sky) for a polarimeter: both images must be isolated and on the detector, as in SPARC4's polarimetric mode.
- **Search.** The field centre moves up to the footprint's half-size from the target, so the target can sit anywhere on the detector (a large field reaches comparison stars at its far side), on a grid of about 100 × 100 pointings. Only if that would need a cone larger than 1° is the search narrowed to fit; `at=dE,dN` scores one pointing. No guider: SkyBound places the instrument's guider itself.
- **Cone.** The Gaia cone must hold every star the search can put on the detector or in the guider: min(reach·√2, extent) + outer + 20″ from the target, where outer is the larger of the detector's extent and the guider's reach (SkyBound sends it as `outer` with the footprint). For ROBO43 (42′ × 28′, off-axis guider 27′ out) that is 0.89°. A cached cone is used if it reaches 95% of that, so SPARC4's 0.27° cones serve fields up to ~12′ (an iKon-L on the 0.6 m Boller & Chivens). A larger one (up to 1°) is downloaded; if a smaller one is cached, the helper answers from it at once, with a warning, and downloads the larger cone in the background, as for the reduced catalogue (section 4). The cone's radius is saved in its `.json`.
- **Answer.** `generic: true`; `target` (with `V` and `mag` in the band), `catalogue.radius_deg`, `settings` (band, margin, isolation radius, twin), `pointing` (offset, η, `fixed`, `left_out`), `comparisons` (labels), `warnings`, `stars` (every star on the detector(s): identity, offsets from the target and the field centre, G, V, BP−RP, the band's magnitude and its difference from the target's, RUWE, colour weight, reasons), `sky` and `report`.

## 13. Tests

```bash
python3 -m unittest test_s4field.py -v
S4FIELD_TEST_CSV=cone_toi2631.csv python3 -m unittest test_s4field.py -v   # + end-to-end
```

The tests cover:
- coordinate parsing;
- Gaia ID lookup;
- the Sesame mirror order and fallback;
- the Exoplanet Archive queries and fallback;
- `--offline` behaviour;
- proper-motion propagation and matching at all three epochs;
- the Gaia Archive job (submit, wait, errors, timeouts) and the cache (download once, reuse offline, refresh);
- an end-to-end run from the target name alone, giving the same pointing as the manual CSV;
- the exposure estimate (peak at 50% of saturation, rounding, cap, seeing scaling) and its warning;
- the version 0.5 rules: second-image direction, colour weights, readout-smear geometry and strength;
- the star list: target pixel, used flags, C# labels and CSV round trip, including the polarimetric second image;
- Johnson V: the G, BP−RP relation, its valid range, and preference for Gaia's synthetic V;
- the AU Mic field of 2026-06-04 (set `S4FIELD_TEST_AUMIC` to its Gaia cone): the tool must land within 15″ of the observed pointing and use C1, C2, C3 and C5, but not C4;
- end-to-end equivalence of Gaia-ID and coordinate input;
- the reduced catalogue: the colour relations on the two targets, continuity at the ends of their range, reading a GAVO CSV (with BP−RP or with BP and RP), and the same pointing and comparison stars as the full cone for AU Mic and TOI-2631 (needs both cones);
- GAVO queries (request, error document, truncation, unreachable), Gaia ID lookup through GAVO when ESA is down, and `--source` (fallback, cached reduced cone, upgrade when ESA returns, `esa` never falling back);
- the local helper over real HTTP: CORS and preflight headers, errors, parameters, and `/api/field` giving exactly the command-line pointing and η for AU Mic, with source_ids as strings and no NaN in the JSON;
- any instrument's footprint (0.13): the raster's margin, a chip gap and a circle; simplifying a curved chip edge (area kept to 0.3%); bad regions; cone sizes (the 0.6 m B&C and Zeiss fields, HSC); comparisons on the detector with the margin, never in a chip gap, both images of a polarimeter on the detector, the band's magnitudes, a fixed pointing, a blank position, a footprint smaller than its margin; a cached cone too small (refused when online, used with a note offline or when the download fails, with the cached position and no new name lookup); `POST /api/fov` then `/api/field?fov=` over HTTP, unknown ids and bad footprints, and a too-small cone answered at once while the larger one is fetched in the background.

The network resolvers are tested with mocked responses, so the suite runs offline. A first live run of a name lookup is still worth doing on a machine with internet access.

## 14. Version history

- **0.13.1** (2026-11-13): a loaded FoV is searched over its whole size (was at most 600″), and the cone also holds the guider (`outer` in `POST /api/fov`).
- **0.13.0** (2026-11-13)
  - Any instrument's field of view (section 12b): `FovGeometry` (polygons and circles, rasterised per margin), `run(..., geometry=)` in basic mode and one band (`V`, `G`, `g`, `r`, `i`, `z`), `Settings.iso_radius` and `Settings.twin` (a polarimeter's second image), `result_json_fov`, `fov_table` and `report_fov`. The helper: `POST /api/fov`, `/api/field?fov=id&band=&margin=&iso=&twin=`, CORS for POST, and `/api/status` gives the cache's last change (`updated`).
  - The Gaia cone's radius is a parameter (`CONE_RADIUS`, 0.27° as before; up to 1° for a large FoV) and is saved with the cone; a smaller cached cone is served at once while the larger one is downloaded in the background, without a new name lookup.
  - SPARC4 is unchanged: same rules, same answers (all earlier tests pass).

- **0.12.0** (2026-11-12)
  - `--max-comps N` / `max_comps=`: at most N comparison stars, default 6 (0 = all, as before). Chosen inside the η search, so the pointing is optimised for those N; the same stars in every band. The answer's `pointing.left_out` counts the stars that qualified but were left out, and the star list marks them.
  - η per band now uses the same comparison set as the worst-band η.
  - The helper answers from its cache without waiting: a cached reduced cone is upgraded from ESA in the background (at most every 15 min per target) instead of before every request. Before, every request -- each drag of the field -- first tried ESA, which can take over a minute to fail, and held up every other request meanwhile.
  - Faster: the isolation check looks only at nearby pairs instead of all n² (identical result, tested against the old method), and the helper keeps the cones it has read in memory. Scoring a dragged field in a 6300-star cone went from ~1 s to <0.1 s; a full search from 1.7 s to 0.7 s.

- **0.11.0** (2026-11-12)
  - A position with no Gaia star (pointing tests, empty fields) gives the field there instead of an error: `--blank` on the command line, always in the helper (`blank=0` to refuse). No comparison stars and no η; the guider, footprints, star list and a warning naming the nearest stars. `run(..., allow_blank=True)` / `run_blank()`.

- **0.10.0** (2026-11-11)
  - `--filters` / `filters=`: every rejection rule can be switched off; with none, magnitudes alone decide.
  - `--dv MIN MAX` / `dv=`: a window in V_comp − V_target for comparison stars.
  - Basic mode's brightness limits are now that V window (default −0.35 to +8 mag) instead of a per-band rule. AU Mic and TOI-2631 basic pointings unchanged.
  - The report's Rules line lists only the rules that are on, and which are off.

- **0.9.2** (2026-11-11)
  - Basic mode: comparisons up to 8 mag fainter (was 5), and no readout trails (they depend on the exposure time). AU Mic basic now lands 3.9″ from the hand-chosen field.
  - A pointing with the target on the field but no usable comparison in some band no longer reports "target outside the field": the answer has `pointing.target_inside` and a separate warning.
  - Every unused star in the star list says why, including when it is too close to the field edge.

- **0.9.1** (2026-11-11)
  - `run(..., at=(dE, dN))` and `/api/field?at=dE,dN`: score one given pointing (SkyBound's draggable field).
  - The helper's answer includes the cone's stars (`sky`), for the interactive guider.
  - A pointing that leaves the target off the field reports η = null with a warning.

- **0.9.0** (2026-11-11)
  - `--basic` (`basic=1` in the helper): brightness-only ranking, no exposure, sky or airmass model (section 8).
  - Helper: name lookups answered from the cache; Sesame skipped for 30 min after it times out.
  - Typed coordinates reuse a cached cone centred within 1′ instead of downloading again.

- **0.8.2** (2026-11-06)
  - `/api/field` also returns the guider geometry (annulus radii, box size and box-centre radius) under `footprint.guider`, for SkyBound's chart.

- **0.8.1** (2026-11-06)
  - The report header shows the s4field version.
  - Near misses: a star just outside the field reads "outside the field by 12″" (was "-12″ from edge"); a star rejected only because a second (polarimetric) image lands too close now says which image and how close.
  - First live runs (AU Mic, 2026-11-06): ESA and GAVO cones both 670 stars around the same centre; same pointing (+125.5″, +33.5″); the GAVO run adds only the Gaia-VARIABLE G = 14.0 star, as predicted.

- **0.8.0** (2026-11-06)
  - `--source auto|esa|gavo`: when the ESA Gaia Archive is down, the cone comes from GAVO's reduced Gaia DR3 (`gaia.dr3lite`), with g, r, i, z from fitted colour relations and a warning that variability flags are missing. Same pointing as the full cone for AU Mic and TOI-2631.
  - A Gaia DR3 source_id given without a CSV is looked up at GAVO when ESA does not answer.
  - `--serve`: local HTTP helper with a JSON API, so SkyBound (a web page) can use the full pipeline.

- **0.7.0** (2026-11-05)
  - Johnson V in the star list and the report: Gaia synthetic `v_jkc_mag` where available (now in the query), otherwise the Riello et al. (2021) G, BP−RP relation; `V_source` says which.
  - The star list has a `label` column (C1, C2, …), the same numbering as the report and finder chart.

- **0.6.0** (2026-11-05)
  - Star list: `--out` also writes `PREFIX_field.csv` with every star on the detector, its Gaia photometry, predicted pixel on each channel (both images in polarimetric mode), predicted peak and noise, and whether it is used or why not.
  - New functions `field_table` and `write_field_csv`.

- **0.5.0** (2026-11-04) — selection rules recalibrated on the hand-made AU Mic and TOI-2631 fields:
  - The fixed magnitude window is gone: the bright side is limited by saturation, the faint side by noise (at most 10× the target's per exposure). `--dmag-bright`/`--dmag-faint` remain as options.
  - Colour is a weight (1 / 0.5 / 0.25), no longer a cut.
  - New readout-smear rule, measured on AU Mic.
  - Comparison candidates within 7′ of the target (`--search-radius`).
  - Edge margin 12″ (was 20″); pointing tolerance 0″ by default (was 15″).
  - Polarimetric second image corrected to 13.8″ at PA 208° (was modelled at PA 29°, the opposite direction).
  - The report lists the brightest unused stars with reasons, and marks trail-blocked bands with `~`.

- **0.4.0** (2026-11-04)
  - `--texp` is optional. Without it, exposure times are estimated so that the target peaks at 50% of saturation at the best seeing, rounded down and capped at `--texp-max` (60 s).
  - The report flags estimated exposures in a warning block, with the scintillation and acquisition-mode hints for sub-second exposures.

- **0.3.0** (2026-11-04)
  - Automatic Gaia download: `--target` alone is enough. The cone is fetched from the ESA Gaia Archive (asynchronous TAP job) and cached in `./cones/` together with the resolved position, so later runs work offline.
  - New options `--cache-dir` and `--refresh`; `--gaia` becomes optional.
  - A Gaia DR3 source_id given without a CSV is looked up in the Gaia Archive.
  - The report states where the catalogue came from.

- **0.2.0** (2026-11-04)
  - Proper motion: the Gaia query now includes `pmra`, `pmdec`; positions are moved to the observing epoch (`--epoch`).
  - The target match is tested at epochs 2000.0, 2016.0 and the observing epoch, so SIMBAD-style coordinates of fast-moving stars (such as AU Mic) match.
  - Older CSVs without proper motions still work, with a warning in the report.

- **0.1.0** (2026-11-04)
  - First version.
  - Measured four-channel footprint; agreed comparison-star rules; noise-penalty score with pointing robustness.
  - TCSPD offset with sign check; guider angle.
  - Target input by coordinates, Gaia DR3 ID, Sesame (CfA mirror first) or NASA Exoplanet Archive.
