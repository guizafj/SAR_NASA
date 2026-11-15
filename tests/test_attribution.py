"""ODbL 1.0 requires attribution wherever the polygons are shown.

`docs/context-sources.md` §5 owns the wording, `meta.provenance.license` carries it into every artefact, and
`LICENSE` records the carve-out. All three were true while the cockpit showed 708 OpenStreetMap features with no
credit anywhere on screen — the obligation attaches to the display, not to a JSON field the reader never opens.

The second half of this file is the other direction of the same claim: the credit must not be readable as a claim
about the SAR values, which are synthetic and must keep saying so.
"""
from __future__ import annotations

import json
import pathlib

from streamlit.testing.v1 import AppTest

CASES = ("mexico-city", "jakarta", "malaga")
APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"


def _rendered_text() -> str:
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not list(at.exception), [str(e.value) for e in at.exception]
    parts = [element.value for element in at.caption]
    parts += [element.value for element in at.markdown]
    return " ".join(str(part) for part in parts)


def test_the_cockpit_credits_openstreetmap_on_screen() -> None:
    text = _rendered_text()
    assert "OpenStreetMap contributors" in text, "no on-screen ODbL credit"
    assert "ODbL 1.0" in text, text[:400]


def test_the_credit_does_not_claim_the_displacement_is_third_party() -> None:
    text = _rendered_text()
    start = text.index("Context layers ©")
    credit = text[start : start + 260]
    assert "mock" in credit, credit
    assert "synthetic" in credit, credit


def test_tab_three_glosses_unknown_and_separates_the_polygons_from_the_credit() -> None:
    """A reader meets `unknown` and the mock/OSM pairing on tab 3 and must be told what they are.

    Without this, `unknown` reads as missing data (it is 68.1 % of Mexico City's cells) and the provenance credit
    sitting above a table of real OpenStreetMap rows reads as a claim about the polygons. The number is deliberately
    not asserted here — the per-case share belongs to the artefact's own sentence, and recomputing it in the view
    would be science in the UI.
    """
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not list(at.exception), [str(e.value) for e in at.exception]
    tab = at.tabs[2]
    text = "\n".join(str(element.value) for element in [*tab.caption, *tab.markdown])
    assert "published category" in text, "tab 3 shows `unknown` without saying it is a published category"
    assert "not the polygons" in text, "tab 3 does not separate the mock provenance id from the OSM polygons"


def test_every_published_case_carries_the_licence_into_its_artefact() -> None:
    for case in CASES:
        meta = json.loads(pathlib.Path(f"data/processed/{case}/meta.json").read_text())
        licence = str(meta.get("provenance", {}).get("license", ""))
        assert "OpenStreetMap contributors" in licence, (case, licence)
        assert "ODbL 1.0" in licence, (case, licence)
