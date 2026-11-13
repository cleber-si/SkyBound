"""Tests for s4field.py target resolution.

The network resolvers are tested with mocked responses, so the suite runs
offline. Run:  python3 -m unittest test_s4field.py -v
Set S4FIELD_TEST_CSV to a Gaia CSV around TOI-2631 to also run the
end-to-end check (Gaia ID and coordinates must give the same pointing).
"""
import io
import math
import os
import unittest
from unittest import mock

import numpy as np

import s4field as F

CSV = os.environ.get("S4FIELD_TEST_CSV")


class FakeCoord:
    def __init__(self, ra, dec):
        from astropy.coordinates import Angle
        self.ra, self.dec = Angle(ra, "deg"), Angle(dec, "deg")


class TestCoordinates(unittest.TestCase):
    def test_sexagesimal_colon(self):
        ra, dec = F.parse_coordinates("22:00:24.76 -48:42:37.69")
        self.assertAlmostEqual(ra, 330.1031667, places=6)
        self.assertAlmostEqual(dec, -48.7104694, places=6)

    def test_sexagesimal_spaces(self):
        ra, dec = F.parse_coordinates("22 00 24.76 -48 42 37.69")
        self.assertAlmostEqual(ra, 330.1031667, places=6)

    def test_degrees(self):
        self.assertEqual(F.parse_coordinates("330.1 -48.7"), (330.1, -48.7))
        self.assertEqual(F.parse_coordinates("330.1, -48.7"), (330.1, -48.7))

    def test_names_are_not_coordinates(self):
        for name in ("TOI-2631", "TOI 2631", "TIC 123456", "HD 1234", "WASP-18",
                     "Gaia DR3 6560684113691404160", "6560684113691404160"):
            self.assertIsNone(F.parse_coordinates(name), name)


class TestGaiaId(unittest.TestCase):
    def setUp(self):
        from astropy.table import Table
        self.tab = Table({"source_id": [111111111111111111, 6560684113691404160],
                          "ra": [1.0, 330.1], "dec": [2.0, -48.7]})

    def test_forms(self):
        for text in ("6560684113691404160", "Gaia DR3 6560684113691404160",
                     "gaia dr3 6560684113691404160", "Gaia 6560684113691404160"):
            self.assertEqual(F.resolve_gaia_id(text, self.tab), (330.1, -48.7), text)

    def test_not_an_id(self):
        self.assertIsNone(F.resolve_gaia_id("TOI-2631", self.tab))

    def test_missing_from_csv(self):
        with self.assertRaises(F.ResolveError):
            F.resolve_gaia_id("Gaia DR3 999999999999999999", self.tab)

    def test_no_csv(self):
        with self.assertRaises(F.ResolveError):
            F.resolve_gaia_id("Gaia DR3 6560684113691404160", None)

    def test_offline_path_uses_no_network(self):
        with mock.patch.object(F, "resolve_sesame") as ses, \
                mock.patch.object(F, "_tap_csv") as tap:
            r = F.resolve_target("Gaia DR3 6560684113691404160", self.tab, offline=True)
        self.assertEqual((r.ra, r.dec), (330.1, -48.7))
        ses.assert_not_called()
        tap.assert_not_called()


class TestSesame(unittest.TestCase):
    def test_cfa_mirror_is_tried_first(self):
        from astropy.coordinates import name_resolve
        seen = []

        def fake(name, parse=False, cache=False):
            seen.append(name_resolve.sesame_url.get()[0])
            return FakeCoord(330.1, -48.7)
        with mock.patch("astropy.coordinates.SkyCoord.from_name", side_effect=fake):
            ra, dec, url = F.resolve_sesame("TOI-2631")
        self.assertEqual(seen, [F.SESAME_MIRRORS[0]])
        self.assertIn("harvard", url)

    def test_falls_back_to_strasbourg(self):
        from astropy.coordinates import name_resolve
        seen = []

        def fake(name, parse=False, cache=False):
            url = name_resolve.sesame_url.get()[0]
            seen.append(url)
            if "harvard" in url:
                raise name_resolve.NameResolveError("timeout")
            return FakeCoord(1.0, 2.0)
        with mock.patch("astropy.coordinates.SkyCoord.from_name", side_effect=fake):
            r = F.resolve_target("HD 1234")
        self.assertEqual(len(seen), 2)
        self.assertEqual(r.source, "Sesame (Strasbourg)")
        self.assertEqual((r.ra, r.dec), (1.0, 2.0))

    def test_mirror_setting_is_restored(self):
        from astropy.coordinates import name_resolve
        before = name_resolve.sesame_url.get()
        with mock.patch("astropy.coordinates.SkyCoord.from_name",
                        return_value=FakeCoord(0, 0)):
            F.resolve_sesame("X")
        self.assertEqual(name_resolve.sesame_url.get(), before)


class TestExoplanetArchive(unittest.TestCase):
    def test_queries(self):
        q = F.exoplanet_query
        self.assertIn("toi >= 2631 and toi < 2632", q("TOI-2631"))
        self.assertIn("toi >= 2631 and toi < 2632", q("toi 2631"))
        self.assertIn("toi = 2631.01", q("TOI-2631.01"))
        self.assertIn("tid = 123456", q("TIC 123456"))
        self.assertIn("tid = 123456", q("TIC-123456"))
        self.assertIn("lower(hostname) = lower('WASP-18')", q("WASP-18"))
        self.assertIn("lower('HD 1''234')", q("HD 1'234"))   # quotes escaped

    def test_used_when_sesame_fails(self):
        rows = [{"toi": "2631.01", "tid": "123", "ra": "330.1031", "dec": "-48.7105"}]
        with mock.patch.object(F, "resolve_sesame", side_effect=F.ResolveError("down")), \
                mock.patch.object(F, "_tap_csv", return_value=rows) as tap:
            r = F.resolve_target("TOI-2631")
        self.assertEqual(r.source, "NASA Exoplanet Archive")
        self.assertEqual((r.ra, r.dec), (330.1031, -48.7105))
        self.assertIn("TOI 2631.01", r.note)
        self.assertIn("from toi", tap.call_args[0][0])

    def test_empty_answer(self):
        with mock.patch.object(F, "resolve_sesame", side_effect=F.ResolveError("Sesame: down")), \
                mock.patch.object(F, "_tap_csv", return_value=[]):
            with self.assertRaises(F.ResolveError) as cm:
                F.resolve_target("TOI-99999")
        self.assertIn("Sesame", str(cm.exception))
        self.assertIn("Exoplanet Archive", str(cm.exception))

    def test_offline_refuses_names(self):
        with mock.patch.object(F, "resolve_sesame") as ses:
            with self.assertRaises(F.ResolveError):
                F.resolve_target("TOI-2631", offline=True)
        ses.assert_not_called()


class TestProperMotion(unittest.TestCase):
    """A fast-moving star (AU Mic-like) among slow ones."""

    def setUp(self):
        from astropy.table import Table
        rng = np.random.default_rng(1)
        n = 40
        ra = 311.29 + rng.uniform(-0.08, 0.08, n)
        dec = -31.34 + rng.uniform(-0.08, 0.08, n)
        self.tab = Table({"source_id": np.arange(n) + 10**17, "ra": ra, "dec": dec,
                          "pmra": np.zeros(n), "pmdec": np.zeros(n),
                          "phot_g_mean_mag": np.linspace(8, 17, n)})
        self.tab["pmra"][0], self.tab["pmdec"][0] = 280.0, -360.6
        self.tab.meta["has_pm"] = True

    def test_propagation_size(self):
        ra, dec = F.propagate(self.tab, 2026.0)
        e, n = F.tangent_offsets(ra[:1], dec[:1], self.tab["ra"][0], self.tab["dec"][0])
        self.assertAlmostEqual(float(e[0]), 2.800, places=3)      # 280 mas/yr x 10 yr
        self.assertAlmostEqual(float(n[0]), -3.606, places=3)
        ra, dec = F.propagate(self.tab, F.GAIA_EPOCH)
        np.testing.assert_allclose(ra, self.tab["ra"])

    def test_epoch_2000_position_is_matched(self):
        ra, dec = F.propagate(self.tab, 2000.0)        # what SIMBAD/Sesame give
        sep = np.hypot(*F.tangent_offsets(np.array([ra[0]]), np.array([dec[0]]),
                                          self.tab["ra"][0], self.tab["dec"][0]))
        self.assertGreater(float(sep[0]), 7.0)          # would fail a 2" match without PM
        s = F.Settings(t_exp={"g": 1, "r": 1, "i": 1, "z": 1}, pointing_unc=0, grid_step=20)
        with mock.patch.object(F, "_eligibility",
                               side_effect=RuntimeError("matched")) as el:
            with self.assertRaises(RuntimeError):
                F.run(self.tab, ra[0], dec[0], s, epoch=2026.8)
        self.assertEqual(el.call_args[0][1], 0)         # target index = the fast star

    def test_no_pm_columns_hint(self):
        tab = self.tab.copy()
        tab.meta["has_pm"] = False
        tab["pmra"][:] = 0
        tab["pmdec"][:] = 0
        ra, dec = F.from_tangent(10.0, 0.0, tab["ra"][0], tab["dec"][0])
        s = F.Settings(t_exp={"g": 1, "r": 1, "i": 1, "z": 1})
        with self.assertRaises(ValueError) as cm:
            F.run(tab, ra, dec, s)
        self.assertIn("no proper motions", str(cm.exception))


@unittest.skipUnless(CSV, "set S4FIELD_TEST_CSV to a Gaia CSV around TOI-2631")
class TestEndToEnd(unittest.TestCase):
    def test_gaia_id_equals_coordinates(self):
        tab = F.read_gaia(CSV)
        s = F.Settings(t_exp={"g": 15, "r": 3, "i": 5, "z": 10}, airmass=1.177)
        a = F.run(tab, *F.parse_coordinates("22:00:24.76 -48:42:37.69"), s)
        rz = F.resolve_target("Gaia DR3 6560684113691404160", tab, offline=True)
        b = F.run(tab, rz.ra, rz.dec, s, resolution=rz)
        self.assertEqual(a.target["source_id"], b.target["source_id"])
        self.assertEqual((a.best["dE"], a.best["dN"]), (b.best["dE"], b.best["dN"]))

    def test_far_position_is_refused(self):
        tab = F.read_gaia(CSV)
        s = F.Settings(t_exp={"g": 15, "r": 3, "i": 5, "z": 10})
        ra, dec = F.parse_coordinates("22:00:24.76 -48:42:47.69")    # 10" off
        with self.assertRaises(ValueError):
            F.run(tab, ra, dec, s)


class FakeArchive:
    """Stands in for _http: a TAP async job that runs for `steps` polls."""

    def __init__(self, result="a,b\n1,2\n", steps=2, final="COMPLETED", redirect=True):
        self.result, self.steps, self.final, self.redirect = result, steps, final, redirect
        self.calls = []

    def __call__(self, url, data=None, timeout=None):
        self.calls.append((url, data))
        base = F.GAIA_TAP
        if url == base + "/async":
            assert data["PHASE"] == "RUN" and data["FORMAT"] == "csv" and data["LANG"] == "ADQL"
            if self.redirect:
                return base + "/async/1700000000000O", "<job/>"
            return url, "<uws:job><uws:jobId>1700000000000O</uws:jobId></uws:job>"
        job = base + "/async/1700000000000O"
        if url == job + "/phase":
            self.steps -= 1
            return url, ("EXECUTING" if self.steps > 0 else self.final)
        if url == job:
            return url, "<uws:errorSummary><uws:message>bad ADQL</uws:message></uws:errorSummary>"
        if url == job + "/results/result":
            return url, self.result
        raise AssertionError(url)


class TestGaiaArchive(unittest.TestCase):
    def test_async_job(self):
        fake = FakeArchive(steps=3)
        with mock.patch.object(F, "_http", side_effect=fake):
            out = F.gaia_tap("SELECT 1", sleep=lambda s: None)
        self.assertEqual(out, "a,b\n1,2\n")
        self.assertEqual(sum(u.endswith("/phase") for u, _ in fake.calls), 3)

    def test_job_id_from_body_without_redirect(self):
        fake = FakeArchive(redirect=False)
        with mock.patch.object(F, "_http", side_effect=fake):
            self.assertEqual(F.gaia_tap("SELECT 1", sleep=lambda s: None), "a,b\n1,2\n")

    def test_job_error_message(self):
        fake = FakeArchive(final="ERROR")
        with mock.patch.object(F, "_http", side_effect=fake):
            with self.assertRaises(F.GaiaArchiveError) as cm:
                F.gaia_tap("SELECT 1", sleep=lambda s: None)
        self.assertIn("bad ADQL", str(cm.exception))

    def test_timeout(self):
        fake = FakeArchive(steps=10**6)
        with mock.patch.object(F, "_http", side_effect=fake):
            with self.assertRaises(F.GaiaArchiveError):
                F.gaia_tap("SELECT 1", sleep=lambda s: None, max_wait=10, poll=2)

    def test_unreachable(self):
        with mock.patch.object(F, "_http", side_effect=OSError("403 Forbidden")):
            with self.assertRaises(F.GaiaArchiveError):
                F.gaia_tap("SELECT 1")

    def test_gaia_id_without_csv_asks_archive(self):
        with mock.patch.object(F, "gaia_tap", return_value="ra,dec\n311.2897,-31.3409\n") as g:
            r = F.resolve_target("Gaia DR3 6794047652729201024")
        self.assertIn("source_id = 6794047652729201024", g.call_args[0][0])
        self.assertEqual((r.ra, r.dec), (311.2897, -31.3409))
        self.assertIn("Gaia Archive", r.source)


class TestCache(unittest.TestCase):
    def test_slugs(self):
        self.assertEqual(F.cache_slug("AU Mic"), "au_mic")
        self.assertEqual(F.cache_slug("au  mic"), "au_mic")
        self.assertEqual(F.cache_slug("TOI-2631.01"), "toi_2631_01")
        self.assertEqual(F.cache_slug("Gaia DR3 123456789012345678"), "gaia_dr3_123456789012345678")
        self.assertEqual(F.cache_slug("330.1 -48.7"), "ra330.1000_decm48.7000")
        self.assertNotEqual(F.cache_slug("330.1 -48.7"), F.cache_slug("330.1 48.7"))

    def _csv(self):
        return ("source_id,ra,dec,pmra,pmdec,phot_g_mean_mag,bp_rp,ruwe,phot_variable_flag,"
                "non_single_star,g_sdss_mag,r_sdss_mag,i_sdss_mag,z_sdss_mag\n"
                "1,311.2897,-31.3409,280,-360,7.8,1.9,1.1,NOT_AVAILABLE,0,9.2,8.0,7.3,6.9\n")

    def test_download_then_cache_then_offline(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rz0 = F.Resolution("AU Mic", 311.2897, -31.3409, "Sesame (CfA mirror)")
            with mock.patch.object(F, "resolve_target", return_value=rz0) as res, \
                    mock.patch.object(F, "gaia_tap", return_value=self._csv()) as g:
                tab, rz = F.obtain_field("AU Mic", d)
                self.assertEqual(g.call_count, 1)
                self.assertTrue(os.path.exists(os.path.join(d, "cone_au_mic.csv")))
                self.assertIn("downloaded now", rz.catalogue)
                tab2, rz2 = F.obtain_field("au mic", d, offline=True)      # same slug
                self.assertEqual(g.call_count, 1)                           # no new download
                self.assertEqual(res.call_count, 1)                         # no new lookup
                self.assertEqual((rz2.ra, rz2.dec), (rz0.ra, rz0.dec))
                self.assertIn("cache (Sesame (CfA mirror))", rz2.source)
                self.assertTrue(tab2.meta["has_pm"])
                F.obtain_field("AU Mic", d, refresh=True)
                self.assertEqual(g.call_count, 2)
            self.assertFalse(any(f.endswith(".part") for f in os.listdir(d)))

    def test_offline_without_cache(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(F.ResolveError):
                F.obtain_field("AU Mic", d, offline=True)

    def test_empty_download_is_not_cached(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rz0 = F.Resolution("X", 1.0, 2.0, "coordinates")
            with mock.patch.object(F, "resolve_target", return_value=rz0), \
                    mock.patch.object(F, "gaia_tap", return_value="source_id,ra,dec\n"):
                with self.assertRaises(F.GaiaArchiveError):
                    F.obtain_field("X", d, source="esa")
            self.assertEqual(os.listdir(d) if os.path.isdir(d) else [], [])


@unittest.skipUnless(CSV, "set S4FIELD_TEST_CSV to a Gaia CSV around TOI-2631")
class TestEndToEndDownload(unittest.TestCase):
    def test_name_only_gives_same_pointing(self):
        """`--target TOI-2631` with nothing else == the manual CSV run."""
        import io, tempfile, contextlib
        with open(CSV) as f:
            text = f.read()
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            with mock.patch.object(F, "resolve_sesame",
                                   return_value=(330.1031667, -48.7104694, F.SESAME_MIRRORS[0])), \
                    mock.patch.object(F, "gaia_tap", return_value=text), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = F.main(["--target", "TOI-2631", "--texp", "15", "3", "5", "10",
                             "--cache-dir", d, "--epoch", "2026.84"])
            self.assertEqual(rc, 0)
            rep = out.getvalue()
            self.assertIn('RA.cos(dec) = -122.5"   DEC = +41.5"', rep)
            self.assertIn("downloaded now from the Gaia Archive", rep)
            out2 = io.StringIO()
            with contextlib.redirect_stdout(out2):
                rc = F.main(["--target", "TOI-2631", "--texp", "15", "3", "5", "10",
                             "--cache-dir", d, "--offline", "--epoch", "2026.84"])
            self.assertEqual(rc, 0)
            self.assertIn('RA.cos(dec) = -122.5"   DEC = +41.5"', out2.getvalue())


class TestExposureEstimate(unittest.TestCase):
    MAGS = {"g": 13.33, "r": 12.87, "i": 12.71, "z": 12.67}          # TOI-2631

    def test_peak_at_or_below_target_fraction(self):
        s = F.Settings()
        est, exact = F.estimate_texp(self.MAGS, s)
        self.assertEqual(est, {"g": 20, "r": 5, "i": 10, "z": 15})
        for b in F.BANDS:
            pk = F.peak_adu(b, self.MAGS[b], F.Settings(t_exp=est))
            self.assertLessEqual(pk, s.texp_peak * 60000 * 1.0001)
            self.assertAlmostEqual(
                F.peak_adu(b, self.MAGS[b], F.Settings(t_exp=exact)) / 60000, 0.5, places=6)

    def test_cap_and_seeing(self):
        faint = {b: 17.0 for b in F.BANDS}
        est, exact = F.estimate_texp(faint, F.Settings(texp_max=60))
        self.assertTrue(all(v == 60 for v in est.values()))
        self.assertTrue(all(v > 60 for v in exact.values()))
        e1, _ = F.estimate_texp(self.MAGS, F.Settings(fwhm_best=1.2))
        _, x3 = F.estimate_texp(self.MAGS, F.Settings(fwhm_best=2.4))
        _, x1 = F.estimate_texp(self.MAGS, F.Settings(fwhm_best=1.2))
        self.assertAlmostEqual(x3["z"] / x1["z"], 4.0, delta=0.3)   # ~ FWHM^2 (sky term aside)

    def test_report_warns(self):
        if not CSV:
            self.skipTest("needs S4FIELD_TEST_CSV")
        tab = F.read_gaia(CSV)
        res = F.run(tab, *F.parse_coordinates("22:00:24.76 -48:42:37.69"), F.Settings())
        rep = F.report(res)
        self.assertIn("EXPOSURE TIMES ESTIMATED", rep)
        self.assertTrue(res.settings.texp_estimated)
        res2 = F.run(tab, *F.parse_coordinates("22:00:24.76 -48:42:37.69"),
                     F.Settings(t_exp={"g": 15, "r": 3, "i": 5, "z": 10}))
        self.assertNotIn("ESTIMATED", F.report(res2))


class TestRules05(unittest.TestCase):
    """v0.5 rules: polarimetric image direction, colour weights, readout smear."""

    def test_second_image_direction(self):
        ve, vn = F._twin()
        self.assertAlmostEqual(math.hypot(ve, vn), 13.8, places=6)
        self.assertLess(ve, 0)          # towards the West ...
        self.assertLess(vn, 0)          # ... and South (PA 208 deg)

    def _tab(self, rows):
        from astropy.table import Table
        t = Table(rows=rows, names=("E", "N", "m", "bp_rp"))
        for b in F.BANDS:
            t[f"{b}_sdss_mag"] = t["m"]
        return t

    def test_smear_pairs(self):
        s = F.Settings(t_exp={b: 0.3 for b in F.BANDS}, fwhm_plan=2.5)
        # 0: bright source at origin; 1: on its row (dN=2"); 2: on its column (dE=2"); 3: elsewhere
        E = np.array([0.0, 100.0, 2.0, 80.0]); N = np.array([0.0, 2.0, 100.0, 90.0])
        m = np.array([7.0, 13.0, 13.0, 13.0])
        ok = np.array([False, True, True, True])
        for band, hit in (("i", {1}), ("z", {1}), ("r", {2}), ("g", {1, 2})):
            src, H, idx = F._smear_pairs(band, m, E, N, ok, 0, s)
            got = {int(idx[k]) for k in np.where(H[list(src).index(0)])[0]} if 0 in src else set()
            self.assertEqual(got, hit, band)
        # fraction: 6 mag brighter, D = 2*2.5"/0.3355" = 14.9 px, 0.3 s -> 5.4 % (> 1 %)
        frac = 10 ** (0.4 * 6) * F.T_VSHIFT * 2 * 2.5 / F.PLATE_SCALE["i"] / 0.3
        self.assertAlmostEqual(frac, 0.054, delta=0.002)
        # a source only 3 mag brighter does not matter (0.7 %)
        src, H, idx = F._smear_pairs("i", np.array([10.0, 13.0, 13.0, 13.0]), E, N, ok, 0, s)
        self.assertNotIn(0, list(src))

    def test_smear_off(self):
        s = F.Settings(t_exp={b: 0.3 for b in F.BANDS}, smear=False)
        src, H, idx = F._smear_pairs("i", np.array([7.0, 13.0]), np.array([0.0, 100.0]),
                                     np.array([0.0, 0.0]), np.array([False, True]), 0, s)
        self.assertEqual(src.size, 0)

    def test_colour_is_a_weight(self):
        s = F.Settings()
        self.assertEqual(s.dcol_far_weight, 0.25)
        self.assertIsNone(s.dmag_bright)
        self.assertIsNone(s.dmag_faint)


class TestFieldTable(unittest.TestCase):
    def _res(self, mode="phot"):
        if not CSV:
            self.skipTest("needs S4FIELD_TEST_CSV")
        tab = F.read_gaia(CSV)
        s = F.Settings(t_exp={"g": 15, "r": 3, "i": 5, "z": 10}, airmass=1.177, mode=mode)
        return F.run(tab, *F.parse_coordinates("22:00:24.76 -48:42:37.69"), s, epoch=2026.58)

    def test_contents_match_the_result(self):
        res = self._res()
        t = F.field_table(res)
        self.assertEqual(list(t["role"]).count("target"), 1)
        row = {int(sid): k for k, sid in enumerate(t["source_id"])}
        T = row[res.target["source_id"]]
        for b in F.BANDS:
            px, py = res.best["target_pixel"][b]
            self.assertAlmostEqual(float(t[f"x_{b}"][T]), px, delta=0.06)
            self.assertAlmostEqual(float(t[f"y_{b}"][T]), py, delta=0.06)
            used = {int(res.stars["source_id"][i]) for i in res.comps[b]}
            self.assertEqual({int(sid) for sid in t["source_id"][t[f"used_{b}"]]}, used)
        self.assertTrue(all(t["on_g"] | t["on_r"] | t["on_i"] | t["on_z"]))
        self.assertTrue(np.all(np.diff(t["G"]) >= 0))                 # brightest first
        self.assertNotIn("x2_g", t.colnames)
        labels = F.comp_labels(res)
        for k, sid in enumerate(t["source_id"]):
            i = int(np.where(res.stars["source_id"] == sid)[0][0])
            self.assertEqual(t["label"][k], labels.get(i, "target" if t["role"][k] == "target" else ""))
        self.assertEqual(sorted(l for l in t["label"] if l.startswith("C")),
                         sorted(f"C{k}" for k in range(1, len(labels) + 1)))
        self.assertTrue(np.all(np.isfinite(np.asarray(t["V"], float))))

    def test_polar_columns_and_csv(self):
        import tempfile
        from astropy.io import ascii
        res = self._res("polar")
        t = F.field_table(res)
        self.assertIn("x2_z", t.colnames)
        dx = float(t["x2_r"][0] - t["x_r"][0]); dy = float(t["y2_r"][0] - t["y_r"][0])
        self.assertAlmostEqual(math.hypot(dx, dy) * F.PLATE_SCALE["r"], 13.8, delta=0.3)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x_field.csv")
            n = F.write_field_csv(res, path)
            with open(path) as f:
                head = f.readline()
            self.assertTrue(head.startswith("# s4field"))
            back = ascii.read(path, format="csv", comment="#")
            self.assertEqual(len(back), n)
            self.assertEqual(int(back["source_id"][0]), int(t["source_id"][0]))


class TestJohnsonV(unittest.TestCase):
    def test_relation_and_preference(self):
        from astropy.table import Table, MaskedColumn
        t = Table({"phot_g_mean_mag": [7.843, 12.0, 12.0, 12.0],
                   "bp_rp": [2.111, 0.8, 6.0, np.nan]})
        V, src = F.johnson_v(t)
        self.assertAlmostEqual(V[0], 8.667, places=3)          # AU Mic (catalogue V ~ 8.6)
        x = 0.8
        self.assertAlmostEqual(V[1], 12.0 - (-0.02704 + 0.01424 * x - 0.2156 * x**2 + 0.01426 * x**3), places=6)
        self.assertTrue(np.isnan(V[2]) and np.isnan(V[3]))      # outside -0.5..5.0 / no colour
        self.assertEqual(list(src), ["G_bprp", "G_bprp", "", ""])
        t["v_jkc_mag"] = MaskedColumn([8.60, 12.3, 0.0, 0.0], mask=[False, True, True, True])
        V, src = F.johnson_v(t)
        self.assertEqual((V[0], src[0]), (8.60, "gaia_synthetic"))   # Gaia's own V wins
        self.assertEqual(src[1], "G_bprp")                           # fallback where missing

    def test_query_asks_for_v(self):
        self.assertIn("s.v_jkc_mag", F.ADQL)


AUMIC = os.environ.get("S4FIELD_TEST_AUMIC")


@unittest.skipUnless(AUMIC, "set S4FIELD_TEST_AUMIC to the AU Mic Gaia cone")
class TestAUMic(unittest.TestCase):
    """The hand-made AU Mic field of 2026-06-04 (polarimetric, L4)."""
    C = {"C1": 6794044358492150912, "C2": 6794044805168744320, "C3": 6794048928337372800,
         "C4": 6794047347790348800, "C5": 6794044873888227456}

    def test_reproduces_observed_field(self):
        tab = F.read_gaia(AUMIC)
        rz = F.resolve_target("Gaia DR3 6794047652729201024", tab, offline=True)
        s = F.Settings(t_exp={"g": 2, "r": 0.2, "i": 0.3, "z": 0.23}, mode="polar",
                       airmass=1.06, fwhm_plan=2.49, fwhm_best=2.49)
        obs = F.parse_coordinates("20:45:19.67 -31:20:07.04")
        res = F.run(tab, rz.ra, rz.dec, s, compare=[("observed", *obs)], epoch=2026.4245)
        b, o = res.best, res.compared[0]
        self.assertLess(math.hypot(b["dE"] - o["dE"], b["dN"] - o["dN"]), 15)   # 10" found
        self.assertLessEqual(o["eta_nominal"], b["eta_nominal"] * 1.01)
        row = {int(res.stars["source_id"][i]): i for i in range(len(res.stars))}
        used = lambda k, band: row[self.C[k]] in res.comps[band]
        for k in ("C1", "C3"):
            self.assertTrue(all(used(k, band) for band in F.BANDS), k)
        self.assertTrue(used("C2", "g"))
        self.assertTrue(used("C5", "g") and used("C5", "r"))
        self.assertTrue(row[self.C["C5"]] in res._smeared["i"])                  # on AU Mic's trail
        self.assertFalse(any(used("C4", band) for band in F.BANDS))              # blended pair


def _lite_csv(full_path, out_path):
    """What GAVO's gaia.dr3lite gives for the same cone: no synthetic
    photometry, no variability or non-single-star flags."""
    from astropy.table import Table
    t = Table.read(full_path, format="ascii.csv")
    keep = [c for c in ("source_id", "ra", "dec", "pmra", "pmdec", "phot_g_mean_mag", "bp_rp", "ruwe")
            if c in t.colnames]
    t[keep].write(out_path, format="ascii.csv", overwrite=True)
    return out_path


LITE_ROWS = ("source_id,ra,dec,pmra,pmdec,phot_g_mean_mag,bp_rp,ruwe\n"
             "1,311.2897,-31.3409,280,-360,7.843,2.111,0.93\n")


class TestColourRelations(unittest.TestCase):
    def test_au_mic_and_toi2631_targets(self):
        # Gaia synthetic g,r,i,z of the two targets (from the full cones)
        for G, x, ref in ((7.843, 2.111, (9.465, 8.079, 7.207, 6.712)),
                          (12.889, 0.853, (13.327, 12.866, 12.714, 12.666))):
            for b, m in zip(F.BANDS, ref):
                self.assertAlmostEqual(float(F.sdss_from_bprp(G, x, b)), m, delta=0.04)

    def test_continuous_at_the_edges(self):
        for b in F.BANDS:
            for edge in F.SDSS_POLY_RANGE:
                a, c = (float(F.sdss_from_bprp(10.0, edge + d, b)) for d in (-1e-6, 1e-6))
                self.assertAlmostEqual(a, c, places=4)
            far = [float(F.sdss_from_bprp(10.0, x, b)) for x in (3.5, 4.0, 4.5)]
            self.assertAlmostEqual(far[1] - far[0], far[2] - far[1], places=9)   # a straight line

    def test_read_lite_csv(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lite.csv")
            with open(p, "w") as f:
                f.write(LITE_ROWS)
            t = F.read_gaia(p)
            self.assertTrue(t.meta["lite"])
            self.assertTrue(all(t["mag_est"]))
            self.assertAlmostEqual(float(t["r_sdss_mag"][0]), 8.079, delta=0.04)
            self.assertEqual(t["phot_variable_flag"][0], "NOT_AVAILABLE")
            self.assertEqual(float(t["non_single_star"][0]), 0.0)
            # BP and RP instead of BP-RP
            with open(p, "w") as f:
                f.write("source_id,ra,dec,phot_g_mean_mag,phot_bp_mean_mag,phot_rp_mean_mag,ruwe\n"
                        "1,311.2897,-31.3409,7.843,8.900,6.789,0.93\n")
            self.assertAlmostEqual(float(F.read_gaia(p)["bp_rp"][0]), 2.111, places=6)

    def test_full_csv_is_not_lite(self):
        if not CSV:
            self.skipTest("needs S4FIELD_TEST_CSV")
        self.assertFalse(F.read_gaia(CSV).meta["lite"])


@unittest.skipUnless(AUMIC and CSV, "needs both cones")
class TestLiteReproducesFull(unittest.TestCase):
    """The reduced GAVO catalogue must give the same pointing (validation of
    2026-11-06: 0.0" shift for AU Mic and TOI-2631)."""

    def _both(self, path, coord, s, epoch):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            lite = F.read_gaia(_lite_csv(path, os.path.join(d, "lite.csv")))
        full = F.read_gaia(path)
        ra, dec = coord
        return (F.run(full, ra, dec, s, epoch=epoch), F.run(lite, ra, dec, s, epoch=epoch))

    def test_au_mic(self):
        rz = F.resolve_target("Gaia DR3 6794047652729201024", F.read_gaia(AUMIC), offline=True)
        s = F.Settings(t_exp={"g": 2, "r": 0.2, "i": 0.3, "z": 0.23}, mode="polar",
                       airmass=1.06, fwhm_plan=2.49, fwhm_best=2.49)
        full, lite = self._both(AUMIC, (rz.ra, rz.dec), s, 2026.4245)
        self.assertEqual((full.best["dE"], full.best["dN"]), (lite.best["dE"], lite.best["dN"]))
        self.assertIn("reduced catalogue", F.report(lite))
        self.assertNotIn("reduced catalogue", F.report(full))
        self.assertEqual(set(F.field_table(lite)["gaia_variable"]), {"unknown"})

    def test_toi2631(self):
        s = F.Settings(t_exp={"g": 15, "r": 3, "i": 5, "z": 10}, airmass=1.177)
        full, lite = self._both(CSV, F.parse_coordinates("22:00:24.76 -48:42:37.69"), s, 2026.58)
        self.assertEqual((full.best["dE"], full.best["dN"]), (lite.best["dE"], lite.best["dN"]))
        for b in F.BANDS:
            self.assertEqual(set(full.comps[b]), set(lite.comps[b]))


class TestGavo(unittest.TestCase):
    def test_sync_query(self):
        with mock.patch.object(F, "_http", return_value=("u", LITE_ROWS)) as h:
            out = F.gavo_tap(F.ADQL_LITE.format(ra=311.2897, dec=-31.3409, radius=F.CONE_RADIUS))
        url, data = h.call_args[0][0], h.call_args[0][1]
        self.assertEqual(url, F.GAVO_TAP + "/sync")
        self.assertIn("gaia.dr3lite", data["QUERY"])
        self.assertEqual(data["FORMAT"], "csv")
        self.assertEqual(out, LITE_ROWS)

    def test_error_document(self):
        vot = ('<?xml version="1.0"?><VOTABLE><RESOURCE type="results">'
               '<INFO name="QUERY_STATUS" value="ERROR">Could not parse your query</INFO>'
               '</RESOURCE></VOTABLE>')
        with mock.patch.object(F, "_http", return_value=("u", vot)):
            with self.assertRaises(F.GaiaArchiveError) as cm:
                F.gavo_tap("SELECT")
        self.assertIn("Could not parse", str(cm.exception))

    def test_truncated(self):
        with mock.patch.object(F, "_http", return_value=("u", "a\n1\n2\n3\n")):
            with self.assertRaises(F.GaiaArchiveError):
                F.gavo_tap("SELECT", maxrec=3)

    def test_unreachable(self):
        with mock.patch.object(F, "_http", side_effect=OSError("timed out")):
            with self.assertRaises(F.GaiaArchiveError):
                F.gavo_tap("SELECT")

    def test_gaia_id_falls_back_to_gavo(self):
        with mock.patch.object(F, "gaia_tap", side_effect=F.GaiaArchiveError("down")), \
                mock.patch.object(F, "gavo_tap", return_value="ra,dec\n311.2897,-31.3409\n") as g:
            r = F.resolve_target("Gaia DR3 6794047652729201024")
        self.assertIn("gaia.dr3lite", g.call_args[0][0])
        self.assertEqual((r.ra, r.dec), (311.2897, -31.3409))
        self.assertIn("GAVO", r.source)


class TestSources(unittest.TestCase):
    FULL = TestCache._csv(None)
    RZ = F.Resolution("AU Mic", 311.2897, -31.3409, "Sesame (CfA mirror)")

    def test_auto_falls_back_then_upgrades(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(F, "resolve_target", return_value=self.RZ), \
                    mock.patch.object(F, "gaia_tap", side_effect=F.GaiaArchiveError("ESA down")), \
                    mock.patch.object(F, "gavo_tap", return_value=LITE_ROWS) as gv:
                tab, rz = F.obtain_field("AU Mic", d)
                self.assertTrue(tab.meta["lite"])
                self.assertIn("ESA archive failed", rz.catalogue)
                self.assertTrue(os.path.exists(os.path.join(d, "cone_au_mic_lite.csv")))
                self.assertFalse(os.path.exists(os.path.join(d, "cone_au_mic.csv")))
                tab, rz = F.obtain_field("AU Mic", d)               # ESA still down
                self.assertEqual(gv.call_count, 1)                  # cached reduced cone
                self.assertTrue(tab.meta["lite"])
                tab, rz = F.obtain_field("AU Mic", d, offline=True)
                self.assertTrue(tab.meta["lite"])
                with self.assertRaises(F.ResolveError):             # no full cone cached
                    F.obtain_field("AU Mic", d, offline=True, source="esa")
            with mock.patch.object(F, "resolve_target", return_value=self.RZ), \
                    mock.patch.object(F, "gaia_tap", return_value=self.FULL):
                tab, rz = F.obtain_field("AU Mic", d)               # ESA back: upgrade
                self.assertFalse(tab.meta["lite"])
                tab, rz = F.obtain_field("AU Mic", d, offline=True)
                self.assertFalse(tab.meta["lite"])                  # full preferred
                tab, rz = F.obtain_field("AU Mic", d, offline=True, source="gavo")
                self.assertTrue(tab.meta["lite"])                   # unless asked

    def test_esa_only_does_not_fall_back(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(F, "resolve_target", return_value=self.RZ), \
                    mock.patch.object(F, "gaia_tap", side_effect=F.GaiaArchiveError("ESA down")), \
                    mock.patch.object(F, "gavo_tap") as gv:
                with self.assertRaises(F.GaiaArchiveError):
                    F.obtain_field("AU Mic", d, source="esa")
                gv.assert_not_called()

    def test_coordinates_reuse_a_nearby_cone(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "cone_au_mic.csv"), "w") as f:
                f.write(self.FULL)
            with open(os.path.join(d, "cone_au_mic.json"), "w") as f:
                json.dump({"ra": 311.29113, "dec": -31.34245, "source": "NASA Exoplanet Archive",
                           "downloaded": "2026-11-06T00:00:00"}, f)
            tab, rz = F.obtain_field("20 45 09.53 -31 20 27.24", d, offline=True)   # 6" away
            self.assertEqual(rz.source, "coordinates")
            self.assertAlmostEqual(rz.ra, 311.289708, places=5)
            self.assertIn("cone_au_mic.csv", rz.catalogue)
            with self.assertRaises(F.ResolveError):                                  # 7' away
                F.obtain_field("20 45 40 -31 20 27", d, offline=True)

    def test_bad_source(self):
        with self.assertRaises(ValueError):
            F.obtain_field("AU Mic", source="simbad")


class TestServe(unittest.TestCase):
    """The local helper, over real HTTP on 127.0.0.1."""

    @classmethod
    def setUpClass(cls):
        import tempfile, threading, json, shutil
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        if AUMIC:
            shutil.copy(AUMIC, os.path.join(d, "cone_au_mic.csv"))
            with open(os.path.join(d, "cone_au_mic.json"), "w") as f:
                json.dump({"target": "AU Mic", "ra": 311.28972, "dec": -31.34090,
                           "source": "Sesame (CfA mirror)", "downloaded": "2026-11-04T00:00:00"}, f)
        box = {}
        ready = threading.Event()

        def up(httpd):
            box["httpd"] = httpd
            ready.set()
        cls.thread = threading.Thread(target=F.serve, kwargs=dict(port=0, cache_dir=d, ready=up),
                                      daemon=True)
        with mock.patch("sys.stderr", new=io.StringIO()):
            cls.thread.start()
            ready.wait(10)
        cls.httpd = box["httpd"]
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(10)
        cls.tmp.cleanup()

    def get(self, path, method="GET"):
        import json, urllib.request, urllib.error
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with mock.patch("sys.stderr", new=io.StringIO()), \
                    urllib.request.urlopen(req, timeout=120) as r:
                body = r.read().decode()
                return r.status, dict(r.headers), (json.loads(body, parse_constant=self._nan) if body else None)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, dict(e.headers), json.loads(e.read().decode())

    @staticmethod
    def _nan(x):
        raise AssertionError(f"{x} in JSON: JavaScript cannot parse it")

    def test_status_and_cors(self):
        code, h, js = self.get("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(h["Access-Control-Allow-Origin"], "*")
        self.assertEqual(js["s4field"], F.__version__)

    def test_preflight(self):
        code, h, _ = self.get("/api/field", method="OPTIONS")
        self.assertEqual(code, 204)
        self.assertEqual(h["Access-Control-Allow-Private-Network"], "true")

    def test_errors(self):
        self.assertEqual(self.get("/api/field")[0], 400)                     # no target
        self.assertEqual(self.get("/api/nothing?target=x")[0], 404)
        code, _, js = self.get("/api/field?target=AU%20Mic&offline=1&mode=spectro")
        self.assertEqual(code, 400)
        self.assertIn("mode", js["error"])
        code, _, js = self.get("/api/cone?target=Nowhere&offline=1")
        self.assertEqual(code, 400)
        self.assertIn("No cached cone", js["error"])

    def test_settings_from_query(self):
        s = F.settings_from_query({"texp": "2,0.2,0.3,0.23", "mode": "polar", "size": "medium",
                                   "airmass": "1.06", "search_radius": "5", "smear": "0",
                                   "bands": "gr", "etc_flux": "1"})
        self.assertEqual(s.t_exp, {"g": 2, "r": 0.2, "i": 0.3, "z": 0.23})
        self.assertEqual((s.mode, s.size, s.airmass, s.search_radius), ("polar", "MEDIUM", 1.06, 300))
        self.assertEqual((s.smear, s.bands), (False, ("g", "r")))
        self.assertEqual(s.flux_scale, {b: 1.0 for b in F.BANDS})
        with self.assertRaises(ValueError):
            F.settings_from_query({"texp": "1,2"})

    @unittest.skipUnless(AUMIC, "needs the AU Mic cone")
    def test_field_matches_the_command_line(self):
        q = ("/api/field?target=AU%20Mic&offline=1&texp=2,0.2,0.3,0.23&mode=polar"
             "&airmass=1.06&fwhm=2.49&fwhm_best=2.49&epoch=2026.4245")
        code, h, js = self.get(q)
        self.assertEqual(code, 200, js)
        tab, rz = F.obtain_field("AU Mic", self.tmp.name, offline=True)
        s = F.Settings(t_exp={"g": 2, "r": 0.2, "i": 0.3, "z": 0.23}, mode="polar",
                       airmass=1.06, fwhm_plan=2.49, fwhm_best=2.49)
        res = F.run(tab, rz.ra, rz.dec, s, resolution=rz, epoch=2026.4245)
        p = js["pointing"]
        self.assertEqual((p["offset_ra_cosdec_arcsec"], p["offset_dec_arcsec"]),
                         (res.best["dE"], res.best["dN"]))
        self.assertAlmostEqual(p["eta"], res.best["eta_nominal"], places=9)
        self.assertEqual(js["target"]["source_id"], "6794047652729201024")      # a string
        stars = {r["source_id"]: r for r in js["stars"]}
        self.assertEqual(stars["6794044358492150912"]["label"], "C1")
        self.assertIn("C1", js["comparisons"]["z"])
        self.assertEqual(len(js["footprint"]["bands"]["g"]), 4)
        self.assertIsNotNone(js["footprint"]["polar_split_arcsec"])
        self.assertIn("SPARC4 field finder", js["report"])
        self.assertFalse(js["catalogue"]["lite"])
        self.assertEqual(js["warnings"], [])

    @unittest.skipUnless(AUMIC, "needs the AU Mic cone")
    def test_estimated_exposures_are_flagged(self):
        code, _, js = self.get("/api/field?target=AU%20Mic&offline=1&epoch=2026.4245")
        self.assertEqual(code, 200, js)
        self.assertTrue(js["settings"]["texp_estimated"])
        self.assertIn("ESTIMATED", js["warnings"][0])

    @unittest.skipUnless(AUMIC, "needs the AU Mic cone")
    def test_resolve_uses_the_cache(self):
        code, _, js = self.get("/api/resolve?target=AU%20Mic")            # no network needed
        self.assertEqual(code, 200)
        self.assertAlmostEqual(js["ra"], 311.28972)
        self.assertIn("cache", js["source"])

    @unittest.skipUnless(AUMIC, "needs the AU Mic cone")
    def test_basic_field(self):
        code, _, js = self.get("/api/field?target=AU%20Mic&offline=1&basic=1&epoch=2026.85")
        self.assertEqual(code, 200, js)
        self.assertTrue(js["settings"]["basic"])
        self.assertIn("BASIC", js["report"])

    @unittest.skipUnless(AUMIC, "needs the AU Mic cone")
    def test_fixed_pointing(self):
        base = "/api/field?target=AU%20Mic&offline=1&texp=2,0.2,0.3,0.23&mode=polar&airmass=1.06&fwhm=2.49&fwhm_best=2.49&epoch=2026.4245"
        _, _, rec = self.get(base)
        _, _, same = self.get(base + "&at=%g,%g" % (rec["pointing"]["offset_ra_cosdec_arcsec"], rec["pointing"]["offset_dec_arcsec"]))
        self.assertTrue(same["pointing"]["fixed"])
        self.assertAlmostEqual(same["pointing"]["eta"], rec["pointing"]["eta"], places=9)
        self.assertEqual(same["comparisons"], rec["comparisons"])
        # the hand-chosen field of 2026-06-04 (+122.4", +29.7"): eta as scored by --compare
        _, _, obs = self.get(base + "&at=122.4,29.7")
        self.assertEqual((obs["pointing"]["offset_ra_cosdec_arcsec"], obs["pointing"]["offset_dec_arcsec"]), (122.4, 29.7))
        self.assertGreaterEqual(obs["pointing"]["eta"], rec["pointing"]["eta"] - 1e-9)
        # the sky around the target, for the interactive viewer
        self.assertEqual(len(rec["sky"]["E"]), 670)
        i = rec["sky"]["id"].index("6794047652729201024")
        self.assertEqual((rec["sky"]["E"][i], rec["sky"]["N"][i]), (0.0, 0.0))
        _, _, off = self.get(base + "&at=400,0")                          # target off the detector
        self.assertIsNone(off["pointing"]["eta"])
        self.assertFalse(off["pointing"]["target_inside"])
        self.assertIn("outside the field", off["warnings"][-1])
        self.assertTrue(rec["pointing"]["target_inside"])
        # target on the field but no comparison anywhere: a different message
        _, _, lonely = self.get(base.replace("texp=2,0.2,0.3,0.23", "texp=60,60,60,60") + "&at=-60,-60")
        if lonely["pointing"]["eta"] is None:
            self.assertTrue(lonely["pointing"]["target_inside"])
            self.assertIn("No usable comparison star", lonely["warnings"][-1])
        # every unused star in the list says why
        for r in rec["stars"]:
            if r["role"] == "":
                self.assertTrue(r["reasons"], r["source_id"])
        code, _, js = self.get(base + "&at=1,2,3")
        self.assertEqual(code, 400)

    @unittest.skipUnless(AUMIC, "needs the AU Mic cone")
    def test_cone(self):
        code, _, js = self.get("/api/cone?target=au%20mic&offline=1")
        self.assertEqual(code, 200)
        self.assertEqual(len(js["stars"]), 670)
        self.assertTrue(all(isinstance(r["source_id"], str) for r in js["stars"]))


class TestBasic(unittest.TestCase):
    """--basic: brightness-only ranking, no exposure/sky/airmass model."""

    def test_rule_value(self):
        self.assertAlmostEqual(F.basic_dmag_bright(F.Settings()), 2.5 * math.log10(0.7 / 0.5), places=9)
        s = F.settings_from_query({"basic": "1"})
        self.assertTrue(s.basic)

    @unittest.skipUnless(CSV, "needs the TOI-2631 cone")
    def test_toi2631_matches_the_full_model(self):
        tab = F.read_gaia(CSV)
        ra, dec = F.parse_coordinates("22:00:24.76 -48:42:37.69")
        full = F.run(tab, ra, dec, F.Settings(t_exp={"g": 15, "r": 3, "i": 5, "z": 10}, airmass=1.177), epoch=2026.58)
        basic = F.run(tab, ra, dec, F.Settings(basic=True), epoch=2026.58)
        d = math.hypot(full.best["dE"] - basic.best["dE"], full.best["dN"] - basic.best["dN"])
        self.assertLess(d, 10.0)                       # same field (6" apart on 2026-11-11)
        rep = F.report(basic)
        self.assertIn("BASIC", rep)
        self.assertNotIn("EXPOSURE TIMES ESTIMATED", rep)
        self.assertNotIn("target g: peak", rep)

    @unittest.skipUnless(AUMIC, "needs the AU Mic cone")
    def test_au_mic_rules(self):
        tab = F.read_gaia(AUMIC)
        ra, dec = F.parse_coordinates("20:45:09.87 -31:20:32.82")
        res = F.run(tab, ra, dec, F.Settings(basic=True), epoch=2026.85)
        it = res.target["index"]
        V = np.asarray(res.stars["V"], float)
        for b in F.BANDS:                                  # the V window, in every band
            dV = V[res.comps[b]] - V[it]
            self.assertTrue(np.all(dV >= F.BASIC_DV[0] - 1e-9))
            self.assertTrue(np.all(dV <= F.BASIC_DV[1] + 1e-9))
        # photon-noise eta from first principles, z band
        F_t = 10 ** (-0.4 * float(res.stars["z_sdss_mag"][it]))
        w = res._weight[res.comps["z"]]
        Fc = 10 ** (-0.4 * np.asarray(res.stars["z_sdss_mag"], float)[res.comps["z"]])
        self.assertAlmostEqual(res.best["eta_bands"]["z"], math.sqrt(1 + F_t / np.sum(w * Fc)), places=6)
        js = F.result_json(res)
        self.assertTrue(js["settings"]["basic"])
        self.assertEqual(js["warnings"], [])
        self.assertFalse(res.settings.smear)                # no trails without exposure times
        self.assertNotIn("readout smear", F.report(res))


class TestResolveHelpers(unittest.TestCase):
    def test_cached_resolution(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(F.cached_resolution("AU Mic", d))
            with open(os.path.join(d, "cone_au_mic_lite.json"), "w") as f:
                json.dump({"ra": 311.29, "dec": -31.34, "source": "NASA Exoplanet Archive"}, f)
            r = F.cached_resolution("au mic", d)
            self.assertEqual((r.ra, r.dec), (311.29, -31.34))
            self.assertIn("cache", r.source)

    def test_sesame_skipped_after_timing_out(self):
        clock = iter([0.0, 10.0, 20.0, 20.0])               # first call took 10 s
        F._SESAME_DOWN_UNTIL = 0.0
        try:
            with mock.patch.object(F._time, "monotonic", side_effect=lambda: next(clock)), \
                    mock.patch.object(F, "resolve_sesame", side_effect=F.ResolveError("Sesame: timeouts")) as ses, \
                    mock.patch.object(F, "resolve_exoplanet_archive", return_value=(1.0, 2.0, "host X")):
                F.resolve_target("Some Star")
                F.resolve_target("Other Star")
            self.assertEqual(ses.call_count, 1)                # skipped the second time
        finally:
            F._SESAME_DOWN_UNTIL = 0.0

    def test_unknown_name_does_not_disable_sesame(self):
        F._SESAME_DOWN_UNTIL = 0.0
        with mock.patch.object(F, "resolve_sesame", side_effect=F.ResolveError("Sesame: not found")), \
                mock.patch.object(F, "resolve_exoplanet_archive", return_value=(1.0, 2.0, "host X")):
            F.resolve_target("Some Star")
        self.assertEqual(F._SESAME_DOWN_UNTIL, 0.0)


class TestFilters(unittest.TestCase):
    """Every rule can be switched off; with none, magnitudes alone decide."""

    @classmethod
    def setUpClass(cls):
        cls.tab = F.read_gaia(AUMIC) if AUMIC else None
        cls.xy = F.parse_coordinates("20:45:09.87 -31:20:32.82")

    def run_(self, **kw):
        return F.run(self.tab, *self.xy, F.Settings(**kw), epoch=2026.85)

    def test_query(self):
        s = F.settings_from_query({"filters": "ruwe,edge", "dv": "-1,6"})
        self.assertEqual(s.filters, frozenset({"ruwe", "edge"}))
        self.assertEqual((s.dv_min, s.dv_max), (-1.0, 6.0))
        self.assertEqual(F.settings_from_query({"filters": "none"}).filters, frozenset())
        self.assertEqual(F.settings_from_query({"dv": ",5"}).dv_min, None)
        with self.assertRaises(ValueError):
            F.settings_from_query({"filters": "ruwe,moon"})
        self.assertEqual(F.dv_window(F.Settings(basic=True)), F.BASIC_DV)
        self.assertEqual(F.dv_window(F.Settings()), (-np.inf, np.inf))
        self.assertEqual(F.active_filters(F.Settings(basic=True)), set(F.BASIC_FILTERS))

    @unittest.skipUnless(AUMIC, "needs the AU Mic cone")
    def test_switching_off(self):
        base = self.run_(basic=True)
        ids = lambda r: {int(r.stars["source_id"][i]) for b in F.BANDS for i in r.comps[b]}
        # the 4.8" pair is rejected for isolation only; switching isolation off admits it
        pair = {6794047347789396736, 6794047347790348800}
        no_iso = self.run_(basic=True, filters=frozenset(set(F.BASIC_FILTERS) - {"isolation"}))
        self.assertFalse(pair & ids(base))
        # a Gaia VARIABLE star comes back when that filter is off
        var = 6794048550380252288
        no_var = self.run_(basic=True, filters=frozenset(set(F.BASIC_FILTERS) - {"variable"}))
        none = self.run_(basic=True, filters=frozenset())
        self.assertTrue(var in ids(none) or var not in ids(base))
        # nothing switched on: only the V window -- every star inside it on the field is used
        rep = F.report(none)
        self.assertIn("OFF:", rep)
        self.assertEqual(none.settings.edge_margin, 0.0)
        for r in F.field_table(none):
            if r["role"] == "" and r["reasons"]:
                self.assertTrue(any(k in r["reasons"] for k in ("dV", "field edge", "outside", "no V")),
                                r["reasons"])

    @unittest.skipUnless(AUMIC, "needs the AU Mic cone")
    def test_dv_window(self):
        res = self.run_(basic=True, dv_min=1.0, dv_max=4.0)
        V = np.asarray(res.stars["V"], float); it = res.target["index"]
        for b in F.BANDS:
            dV = V[res.comps[b]] - V[it]
            self.assertTrue(np.all((dV >= 1.0) & (dV <= 4.0)))
        js = F.result_json(res)
        self.assertEqual(js["settings"]["dv_window"], [1.0, 4.0])

    @unittest.skipUnless(AUMIC, "needs the AU Mic cone")
    def test_advanced_defaults_unchanged(self):
        a = self.run_(t_exp={"g": 2, "r": 0.2, "i": 0.3, "z": 0.23}, mode="polar", airmass=1.06,
                      fwhm_plan=2.49, fwhm_best=2.49)
        b = self.run_(t_exp={"g": 2, "r": 0.2, "i": 0.3, "z": 0.23}, mode="polar", airmass=1.06,
                      fwhm_plan=2.49, fwhm_best=2.49, filters=frozenset(F.FILTERS))
        self.assertEqual((a.best["dE"], a.best["dN"]), (b.best["dE"], b.best["dN"]))
        no_trails = self.run_(t_exp={"g": 2, "r": 0.2, "i": 0.3, "z": 0.23}, mode="polar",
                              airmass=1.06, fwhm_plan=2.49, fwhm_best=2.49,
                              filters=frozenset(set(F.FILTERS) - {"trails"}))
        self.assertFalse(no_trails.settings.smear)


if __name__ == "__main__":
    unittest.main()


def _cone(ra0, dec0, hole=60.0, seed=3, n_stars=400):
    """A synthetic Gaia cone (every column read_gaia gives) with no star within
    `hole` arcsec of (ra0, dec0), and one guide-range star in the guider annulus."""
    from astropy.table import Table
    rng = np.random.default_rng(seed)
    e = rng.uniform(-900, 900, n_stars)
    n = rng.uniform(-900, 900, n_stars)
    keep = np.hypot(e, n) > hole
    e, n = list(e[keep]), list(n[keep])
    e.append(0.0); n.append(-664.0)                     # guider annulus (r_in 570, r_out 759)
    ra, dec = zip(*[F.from_tangent(x, y, ra0, dec0) for x, y in zip(e, n)])
    k = len(e)
    G = np.r_[rng.uniform(11, 17.9, k - 1), 10.5]
    col = rng.uniform(0.5, 1.5, k)
    t = Table({"source_id": np.arange(k) + 4 * 10**18, "ra": ra, "dec": dec,
               "pmra": np.zeros(k), "pmdec": np.zeros(k), "phot_g_mean_mag": G, "bp_rp": col,
               "ruwe": np.full(k, 1.0), "phot_variable_flag": ["NOT_AVAILABLE"] * k,
               "non_single_star": np.zeros(k), "mag_est": np.zeros(k, bool)})
    for b, off in zip(F.BANDS, (0.5, -0.1, -0.3, -0.4)):
        t[f"{b}_sdss_mag"] = G + off * col
    t["V"], t["V_source"] = F.johnson_v(t)
    t.meta["has_pm"] = True
    return t


class TestBlankPosition(unittest.TestCase):
    """A position with no Gaia star (pointing tests, empty fields)."""

    def setUp(self):
        self.ra0, self.dec0 = 136.7167, 8.0                 # an arbitrary position
        self.tab = _cone(self.ra0, self.dec0)

    def test_refused_without_allow_blank(self):
        with self.assertRaises(ValueError) as cm:
            F.run(self.tab, self.ra0, self.dec0, F.Settings(basic=True), epoch=2026.86)
        self.assertIn("--blank", str(cm.exception))

    def test_field_at_the_position(self):
        res = F.run(self.tab, self.ra0, self.dec0, F.Settings(basic=True), epoch=2026.86,
                    allow_blank=True)
        t, b = res.target, res.best
        self.assertTrue(t["blank"])
        self.assertIsNone(t["index"])
        self.assertEqual((b["dE"], b["dN"]), (0.0, 0.0))
        self.assertAlmostEqual(b["ra"], self.ra0, places=9)
        self.assertTrue(b["target_inside"])
        self.assertTrue(all(v.size == 0 for v in res.comps.values()))
        self.assertTrue(math.isnan(b["eta_nominal"]))
        self.assertGreater(t["match_sep"], 60.0)
        g = res.guider["candidates"]
        self.assertTrue(g and abs(g[0]["G"] - 10.5) < 1e-9 and abs(g[0]["r"] - 664.0) < 0.5)

    def test_report_and_star_list(self):
        res = F.run(self.tab, self.ra0, self.dec0, F.Settings(basic=True), epoch=2026.86,
                    allow_blank=True)
        rep = F.report(res)
        self.assertIn("EMPTY POSITION", rep)
        self.assertIn("BRIGHTEST STARS IN THE FIELD", rep)
        self.assertNotIn("COMPARISON STARS", rep)
        ft = F.field_table(res)
        self.assertGreater(len(ft), 0)
        self.assertNotIn("target", list(ft["role"]))
        self.assertEqual(set(ft["reasons"]), {""})
        self.assertFalse(any(np.asarray(ft["used_r"])))

    def test_json_for_skybound(self):
        import json
        res = F.run(self.tab, self.ra0, self.dec0, F.Settings(basic=True), epoch=2026.86,
                    allow_blank=True)
        js = F.result_json(res)
        json.dumps(js, allow_nan=False)                     # what the browser parses
        self.assertTrue(js["target"]["blank"])
        self.assertIsNone(js["target"]["source_id"])
        self.assertIsNone(js["target"]["G"])
        self.assertIsNone(js["pointing"]["eta"])
        self.assertTrue(js["warnings"][0].startswith("No Gaia star within 2\""))
        self.assertEqual(js["comparisons"], {b: [] for b in F.BANDS})
        self.assertEqual(len(js["warnings"]), 1)

    def test_dragged_pointing_and_advanced(self):
        s = F.Settings(t_exp={"g": 10, "r": 5, "i": 5, "z": 10}, mode="polar")
        res = F.run(self.tab, self.ra0, self.dec0, s, epoch=2026.86, at=(30.0, -20.0),
                    allow_blank=True)
        self.assertEqual((res.best["dE"], res.best["dN"]), (30.0, -20.0))
        self.assertTrue(res._fixed)
        ft = F.field_table(res)
        self.assertTrue(np.all(np.isfinite(np.asarray(ft["peak_adu_r"], float))))
        self.assertIn("x2_r", ft.colnames)
        self.assertIn("t_exp g=10s", F.report(res))

    def test_no_exposure_times(self):
        import tempfile
        res = F.run(self.tab, self.ra0, self.dec0, F.Settings(), epoch=2026.86, allow_blank=True)
        self.assertIn("t_exp not given", F.report(res))
        self.assertIsNone(F.result_json(res)["settings"]["t_exp"]["g"])
        with tempfile.TemporaryDirectory() as d:
            F.write_field_csv(res, os.path.join(d, "x.csv"))
            with open(os.path.join(d, "x.csv")) as f:
                self.assertIn("t_exp not given", f.read())

    def test_a_star_there_is_still_the_target(self):
        tab = _cone(self.ra0, self.dec0, hole=0.0)
        tab["ra"][0], tab["dec"][0] = self.ra0, self.dec0
        res = F.run(tab, self.ra0, self.dec0, F.Settings(basic=True), epoch=2026.86,
                    allow_blank=True)
        self.assertFalse(res.target.get("blank", False))
        self.assertEqual(res.target["index"], 0)


class TestComparisonLimit(unittest.TestCase):
    """At most N comparison stars (default 6), chosen inside the eta search."""

    @classmethod
    def setUpClass(cls):
        tab = _cone(150.0, -20.0, hole=0.0, seed=7, n_stars=3000)   # ~150 usable stars in the field
        tab["ra"][0], tab["dec"][0] = 150.0, -20.0
        for b, off in zip(F.BANDS, (0.5, -0.1, -0.3, -0.4)):
            tab[f"{b}_sdss_mag"][0] = 13.0 + off * tab["bp_rp"][0]
        tab["phot_g_mean_mag"][0] = 13.0
        tab["V"], tab["V_source"] = F.johnson_v(tab)
        cls.tab = tab
        cls.capped = F.run(tab, 150.0, -20.0, F.Settings(basic=True), epoch=2026.9)
        cls.free = F.run(tab, 150.0, -20.0, F.Settings(basic=True, max_comps=None), epoch=2026.9)

    def distinct(self, res):
        return set().union(*[set(v.tolist()) for v in res.comps.values()])

    def test_default_is_six(self):
        self.assertEqual(F.Settings().max_comps, 6)
        self.assertGreater(len(self.distinct(self.free)), 30)      # the problem: dozens
        self.assertEqual(len(self.distinct(self.capped)), 6)

    def test_the_best_six_at_that_pointing(self):
        # the chosen set is the 6 usable stars with the largest summed relative weight
        r, s = self.capped, self.capped.settings
        it, ce, cn = r.target["index"], r.best["dE"], r.best["dN"]
        ok, weight, sigma, *_ , smear = F._eligibility(r.stars, it, s)
        score = {}
        for b in s.bands:
            idx, use = F._usable(b, ce, cn, r.stars, ok, smear, r.per, s)
            for k in idx[use[0]]:
                score[k] = score.get(k, 0) + weight[k] / sigma[b][k] ** 2 * sigma[b][it] ** 2
        top = set(sorted(score, key=score.get, reverse=True)[:6])
        self.assertEqual(self.distinct(r), top)

    def test_eta_consistent_and_optimised_for_six(self):
        r = self.capped
        self.assertAlmostEqual(r.best["eta_nominal"], max(r.best["eta_bands"].values()), places=9)
        self.assertGreaterEqual(r.best["eta_nominal"], self.free.best["eta_nominal"] - 1e-12)
        # scored with 6 stars, the search beats the pointing that was best for all stars
        args = F._eligibility(r.stars, r.target["index"], r.settings)
        at_free = F.run(self.tab, 150.0, -20.0, F.Settings(basic=True), epoch=2026.9,
                        at=(self.free.best["dE"], self.free.best["dN"]))
        self.assertLessEqual(r.best["eta_robust"], at_free.best["eta_nominal"] + 1e-12)

    def test_reasons_report_and_json(self):
        ft = F.field_table(self.capped)
        lim = [w for w in ft["reasons"] if "comparison limit" in w]
        self.assertGreater(len(lim), 20)
        self.assertEqual(sum(ft["role"] == "comparison"), 6)
        rep = F.report(self.capped)
        self.assertIn("at most 6 comparisons", rep)
        self.assertRegex(rep, r"\+ \d+ more stars qualify here; the limit keeps the 6")
        self.assertEqual(F.result_json(self.capped)["settings"]["max_comps"], 6)
        self.assertNotIn("comparison limit", " ".join(F.field_table(self.free)["reasons"]))

    def test_custom_limit_and_helper_parameter(self):
        r3 = F.run(self.tab, 150.0, -20.0, F.Settings(basic=True, max_comps=3), epoch=2026.9)
        self.assertEqual(len(self.distinct(r3)), 3)
        self.assertEqual(F.settings_from_query({"max_comps": "0"}).max_comps, None)
        self.assertEqual(F.settings_from_query({"max_comps": "10"}).max_comps, 10)
        with self.assertRaises(ValueError):
            F.settings_from_query({"max_comps": "-1"})


class TestIsolationSweep(unittest.TestCase):
    """_contaminations (nearby pairs only) == the old all-pairs matrices."""

    @staticmethod
    def brute(E, N, G, r, dmag, polar):
        dE, dN = E[:, None] - E[None, :], N[:, None] - N[None, :]
        bright = (G[None, :] - G[:, None]) < dmag
        np.fill_diagonal(bright, False)
        close = np.hypot(dE, dN) < r
        if polar:
            ve, vn = F._twin()
            close |= np.hypot(dE - ve, dN - vn) < r
            close |= np.hypot(dE + ve, dN + vn) < r
        i, j = np.where(close & bright)
        return set(zip(i.tolist(), j.tolist()))

    def test_same_pairs(self):
        rng = np.random.default_rng(5)
        for polar in (False, True):
            for n, box in ((1500, 400.0), (900, 120.0)):       # sparse and crowded
                E, N = rng.uniform(-box, box, n), rng.uniform(-box, box, n)
                E[:20] = E[20:40] + rng.normal(0, 3, 20)     # some tight pairs
                G = rng.uniform(9, 18, n)
                i, j = F._contaminations(E, N, G, 7.5, 5.0, polar)
                self.assertEqual(set(zip(i.tolist(), j.tolist())), self.brute(E, N, G, 7.5, 5.0, polar),
                                 f"polar={polar} n={n}")

    def test_eligibility_reasons_unchanged(self):
        tab = _cone(150.0, -20.0, hole=0.0, seed=7, n_stars=1500)
        tab["ra"][0], tab["dec"][0] = 150.0, -20.0                  # the target
        res = F.run(tab, 150.0, -20.0, F.Settings(basic=True, mode="polar"), epoch=2026.9)
        self.assertFalse(res.target.get("blank", False))
        E, N = np.asarray(res.stars["E"]), np.asarray(res.stars["N"])
        G = np.asarray(res.stars["phot_g_mean_mag"], float)
        r = F.Settings().iso_fwhm * F.Settings().fwhm_plan
        bad = {i for i, _ in self.brute(E, N, G, r, F.Settings().iso_dmag, True)}
        tagged = {i for i, w in res.reasons.items() if any(x.startswith("neighbour") for x in w)}
        self.assertEqual(tagged, bad)


class TestReducedConeUpgrade(unittest.TestCase):
    """With only the reduced (GAVO) cone cached, the helper answers from it at once
    and fetches the full cone from ESA in the background, at most every 15 min."""

    def setUp(self):
        import tempfile, threading, json
        self.tmp = tempfile.TemporaryDirectory()
        d = self.dir = self.tmp.name
        t = _cone(170.3, -66.17, hole=0.0, seed=11, n_stars=1500)
        t["ra"][0], t["dec"][0] = 170.3, -66.17
        keep = ["source_id", "ra", "dec", "pmra", "pmdec", "phot_g_mean_mag", "bp_rp", "ruwe",
                "phot_variable_flag", "non_single_star", "g_sdss_mag", "r_sdss_mag", "i_sdss_mag", "z_sdss_mag"]
        self.csv = t[keep]
        self.csv.write(os.path.join(d, "cone_toi_x_lite.csv"), format="ascii.csv")
        with open(os.path.join(d, "cone_toi_x_lite.json"), "w") as f:
            json.dump({"target": "TOI-X", "ra": 170.3, "dec": -66.17, "source": "MAST",
                       "downloaded": "2026-11-12T00:00:00"}, f)
        self.calls = []
        box, ready = {}, threading.Event()

        def up(httpd):
            box["httpd"] = httpd
            ready.set()
        self.thread = threading.Thread(target=F.serve, kwargs=dict(port=0, cache_dir=d, ready=up), daemon=True)
        self.err = io.StringIO()
        self.p = mock.patch("sys.stderr", new=self.err)
        self.p.start()
        self.thread.start()
        ready.wait(10)
        self.httpd = box["httpd"]
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join(10)
        self.p.stop()
        self.tmp.cleanup()

    def field(self, at=""):
        import json, time, urllib.request
        t0 = time.time()
        with urllib.request.urlopen(f"{self.base}/api/field?target=TOI-X&basic=1&epoch=2026.9{at}", timeout=60) as r:
            js = json.loads(r.read())
        return js, time.time() - t0

    def test_slow_failing_esa_never_blocks(self):
        import time

        def slow_fail(text, rz, cache_dir, lite, radius=F.CONE_RADIUS):
            self.calls.append((text, lite, rz.ra, rz.dec, rz.source))
            time.sleep(3)
            raise F.GaiaArchiveError("Can not execute the following SQL ... canceling statement")
        with mock.patch.object(F, "_download", slow_fail):
            for at in ("", "&at=50.0,3.5", "&at=10.0,-20.0", "&at=0.0,0.0"):
                js, dt = self.field(at)
                self.assertLess(dt, 2.0, f"request {at!r} waited for ESA")
                self.assertIn("reduced", js["catalogue"]["note"])
            time.sleep(3.5)
            self.field("&at=5.0,5.0")
        self.assertEqual(len(self.calls), 1, "ESA tried more than once within 15 min")
        text, lite, ra, dec, src = self.calls[0]
        self.assertEqual((text, lite, ra, dec, src), ("TOI-X", False, 170.3, -66.17, "MAST"))
        self.assertIn("not available", self.err.getvalue())

    def test_full_cone_used_once_it_arrives(self):
        import json, time

        def ok(text, rz, cache_dir, lite, radius=F.CONE_RADIUS):
            self.calls.append(text)
            time.sleep(1)
            self.csv.write(os.path.join(cache_dir, "cone_toi_x.csv"), format="ascii.csv")
            with open(os.path.join(cache_dir, "cone_toi_x.json"), "w") as f:
                json.dump({"target": text, "ra": rz.ra, "dec": rz.dec, "source": rz.source,
                           "downloaded": "2026-11-12T01:00:00"}, f)
            return None, rz
        with mock.patch.object(F, "_download", ok):
            js, dt = self.field()
            self.assertIn("reduced", js["catalogue"]["note"])
            self.assertLess(dt, 2.0)
            time.sleep(1.8)
            js, _ = self.field("&at=10.0,10.0")
        self.assertNotIn("reduced", js["catalogue"]["note"])
        self.assertEqual(self.calls, ["TOI-X"])


# ----------------------------------------------------------------------------
# Any instrument's footprint (SkyBound's loaded FoVs), s4field 0.13
# ----------------------------------------------------------------------------

def _square(h, cx=0.0, cy=0.0):
    return {"shape": "polygon", "pts": [[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]]}


class TestFovGeometry(unittest.TestCase):
    def test_margin_gap_and_circle(self):
        m = F.FovGeometry([_square(150)]).mask(12)
        self.assertTrue(m.inside(0, 0) and m.inside(137, 0) and m.inside(-137, 137))
        self.assertFalse(m.inside(139, 0) or m.inside(0, -139) or m.inside(200, 0))
        self.assertAlmostEqual(m.area / 276 ** 2, 1, delta=0.002)
        two = F.FovGeometry([{"shape": "polygon", "pts": [[-210, -100], [-10, -100], [-10, 100], [-210, 100]]},
                             {"shape": "polygon", "pts": [[10, -100], [210, -100], [210, 100], [10, 100]]}])
        self.assertFalse(two.mask(12).inside(0, 0))                 # the chip gap
        self.assertFalse(two.mask(12).inside(21, 0))                # 11" from its chip's edge
        self.assertTrue(two.mask(12).inside(23, 0) and two.mask(0).inside(11, 0))
        c = F.FovGeometry([{"shape": "circle", "c": [0, 0], "r": 100}])
        self.assertTrue(c.mask(12).inside(87.5, 0))
        self.assertFalse(c.mask(12).inside(88.5, 0))

    def test_curved_chip_is_simplified(self):
        a = np.radians(np.arange(0, 360, 0.5))                     # a 720-vertex circle
        g = F.FovGeometry([{"shape": "polygon", "pts": np.c_[300 * np.cos(a), 300 * np.sin(a)].tolist()}])
        self.assertLess(len(g.regions[0]["pts"]), 200)
        self.assertAlmostEqual(g.mask(0).area / (math.pi * 300 ** 2), 1, delta=0.003)

    def test_bad_regions(self):
        for bad in ([], [{"shape": "polygon", "pts": [[0, 0], [1, 1]]}], [{"shape": "box"}],
                    [{"shape": "circle", "c": [0, 0], "r": -1}]):
            with self.assertRaises((ValueError, KeyError)):
                F.FovGeometry(bad)

    def test_cone_size(self):
        # an 11.7' square (iKon-L on the 0.6 m B&C) fits SPARC4's cached 0.27 deg cone;
        # a 12.7' one (on the Zeiss) asks for 0.31 deg; HSC-sized ones stop at 1 deg
        self.assertTrue(F.FovGeometry([_square(352)]).covers(F.CONE_RADIUS))
        z = F.FovGeometry([_square(380)])
        self.assertFalse(z.covers(F.CONE_RADIUS))
        self.assertEqual(z.cone_radius(), 0.31)
        self.assertEqual(F.FovGeometry([_square(2700)]).cone_radius(), F.CONE_RADIUS_MAX)

    def test_large_field_reach_and_guider(self):
        # ROBO43 (42.1' x 28.1'): the target may sit anywhere on the detector, and
        # the cone also holds its off-axis guider, 1635" out (0.89 deg, not 0.67)
        rect = {"shape": "polygon", "pts": [[-1263.5, -842.8], [1263.5, -842.8], [1263.5, 842.8], [-1263.5, 842.8]]}
        g = F.FovGeometry([rect])
        self.assertEqual(g.reach(), 1263.5)
        self.assertEqual(g.cone_radius(), 0.85)
        gg = F.FovGeometry([rect], outer=1635.0)
        self.assertEqual(gg.reach(), 1263.5)
        self.assertEqual(gg.cone_radius(), 0.89)
        # a field too large for a 1 deg cone searches less, so the cone still holds it
        big = F.FovGeometry([_square(1500)], outer=1600.0)
        self.assertLess(big.reach(), 1500)
        self.assertLessEqual(big.need(), F.CONE_RADIUS_MAX + 1e-9)
        with self.assertRaises(ValueError):
            F.FovGeometry([rect], outer=-1)


class TestLoadedFovField(unittest.TestCase):
    def setUp(self):
        self.tab = _cone(150.0, -20.0, hole=0.0, seed=7, n_stars=3000)
        sep = np.hypot(*F.tangent_offsets(np.asarray(self.tab["ra"]), np.asarray(self.tab["dec"]), 150.0, -20.0))
        it = int(np.argmin(sep))
        self.ra, self.dec = float(self.tab["ra"][it]), float(self.tab["dec"][it])

    def run_fov(self, regions, at=None, **q):
        geom = F.FovGeometry(regions)
        s = F.fov_settings_from_query({k: str(v) for k, v in q.items()})
        res = F.run(self.tab, self.ra, self.dec, s, epoch=2026.9, geometry=geom, at=at)
        return res, F.result_json_fov(res, "test")

    def test_comparisons_on_the_detector_with_margin(self):
        res, js = self.run_fov([_square(352)])
        self.assertEqual(len(js["comparisons"]), 6)
        self.assertTrue(1 < js["pointing"]["eta"] < 1.5)
        ce, cn = res.best["dE"], res.best["dN"]
        for r in js["stars"]:
            if r["role"]:
                x, y = r["dE_target_arcsec"] - ce, r["dN_target_arcsec"] - cn
                self.assertLessEqual(max(abs(x), abs(y)), 352 - 12 + 0.5, r["label"])
        self.assertEqual(js["settings"]["band"], "V")
        tgt = [r for r in js["stars"] if r["role"] == "target"][0]
        self.assertEqual(tgt["dmag"], 0.0)
        import json
        json.dumps(js, allow_nan=False)                           # JavaScript can read it

    def test_never_in_a_chip_gap(self):
        chips = [{"shape": "polygon", "pts": [[-352, -352], [-15, -352], [-15, 352], [-352, 352]]},
                 {"shape": "polygon", "pts": [[15, -352], [352, -352], [352, 352], [15, 352]]}]
        res, js = self.run_fov(chips, max_comps=0)
        ce = res.best["dE"]
        self.assertGreater(len(js["comparisons"]), 6)
        for r in js["stars"]:
            if r["role"] == "comparison":
                self.assertGreaterEqual(abs(r["dE_target_arcsec"] - ce), 15 + 12 - 0.5, r["label"])
        self.assertTrue(res.best["target_inside"])
        self.assertGreaterEqual(abs(ce), 27 - 0.5)                  # the target is off the gap too

    def test_band_twin_and_fixed_pointing(self):
        res, js = self.run_fov([_square(352)], at=(20, -10), band="G", twin="-6.5,-12.1", max_comps=3)
        self.assertTrue(js["pointing"]["fixed"])
        self.assertEqual((js["pointing"]["offset_ra_cosdec_arcsec"], js["pointing"]["offset_dec_arcsec"]), (20, -10))
        self.assertEqual(js["settings"]["twin"], [-6.5, -12.1])
        self.assertLessEqual(len(js["comparisons"]), 3)
        for r in js["stars"]:
            if r["role"] == "comparison":
                self.assertAlmostEqual(r["mag"], r["G"], places=3)
                x2, y2 = r["dE_field_arcsec"] - 6.5, r["dN_field_arcsec"] - 12.1
                self.assertLessEqual(max(abs(x2), abs(y2)), 352 - 12 + 0.5)   # second image on it too
        with self.assertRaises(ValueError):
            F.fov_settings_from_query({"band": "K"})

    def test_blank_position(self):
        tab = _cone(136.7167, 8.0)
        res = F.run(tab, 136.7167, 8.0, F.fov_settings_from_query({}), epoch=2026.9,
                    geometry=F.FovGeometry([_square(300)]), allow_blank=True)
        js = F.result_json_fov(res)
        self.assertTrue(js["target"]["blank"])
        self.assertEqual(js["comparisons"], [])
        self.assertTrue(any("No Gaia star" in w for w in js["warnings"]))

    def test_too_small_for_the_margin(self):
        res, js = self.run_fov([_square(3)])                     # a 6" IFU, 12" margin
        self.assertEqual(js["comparisons"], [])
        self.assertEqual((js["pointing"]["offset_ra_cosdec_arcsec"], js["pointing"]["offset_dec_arcsec"]), (0.0, 0.0))
        self.assertTrue(any("too small" in w for w in js["warnings"]))
        import json
        json.dumps(js, allow_nan=False)

    def test_sparc4_unchanged(self):
        res = F.run(self.tab, self.ra, self.dec, F.Settings(basic=True), epoch=2026.9)
        self.assertIsNone(res._geometry)
        self.assertEqual(set(res.best["target_pixel"]), set(F.BANDS))


class TestConeRadiusCache(unittest.TestCase):
    def test_small_cached_cone_is_not_enough_but_used_offline(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            t = _cone(170.3, -66.17, hole=0.0, seed=11, n_stars=300)
            t.write(os.path.join(d, "cone_toi_y.csv"), format="ascii.csv")
            with open(os.path.join(d, "cone_toi_y.json"), "w") as f:
                json.dump({"target": "TOI-Y", "ra": 170.3, "dec": -66.17, "source": "MAST",
                           "downloaded": "2026-11-12T00:00:00"}, f)       # no radius: SPARC4's 0.27
            self.assertIsNotNone(F._read_cached("TOI-Y", d, False, need=0.25))
            self.assertIsNone(F._read_cached("TOI-Y", d, False, need=0.29))
            tab, rz = F.obtain_field("TOI-Y", d, offline=True, radius=0.31, need=0.29)
            self.assertIn("SMALLER than this FoV needs", rz.catalogue)

    def test_larger_cone_reuses_the_cached_position(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            t = _cone(170.3, -66.17, hole=0.0, seed=11, n_stars=300)
            t.write(os.path.join(d, "cone_toi_z.csv"), format="ascii.csv")
            with open(os.path.join(d, "cone_toi_z.json"), "w") as f:
                json.dump({"target": "TOI-Z", "ra": 170.3, "dec": -66.17, "source": "MAST",
                           "downloaded": "2026-11-12T00:00:00"}, f)
            seen = []

            def dl(text, rz, cache_dir, lite, radius=F.CONE_RADIUS):
                seen.append((rz.ra, rz.dec, rz.source, radius))
                raise F.GaiaArchiveError("down")
            with mock.patch.object(F, "_download", dl), mock.patch.object(F, "resolve_target") as rt, \
                    mock.patch("sys.stderr", new=io.StringIO()):
                tab, rz = F.obtain_field("TOI-Z", d, radius=0.31, need=0.29)
            rt.assert_not_called()                                     # no new name lookup
            self.assertEqual(seen[0], (170.3, -66.17, "MAST", 0.31))
            self.assertIn("SMALLER", rz.catalogue)

    def test_download_asks_for_the_radius(self):
        import json, tempfile
        seen = {}

        def fake(adql):
            seen["adql"] = adql
            return "source_id,ra,dec,pmra,pmdec,phot_g_mean_mag,bp_rp,ruwe\n1,10.0,-20.0,0,0,12,0.8,1.0\n"
        with tempfile.TemporaryDirectory() as d, mock.patch.object(F, "gavo_tap", fake), \
                mock.patch("sys.stderr", new=io.StringIO()):
            rz = F.Resolution("X", 10.0, -20.0, "test")
            F._download("X", rz, d, True, 0.31)
            with open(os.path.join(d, "cone_x_lite.json")) as f:
                self.assertEqual(json.load(f)["radius_deg"], 0.31)
        self.assertIn("0.3100))", seen["adql"])


class TestServeFov(unittest.TestCase):
    """POST /api/fov, then /api/field?fov=..., over real HTTP."""

    @classmethod
    def setUpClass(cls):
        import tempfile, threading, json
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        t = _cone(150.0, -20.0, hole=0.0, seed=7, n_stars=3000)
        t.write(os.path.join(d, "cone_fov_test.csv"), format="ascii.csv")
        sep = np.hypot(*F.tangent_offsets(np.asarray(t["ra"]), np.asarray(t["dec"]), 150.0, -20.0))
        it = int(np.argmin(sep))
        with open(os.path.join(d, "cone_fov_test.json"), "w") as f:
            json.dump({"target": "FOV-TEST", "ra": float(t["ra"][it]), "dec": float(t["dec"][it]),
                       "source": "test", "downloaded": "2026-11-12T00:00:00"}, f)
        box, ready = {}, threading.Event()

        def up(httpd):
            box["httpd"] = httpd
            ready.set()
        cls.thread = threading.Thread(target=F.serve, kwargs=dict(port=0, cache_dir=d, ready=up), daemon=True)
        with mock.patch("sys.stderr", new=io.StringIO()):
            cls.thread.start()
            ready.wait(10)
        cls.httpd = box["httpd"]
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(10)
        cls.tmp.cleanup()

    def call(self, path, body=None, method=None):
        import json, urllib.request, urllib.error
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if data else "GET"),
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with mock.patch("sys.stderr", new=io.StringIO()), urllib.request.urlopen(req, timeout=120) as r:
                txt = r.read().decode()
                return r.status, dict(r.headers), json.loads(txt) if txt else None
        except urllib.error.HTTPError as e:
            with e:
                return e.code, dict(e.headers), json.loads(e.read().decode())

    def test_post_then_field(self):
        code, _, js = self.call("/api/fov", {"science": [_square(352)], "name": "iKon-L"})
        self.assertEqual(code, 200)
        self.assertEqual(js["regions"], 1)
        fid = js["fov"]
        code, _, f = self.call(f"/api/field?target=FOV-TEST&fov={fid}&band=V&margin=12&iso=7.5&epoch=2026.9&offline=1")
        self.assertEqual(code, 200, f)
        self.assertTrue(f["generic"])
        self.assertEqual(len(f["comparisons"]), 6)
        self.assertIn("iKon-L", f["report"])
        self.assertEqual(self.call("/api/fov", {"science": [_square(352)]})[2]["fov"], fid)   # same footprint, same id

    def test_larger_cone_never_blocks(self):
        import time
        calls = []

        def slow_fail(text, rz, cache_dir, lite, radius=F.CONE_RADIUS):
            calls.append((text, radius))
            time.sleep(5)
            raise F.GaiaArchiveError("down")
        fid = self.call("/api/fov", {"science": [_square(380)]})[2]["fov"]     # 12.7': needs 0.31 deg
        with mock.patch.object(F, "_download", slow_fail):
            for at in ("", "&at=30,0", "&at=0,40"):
                t0 = time.time()
                code, _, f = self.call(f"/api/field?target=FOV-TEST&fov={fid}&epoch=2026.9{at}")
                self.assertEqual(code, 200, f)
                self.assertLess(time.time() - t0, 4.0)                 # the search, not the 5 s download
                self.assertIn("SMALLER", f["catalogue"]["note"])
                self.assertTrue(any("background" in w for w in f["warnings"]))
            time.sleep(10.5)
        self.assertEqual({c[1] for c in calls}, {0.31})
        self.assertLessEqual(len(calls), 2)                      # ESA, then GAVO, once in 15 min

    def test_errors_and_preflight(self):
        code, _, js = self.call("/api/field?target=FOV-TEST&fov=nope&offline=1")
        self.assertEqual((code, js["code"]), (404, "unknown_fov"))
        self.assertEqual(self.call("/api/fov", {"science": []})[0], 400)
        self.assertEqual(self.call("/api/fov", {"nothing": 1})[0], 400)
        code, h, _ = self.call("/api/fov", method="OPTIONS")
        self.assertEqual(code, 204)
        self.assertIn("POST", h["Access-Control-Allow-Methods"])
