# FoV samples

Aladin "Load instrument FoV" files used by `tools/fov_test.html`. They are copies of the files as their providers distribute them (collected 2026-11-12), kept here so the parser test runs offline.

| File | Instrument | Source |
|---|---|---|
| `sparc4_fov.xml` | SPARC4, OPD 1.6 m (science square, guider box, patrol annulus; L. Fraga) | COAST/LNA, https://coast.lna.br/home/sparc4/information-for-users |
| `Subaru_HSC_FoV.xml` | Hyper Suprime-Cam, Subaru (104 CCDs; older VertexRAOffset dialect) | https://subarutelescope.org/Observing/queue/misc/hsc_field_check_aladin.html |
| `focas_ifu_fov.vot` | FOCAS IFU, Subaru | https://www.naoj.org/Instruments/FOCAS/ifu/focas_ifu_fov.html |
| `weaveLIFU.vot` | WEAVE Large IFU, WHT (600 fibres as circles) | https://ingpublic.atlassian.net/wiki/spaces/INGPublic/pages/19922999/ |
| `triccs.vot` | TriCCS, Seimei 3.8 m | https://www.o.kwasan.kyoto-u.ac.jp/inst/triccs/performance/triccs.vot |
| `kools-fov.vot` | KOOLS-IFU, Seimei 3.8 m (110 hexagonal fibres) | https://www.o.kwasan.kyoto-u.ac.jp/inst/p-kools/performance/kools-fov.vot |
| `GMOS_N_BBOX_APPROX.xml` | GMOS-N, **approximate** 330″ bounding box, not an official footprint | made by the collection's `make_approx_fovs.py` |
| `skybound_orientation_check.xml` | test pattern: a square at x = +300″, a circle at y = +300″ | SkyBound |

Use the official files for planning; the approximate box ignores chip gaps, guiders and offsets.
