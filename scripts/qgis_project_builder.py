"""Build the QC project — and render it — using QGIS itself.

Run by ``scripts/style_qgis_layers.py`` through ``qgis --code``; never by hand.

    qgis --nologo --nocustomization --profiles-path <tmp> --profile qc \
         --code scripts/qgis_project_builder.py --py-args <spec.json>

Why a QGIS-side helper instead of writing the .qgs XML directly
---------------------------------------------------------------
A project file is not a document format: it is a snapshot of QGIS's own object graph, and a
hand-written one fails in ways that look like styling bugs. The first attempt here produced a
1600x900 canvas of pure white, for two independent reasons:

* no ``<mapcanvas>`` extent, so the renderer had no window onto the data at all; and
* a single canvas trying to frame Málaga, Mexico City and Jakarta simultaneously — three
  continents in one extent, which is a few pixels of nothing. That is a *design* error, not an XML
  one: a QC image has to be one city at its own scale.

So this file asks QGIS for the objects (``QgsVectorLayer``, ``QgsProject``, ``QgsMapRenderer``) and
lets it serialise them. If QGIS opens the result, the result is a project.

Two more things are load-bearing, both found the hard way:

* ``QT_QPA_PLATFORM=offscreen`` with a throwaway profile. The user's profile loads GUI plugins
  that dereference ``iface``; under ``offscreen`` that blocks the event loop forever, and the run
  dies on a timeout with *no output* — which reads like a QML bug rather than a profile bug.
* ``os._exit`` after writing. QGIS's own ``--snapshot`` path renders the file and then refuses to
  quit, so a plain ``subprocess.run`` hangs for its full timeout even though the PNG is on disk.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import traceback

try:
    from qgis.core import (
        QgsApplication,
        QgsCoordinateReferenceSystem,
        QgsMapRendererParallelJob,
        QgsMapSettings,
        QgsProject,
        QgsRectangle,
        QgsVectorLayer,
    )
    from qgis.PyQt.QtCore import QSize, QTimer
    from qgis.PyQt.QtGui import QColor, QImage
except ModuleNotFoundError as _exc:  # pragma: no cover - depends on the host, not the repo
    # The QGIS bindings are a SYSTEM package, not a pip dependency: `pip install -r
    # requirements.txt` never provides them. Importing at module scope made every invocation die in
    # a traceback, which reads like a broken script rather than a missing host dependency, so the
    # failure is deferred to run time with an actionable message and the project's exit code 3.
    _QGIS_IMPORT_ERROR: Exception | None = _exc
else:
    _QGIS_IMPORT_ERROR = None

STATUS = "/tmp/qgis_builder_status.json"


def build(spec: dict) -> dict:
    result: dict = {"layers": [], "errors": []}
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem(spec.get("crs", "EPSG:4326")))
    root = project.layerTreeRoot()

    by_case: dict[str, dict[str, QgsVectorLayer]] = {}
    for group_name in spec["groups"]:
        group = root.addGroup(group_name)
        by_case[group_name] = {}
        for layer_spec in spec["layers"]:
            if layer_spec["group"] != group_name:
                continue
            layer = QgsVectorLayer(layer_spec["source"], layer_spec["name"], "ogr")
            if not layer.isValid():
                result["errors"].append(f"{layer_spec['name']}: invalid layer ({layer_spec['source']})")
                continue
            loaded = layer.loadNamedStyle(layer_spec["style"])
            if not _style_loaded(loaded):
                result["errors"].append(f"{layer_spec['name']}: style rejected ({layer_spec['style']})")
            project.addMapLayer(layer, addToLegend=False)
            node = group.addLayer(layer)
            node.setItemVisibilityChecked(bool(layer_spec.get("visible", True)))
            by_case[group_name][layer_spec["role"]] = layer
            result["layers"].append(
                {
                    "name": layer_spec["name"],
                    "renderer": layer.renderer().type() if layer.renderer() else None,
                    "features": layer.featureCount(),
                }
            )

    project_path = spec.get("project_path")
    if project_path:
        result["project_written"] = bool(project.write(project_path))
        # Read the written file back: a project that opens but silently lost its renderers is the
        # failure this whole exercise exists to catch, and the in-memory object cannot prove it.
        written = pathlib.Path(project_path)
        if written.is_file():
            result["project_renderers"] = written.read_text().count("<renderer-v2")
            result["project_path"] = str(written)

    render = spec.get("render")
    if render:
        case = render["case"]
        # A QC image is one city: everything outside the chosen case is switched off, or the
        # extent has to span continents and the map is empty by construction.
        for group_name, layers in by_case.items():
            for layer in layers.values():
                node = root.findLayer(layer.id())
                if node is not None:
                    node.setItemVisibilityChecked(group_name == case)
        velocity = by_case.get(case, {}).get("velocity")
        if velocity is None:
            result["errors"].append(f"no velocity layer for case {case!r}")
        else:
            destination = QgsCoordinateReferenceSystem("EPSG:3857")
            extent: QgsRectangle = velocity.extent()
            pad_x, pad_y = extent.width() * 0.04, extent.height() * 0.04
            padded = QgsRectangle(
                extent.xMinimum() - pad_x,
                extent.yMinimum() - pad_y,
                extent.xMaximum() + pad_x,
                extent.yMaximum() + pad_y,
            )
            settings = QgsMapSettings()
            settings.setDestinationCrs(destination)
            settings.setLayers(
                [layer for layer in by_case[case].values() if root.findLayer(layer.id()).isVisible()]
            )
            settings.setOutputSize(QSize(int(render.get("width", 1600)), int(render.get("height", 900))))
            settings.setExtent(_transform_rect(padded, velocity, destination))
            settings.setBackgroundColor(QColor(render.get("background", "#ffffff")))
            job = QgsMapRendererParallelJob(settings)
            job.start()
            job.waitForFinished()
            image: QImage = job.renderedImage()
            result["render"] = {
                "target": render["target"],
                "saved": bool(image.save(render["target"])),
                "size": [image.width(), image.height()],
                "extent": [
                    settings.extent().xMinimum(),
                    settings.extent().yMinimum(),
                    settings.extent().xMaximum(),
                    settings.extent().yMaximum(),
                ],
            }
    return result


def _style_loaded(result) -> bool:
    """Did `loadNamedStyle` succeed? The tuple order is not stable across QGIS versions.

    QGIS 4 returns ``(message, success)`` — so reading ``result[0]`` reads the message, which is
    ``''`` on success and therefore falsy. That inversion reported fifteen correctly styled layers
    as rejected while the map rendered perfectly. Take whichever element is a bool.
    """
    if isinstance(result, tuple):
        flags = [item for item in result if isinstance(item, bool)]
        return bool(flags and flags[0])
    return bool(result)


def _transform_rect(rect: QgsRectangle, layer: QgsVectorLayer, destination: QgsCoordinateReferenceSystem) -> QgsRectangle:
    """Reproject a layer's extent into the render CRS (Web Mercator, for a sane aspect ratio)."""
    from qgis.core import QgsCoordinateTransform, QgsProject

    transform = QgsCoordinateTransform(layer.crs(), destination, QgsProject.instance())
    return transform.transformBoundingBox(rect)


def main() -> None:
    spec_path = sys.argv[1]
    result: dict
    status = STATUS
    try:
        with open(spec_path) as fh:
            spec = json.load(fh)
        status = spec.get("status", STATUS)
        result = build(spec)
    except Exception:
        result = {"errors": ["builder crashed\n" + traceback.format_exc()]}
    with open(status, "w") as fh:
        json.dump(result, fh, indent=2)
    sys.stdout.flush()
    os._exit(0 if not result.get("errors") else 1)


if _QGIS_IMPORT_ERROR is None:
    QTimer.singleShot(300, main)
else:  # pragma: no cover - depends on the host
    print(
        "qgis_project_builder: the QGIS Python bindings are required and were not importable "
        f"({_QGIS_IMPORT_ERROR}).\n"
        "  They are a system package, not a pip dependency: install QGIS, or run this inside a "
        "QGIS python (e.g. `qgis --python`). `scripts/style_qgis_layers.py` generates the same "
        ".qml files without needing QGIS at all.",
        file=sys.stderr,
    )
    raise SystemExit(3)
