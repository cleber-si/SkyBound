"""Tests for tools/build_targets.py (synthetic fixture tables with the real column layouts).

    python3 -m unittest discover -s tools -p "test_*.py"
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_targets as B  # noqa: E402

NASA_PS = """# This file was produced by the NASA Exoplanet Archive  (fixture)
# COLUMN pl_name: Planet Name
pl_name,hostname,hd_name,hip_name,tic_id,gaia_id,ra,dec,sy_vmag
AU Mic b,AU Mic,HD 197481,HIP 102409,TIC 441420236,Gaia DR2 6794047652729201024,311.2897,-31.3409,8.81
AU Mic c,AU Mic,HD 197481,HIP 102409,TIC 441420236,Gaia DR2 6794047652729201024,311.2897,-31.3409,8.81
Kepler-1658 b,KOI-4,,,TIC 120571842,,293.0,38.9,11.6
"""
NASA_TOI = """# NASA TOI list (fixture)
toi,tid,tfopwg_disp,ra,dec,st_tmag
2221.01,441420236,KP,311.29,-31.34,6.76
2631.01,12345678,PC,100.5,-20.25,10.1
2631.02,12345678,PC,100.5,-20.25,10.1
"""
EXOFOP = """TIC ID,TOI,RA,Dec,TESS Mag
99999999,9001.01,20:45:09.53,-31:20:27.2,9.0
"""
EU = """name,planet_status,ra,dec,star_name,alternate_names,star_alternate_names
CoRoT-2 b,Confirmed,291.7771,1.3837,CoRoT-2,CoRoT-2 Ab,"2MASS J19270649+0123013, GSC 00465-00211"
AU Mic b,Confirmed,311.0,-31.0,AU Mic,,"HD 197481"
"""
OTHER = "a,b,c\n1,2,3\n"


class BuildTargets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        for name, text in [("PSCompPars.csv", NASA_PS), ("TOI.csv", NASA_TOI), ("exofop.csv", EXOFOP),
                           ("exoplanet.eu_catalog.csv", EU), ("notes.csv", OTHER)]:
            with open(os.path.join(d, name), "w") as f:
                f.write(text)
        self.out = os.path.join(d, "targets.js")
        self.p = B.build([d], self.out, quiet=True)
        self.idx = {}
        for i, (ra, dec, si, names) in enumerate(self.p["rows"]):
            for n in names.split("|"):
                self.idx.setdefault(B.key(n), (ra, dec, self.p["sources"][si], names))

    def tearDown(self):
        self.tmp.cleanup()

    def test_output_is_a_script(self):
        with open(self.out) as f:
            text = f.read()
        self.assertTrue(text.split("\n", 2)[2].startswith("window.SB_TARGETS = "))
        json.loads(text.split("window.SB_TARGETS = ", 1)[1].rstrip().rstrip(";"))

    def test_unknown_table_skipped(self):
        self.assertNotIn("notes.csv", self.p["files"])
        self.assertEqual(len(self.p["files"]), 4)

    def test_aliases_and_keys(self):
        for name in ["AU Mic", "au mic", "HD 197481", "HIP102409", "TIC 441420236", "Gaia DR2 6794047652729201024"]:
            self.assertIn(B.key(name), self.idx, name)
        self.assertEqual(self.idx[B.key("AU Mic")][2], "NASA Exoplanet Archive")

    def test_priority_first_source_keeps_a_name(self):
        # AU Mic is also in exoplanet.eu at a cruder position: the NASA row wins
        self.assertAlmostEqual(self.idx[B.key("AU Mic")][0], 311.2897)
        # TIC 441420236 belongs to the NASA row; TOI-2221 (same star) still resolves via the TOI list
        self.assertEqual(self.idx[B.key("TIC 441420236")][2], "NASA Exoplanet Archive")
        self.assertEqual(self.idx[B.key("TOI-2221")][2], "NASA TOI list")

    def test_planets_fold_into_hosts(self):
        self.assertNotIn(B.key("AU Mic b"), self.idx)            # the page strips " b" itself
        self.assertIn(B.key("Kepler-1658 b"), self.idx)          # host is KOI-4: keep the planet name
        self.assertIn(B.key("TOI-2631"), self.idx)
        self.assertEqual(sum(B.key("TOI-2631") in [B.key(n) for n in r[3].split("|")] for r in self.p["rows"]), 1)

    def test_exofop_sexagesimal(self):
        ra, dec, src, _ = self.idx[B.key("TOI-9001")]
        self.assertEqual(src, "ExoFOP TOI list")
        self.assertAlmostEqual(ra, 311.28971, places=4)
        self.assertAlmostEqual(dec, -31.34089, places=4)

    def test_exoplanet_eu_alternates(self):
        for name in ["CoRoT-2", "GSC 00465-00211", "2MASS J19270649+0123013"]:
            self.assertEqual(self.idx[B.key(name)][2], "exoplanet.eu", name)

    def test_helpers(self):
        self.assertEqual(B.host_of("CoRoT-2 Ab"), "CoRoT-2")
        self.assertEqual(B.host_of("HD 189733 A"), "HD 189733 A")  # a star component, not a planet
        self.assertEqual(B.key("TOI–2631"), "toi2631")             # en dash
        self.assertAlmostEqual(B.angle("-00 30 00", False), -0.5)
        self.assertAlmostEqual(B.angle("12:00:00", True), 180.0)
        self.assertIsNone(B.angle("", True))

    def test_nothing_usable(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "x.csv"), "w") as f:
                f.write(OTHER)
            with self.assertRaises(SystemExit):
                B.build([d], os.path.join(d, "t.js"), quiet=True)


if __name__ == "__main__":
    unittest.main()
