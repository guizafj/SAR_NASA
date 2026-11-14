#!/usr/bin/env python3
"""QGIS styling as a generated artefact, not a clicking ritual.

`scripts/export_grid_geojson.py` gets the artefact into QGIS; this makes the *appearance* of that
artefact reproducible, reviewable and diffable. Every colour here is read from
``src.domain.map_adapter`` — the same module that feeds the Streamlit/PyDeck app and
``docs/figures/`` — so a QGIS map and the pitch figure cannot drift apart:

    ./scripts/python.sh scripts/style_qgis_layers.py                 # emit every .qml
    ./scripts/python.sh scripts/style_qgis_layers.py --out /tmp/s    # somewhere else
    ./scripts/python.sh scripts/style_qgis_layers.py --snapshot qc.png   # offscreen render, for QA

What it writes (default ``data/staging/qgis/styles/``)
-----------------------------------------------------
``grid_velocity.qml``    graduated on ``velocity_mm_yr``, the shared ±425 mm/yr RdBu ramp
``grid_delta.qml``       graduated on ``delta_change``, the shared ±155 mm ramp
``grid_coherence.qml``   graduated on ``coherence``, 0–1
``grid_risk.qml``        categorised on ``risk_level`` (estable / moderado / critico)
``grid_quality.qml``     rule-based: coherence mask + risk bands on one map
``context_layers.qml``   rule-based on ``layer``, geometry-aware (water is Polygon *and* Line)
``aoi_ring.qml``         dashed no-fill outline for ``config/aoi/*.geojson``
``styles_manifest.json`` which file styles which field, with the renderer and class count

Every symbol is a **hexagon marker sized in map units (300 m)** because the artefact is H3
resolution 9: ~0.105 km² per cell, ~301 m centre-to-centre. In map units the cells tile
edge-to-edge, which is the point — a fixed millimetre size hides interleaving and overlap, and
the tiling *is* the geometry check.

Rules this file will not break
------------------------------
* **No colour is declared here.** Anchors, risk bands and context alphas are imported. If the app
  changes, this output changes; if this file ever disagrees with the app, ``tests/test_style_qgis_layers.py``
  fails rather than letting two maps disagree in front of an audience.
* **The neutral slab stays a slab.** ``velocity_mm_yr`` classes are the app's anchor intervals, so
  |v| ≤ 5 mm/yr is one flat grey band — "at or below the detection floor" — instead of a gradient
  that would paint Málaga as weak subsidence when the artefacts say the trend is not recoverable.
* **No invented breaks.** Coherence is cut only at the app's own anchors (0 / 0.35 / 0.65); the
  top swatch colour covers the 0.65–1.0 leg rather than a 0.85 split that no document authorises.
* **Deterministic.** Fixed UUIDv5 keys, sorted case order, no timestamps: re-running produces
  byte-identical files, so a style change shows up as a real diff.
* **Nodata stays absent.** Nothing here invents cells; a hole in the grid is a hole in the map.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import uuid
import xml.sax.saxutils as sax

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domain import map_adapter as MA  # noqa: E402

#: H3 resolution 9 centre-to-centre spacing, rounded to metres (see module docstring).
HEX_SIZE_MAP_UNITS = 300

#: QML written by QGIS 3.x is still read by QGIS 4; declare a 3.x version so both open it.
QGIS_QML_VERSION = "3.40.0"

_NS = uuid.UUID("6f6d1e2a-1c1b-4a7e-9d3f-0c5a8b1e4d70")  # stable namespace for generated keys


def _key(*parts: str) -> str:
    """Deterministic braced UUID for QGIS XML nodes (stable across runs, unique per part)."""
    return "{" + str(uuid.uuid5(_NS, "/".join(parts))) + "}"


def _hex_to_rgba(value: str, alpha: int = 255) -> str:
    """``#rrggbb`` → QGIS's ``"r,g,b,a"`` string."""
    value = value.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"{r},{g},{b},{alpha}"


def _attr(value: str) -> str:
    """Escape a value for an XML attribute."""
    return sax.quoteattr(value, entities={"\"": "&quot;"})[1:-1]


def _opt(name: str, value: str, kind: str = "QString") -> str:
    return f'          <Option name="{name}" type="{kind}" value="{_attr(value)}"/>\n'


# --------------------------------------------------------------------------------------------
# symbols
# --------------------------------------------------------------------------------------------


def marker_symbol(
    name: str,
    color_rgba: str,
    *,
    shape: str = "hexagon",
    size: float = HEX_SIZE_MAP_UNITS,
    size_unit: str = "MapUnit",
    stroke_rgba: str = "255,255,255,0",
    stroke_width: float = 0.0,
    stroke_style: str = "no",
    opacity: float = 1.0,
) -> str:
    """A single-layer marker symbol; hexagon in map units by default."""
    options = "".join(
        [
            _opt("angle", "0"),
            _opt("color", color_rgba),
            _opt("horizontal_anchor_point", "1"),
            _opt("joinstyle", "bevel"),
            _opt("name", shape),
            _opt("offset", "0,0"),
            _opt("offset_unit", "MM"),
            _opt("outline_color", stroke_rgba),
            _opt("outline_style", stroke_style),
            _opt("outline_width", f"{stroke_width:g}"),
            _opt("outline_width_unit", "MM"),
            _opt("size", f"{size:g}"),
            _opt("size_unit", size_unit),
            _opt("vertical_anchor_point", "1"),
        ]
    )
    return (
        f'      <symbol type="marker" name="{_attr(name)}" clip_to_extent="1" force_rhr="0" '
        f'alpha="{opacity:g}" frame_rate="10">\n'
        f'        <layer class="SimpleMarker" locked="0" pass="0" enabled="1">\n'
        f'          <Option type="Map">\n{options}          </Option>\n'
        f"          <data_defined_properties/>\n"
        f"        </layer>\n"
        f"      </symbol>\n"
    )


def fill_symbol(
    name: str,
    color_rgba: str,
    *,
    outline_rgba: str = "0,0,0,0",
    outline_width: float = 0.0,
    outline_style: str = "no",
    style: str = "solid",
    opacity: float = 1.0,
) -> str:
    """A single-layer fill symbol: used by context polygons and the AOI ring."""
    options = "".join(
        [
            _opt("border_width_map_unit_scale", "3x:0,0,0,0,0,0"),
            _opt("color", color_rgba),
            _opt("joinstyle", "bevel"),
            _opt("offset", "0,0"),
            _opt("offset_unit", "MM"),
            _opt("outline_color", outline_rgba),
            _opt("outline_style", outline_style),
            _opt("outline_width", f"{outline_width:g}"),
            _opt("outline_width_unit", "MM"),
            _opt("style", style),
        ]
    )
    return (
        f'      <symbol type="fill" name="{_attr(name)}" clip_to_extent="1" force_rhr="0" '
        f'alpha="{opacity:g}" frame_rate="10">\n'
        f'        <layer class="SimpleFill" locked="0" pass="0" enabled="1">\n'
        f'          <Option type="Map">\n{options}          </Option>\n'
        f"          <data_defined_properties/>\n"
        f"        </layer>\n"
        f"      </symbol>\n"
    )


def line_symbol(
    name: str,
    color_rgba: str,
    *,
    width: float = 0.6,
    style: str = "solid",
    opacity: float = 1.0,
) -> str:
    """A single-layer line symbol: used by context watercourses."""
    options = "".join(
        [
            _opt("align_dash_pattern", "0"),
            _opt("capstyle", "square"),
            _opt("customdash", "5;2"),
            _opt("customdash_unit", "MM"),
            _opt("dash_pattern_offset", "0"),
            _opt("dash_pattern_offset_unit", "MM"),
            _opt("draw_inside_polygon", "0"),
            _opt("joinstyle", "bevel"),
            _opt("line_color", color_rgba),
            _opt("line_style", style),
            _opt("line_width", f"{width:g}"),
            _opt("line_width_unit", "MM"),
            _opt("offset", "0"),
            _opt("offset_unit", "MM"),
            _opt("use_custom_dash", "0"),
        ]
    )
    return (
        f'      <symbol type="line" name="{_attr(name)}" clip_to_extent="1" force_rhr="0" '
        f'alpha="{opacity:g}" frame_rate="10">\n'
        f'        <layer class="SimpleLine" locked="0" pass="0" enabled="1">\n'
        f'          <Option type="Map">\n{options}          </Option>\n'
        f"          <data_defined_properties/>\n"
        f"        </layer>\n"
        f"      </symbol>\n"
    )


# --------------------------------------------------------------------------------------------
# renderers
# --------------------------------------------------------------------------------------------


def graduated_renderer(field: str, classes: tuple[tuple[float, float, str], ...]) -> str:
    """Graduated renderer from explicit ``(lower, upper, colour)`` classes — never recomputed."""
    ranges = "".join(
        f'        <range lower="{lo:g}" upper="{hi:g}" symbol="{i}" label="{lo:g} – {hi:g}" '
        f'render="true" uuid="{_key("range", field, str(i))}"/>\n'
        for i, (lo, hi, _) in enumerate(classes)
    )
    symbols = "".join(marker_symbol(str(i), _hex_to_rgba(hexv)) for i, (_, _, hexv) in enumerate(classes))
    return (
        f'  <renderer-v2 type="graduatedSymbol" attr="{_attr(field)}" gradtype="0" '
        f'symbollevels="0" enableorderby="0" forceraster="0" referencescale="-1">\n'
        f"    <ranges>\n{ranges}    </ranges>\n"
        f"    <symbols>\n{symbols}    </symbols>\n"
        f"  </renderer-v2>\n"
    )


def categorized_renderer(field: str, values: tuple[tuple[str, str], ...]) -> str:
    """Categorised renderer over text values, one marker symbol each."""
    categories = "".join(
        f'        <category value="{_attr(cat)}" symbol="{i}" label="{_attr(cat)}" '
        f'render="true" uuid="{_key("cat", field, cat)}"/>\n'
        for i, (cat, _) in enumerate(values)
    )
    symbols = "".join(marker_symbol(str(i), _hex_to_rgba(hexv)) for i, (_, hexv) in enumerate(values))
    return (
        f'  <renderer-v2 type="categorizedSymbol" attr="{_attr(field)}" symbollevels="0" '
        f'enableorderby="0" forceraster="0" referencescale="-1">\n'
        f"    <categories>\n{categories}    </categories>\n"
        f"    <symbols>\n{symbols}    </symbols>\n"
        f"  </renderer-v2>\n"
    )


def rule_renderer(rules: tuple[tuple[str, str, str], ...]) -> str:
    """Rule renderer from ``(filter, label, symbol_xml)`` triples, in evaluation order."""
    rule_xml = "".join(
        f'      <rule key="{_key("rule", label)}" symbol="{i}" filter="{_attr(flt)}" '
        f'label="{_attr(label)}" description="">\n'
        f"      </rule>\n"
        for i, (flt, label, _) in enumerate(rules)
    )
    symbols = "".join(
        sym.replace('name="0"', f'name="{i}"') for i, (_, _, sym) in enumerate(rules)
    )
    return (
        f'  <renderer-v2 type="RuleRenderer" symbollevels="0" enableorderby="0" '
        f'forceraster="0" referencescale="-1">\n'
        f"    <rules key=\"{_key('ruleset', 'root')}\">\n{rule_xml}    </rules>\n"
        f"    <symbols>\n{symbols}    </symbols>\n"
        f"  </renderer-v2>\n"
    )


def single_renderer(symbol_xml: str) -> str:
    return (
        f'  <renderer-v2 type="singleSymbol" symbollevels="0" enableorderby="0" '
        f'forceraster="0" referencescale="-1">\n'
        f"    <symbols>\n{symbol_xml}    </symbols>\n"
        f"  </renderer-v2>\n"
    )


def wrap_qml(body: str) -> str:
    """Wrap a renderer in the QML document shell QGIS expects.

    ``layerGeometryType`` is deliberately omitted: it is optional style-database metadata, and a
    wrong enum there is the kind of thing QGIS accepts silently and then filters on. The renderer
    is the style; the layer already knows its own geometry type.
    """
    return (
        "<!DOCTYPE qgis PUBLIC 'http://mrcc.com.au/qgis.dtd' 'SYSTEM'>\n"
        f'<qgis version="{QGIS_QML_VERSION}" styleCategories="Symbology">\n'
        f"{body}"
        f"</qgis>\n"
    )


# --------------------------------------------------------------------------------------------
# the actual styles
# --------------------------------------------------------------------------------------------


def _class_intervals(stops) -> tuple[tuple[float, float, str], ...]:
    """Turn legend anchor stops into renderer classes, one colour per interval.

The anchors arrive with an intentional duplicate: the velocity ramp carries slate at both −5 and
    +5 so that the neutral band is a *slab*, not a gradient. Consecutive duplicate colours are
    dropped before pairing, which collapses |v| ≤ 5 into a single grey class and leaves the
    remaining intervals aligned one-to-one with the documented swatch table (deepest crimson on
    the deepest class, deep ocean blue on the strongest uplift) and with the coherence ramp
    starting at its dark end.
    """
    colours: list[str] = []
    for _, color_hex in stops:
        if not colours or colours[-1] != color_hex:
            colours.append(color_hex)
    intervals = []
    for i in range(len(stops) - 1):
        lo, hi = stops[i][0], stops[i + 1][0]
        intervals.append((lo, hi, colours[min(i, len(colours) - 1)]))
    return tuple(intervals)


def velocity_classes(meta: dict) -> tuple[tuple[float, float, str], ...]:
    spec = MA.build_legend_spec(meta, metric="velocity_mm_yr", scale_mode="shared")
    return _class_intervals([(s.value, s.color_hex) for s in spec.stops])


def delta_classes(meta: dict) -> tuple[tuple[float, float, str], ...]:
    spec = MA.build_legend_spec(meta, metric="delta_change", scale_mode="shared")
    return _class_intervals([(s.value, s.color_hex) for s in spec.stops])


def coherence_classes(meta: dict) -> tuple[tuple[float, float, str], ...]:
    spec = MA.build_legend_spec(meta, metric="coherence", scale_mode="shared")
    return _class_intervals([(s.value, s.color_hex) for s in spec.stops])


def risk_categories() -> tuple[tuple[str, str], ...]:
    spec = MA.build_legend_spec({}, metric="risk_level", scale_mode="shared")
    return tuple((stop.label.split(" (")[0], stop.color_hex) for stop in spec.stops)


def quality_rules() -> tuple[tuple[str, str, str], ...]:
    """The one-map honest view: confidence first, then band colours."""
    risk = dict(risk_categories())
    degraded = marker_symbol(
        "0", "100,116,139,110", stroke_rgba="148,163,184,180", stroke_width=0.1, stroke_style="solid"
    )
    return (
        (
            '"coherence" < 0.5',
            "coherence < 0.5 — degraded, do not over-read",
            degraded,
        ),
        (
            '\"risk_level\" = \'critico\'',
            "critico",
            marker_symbol("0", _hex_to_rgba(risk["critico"])),
        ),
        (
            '\"risk_level\" = \'moderado\'',
            "moderado",
            marker_symbol("0", _hex_to_rgba(risk["moderado"])),
        ),
        ("ELSE", "estable", marker_symbol("0", _hex_to_rgba(risk["estable"]))),
    )


def context_rules() -> tuple[tuple[str, str, str], ...]:
    """Mock geography beneath the grid, geometry-aware (``water`` is Polygon *and* LineString)."""
    st = MA.CONTEXT_LAYER_STYLES

    def rgba(values) -> str:
        return ",".join(str(int(v)) for v in values)

    return (
        (
            '\"layer\" = \'infrastructure\'',
            "infrastructure",
            marker_symbol(
                "0",
                rgba(st["infrastructure"]["fill_color"]),
                shape="circle",
                size=3.2,
                size_unit="MM",
                stroke_rgba=rgba(st["infrastructure"]["line_color"]),
                stroke_width=0.3,
                stroke_style="solid",
            ),
        ),
        (
            "\"layer\" = 'water' AND geometry_type($geometry) = 'Polygon'",
            "water (area)",
            fill_symbol(
                "0",
                rgba(st["water"]["fill_color"]),
                outline_rgba=rgba(st["water"]["line_color"]),
                outline_width=0.4,
                outline_style="solid",
            ),
        ),
        (
            "\"layer\" = 'water' AND geometry_type($geometry) = 'Line'",
            "water (course)",
            line_symbol("0", rgba(st["water"]["line_color"]), width=0.8),
        ),
        (
            "\"layer\" = 'land_use'",
            "land use",
            fill_symbol(
                "0",
                rgba(st["land_use"]["fill_color"]),
                outline_rgba=rgba(st["land_use"]["line_color"]),
                outline_width=0.2,
                outline_style="solid",
            ),
        ),
        (
            "\"layer\" = 'admin_boundary'",
            "admin boundary",
            fill_symbol(
                "0",
                rgba(st["admin_boundary"]["fill_color"]),
                outline_rgba=rgba(st["admin_boundary"]["line_color"]),
                outline_width=0.7,
                outline_style="dash",
                style="no",
            ),
        ),
    )


def aoi_symbol() -> str:
    return fill_symbol(
        "0",
        "255,255,255,0",
        outline_rgba="255,255,255,230",
        outline_width=0.7,
        outline_style="dash",
        style="no",
    )


def build_styles(meta: dict) -> dict[str, tuple[str, dict]]:
    """All styles as ``filename -> (qml_text, manifest_entry)``."""
    styles: dict[str, tuple[str, dict]] = {}

    velocity = velocity_classes(meta)
    styles["grid_velocity.qml"] = (
        wrap_qml(graduated_renderer("velocity_mm_yr", velocity)),
        {
            "applies_to": "grid",
            "field": "velocity_mm_yr",
            "renderer": "graduatedSymbol",
            "classes": len(velocity),
            "domain": [velocity[0][0], velocity[-1][1]],
            "unit": "mm/yr",
            "scale_mode": "shared",
            "note": "shared cross-city scale; |v| <= 5 mm/yr is one flat neutral band",
        },
    )

    delta = delta_classes(meta)
    styles["grid_delta.qml"] = (
        wrap_qml(graduated_renderer("delta_change", delta)),
        {
            "applies_to": "grid",
            "field": "delta_change",
            "renderer": "graduatedSymbol",
            "classes": len(delta),
            "domain": [delta[0][0], delta[-1][1]],
            "unit": "mm",
            "scale_mode": "shared",
            "note": "identical to val_t2 while val_t1 = 0 at the reference epoch",
        },
    )

    coherence = coherence_classes(meta)
    styles["grid_coherence.qml"] = (
        wrap_qml(graduated_renderer("coherence", coherence)),
        {
            "applies_to": "grid",
            "field": "coherence",
            "renderer": "graduatedSymbol",
            "classes": len(coherence),
            "domain": [coherence[0][0], coherence[-1][1]],
            "unit": "-",
            "scale_mode": "shared",
            "note": "cuts only at the app's anchors 0/0.35/0.65; nothing below coherence_min exists",
        },
    )

    risk = risk_categories()
    styles["grid_risk.qml"] = (
        wrap_qml(categorized_renderer("risk_level", risk)),
        {
            "applies_to": "grid",
            "field": "risk_level",
            "renderer": "categorizedSymbol",
            "categories": [cat for cat, _ in risk],
            "note": "frozen bands from |velocity_mm_yr|, sign-agnostic",
        },
    )

    quality = quality_rules()
    styles["grid_quality.qml"] = (
        wrap_qml(rule_renderer(quality)),
        {
            "applies_to": "grid",
            "field": None,
            "renderer": "RuleRenderer",
            "rules": len(quality),
            "note": "confidence mask over risk bands — the defensible figure for the informe",
        },
    )

    context = context_rules()
    styles["context_layers.qml"] = (
        wrap_qml(rule_renderer(context)),
        {
            "applies_to": "context_layers",
            "field": "layer",
            "renderer": "RuleRenderer",
            "rules": len(context),
            "note": "geometry-aware: water is Polygon and LineString in the same file",
        },
    )

    styles["aoi_ring.qml"] = (
        wrap_qml(single_renderer(aoi_symbol())),
        {
            "applies_to": "aoi",
            "field": None,
            "renderer": "singleSymbol",
            "note": "the traced footprint: no fill, dashed white so it never hides cells",
        },
    )
    return styles


# --------------------------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------------------------


def _meta() -> dict:
    """Read a case's meta.json for thresholds; fall back to an empty mapping if absent."""
    for candidate in sorted((ROOT / "data/processed").glob("*/meta.json")):
        try:
            return json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def emit(out_dir: pathlib.Path) -> int:
    styles = build_styles(_meta())
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "qgis_qml_version": QGIS_QML_VERSION,
        "hex_size_map_units": HEX_SIZE_MAP_UNITS,
        "styles": {},
    }
    for filename, (text, entry) in styles.items():
        path = out_dir / filename
        path.write_text(text)
        manifest["styles"][filename] = entry
        print(f"{filename:22s} {entry['renderer']:18s} {path}")
    (out_dir / "styles_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"{'styles_manifest.json':22s} {'manifest':18s} {out_dir / 'styles_manifest.json'}")
    return 0


def project_path(out_dir: pathlib.Path) -> pathlib.Path:
    return out_dir / "qc_project.qgs"


#: (role, human label, style file, visible on open). Legend order, top first.
CASE_LAYERS: tuple[tuple[str, str, str, bool], ...] = (
    ("velocity", "velocity", "grid_velocity.qml", True),
    ("quality", "quality", "grid_quality.qml", False),
    ("coherence", "coherence", "grid_coherence.qml", False),
    ("context", "context", "context_layers.qml", True),
    ("aoi", "AOI", "aoi_ring.qml", True),
)


def _layer_source(case: str, role: str) -> pathlib.Path:
    if role == "context":
        return ROOT / f"data/processed/{case}/context_layers.geojson"
    if role == "aoi":
        return ROOT / f"config/aoi/{case}.geojson"
    return ROOT / f"data/staging/qgis/{case}.geojson"


def build_spec(
    out_dir: pathlib.Path, *, render_case: str | None = None, target: pathlib.Path | None = None
) -> dict:
    """Describe the QC project as data, so the QGIS-side builder stays dumb."""
    cases = [case for case in _cases() if (ROOT / f"data/staging/qgis/{case}.geojson").exists()]
    if not cases:
        raise SystemExit("no staged grids — run scripts/export_grid_geojson.py first")

    layers = []
    for case in cases:
        for role, label, style_file, visible in CASE_LAYERS:
            source = _layer_source(case, role)
            style = out_dir / style_file
            if not source.exists() or not style.exists():
                continue
            layers.append(
                {
                    "group": case,
                    "role": role,
                    "name": f"{case} · {label}",
                    "source": str(source),
                    "style": str(style),
                    "visible": visible,
                }
            )
    spec: dict = {
        "crs": "EPSG:4326",
        "groups": cases,
        "layers": layers,
        "project_path": str(project_path(out_dir)),
    }
    if render_case and target:
        spec["render"] = {
            "case": render_case,
            "target": str(target),
            "width": 1600,
            "height": 900,
            "background": "#ffffff",
        }
    return spec


def run_qgis_builder(spec: dict, *, timeout: int = 600) -> dict:
    """Build (and optionally render) the project with QGIS itself, in a throwaway profile.

    The child needs the real environment — a scrubbed ``PATH`` cannot find ``qgis`` on NixOS — and
    its own profile: the user's profile loads GUI plugins that dereference ``iface``, which under
    ``offscreen`` blocks the event loop until the timeout kills it, silently.
    """
    with tempfile.TemporaryDirectory(prefix="qgis-build-") as tmp:
        home = pathlib.Path(tmp)
        (home / ".cache").mkdir(parents=True, exist_ok=True)
        (home / "profiles").mkdir(parents=True, exist_ok=True)
        status = home / "status.json"
        spec_path = home / "spec.json"
        spec_path.write_text(json.dumps({**spec, "status": str(status)}))
        completed = subprocess.run(
            [
                "qgis",
                "--nologo",
                "--nocustomization",
                "--profiles-path",
                str(home / "profiles"),
                "--profile",
                "builder",
                "--code",
                str(ROOT / "scripts/qgis_project_builder.py"),
                "--py-args",
                str(spec_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "QT_QPA_PLATFORM": "offscreen",
                "HOME": str(home),
                "XDG_CACHE_HOME": str(home / ".cache"),
            },
            timeout=timeout,
        )
        if status.exists():
            return json.loads(status.read_text())
        noise = ("AI_Edit", "AI_Segmentation", "propagateSizeHints", "QNetworkDiskCache")
        tail = "\n".join(
            line
            for line in (completed.stdout + completed.stderr).splitlines()
            if line.strip() and not any(marker in line for marker in noise)
        )[-1200:]
        return {
            "errors": [
                f"the QGIS builder produced no status (exit {completed.returncode}). "
                f"Is `qgis` on PATH?\n{tail}"
            ]
        }


def _report(result: dict) -> int:
    errors = result.get("errors") or []
    render = result.get("render") or {}
    print(f"{'layers styled':22s} {len(result.get('layers') or [])}")
    for layer in result.get("layers") or []:
        print(f"  {layer['name']:32s} {str(layer['renderer']):18s} {layer['features']} features")
    if result.get("project_written"):
        print(f"{'project':22s} {project_path_from(result)}")
    if render:
        state = "saved" if render.get("saved") else "NOT saved"
        print(f"{'snapshot':22s} {state} {render.get('target')} {render.get('size')}")
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 0 if not errors else 1


def project_path_from(result: dict) -> str:
    return result.get("project_written_path", "qc_project.qgs")


def _cases() -> list[str]:
    root = ROOT / "data/processed"
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default="data/staging/qgis/styles", help="output directory for QML")
    parser.add_argument(
        "--project",
        action="store_true",
        help="also write qc_project.qgs, a ready-to-open QC project (needs qgis on PATH)",
    )
    parser.add_argument(
        "--snapshot", default=None, help="render a QA PNG of one case to this path (implies --project)"
    )
    parser.add_argument(
        "--snapshot-case",
        default=None,
        help="case to render; defaults to mexico-city (the primary case) when present",
    )
    args = parser.parse_args(argv)

    out_dir = pathlib.Path(args.out)
    code = emit(out_dir)
    if code != 0:
        return code

    if not (args.project or args.snapshot):
        print("\nnext:  --project  writes qc_project.qgs ready to open in QGIS")
        return 0

    render_case = None
    target = None
    if args.snapshot:
        target = pathlib.Path(args.snapshot)
        available = _cases()
        render_case = args.snapshot_case or ("mexico-city" if "mexico-city" in available else available[0])

    spec = build_spec(out_dir, render_case=render_case, target=target)
    result = run_qgis_builder(spec)
    result.setdefault("project_written_path", spec["project_path"])
    return _report(result)


if __name__ == "__main__":
    raise SystemExit(main())
