"""Guard: a pinned context is published in the pin's OWN order, so `context_layers.geojson` is stable.

The property this module pins
-----------------------------
For each published case, `data/processed/<case>/context_layers.geojson` carries the features of
`config/context/<case>.json` **in the order the pin stores them**. Not "the same set", not "the same
counts": the sequence of `(class, name)` pairs, per layer, must be identical. That is what makes the
file byte-identical on two machines that resolve the same context, and it is the only thing standing
between "reproducible artefact" and "diff noise nobody can explain".

What a silent break costs
-------------------------
`pipeline/mock/artifacts.py::_published_context_features` used to re-rank whatever
`CaseContext.features` it was handed through `pipeline/context.py::select_for_publication`, which
orders by geometry size (Web Mercator area for polygons, length for lines and points), then class,
then name, then the geometry's own WKB. That order is a function of the *geometry*. On the pinned path
the geometry is the pin's — already simplified and rounded to `PIN_PRECISION_DEG`; on the
fresh-extract path it is the raw OSM geometry. So the same features came out in two different orders
depending on which path built them: `data/processed/` regenerated from an extract did not match the
committed file, while describing exactly the same city. The artefact records no ordering rationale,
so a reviewer diffing the file sees a rewording-sized change in something declared deterministic and
has no way to tell it apart from a real change of content.

The fix is a flag, not a second ranking: `CaseContext.pinned` is True when the geometry came from the
pin, and the writer publishes the pin's stored order untouched in that case. The tests below fail if
that flag stops being honoured, if a pin's own digest stops being checked, or if the published
artefact stops matching the pin it claims to reproduce.

Everything here reads `data/**`, `config/**` and `pipeline/**`; nothing is written outside `tmp_path`,
nothing touches the network, and no number is copied out of an artefact.
"""

from __future__ import annotations

import json
import pathlib
import sys
import types
from collections import Counter

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon, shape

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pytest from the repo root, exactly like the pipeline modules do
    sys.path.insert(0, str(ROOT))

from pipeline import context as CTX  # noqa: E402
from pipeline.ingest import pin_context as PIN  # noqa: E402
from pipeline.mock import artifacts as A  # noqa: E402
from src.domain import contracts as C  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
CONTEXT_FILE = "context_layers.geojson"

#: The three context layers that come from the pin, artefact layer -> pin key. `pin_context.py`
#: stores the OSM admin layer under `admin` and publishes it as `admin_boundary`.
LAYERS: tuple[tuple[str, str], ...] = (
    ("water", "water"),
    ("infrastructure", "infrastructure"),
    ("admin_boundary", "admin"),
)

#: `pipeline/mock/artifacts.py::build_context` publishes the analysis extent as its own feature,
#: from `config/aoi/<case>.geojson` (`_aoi_feature`), and `_admin_features` deliberately passes
#: `skip_classes=("aoi",)` so the pin never carries one. It is therefore the one published
#: `admin_boundary` feature that is legitimately absent from the pin, and it is excluded by name
#: below — not by "whatever happens to be missing", which would hide a dropped pinned feature.
AOI_CLASS = "aoi"

NO_ARTEFACTS = (
    "no context_layers.geojson under data/processed — run "
    "./scripts/python.sh pipeline/generate_mock.py to publish the cases first"
)


#: Contract cases whose context layer is actually on disk. Parametrising only over published cases
#: keeps the per-case comparisons from erroring on a tree where `data/processed/` has not been
#: generated. What it does *not* do is shout: an empty `parametrize` is collected as **one skip per
#: function**, and this module used to add a `skipif(not CASES)` on top, so a tree without artefacts
#: reported six quiet `s` markers and exit status 0. Measured on this repository (moving
#: `data/processed/` aside): `12 passed` -> `6 skipped`.
#: `test_every_contract_case_is_published_here` and `test_every_contract_case_has_a_committed_pin`
#: below are the fail-closed half this module needs: all three cases and all three pins are
#: committed, so their absence is a defect to report, not a condition to skip.
CASES: tuple[str, ...] = tuple(
    case for case in C.CASE_IDS if (PROCESSED / case / CONTEXT_FILE).is_file()
)


def test_every_contract_case_is_published_here() -> None:
    """Every case the contract names must have a published context layer in this repository.

    Deliberately *not* parametrised over `CASES`: that is the whole point. A guard that discovers
    its own inputs cannot notice its inputs disappearing, and this module exists because the order
    of a committed file must not drift from the committed pin without a word.
    """
    missing = [case for case in C.CASE_IDS if case not in CASES]
    assert not missing, (
        f"{PROCESSED} carries no {CONTEXT_FILE} for {missing}, so every per-case check in this "
        f"module would skip and the file would report green. {NO_ARTEFACTS}"
    )
    assert CASES, (
        "no published case at all — this suite has nothing to check and must not pass. "
        f"{NO_ARTEFACTS}"
    )


def test_every_contract_case_has_a_committed_pin() -> None:
    """Every contract case must ship its pin, because all three were derived from an OSM extract.

    `_pin` skips a case with no pin, which is honest for a case whose context is the declared
    synthetic fallback (never derived from OSM, so no stored order to compare). It is not honest for
    `mexico-city`, `jakarta` or `malaga`: their pins are committed under `config/context/`, and
    moving that directory aside used to remove 9 of this module's 12 items (`3 passed, 9 skipped`)
    while the file still exited green. Not parametrised over the pins, for the same reason as above.
    """
    missing = [case for case in C.CASE_IDS if CTX.read_pin(case) is None]
    assert not missing, (
        f"config/context/ carries no pin for {missing}, so the order checks below would skip them "
        "instead of comparing anything. Every contract case is OSM-derived and its pin is "
        "committed: restore config/context/<case>.json rather than skipping the comparison"
    )
    assert C.CASE_IDS, "the contract declares no case at all, so there is no pin to check"


def _artefact(case: str) -> dict:
    return json.loads((PROCESSED / case / CONTEXT_FILE).read_text(encoding="utf-8"))


def _pin(case: str) -> dict:
    """The case's pin, digest verified by `read_pin`; skips when the case legitimately has none.

    `read_pin` returns `None` for a case with no committed pin, which is a legitimate state: a case
    whose context is the declared synthetic fallback was never derived from OSM, so there is no
    stored order to compare against and no claim to test. A missing pin for an OSM-derived artefact
    is caught by `tests/test_context_pin_reproducibility.py`, which owns that question; a missing pin
    for a *contract* case is caught by `test_every_contract_case_has_a_committed_pin` below, because
    all three of those cases are OSM-derived and their pins are committed.
    """
    payload = CTX.read_pin(case)
    if payload is None:
        pytest.skip(
            f"{case}: no config/context/{case}.json, so the published context carries no stored "
            "order — a synthetic (or freshly extracted) layer has nothing to compare, and demanding "
            "a pin here would demand one for a layer that was never OSM"
        )
    return payload


def _layer_features(collection: dict, layer: str) -> list[dict]:
    """Published features of one layer, in file order (the order under test)."""
    return [
        feature
        for feature in collection.get("features") or ()
        if (feature.get("properties") or {}).get("layer") == layer
    ]


def _pair(feature: dict) -> tuple[str, str | None]:
    """`(class, name)`: the pair the pin and the artefact both carry. Blank name -> `None`.

    `None` is the normal form of "no name" on both sides: `pin_context._layer_features` omits a
    blank name and `artifacts._props` does the same, so comparing raw mappings would fail on a
    missing key rather than on an order.
    """
    properties = feature.get("properties") or {}
    name = properties.get("name")
    if name is None or str(name).strip() == "":
        return str(properties.get("class")), None
    return str(properties.get("class")), str(name)


def _pinned_pairs(pin: dict, key: str) -> list[tuple[str, str | None]]:
    return [_pair(feature) for feature in pin.get(key) or ()]


def _first_divergence(
    published: list[tuple], pinned: list[tuple]
) -> str:
    """Human-sized report: the first index that differs, or the length mismatch."""
    for index, (left, right) in enumerate(zip(published, pinned)):
        if left != right:
            return (
                f"first divergence at position {index}: artefact {left!r} != pin {right!r} "
                f"({len(published)} published vs {len(pinned)} pinned)"
            )
    return f"common prefix, length mismatch: {len(published)} published vs {len(pinned)} pinned"


# ---------------------------------------------------------------------------------------------
# 1. Artefact level: the published order IS the pin's order
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_published_context_order_equals_the_pinned_order(case):
    """Each layer's `(class, name)` sequence in the artefact equals the pin's, element by element.

    This asserts the *sequence*, not the count: the same features shuffled would keep every count
    and every number in `meta.json` intact while making `context_layers.geojson` differ from the
    committed bytes. A count-based check cannot see that, which is exactly how the double ranking
    survived review.
    """
    pin = _pin(case)
    collection = _artefact(case)
    problems: list[str] = []
    for layer, key in LAYERS:
        published = [(cls, name) for cls, name in map(_pair, _layer_features(collection, layer))]
        if layer == "admin_boundary":
            # The AOI is published from config/aoi/<case>.geojson by `_aoi_feature`, never from the
            # pin; dropping it here keeps the comparison to the features the pin actually stores.
            published = [pair for pair in published if pair[0] != AOI_CLASS]
        pinned = _pinned_pairs(pin, key)
        assert pinned, (
            f"{case}: the pin carries no {key} feature, so the artefact's {layer} layer cannot be "
            "compared against it — a pin emptied by accident must fail here, not pass silently"
        )
        if published != pinned:
            problems.append(f"{case}/{layer}: {_first_divergence(published, pinned)}")
    assert not problems, (
        "a pinned context must be published in the pin's stored order: re-ranking it ranks "
        "simplified geometry on the pinned path and raw geometry on the fresh-extract path, which "
        "is how the same features came out in two orders and the file stopped being byte-identical\n"
        + "\n".join(problems)
    )


# ---------------------------------------------------------------------------------------------
# 2. Artefact level: nothing unpinned is ordered against the ranking by hand
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_unpinned_features_do_not_contradict_the_ranking(case):
    """Features the pin does not carry must still be ordered by `select_for_publication`.

    A published feature that is *not* in the pin can only have got there through the ranking, so its
    position must be the position `select_for_publication` gives it. The assertion asks the module
    itself to re-rank those features (cap = the whole set, so the ranking may only reorder, never
    drop) and checks that the answer is a no-op: this module must not encode the ranking a second
    time, or it would freeze a copy of a sort key that is free to evolve in `pipeline/context.py`.

    On today's artefacts every published feature comes from the pin (the only exception being the
    AOI, published from config/aoi and excluded above), so the assertion below is vacuous and the
    function returns after the membership check. It fires as soon as a case publishes anything the
    pin does not store, which is the path where the ranking is still the authority.
    """
    pin = _pin(case)
    collection = _artefact(case)

    extra: list[dict] = []
    for layer, key in LAYERS:
        remaining = Counter(_pinned_pairs(pin, key))
        for feature in _layer_features(collection, layer):
            pair = _pair(feature)
            if layer == "admin_boundary" and pair[0] == AOI_CLASS:
                continue  # published from config/aoi, never ranked (see `_admin_features`)
            if remaining[pair] > 0:
                remaining[pair] -= 1
                continue
            extra.append(feature)
        leftover = {pair: count for pair, count in remaining.items() if count > 0}
        assert not leftover, (
            f"{case}/{layer}: {sum(leftover.values())} pinned feature(s) never reached the artefact "
            f"({sorted(leftover)[:5]}…): a pin entry dropped by the writer is a content change, not "
            "an ordering question"
        )

    if not extra:
        # Every published context feature is pinned, so there is no unpinned order to check against
        # the ranking. Stated here rather than as a `skip` because the two assertions above did run.
        return

    frame = gpd.GeoDataFrame(
        {
            "layer": [str((f["properties"] or {}).get("layer")) for f in extra],
            "class": [_pair(f)[0] for f in extra],
            "name": [_pair(f)[1] for f in extra],
            "position": list(range(len(extra))),  # the artefact's own order, the thing under test
        },
        geometry=[shape(f["geometry"]) for f in extra],
        crs="EPSG:4326",
    )
    ranked = CTX.select_for_publication(frame, cap=len(frame))
    assert list(ranked["position"]) == list(range(len(extra))), (
        f"{case}: {len(extra)} published context feature(s) are absent from the pin, so their order "
        "is the ranking's decision, but the artefact does not order them the way "
        "`context.select_for_publication` does — the writer ranked them by hand or re-ranked an "
        f"already ranked list; artefact positions {list(range(len(extra)))}, ranking "
        f"{list(ranked['position'])}"
    )


# ---------------------------------------------------------------------------------------------
# 3. Unit level: the writer honours `context.pinned` (no artefacts involved)
# ---------------------------------------------------------------------------------------------


def _square(west: float, south: float, size: float) -> Polygon:
    return Polygon(
        [(west, south), (west + size, south), (west + size, south + size), (west, south + size)]
    )


def _unit_frame() -> gpd.GeoDataFrame:
    """Four context rows in deliberately NON-size order, with a line and a point for the kinds.

    Written smallest, line, largest, point: `select_for_publication` groups by geometry kind
    (polygon -> line -> point) and sorts each kind by size, so a correct ranking must move the
    largest polygon to the front. That makes "input order" and "ranked order" different sequences,
    which is the only reason the two branches below can be told apart at all.
    """
    rows = [
        ("water", "reservoir", "small-polygon", _square(0.0, 0.0, 0.002)),
        ("water", "river", "short-line", LineString([(0.0, 0.0), (0.01, 0.01)])),
        ("water", "reservoir", "large-polygon", _square(0.0, 0.0, 0.02)),
        ("water", "water-well", "a-point", Point(0.01, 0.01)),
    ]
    return gpd.GeoDataFrame(
        {
            "layer": [row[0] for row in rows],
            "class": [row[1] for row in rows],
            "name": [row[2] for row in rows],
            "case_id": ["unit-order"] * len(rows),
            "crs": ["EPSG:4326"] * len(rows),
            "source": ["unit (test fixture, not data)"] * len(rows),
            "provenance_id": ["unit-order+mock+test+00000000"] * len(rows),
        },
        geometry=[row[3] for row in rows],
        crs="EPSG:4326",
    )


def _fake_model(pinned: bool) -> types.SimpleNamespace:
    """The minimum `_published_context_features` touches: `model.case` and `model.context`.

    `context.features_of` is the real selection predicate (`features["layer"] == layer`), so a change
    to that method's meaning cannot hide behind a mock that returns a constant.
    """
    frame = _unit_frame()

    def features_of(layer: str, _frame: gpd.GeoDataFrame = frame) -> gpd.GeoDataFrame:
        return _frame[_frame["layer"] == layer]

    context = types.SimpleNamespace(
        features=frame,
        pinned=pinned,
        synthetic=False,
        source="unit (test fixture, not data)",
        features_of=features_of,
    )
    return types.SimpleNamespace(case=types.SimpleNamespace(case_id="unit-order"), context=context)


def _published_names(model: types.SimpleNamespace) -> list[str]:
    features = A._published_context_features(
        model,
        "water",
        "unit-order+mock+test+00000000",
        cap=CTX.FEATURE_CAP_PER_KIND,
        rank_by="area",
    )
    return [str(feature["properties"].get("name")) for feature in features]


@pytest.mark.parametrize("case", CASES)
def test_the_pin_count_is_not_the_property_under_test(case):
    """The pin and the artefact agree on the pairing key, so an order test is not a count test.

    Guards the comparison itself: if the pin stopped naming its layers the way the artefact does,
    test 1 above would compare two empty sequences and pass while the published file was reordered.
    """
    pin = _pin(case)
    collection = _artefact(case)
    for layer, key in LAYERS:
        assert key in pin, f"{case}: the pin has no {key!r} key — nothing for {layer} to come from"
        assert _layer_features(collection, layer), (
            f"{case}: the artefact publishes no {layer} feature, so its order cannot be checked "
            "against the pin — a layer the pin fills must not vanish from the artefact"
        )


def test_the_writer_publishes_the_pin_order_untouched():
    """`context.pinned is True` -> the writer keeps the stored order instead of re-ranking.

    The regression this forbids is the one the reviewer measured: the writer announced a ranking and
    applied it to already-pinned, already-simplified geometry, so the pinned path and the
    fresh-extract path produced the same features in different orders. Silent cost: a byte diff in a
    file declared reproducible, attributed to nothing, on a pipeline nobody would then trust.
    """
    model = _fake_model(pinned=True)
    published = _published_names(model)
    stored = [str(name) for name in model.context.features["name"]]
    assert published == stored, (
        "a pinned context must be published in its stored order, untouched: "
        f"stored {stored}, published {published}"
    )


def test_the_writer_reranks_only_when_the_context_is_not_pinned():
    """`context.pinned is False` -> the order follows `select_for_publication`, cap included.

    Both halves are needed: if the writer ignored the flag in the *other* direction (never ranking),
    a fresh extract would publish OSM's own element order, which is not stable between fetches.
    The cap is passed as `FEATURE_CAP_PER_KIND`, the module's own number, so this test cannot drift
    from the writer by carrying a private copy of it.
    """
    model = _fake_model(pinned=False)
    published = _published_names(model)
    ranked = [
        str(name)
        for name in CTX.select_for_publication(
            model.context.features, cap=CTX.FEATURE_CAP_PER_KIND
        )["name"]
    ]
    stored = [str(name) for name in model.context.features["name"]]
    assert ranked != stored, (
        "the fixture no longer distinguishes stored order from ranked order (smallest-first vs "
        f"size-descending): both are {stored}, so this test would pass for the wrong reason"
    )
    assert published == ranked, (
        "an unpinned context must still be ranked before publication: "
        f"ranking says {ranked}, the writer published {published}"
    )


# ---------------------------------------------------------------------------------------------
# 4. Unit level: a pin that fails its own digest is never read
# ---------------------------------------------------------------------------------------------


def _unit_pin_payload() -> dict:
    """A minimal pin, shaped like `pin_context.build_pin`'s output but holding nothing real."""
    return {
        "case_id": "unit-order",
        "crs": "EPSG:4326",
        "source": "unit (test fixture, not OSM data)",
        "record": {"retrieved_utc": None, "timestamp_osm_base": None},
        "cell_land_use": {"8928308280fff": "water", "8928308280aa0": "urban-residential"},
        "water": [
            {
                "type": "Feature",
                "properties": {"class": "reservoir", "name": "editable-label-zzz"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]],
                },
            }
        ],
        "infrastructure": [
            {
                "type": "Feature",
                "properties": {"class": "water-well"},
                "geometry": {"type": "Point", "coordinates": [0.005, 0.005]},
            }
        ],
        "admin": [],
    }


def test_read_pin_refuses_a_pin_that_fails_its_own_digest(tmp_path):
    """A hand-edited pin must raise, not publish labels nobody derived.

    `pin_context.write_pin` stamps `pin_sha256` over the canonical body and `context.read_pin`
    recomputes it. Without that check a one-character edit to a committed label would be adopted
    silently: `context_layers.geojson` and the grid's `land_use` column would then describe a city
    that no derivation produced, and every downstream gate would still pass because the file parses
    and its schema is intact.
    """
    path = CTX.pin_path("unit-order", tmp_path)
    PIN.write_pin(_unit_pin_payload(), path)

    # Positive control first: the same file, unedited, must read, so a raise below is attributable to
    # the digest and not to a malformed fixture.
    assert CTX.read_pin("unit-order", tmp_path)["cell_land_use"]["8928308280fff"] == "water"

    text = path.read_text(encoding="utf-8")
    flipped = text.replace("editable-label-zzz", "editable-label-zzZ", 1)
    assert flipped != text, "the fixture's label did not appear in the written pin; nothing was flipped"
    path.write_text(flipped, encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        CTX.read_pin("unit-order", tmp_path)
    assert "pin_sha256" in str(excinfo.value), (
        f"a corrupted pin raised, but not as a digest failure: {excinfo.value}"
    )
