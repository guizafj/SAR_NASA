"""Guard: the declared synthetic fallback must run, and must still say what it is.

Why this module exists
----------------------
A clone with neither the (gitignored) `data/raw/<case>/context/` extract nor the committed pin
`config/context/<case>.json` has exactly one way to build an artefact: the **declared synthetic
fallback** in `pipeline/context.py`. A fresh-clone simulation took that path and it crashed —

    AttributeError: 'Coast' object has no attribute 'signed_distance'

— on both coastal cases. `Coast.signed_distance` had been removed when the sea became a derived
polygon, but `pipeline/mock/model.py::_synthetic_land_use` kept calling it. The fix recomputes the
declared side from `polyline_metrics`. No test exercised the path, which is why it shipped broken.

What a silent break costs: the fallback is the *only* producer for such a clone, so the failure is
total (no grid, no series, no context, no meta -> the cockpit shows nothing) and it is invisible on
a developer machine, where the extract and the pin are both present. The quieter variant — water
labelled on the wrong side of the shoreline — is worse, because it produces a complete, plausible
artefact that publishes "ocean with subsidence".

Everything here is offline: `config/study_cases.yaml` is read, `data/raw/` is never touched (the
injected `root` points at an empty `tmp_path` directory), nothing is written under
`data/processed/` (`tmp_path` only), and no `pytest.skip` hides a missing precondition.
"""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pytest from the repo root, like `python pipeline/generate_mock.py`
    sys.path.insert(0, str(ROOT))

from pipeline import context as G  # noqa: E402
from pipeline.generate_mock import DEFAULT_SEED, generate_case  # noqa: E402
from pipeline.mock import artifacts as A  # noqa: E402
from pipeline.mock import checks as K  # noqa: E402
from pipeline.mock import model as M  # noqa: E402
from pipeline.mock.cases import StudyCases, load_study_cases  # noqa: E402
from src.domain import contracts as C  # noqa: E402

CASES: tuple[str, ...] = tuple(C.CASE_IDS)
#: The two cases the fallback crashed on: `_synthetic_land_use` reached for a coastline.
COASTAL: tuple[str, ...] = ("jakarta", "malaga")
#: The declared side of each synthetic coastline: -1 == water on the right/south of a w->e line.
DECLARED_SEA_SIDE: dict[str, int] = {"jakarta": +1, "malaga": -1}

#: Inland probe points, hand-placed inside each AOI and clear of the invented lake, river, canal
#: and corridor (so a label is stable under any rho); each case reads by its frozen case_id.
INLAND_CELLS: dict[str, tuple[tuple[float, float], ...]] = {
    "mexico-city": ((-99.145, 19.428), (-99.120, 19.435), (-99.100, 19.422), (-99.130, 19.450)),
    "jakarta": ((106.855, -6.190), (106.900, -6.200), (106.830, -6.170)),
    "malaga": ((-4.500, 36.715), (-4.450, 36.700)),
}
#: One point clearly on the declared sea side of each synthetic coastline, so "water is reachable"
#: is a property of the rule and not of the probe list. (Jakarta's coverage ellipse sits south of
#: its bay, so this probe is legitimately outside it: rho > 1 is an input, never an assertion.)
SEA_CELLS: dict[str, tuple[float, float]] = {"jakarta": (106.860, -6.114), "malaga": (-4.470, 36.670)}

ARTEFACTS: tuple[str, ...] = ("nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json")


@pytest.fixture(scope="module")
def study() -> StudyCases:
    """The configured study cases: every parameter of the rule comes from here, none from a test."""
    return load_study_cases()


def _rho(study: StudyCases, case_id: str, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Normalised elliptical radius in the case's own coverage ellipse (`context.ellipse_rho`)."""
    ellipse = study.cases[case_id].field.coverage_ellipse
    _, _, rho = G.ellipse_rho(
        lon,
        lat,
        ellipse.center_lon,
        ellipse.center_lat,
        ellipse.semi_axes_km[0],
        ellipse.semi_axes_km[1],
        ellipse.rotation_deg,
    )
    return rho


def _synthetic_labels(
    study: StudyCases, case_id: str, lon: np.ndarray, lat: np.ndarray
) -> np.ndarray:
    """Run the fallback rule itself, with a fixed seed so a rerun cannot move the bare patches."""
    return M._synthetic_land_use(
        study.cases[case_id],
        G.SYNTHETIC[case_id],
        np.asarray(lon, dtype=float),
        np.asarray(lat, dtype=float),
        _rho(study, case_id, np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)),
        np.random.default_rng(0),
    )


def _provenance(study: StudyCases, case_id: str) -> str:
    return A.provenance_id(case_id, DEFAULT_SEED, study.dates)


# ---------------------------------------------------------------------------------------------
# 1. The rule runs for every case and speaks the frozen vocabulary
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", CASES)
def test_synthetic_land_use_runs_and_stays_inside_the_frozen_vocabulary(
    study: StudyCases, case_id: str
) -> None:
    """No exception on any case, and every label is a declared `contracts.LAND_USE` member.

    This is the crash the file pins: an `AttributeError` here aborts the whole generator, so a
    clone gets four missing files instead of four declared-synthetic ones. A label outside the
    vocabulary is the slower failure — the artefact gate (`contracts.validate_all`) rejects it, so
    the fallback would "run" and still publish nothing.

    Water must be *reachable* for the coastal cases and *absent* for Mexico City's inland probes:
    its water comes from the lake ellipse, which the next test asserts separately.
    """
    probes = INLAND_CELLS[case_id]
    if case_id in SEA_CELLS:
        probes = probes + (SEA_CELLS[case_id],)
    lon = np.array([point[0] for point in probes], dtype=float)
    lat = np.array([point[1] for point in probes], dtype=float)

    labels = _synthetic_labels(study, case_id, lon, lat)

    assert labels.shape == (len(probes),), f"{case_id}: {labels.shape} labels for {len(probes)} cells"
    undeclared = sorted(set(labels.tolist()) - set(C.LAND_USE))
    assert not undeclared, (
        f"{case_id}: the synthetic fallback produced {undeclared}, which contracts.LAND_USE does "
        f"not declare {list(C.LAND_USE)}; every artefact built from it would fail the gate with no "
        "cell published at all"
    )
    if case_id in DECLARED_SEA_SIDE:
        assert "water" in labels, (
            f"{case_id}: the fallback produced no water cell anywhere, not even at a probe on the "
            "declared sea side — the coastline branch of _synthetic_land_use is not being applied "
            "(the 'sea side' sign is the part that was removed with Coast.signed_distance)"
        )
    else:
        assert "water" not in labels, (
            f"{case_id}: an inland probe away from the lake ellipse came out 'water' "
            f"({dict(zip(probes, labels.tolist(), strict=True))}); the fallback is inventing "
            "hydrology outside its declared shapes"
        )


def test_mexico_city_water_comes_from_the_lake_ellipse(study: StudyCases) -> None:
    """Mexico City has no synthetic coastline: its water is the lake, so the lake cells are water.

    If this fails while the coastal test passes, the water rule collapsed onto "everything is
    land" (or onto the coastline only), and the only city whose hydrology is a declared ellipse
    loses it — a silent change in what the map shows for the primary case.
    """
    case_id = "mexico-city"
    lake = G.SYNTHETIC[case_id].lake
    assert lake is not None, "the synthetic geography of mexico-city declares no lake"
    lake_lon, lake_lat, *_ = lake
    lon = np.array([lake_lon, lake_lon + 0.005], dtype=float)
    lat = np.array([lake_lat, lake_lat - 0.004], dtype=float)

    labels = _synthetic_labels(study, case_id, lon, lat)

    assert labels.tolist() == ["water", "water"], (
        f"mexico-city: cells at the centre of the declared lake ellipse "
        f"({lake_lon}, {lake_lat}) and 0.005 deg east came out {labels.tolist()}; the lake branch "
        "of _synthetic_land_use is the only hydrology this case has"
    )


# ---------------------------------------------------------------------------------------------
# 2. The sea side is the DECLARED one (the sign the removed API used to encode)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(DECLARED_SEA_SIDE))
def test_sea_is_on_the_declared_side_of_the_coastline(study: StudyCases, case_id: str) -> None:
    """`Coast.sea_side` still decides the label: +1 north of a west->east line, -1 south of it.

    This is the assertion the deleted `signed_distance` used to guarantee. Inverting the sign is
    the exact defect the removal was meant to prevent on the real path (`sea_polygons`), and the
    fallback would reintroduce it here: Jakarta's whole city would be labelled water (so the
    quality filter drops every cell and the case publishes an empty grid) and the bay labelled
    land. The probe offset is ~4.4 km, far enough that the nearest segment cannot flip sides.
    """
    geo = G.SYNTHETIC[case_id]
    assert geo.coast is not None, f"{case_id}: no synthetic coastline to test the declared side of"
    assert geo.coast.sea_side == DECLARED_SEA_SIDE[case_id], (
        f"{case_id}: the declared sea side is {geo.coast.sea_side}, the module's own convention "
        f"says {DECLARED_SEA_SIDE[case_id]}; the sign convention changed and every label below "
        "inherits the change"
    )
    verts = np.asarray(geo.coast.verts, dtype=float)
    # A real vertex near the middle of the line, so both probes lean on the same segment.
    anchor = verts[int(np.argmin(np.abs(verts[:, 0] - verts[:, 0].mean())))]
    offset_deg = 0.04
    north = (anchor[0], anchor[1] + offset_deg)
    south = (anchor[0], anchor[1] - offset_deg)

    north_label, south_label = _synthetic_labels(
        study, case_id, np.array([north[0], south[0]]), np.array([north[1], south[1]])
    )

    sea, land = (north_label, south_label) if geo.coast.sea_side > 0 else (south_label, north_label)
    sea_point, land_point = (north, south) if geo.coast.sea_side > 0 else (south, north)
    assert sea == "water", (
        f"{case_id}: {sea_point} is on the declared sea side (sea_side={geo.coast.sea_side}) but "
        f"was labelled {sea!r}; the coastline branch reads the side sign backwards"
    )
    assert land != "water", (
        f"{case_id}: {land_point} is on the land side (sea_side={geo.coast.sea_side}) but was "
        f"labelled {land!r}; land published as water loses its displacement to the coherence filter"
    )


# ---------------------------------------------------------------------------------------------
# 3. End to end: the generator really writes a declared-synthetic case
# ---------------------------------------------------------------------------------------------


def test_generator_writes_the_declared_fallback_without_an_extract_or_a_pin(
    study: StudyCases, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`generate_case` on the fallback branch: four artefacts, declared geography, gate green.

    Forcing without editing the pipeline: the generator's own `load_context` call is routed through
    its documented knobs — `pin_directory` and `root` point at empty `tmp_path` directories and
    `allow_synthetic=True` — which is exactly the state of a fresh clone. `checks.assert_case`
    rebuilds the model from `meta.provenance.mock_seed`, so the routing has to hold for the gate
    too, not just for the first build.

    One case (Málaga) is enough: the crash was per-coastline, and case 1 above already covers all
    three. The whole generator takes well under the 60 s budget, so no branch of this test is
    weakened to `build_case` + `build_context` + `build_meta`.

    What a silent break costs: `generate_case` is the documented way to regenerate a clone's
    artefacts. If it dies here the repo has no artefact producer at all on a fresh clone; if it
    succeeds but mislabels the geography, a reader takes invented geometry for mapped data.
    """
    case_id = "malaga"
    case = study.cases[case_id]
    provenance = _provenance(study, case_id)

    empty_pins = tmp_path / "no-pins"
    empty_raw = tmp_path / "no-raw-extract"
    real_load_context = G.load_context

    def fallback_only(*args: object, **kwargs: object) -> G.CaseContext:
        """`load_context` with the pin directory and the raw extract pointing at empty dirs."""
        return real_load_context(
            *args,
            **{**kwargs, "root": empty_raw, "pin_directory": empty_pins, "allow_synthetic": True},  # type: ignore[arg-type]
        )

    monkeypatch.setattr(G, "load_context", fallback_only)

    out_dir = tmp_path / "processed"
    report = generate_case(case, study, out_dir, DEFAULT_SEED, per_cell_series=False)
    assert report.case_id == case_id and report.n_cells > 0, (
        f"{case_id}: generate_case returned {report!r}; a fallback run must publish cells, not an "
        "empty case"
    )

    case_dir = out_dir / case_id
    missing = [name for name in ARTEFACTS if not (case_dir / name).is_file()]
    assert not missing, f"{case_id}: the fallback branch wrote no {missing} under {case_dir}"

    # The declared-synthetic sentence, taken from the writer itself rather than retyped here.
    context = G.synthetic_context(case_id, provenance)
    expected_sentence = A._geography_limitation(SimpleNamespace(context=context))
    meta = K.load_case(case_dir).meta
    assert expected_sentence in meta["limitations"], (
        f"{case_id}: meta.limitations does not carry the synthetic-geography sentence "
        f"{expected_sentence!r}; a fallback artefact that does not declare its invented geography "
        f"reads as mapped data. Published limitations: {meta['limitations']}"
    )

    # Every context feature says it is mock, except the AOI: its source is the configured analysis
    # extent (`config/aoi/<case>.geojson`), which is real geography and would be a lie to relabel.
    features = K.load_case(case_dir).context["features"]
    assert features, f"{case_id}: the published context carries no feature at all"
    aoi_sources = 0
    for index, feature in enumerate(features):
        props = feature["properties"]
        is_aoi = props["layer"] == "admin_boundary" and props["class"] == "aoi"
        if is_aoi:
            aoi_sources += 1
            assert "synthetic" not in str(props["source"]).lower(), (
                f"{case_id}: the AOI feature claims a synthetic source {props['source']!r}: the "
                "analysis extent comes from config/aoi, not from the invented geography"
            )
            continue
        assert "synthetic" in str(props["source"]).lower(), (
            f"{case_id}: context feature {index} (layer {props['layer']!r}, class "
            f"{props['class']!r}) publishes source {props['source']!r}, which does not name the "
            "synthetic fallback; a clone cannot tell that geometry from the OpenStreetMap extract"
        )
    assert aoi_sources == 1, f"{case_id}: {aoi_sources} AOI features published, expected exactly one"

    # And the post-write gate itself, on the files just written (re-run here so this module fails
    # with its own message instead of inheriting generate_case's).
    gate = K.assert_case(case, study, case_dir)
    assert gate.checks, f"{case_id}: checks.assert_case returned no passing checks — it did not run"
    assert case_id in gate.case_id


# ---------------------------------------------------------------------------------------------
# 4. The fallback never pretends to be real geography
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", CASES)
def test_synthetic_context_declares_itself_and_uses_the_frozen_vocabulary(
    study: StudyCases, case_id: str
) -> None:
    """`synthetic=True`, no pinned labels, no derived sea polygon, declared layers/classes only.

    `labels` is what `label_cells` trusts blindly, and `sea` is what overrides a class to water, so
    either one being set on the fallback would let a mock context bypass the rules the real path is
    held to. A layer/class outside `contracts.CONTEXT_CLASSES` fails `validate_context`, i.e. the
    artefact gate — the fallback would run and publish nothing. And `synthetic=True` is what makes
    every published `source` and the geography limitation say so.
    """
    context = G.synthetic_context(case_id, _provenance(study, case_id))

    assert context.synthetic is True, (
        f"{case_id}: the fallback context does not flag itself synthetic, so nothing downstream "
        "(sources, licence, limitation sentence) can disclose that the geography is invented"
    )
    assert context.labels is None, (
        f"{case_id}: the fallback carries pinned cell labels {context.labels!r}; `label_cells` "
        "would use them instead of the synthetic rule, publishing a provenance it cannot support"
    )
    assert context.sea is None, (
        f"{case_id}: the fallback carries a derived sea polygon {context.sea!r}; that polygon is "
        "derived from a real coastline, and this context has none"
    )
    assert not context.features.empty, f"{case_id}: the fallback context publishes no feature"
    for layer, klass in zip(context.features["layer"], context.features["class"], strict=True):
        assert layer in C.CONTEXT_LAYERS, f"{case_id}: layer {layer!r} not in {C.CONTEXT_LAYERS}"
        assert klass in C.CONTEXT_CLASSES[layer], (
            f"{case_id}: class {klass!r} not in contracts.CONTEXT_CLASSES[{layer!r}] "
            f"{list(C.CONTEXT_CLASSES[layer])}; validate_context rejects the whole context artefact"
        )


# ---------------------------------------------------------------------------------------------
# 5. Non-vacuity: the guard must pin the crash, not a renamed method
# ---------------------------------------------------------------------------------------------


def test_the_removed_signed_distance_api_is_gone_and_no_longer_needed(study: StudyCases) -> None:
    """`Coast.signed_distance` is gone on purpose, and the fallback still finds water.

    Without the second assertion this module would keep passing on a tree that reintroduced the
    method (the crash would be masked, not prevented). Without the first, a future refactor could
    "restore" a single signed offset and silently undo the reason it was removed: the real sea is a
    polygon now (`context.sea_polygons`), because one global sign cannot describe a bay.
    """
    assert not hasattr(G.Coast, "signed_distance"), (
        "Coast.signed_distance is back: the sea is derived geometrically (context.sea_polygons), "
        "so one signed offset per coastline cannot describe a bay and a wrong sign labels land as "
        "water. If the fallback needs a side, it must read `Coast.sea_side` as _synthetic_land_use "
        "does today"
    )

    case_id = "jakarta"
    lon = np.array([SEA_CELLS[case_id][0]], dtype=float)
    lat = np.array([SEA_CELLS[case_id][1]], dtype=float)
    labels = _synthetic_labels(study, case_id, lon, lat)
    assert labels.tolist() == ["water"], (
        f"{case_id}: a point on the declared sea side was labelled {labels.tolist()} — the fallback "
        "lost the coastline branch, so the fix depends on something that is not there"
    )

    # The surviving API is unsigned: a distance is never negative, on either side of the line.
    distance = G.SYNTHETIC[case_id].coast.distance(lon, lat)
    assert np.all(np.isfinite(distance)) and np.all(distance >= 0.0), (
        f"{case_id}: Coast.distance returned {distance!r}; it must stay an unsigned distance in km"
    )
