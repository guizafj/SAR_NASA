"""Synthetic geography per case — an explicit, documented invention.

The frozen contract fixes the *schema* of `context_layers.geojson`, not the geography. Nobody has
time tonight to fetch GHSL/OSM, so each case gets the crudest defensible shape:

* Mexico City — closed basin, no coastline: one lake (the former Texcoco bed) + one river.
* Jakarta     — coastal, sea to the north: one coastline + one flood canal.
* Málaga      — coastal, sea to the south: one coastline + the Guadalhorce river mouth.

Plus one industrial corridor per case, so `land_use` can carry `industrial` / `infrastructure`
honestly instead of painting random cells. Every coordinate and name below is synthetic and says
so: `source` in the GeoJSON is `"mock (...)"`, never a real provider. Nothing here is a claim
about the real city — it exists to exercise the schema and the map layers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON_EQ = 111.320


def km_per_deg_lon(lat: float) -> float:
    return KM_PER_DEG_LON_EQ * math.cos(math.radians(lat))


def lonlat_to_local_km(
    lon: np.ndarray, lat: np.ndarray, lon0: float, lat0: float
) -> tuple[np.ndarray, np.ndarray]:
    """Equirectangular projection around (lon0, lat0). Adequate: AOIs are < 60 km wide."""
    return (np.asarray(lon, float) - lon0) * km_per_deg_lon(lat0), (
        np.asarray(lat, float) - lat0
    ) * KM_PER_DEG_LAT


def local_km_to_lonlat(x: np.ndarray, y: np.ndarray, lon0: float, lat0: float) -> tuple[np.ndarray, np.ndarray]:
    return lon0 + np.asarray(x, float) / km_per_deg_lon(lat0), lat0 + np.asarray(y, float) / KM_PER_DEG_LAT


def ellipse_rho(
    lon: np.ndarray, lat: np.ndarray, center_lon: float, center_lat: float, a_km: float, b_km: float, rotation_deg: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normalised elliptical radius and the rotated frame (u along the a-axis, v along b).

    `rho <= 1` == centroid inside the coverage ellipse. Used both for the coverage mask and for
    the land-use rings, so the two can never disagree.
    """
    x, y = lonlat_to_local_km(lon, lat, center_lon, center_lat)
    theta = math.radians(rotation_deg)
    u = x * math.cos(theta) + y * math.sin(theta)
    v = -x * math.sin(theta) + y * math.cos(theta)
    return u, v, np.sqrt((u / a_km) ** 2 + (v / b_km) ** 2)


def ellipse_ring(
    center_lon: float, center_lat: float, a_km: float, b_km: float, rotation_deg: float, n: int = 72
) -> list[list[float]]:
    """Closed lon/lat ring for an ellipse given in km (EPSG:4326 output)."""
    t = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    u, v = a_km * np.cos(t), b_km * np.sin(t)
    theta = math.radians(rotation_deg)
    x = u * math.cos(theta) - v * math.sin(theta)
    y = u * math.sin(theta) + v * math.cos(theta)
    lon, lat = local_km_to_lonlat(x, y, center_lon, center_lat)
    ring = [[round(float(lo), 6), round(float(la), 6)] for lo, la in zip(lon, lat, strict=True)]
    ring.append(ring[0])
    return ring


@dataclass(frozen=True)
class Coast:
    """Polyline (lon, lat) with the sea on the left (+1) or right (-1) of the a->b direction."""

    verts: tuple[tuple[float, float], ...]
    sea_side: int  # +1 = left/north of a w->e line, -1 = right/south

    def signed_distance(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """Distance in km to the coastline, positive on the sea side, negative inland."""
        dist, side = polyline_metrics(lon, lat, self.verts)
        return dist * np.where(side == self.sea_side, 1.0, -1.0)


def polyline_metrics(
    lon: np.ndarray, lat: np.ndarray, verts: tuple[tuple[float, float], ...] | list[tuple[float, float]]
) -> tuple[np.ndarray, np.ndarray]:
    """Distance (km) from points to a polyline and the side sign of the nearest segment.

    Side sign is `sign(cross(b-a, p-a))` in the local metric frame: +1 left, -1 right.
    """
    lon = np.asarray(lon, float)
    lat = np.asarray(lat, float)
    lon0 = float(np.mean([v[0] for v in verts]))
    lat0 = float(np.mean([v[1] for v in verts]))
    px, py = lonlat_to_local_km(lon, lat, lon0, lat0)
    pts = np.array([lonlat_to_local_km(np.array([v[0]]), np.array([v[1]]), lon0, lat0) for v in verts])
    ax, ay = pts[:, 0], pts[:, 1]
    best = np.full(px.shape, np.inf)
    side = np.zeros(px.shape)
    for i in range(len(verts) - 1):
        dx, dy = ax[i + 1] - ax[i], ay[i + 1] - ay[i]
        length2 = dx * dx + dy * dy
        if length2 == 0.0:
            continue
        t = np.clip(((px - ax[i]) * dx + (py - ay[i]) * dy) / length2, 0.0, 1.0)
        d = np.hypot(px - (ax[i] + t * dx), py - (ay[i] + t * dy))
        closer = d < best
        best = np.where(closer, d, best)
        cross = dx * (py - ay[i]) - dy * (px - ax[i])
        side = np.where(closer, np.sign(cross), side)
    return best, side


@dataclass(frozen=True)
class CaseGeography:
    coast: Coast | None
    #: lake / water body: (center_lon, center_lat, a_km, b_km, rotation_deg)
    lake: tuple[float, float, float, float, float] | None
    #: river or canal: (context class, name, verts)
    waterway: tuple[str, str, tuple[tuple[float, float], ...]] | None
    #: industrial corridor: (azimuth_deg clockwise from east, length_km, name)
    corridor: tuple[float, float, str]
    #: infrastructure POIs: (context class, name, lon, lat)
    points: tuple[tuple[str, str, float, float], ...] = ()
    #: name of the inland water body, when a case has one instead of a coastline
    lake_name: str | None = None
    land_use_source: str = "mock (synthetic land-cover rule)"
    water_source: str = "mock (synthetic hydrology)"
    infrastructure_source: str = "mock (synthetic utility POI)"
    boundary_source: str = "mock (synthetic AOI)"


GEOGRAPHY: dict[str, CaseGeography] = {
    "mexico-city": CaseGeography(
        coast=None,
        lake=(-99.015, 19.445, 3.4, 2.2, 20.0),
        lake_name="Lago de Texcoco (sintético)",
        waterway=(
            "river",
            "Río Churubusco (sintético)",
            ((-99.245, 19.352), (-99.160, 19.365), (-99.075, 19.376), (-98.995, 19.398)),
        ),
        corridor=(20.0, 18.0, "Corredor industrial Vallejo (sintético)"),
        points=(
            ("metro-station", "Estación Metro Zócalo (sintética)", -99.133, 19.433),
            ("water-well", "Pozo Roma (sintético)", -99.160, 19.420),
            ("water-treatment", "Planta de tratamiento Texcoco (sintética)", -99.010, 19.452),
        ),
    ),
    "jakarta": CaseGeography(
        coast=Coast(
            (
                (106.700, -6.132),
                (106.780, -6.140),
                (106.860, -6.134),
                (106.940, -6.142),
                (107.000, -6.136),
            ),
            sea_side=+1,  # north of a west->east coast
        ),
        lake=None,
        waterway=(
            "canal",
            "Kanal Banjir Barat (sintético)",
            ((106.788, -6.140), (106.805, -6.210), (106.820, -6.280), (106.835, -6.360)),
        ),
        corridor=(100.0, 16.0, "Koridor industri Cakung (sintético)"),
        points=(
            ("metro-station", "Estación Metro Bundaran HI (sintética)", 106.823, -6.193),
            ("water-well", "Pozo Pluit (sintético)", 106.795, -6.155),
            ("water-treatment", "Planta de tratamiento Marunda (sintética)", 106.905, -6.220),
        ),
    ),
    "malaga": CaseGeography(
        coast=Coast(
            (
                (-4.600, 36.688),
                (-4.530, 36.681),
                (-4.470, 36.690),
                (-4.400, 36.678),
                (-4.300, 36.684),
            ),
            sea_side=-1,  # south of a west->east coast
        ),
        lake=None,
        waterway=(
            "river",
            "Río Guadalhorce (sintético)",
            ((-4.480, 36.840), (-4.462, 36.760), (-4.446, 36.700), (-4.436, 36.684)),
        ),
        corridor=(260.0, 14.0, "Corredor industrial Guadalhorce (sintético)"),
        points=(
            ("metro-station", "Estación Metro El Perchel (sintética)", -4.430, 36.715),
            ("water-well", "Pozo Campanillas (sintético)", -4.470, 36.735),
            ("water-treatment", "Planta de tratamiento Guadalhorce (sintética)", -4.400, 36.700),
        ),
    ),
}


def corridor_segment(
    geo: CaseGeography, center_lon: float, center_lat: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Corridor axis as a lon/lat segment starting at the coverage-ellipse centre."""
    azimuth_deg, length_km, _ = geo.corridor
    theta = math.radians(azimuth_deg)
    x, y = length_km * math.cos(theta), length_km * math.sin(theta)
    end_lon, end_lat = local_km_to_lonlat(np.array([x]), np.array([y]), center_lon, center_lat)
    return (center_lon, center_lat), (float(end_lon[0]), float(end_lat[0]))
