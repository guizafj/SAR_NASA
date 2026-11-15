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

from src.domain import contracts as C  # noqa: E402
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

#: Which symbol a geometry kind must get: a polygon is a fill, a line a line, a point a marker.
SYMBOL_FOR_KIND = {"Polygon": "fill", "Line": "line", "Point": "marker"}

#: Geometry kinds every context class must be styled for, per layer. QGIS's `geometry_type()`
#: collapses `MultiPolygon`/`MultiLineString` onto these three names, and the vocabulary follows the
#: published artefacts: a levee can arrive as a polygon, a pipeline as a line, a district boundary as
#: ways, and a water spring as a point (`docs/context-sources.md` §1 lists `natural=spring`). An
#: `infrastructure` point is deliberately *not* demanded: no case publishes one, so requiring six
#: rules for geometry that cannot currently exist would ask the generator to style the imaginary. A
#: point-valued infrastructure feature still draws through the layer's marker fallback, so nothing
#: is lost — it just is not labelled as a class.
CONTEXT_KINDS = {
    "land_use": ("Polygon",),
    "infrastructure": ("Polygon", "Line"),
    "water": ("Polygon", "Line", "Point"),
    "admin_boundary": ("Polygon", "Line"),
}


def _kinds(layer: str, context_class: str) -> tuple[str, ...]:
    """Kinds one class must be styled for. `aoi` is exempt from the layer's line rules.

    `aoi` is the traced footprint: the published artefacts carry it as a polygon ring only
    (`config/aoi/*.geojson` is a closed ring), so demanding a line or point rule for it would ask
    the generator to style geometry that cannot exist. Every other class follows its layer.
    """
    if layer == "admin_boundary" and context_class == "aoi":
        return ("Polygon",)
    return CONTEXT_KINDS[layer]

#: GeoJSON geometry type -> the name `geometry_type()` reports for it.
_GEOJSON_TO_KIND = {
    "Point": "Point",
    "MultiPoint": "Point",
    "LineString": "Line",
    "MultiLineString": "Line",
    "Polygon": "Polygon",
    "MultiPolygon": "Polygon",
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


def _names(rule: dict, layer: str, context_class: str, kind: str) -> bool:
    """True when the rule's filter is exactly `layer` AND `class` AND this geometry kind."""
    flt = rule["filter"]
    return (
        f'"layer" = \'{layer}\'' in flt
        and f'"class" = \'{context_class}\'' in flt
        and f"geometry_type($geometry) = '{kind}'" in flt
    )


def _published_context_triples() -> set[tuple[str, str, str]]:
    """Every (layer, class, geometry-kind) the published artefacts actually contain."""
    triples: set[tuple[str, str, str]] = set()
    for path in sorted((ROOT / "data/processed").glob("*/context_layers.geojson")):
        for feature in json.loads(path.read_text())["features"]:
            props = feature["properties"]
            triples.add(
                (props["layer"], props["class"], _GEOJSON_TO_KIND[feature["geometry"]["type"]])
            )
    return triples


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


def test_every_context_class_has_a_rule_for_every_geometry_kind(styled: pathlib.Path) -> None:
    """A class is styled once per geometry kind it may arrive as, and every rule is labelled.

    This is the property the old renderer only had for `water`: an unlabelled or missing rule means
    a feature quietly falls through to the layer fallback, which is how `infrastructure` polygons
    and lines, and `admin_boundary` lines, used to disappear into a layer-wide symbol.
    """
    rules = _rules(styled / "context_layers.qml")
    labels = [rule["label"] for rule in rules]
    assert len(labels) == len(set(labels)), "two rules share a label; the QGIS rule keys collide"
    for layer, classes in C.CONTEXT_CLASSES.items():
        for context_class in classes:
            for kind in _kinds(layer, context_class):
                matches = [r for r in rules if _names(r, layer, context_class, kind)]
                assert len(matches) == 1, (
                    f"{layer}/{context_class}/{kind}: expected exactly one rule, "
                    f"found {len(matches)} — it would fall to the layer fallback instead"
                )
                rule = matches[0]
                assert rule["label"].strip(), f"{layer}/{context_class}/{kind}: unlabelled rule"
                assert context_class in rule["label"], (
                    f"{layer}/{context_class}/{kind}: the legend label does not name the class"
                )
                assert rule["symbol"].get("type") == SYMBOL_FOR_KIND[kind], (
                    f"{rule['label']}: a {kind} needs a {SYMBOL_FOR_KIND[kind]} symbol"
                )


def test_published_context_features_are_all_styled(styled: pathlib.Path) -> None:
    """Ground truth: every triple the artefacts contain resolves to a labelled rule of the right kind."""
    triples = _published_context_triples()
    assert triples, "no published context artefact to check the styles against"
    rules = _rules(styled / "context_layers.qml")
    for layer, context_class, kind in sorted(triples):
        matches = [r for r in rules if _names(r, layer, context_class, kind)]
        assert matches, f"{layer}/{context_class}/{kind} is published but has no rule of its own"
        assert matches[0]["label"].strip(), f"{layer}/{context_class}/{kind}: unlabelled rule"
        assert matches[0]["symbol"].get("type") == SYMBOL_FOR_KIND[kind]
    published_kinds: dict[str, set[str]] = {}
    for layer, _, kind in triples:
        published_kinds.setdefault(layer, set()).add(kind)
    for layer, kinds in published_kinds.items():
        unknown = kinds - set(CONTEXT_KINDS[layer])
        assert not unknown, (
            f"{layer} is published as {sorted(unknown)}: widen CONTEXT_KINDS in this test and the "
            f"generator, or the style will silently drop those features"
        )


def test_water_rules_split_by_geometry(styled: pathlib.Path) -> None:
    """`water` is a Polygon *and* a LineString in the same file; one symbol cannot serve both."""
    rules = _rules(styled / "context_layers.qml")
    water = [rule for rule in rules if rule["filter"].startswith('"layer" = \'water\'')]
    assert water, "no water rules emitted"
    by_kind: dict[str, list[dict]] = {}
    for rule in water:
        kinds = [
            kind
            for kind in SYMBOL_FOR_KIND
            if f"geometry_type($geometry) = '{kind}'" in rule["filter"]
        ]
        assert len(kinds) == 1, (
            f"water rule without exactly one geometry predicate: {rule['filter']!r} — an "
            f"unqualified water rule draws a polygon and a watercourse with one symbol"
        )
        assert rule["symbol"].get("type") == SYMBOL_FOR_KIND[kinds[0]], (
            f"{rule['label']}: a {kinds[0]} needs a {SYMBOL_FOR_KIND[kinds[0]]} symbol"
        )
        by_kind.setdefault(kinds[0], []).append(rule)
    assert set(by_kind) == {"Polygon", "Line", "Point"}
    labels = {rule["label"] for rule in water}
    assert {"water (area)", "water (course)", "water (point)"} <= labels, (
        "the undeclared-class fallbacks must stay one rule per kind: water is still two geometries"
    )


def test_admin_boundary_never_hides_the_grid(styled: pathlib.Path) -> None:
    rules = _rules(styled / "context_layers.qml")
    admin = [rule for rule in rules if rule["filter"].startswith('"layer" = \'admin_boundary\'')]
    assert admin, "no admin_boundary rules emitted"
    for rule in admin:
        if rule["symbol"].get("type") != "fill":
            continue
        assert rule["options"]["style"] == "no", (
            f"{rule['label']}: a filled admin boundary would hide the cells beneath it"
        )
        assert rule["options"]["outline_style"] == "dash", f"{rule['label']}: expected a dashed ring"
    labelled = {rule["label"]: rule for rule in admin}
    assert labelled["admin boundary"]["options"]["style"] == "no"
    assert labelled["admin boundary"]["options"]["outline_style"] == "dash"


def test_unknown_is_not_readable_as_a_data_class(styled: pathlib.Path) -> None:
    """`unknown` means "not mapped here". It must not look like a land-use class or like the fallback."""
    rules = {rule["label"]: rule for rule in _rules(styled / "context_layers.qml")}
    unknown = [rule for label, rule in rules.items() if label.endswith(": unknown")]
    assert len(unknown) == 1, "contracts.LAND_USE declares `unknown`: it needs exactly one rule"
    unknown = unknown[0]
    data_classes = [
        rule
        for label, rule in rules.items()
        if label.startswith("land use area:") and rule is not unknown
    ]
    assert data_classes, "no land-use data classes emitted"
    for rule in data_classes:
        assert unknown["options"]["color"] != rule["options"]["color"], (
            f"unknown shares its fill with {rule['label']}"
        )
        assert unknown["options"]["outline_style"] != rule["options"]["outline_style"], (
            f"unknown shares its outline treatment with {rule['label']}"
        )
        assert _rgba_alpha(unknown["options"]["color"]) < _rgba_alpha(rule["options"]["color"])
    catch_all = rules["land use"]
    assert unknown["options"]["color"] != catch_all["options"]["color"]
    assert unknown["options"]["outline_style"] == "dash"
    assert catch_all["options"]["outline_style"] == "solid"


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


def test_committed_styles_are_what_the_generator_emits(tmp_path: pathlib.Path) -> None:
    """The `.qml` under `data/staging/qgis/styles/` is an artefact of this script, not a hand edit.

    Without this, the styles on disk can drift from the generator for as long as nobody renders a
    map — exactly how a 47-rule context renderer ended up on disk next to a 5-rule generator.
    """
    committed = ROOT / "data/staging/qgis/styles"
    if not committed.is_dir():
        pytest.skip("data/staging is gitignored and absent in this tree")
    out = tmp_path / "styles"
    completed = subprocess.run(
        [sys.executable, "scripts/style_qgis_layers.py", "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (completed.stdout + completed.stderr)[-1500:]
    produced = sorted(path.name for path in out.iterdir())
    assert produced, "the generator emitted nothing"
    for name in produced:
        on_disk = committed / name
        assert on_disk.is_file(), f"{name} is missing from data/staging/qgis/styles"
        assert on_disk.read_bytes() == (out / name).read_bytes(), (
            f"{name} on disk is not what the generator emits: rerun "
            f"`./scripts/python.sh scripts/style_qgis_layers.py` instead of editing a .qml by hand"
        )
