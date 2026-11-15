"""The QGIS styles must stay the app's palette, not a second opinion about it.

`scripts/style_qgis_layers.py` promises that a QGIS map and the Streamlit/PyDeck app cannot drift
apart, because every colour is read from `src.domain.map_adapter`. That promise is only worth
something if something fails when it stops being true — which is what this module does.

Two distinct failures are covered, and they are not the same test:

* **Parity with the app.** The rendered colours, the risk hexes and the context alphas are
  recomputed here from `map_adapter`/`build_legend_spec` and compared to what the script emitted.
  If someone retunes the ramp for the pitch figure, the QML must move with it.
* **Pinned appearance.** The velocity classes are also asserted against a literal table, because
  parity alone would happily follow the app off a cliff: an edit that swapped crimson for blue
  would pass "matches `map_adapter`" and still publish a map where subsidence reads as uplift.
  The literal table is the documented swatch order (deepest crimson on the strongest subsidence,
  one flat slate slab at the detection floor, deep ocean blue on the strongest uplift).

The styling decisions that carry scientific meaning get their own assertions: the |v| <= 5 mm/yr
band is a single flat class rather than a gradient, coherence is cut only at the app's own anchors
(no invented 0.85 break), the admin boundary keeps its transparent fill, and every grid symbol is
a plain **fill** — because the geometry exported for QGIS is the cell polygon itself.

Rendering is verified by QGIS itself, not here: each emitted style was loaded onto a real layer
offscreen (`qgis --code`, `loadNamedStyle`) and read back, and a 1600x900 render was inspected.
The first attempt drew markers sized in map units, which QGIS reads in the *project* CRS: with
EPSG:4326 that unit is degrees, so the map was three hexagons spanning the globe. Hence
`test_no_symbol_is_sized_in_map_units` — that failure is invisible in the XML and obvious on the
canvas, which is exactly the kind of thing that needs a test rather than a habit of looking.
This suite keeps it true without needing QGIS installed, so CI stays hermetic.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domain import map_adapter as MA  # noqa: E402

STYLES_MANIFEST = "styles_manifest.json"

#: The documented swatch table, pinned. Order matters: this is the colour a reader sees.
VELOCITY_TABLE = (
    (-425.0, -275.0, "#67001f"),
    (-275.0, -150.0, "#b2182b"),
    (-150.0, -60.0, "#d6604d"),
    (-60.0, -25.0, "#f4a582"),
    (-25.0, -5.0, "#fddbc7"),
    (-5.0, 5.0, "#94a3b8"),
    (5.0, 25.0, "#d1e5f0"),
    (25.0, 150.0, "#4393c3"),
    (150.0, 425.0, "#053061"),
)

#: Context alphas per layer, from CONTEXT_LAYER_STYLES. Alpha is the honesty channel here: a solid
#: land-use fill would hide the cells it is supposed to sit beneath.
CONTEXT_ALPHAS = {
    "infrastructure": (180, 240),
    "water (area)": (75, 220),
    "water (course)": (220, None),
    "land use": (40, 110),
    "admin boundary": (0, 200),
}


@pytest.fixture(scope="module")
def styled(tmp_path_factory) -> pathlib.Path:
    out = tmp_path_factory.mktemp("qgis_styles")
    completed = subprocess.run(
        [sys.executable, "scripts/style_qgis_layers.py", "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (completed.stdout + completed.stderr)[-1500:]
    return out


def _rgba_to_hex(value: str) -> str:
    r, g, b, *_ = (int(part) for part in value.split(","))
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgba_alpha(value: str) -> int:
    return int(value.split(",")[3])


def _options(symbol: ET.Element) -> dict[str, str]:
    """Flatten a symbol's `<Option name=... value=.../>` children."""
    return {opt.get("name", ""): opt.get("value", "") for opt in symbol.iter("Option")}


def _ranges(qml: pathlib.Path) -> list[tuple[float, float, str]]:
    """Every graduated class as (lower, upper, colour-hex), in document order."""
    root = ET.parse(qml).getroot()
    symbols = root.findall(".//renderer-v2/symbols/symbol")
    out = []
    for rng in root.findall(".//renderer-v2/ranges/range"):
        index = int(rng.get("symbol", "0"))
        colour = _rgba_to_hex(_options(symbols[index])["color"])
        out.append((float(rng.get("lower")), float(rng.get("upper")), colour))
    return out


def _rules(qml: pathlib.Path) -> list[dict]:
    """Every rule as {label, filter, symbol options}."""
    root = ET.parse(qml).getroot()
    symbols = root.findall(".//renderer-v2/symbols/symbol")
    out = []
    for rule in root.findall(".//renderer-v2/rules/rule"):
        index = int(rule.get("symbol", "0"))
        out.append(
            {
                "label": rule.get("label", ""),
                "filter": rule.get("filter", ""),
                "symbol": symbols[index],
                "options": _options(symbols[index]),
            }
        )
    return out


def _categories(qml: pathlib.Path) -> dict[str, str]:
    root = ET.parse(qml).getroot()
    symbols = root.findall(".//renderer-v2/symbols/symbol")
    return {
        cat.get("value"): _rgba_to_hex(_options(symbols[int(cat.get("symbol", "0"))])["color"])
        for cat in root.findall(".//renderer-v2/categories/category")
    }


# --------------------------------------------------------------------------------------------
# parity with the application
# --------------------------------------------------------------------------------------------


def test_emits_every_style_with_a_manifest(styled: pathlib.Path) -> None:
    manifest = json.loads((styled / STYLES_MANIFEST).read_text())
    on_disk = sorted(p.name for p in styled.glob("*.qml"))
    assert sorted(manifest["styles"]) == on_disk, "manifest and emitted files disagree"
    assert "cell_geometry" in manifest, "the manifest must state what geometry the styles expect"
    for name, entry in manifest["styles"].items():
        assert entry["renderer"], f"{name}: manifest entry needs a renderer"
        assert "note" in entry, f"{name}: an undocumented style is a style nobody can defend"


def test_velocity_classes_are_the_documented_swatch_table(styled: pathlib.Path) -> None:
    assert _ranges(styled / "grid_velocity.qml") == list(VELOCITY_TABLE)


def test_velocity_colours_follow_map_adapter(styled: pathlib.Path) -> None:
    """Parity, not taste: the app's shared ramp must produce exactly these colours."""
    spec = MA.build_legend_spec({}, metric="velocity_mm_yr", scale_mode="shared")
    colours: list[str] = []
    for stop in spec.stops:
        if not colours or colours[-1] != stop.color_hex:
            colours.append(stop.color_hex)
    emitted = [colour for _, _, colour in _ranges(styled / "grid_velocity.qml")]
    assert emitted == colours, (
        "the QML ramp and src.domain.map_adapter disagree — the app, the figures and QGIS must show "
        "one palette, so fix whichever of the two drifted"
    )


def test_neutral_band_stays_one_flat_slab(styled: pathlib.Path) -> None:
    """|v| <= 5 mm/yr is the detection floor. A gradient there would invent weak subsidence."""
    neutral = [rng for rng in _ranges(styled / "grid_velocity.qml") if rng[0] == -5.0 and rng[1] == 5.0]
    assert len(neutral) == 1
    assert neutral[0][2] == "#94a3b8"
    slate = [rng for rng in _ranges(styled / "grid_velocity.qml") if rng[2] == "#94a3b8"]
    assert len(slate) == 1, "slate must be one band, not a multi-class neutral gradient"


def test_coherence_is_cut_only_at_the_apps_anchors(styled: pathlib.Path) -> None:
    """No invented break: a 0.85 split would be a threshold no document authorises."""
    bounds = [lo for lo, _, _ in _ranges(styled / "grid_coherence.qml")]
    assert bounds == [0.0, 0.35, 0.65]


def test_risk_categories_are_the_frozen_band_colours(styled: pathlib.Path) -> None:
    expected = {
        level: f"#{r:02x}{g:02x}{b:02x}" for level, (r, g, b) in MA.RISK_COLORS_RGB.items()
    }
    assert _categories(styled / "grid_risk.qml") == expected


def test_delta_scale_is_the_shared_domain(styled: pathlib.Path) -> None:
    ranges = _ranges(styled / "grid_delta.qml")
    assert (ranges[0][0], ranges[-1][1]) == MA.GLOBAL_DELTA_RANGE_MM
    assert len(ranges) == len(VELOCITY_TABLE)


# --------------------------------------------------------------------------------------------
# the styling decisions that carry meaning
# --------------------------------------------------------------------------------------------


def test_context_layers_keep_the_app_alphas(styled: pathlib.Path) -> None:
    rules = {rule["label"]: rule for rule in _rules(styled / "context_layers.qml")}
    for label, (fill_alpha, line_alpha) in CONTEXT_ALPHAS.items():
        assert label in rules, f"context rule {label!r} disappeared"
        options = rules[label]["options"]
        fill = options.get("color") or options.get("line_color")
        assert _rgba_alpha(fill) == fill_alpha, f"{label}: fill alpha drifted from map_adapter"
        if line_alpha is not None:
            assert _rgba_alpha(options["outline_color"]) == line_alpha


def test_water_rules_split_by_geometry(styled: pathlib.Path) -> None:
    """`water` is a Polygon *and* a LineString in the same file; one symbol cannot serve both."""
    rules = _rules(styled / "context_layers.qml")
    water = [rule for rule in rules if rule["filter"].startswith('"layer" = \'water\'')]
    assert len(water) == 2, f"expected an area rule and a course rule, got {len(water)}"
    assert any("'Polygon'" in rule["filter"] and rule["symbol"].get("type") == "fill" for rule in water)
    assert any("'Line'" in rule["filter"] and rule["symbol"].get("type") == "line" for rule in water)


def test_admin_boundary_never_hides_the_grid(styled: pathlib.Path) -> None:
    rules = {rule["label"]: rule for rule in _rules(styled / "context_layers.qml")}
    assert rules["admin boundary"]["options"]["style"] == "no"
    assert rules["admin boundary"]["options"]["outline_style"] == "dash"


def test_aoi_ring_is_an_unfilled_dashed_outline(styled: pathlib.Path) -> None:
    root = ET.parse(styled / "aoi_ring.qml").getroot()
    options = _options(root.find(".//renderer-v2/symbols/symbol"))
    assert options["style"] == "no"
    assert options["outline_style"] == "dash"


def test_grid_symbols_are_polygon_fills(styled: pathlib.Path) -> None:
    """Cells are drawn as the exported polygon, so a fill is the only symbol that can be correct."""
    root = ET.parse(styled / "grid_velocity.qml").getroot()
    symbols = root.findall(".//renderer-v2/symbols/symbol")
    assert symbols, "no symbols emitted"
    for symbol in symbols:
        assert symbol.get("type") == "fill", "a grid cell must be filled, not drawn as a marker"
        assert "size" not in _options(symbol), "a fill needs no marker size"


def test_no_symbol_is_sized_in_map_units(styled: pathlib.Path) -> None:
    """Regression guard for the world-of-giant-hexagons bug.

    `size_unit="MapUnit"` means map units of the *project CRS*. In EPSG:4326 that is degrees, so a
    marker meant to be 349 m became 349 degrees of longitude and three hexagons covered the globe.
    Symbol sizes must be in millimetres (screen) — geometry carries the scale, not the symbol.
    """
    for qml in styled.glob("*.qml"):
        root = ET.parse(qml).getroot()
        for symbol in root.findall(".//renderer-v2/symbols/symbol"):
            unit = _options(symbol).get("size_unit")
            assert unit != "MapUnit", (
                f"{qml.name}: marker sized in map units — this is degrees under an EPSG:4326 "
                f"project, and it renders the world as a handful of giant hexagons"
            )


def test_quality_rule_puts_confidence_before_risk(styled: pathlib.Path) -> None:
    """First match wins: a degraded cell must not be coloured as a confident 'critico'."""
    rules = _rules(styled / "grid_quality.qml")
    assert rules[0]["filter"] == '"coherence" < 0.5'
    assert rules[-1]["filter"] == "ELSE"


# --------------------------------------------------------------------------------------------
# the file properties that make a style an artefact
# --------------------------------------------------------------------------------------------


def test_every_style_is_well_formed_xml(styled: pathlib.Path) -> None:
    for qml in styled.glob("*.qml"):
        ET.parse(qml)  # raises on malformed XML


def test_output_is_deterministic(tmp_path_factory) -> None:
    """Byte-identical re-runs keep a style change visible as a real diff."""
    first = tmp_path_factory.mktemp("run_a")
    second = tmp_path_factory.mktemp("run_b")
    for out in (first, second):
        completed = subprocess.run(
            [sys.executable, "scripts/style_qgis_layers.py", "--out", str(out)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, (completed.stdout + completed.stderr)[-1000:]
    for qml in sorted(first.glob("*")):
        assert qml.read_bytes() == (second / qml.name).read_bytes(), f"{qml.name} is not deterministic"
