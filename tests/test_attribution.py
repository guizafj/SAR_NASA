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

import pandas as pd
from streamlit.testing.v1 import AppTest

from src.ui.context_view import context_caption

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
    assert "not the origin of the polygons" in text, "tab 3 does not separate the mock provenance id from the OSM polygons"
    # Both causes, not one: naming only "no mapped polygon" is false — in Málaga every unknown cell sits inside one.
    assert "either no mapped polygon contains" in text, text[:300]
    assert "tags map to no class" in text, text[:300]
    # The land_use shapes are dissolved H3 cells, not OSM geometry, and the caption must say so.
    assert "dissolved H3 cells" in text, text[:300]


def test_the_caption_follows_the_payload_instead_of_asserting_openstreetmap() -> None:
    """A synthetic fallback must never be credited to OpenStreetMap.

    The first version of this caption was a literal: driven with a fallback artefact — whose every `Source` cell reads
    `mock (synthetic …)` — it still printed "these context polygons are real OpenStreetMap data". A caption that cannot
    be wrong is a caption nobody checked, so the text is now a pure function of the rows and this test drives it.
    """
    osm = pd.DataFrame(
        {
            "layer": ["water", "land_use"],
            "source": [
                "OpenStreetMap contributors (ODbL 1.0), Overpass API extract",
                "OpenStreetMap contributors (ODbL 1.0), Overpass API extract",
            ],
        }
    )
    fallback = pd.DataFrame(
        {
            "layer": ["water", "land_use"],
            "source": ["mock (synthetic hydrology)", "mock (synthetic land-cover rule)"],
        }
    )
    real_text = context_caption(osm)
    fallback_text = context_caption(fallback)
    assert "OpenStreetMap provenance" in real_text, real_text
    assert "dissolved H3 cells" in real_text, real_text
    assert "OpenStreetMap" not in fallback_text.replace("not OpenStreetMap data", ""), fallback_text
    assert "synthetic fallback" in fallback_text, fallback_text


def test_every_published_case_carries_the_licence_into_its_artefact() -> None:
    for case in CASES:
        meta = json.loads(pathlib.Path(f"data/processed/{case}/meta.json").read_text())
        licence = str(meta.get("provenance", {}).get("license", ""))
        assert "OpenStreetMap contributors" in licence, (case, licence)
        assert "ODbL 1.0" in licence, (case, licence)
