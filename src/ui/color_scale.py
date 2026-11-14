"""One color domain for the layer and the legend.

# TODO(contract): replace FALLBACK_DOMAIN when meta publishes color_domain.
These bounds are a presentation placeholder, not a measurement.
"""

from __future__ import annotations

FALLBACK_DOMAIN = (-8.0, 8.0)

# Same stops the legend paints. Coral is the low end of the ramp only.
STOPS: tuple[tuple[float, tuple[int, int, int, int]], ...] = (
    (0.0, (244, 63, 94, 200)),
    (0.5, (245, 158, 11, 200)),
    (1.0, (6, 182, 212, 200)),
)


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def rgba_at(t: float) -> list[int]:
    """Sample the shared ramp. t is clipped to [0, 1] (saturation)."""
    clipped = min(1.0, max(0.0, float(t)))
    for index, (stop, color) in enumerate(STOPS[1:], start=1):
        left_t, left = STOPS[index - 1]
        if clipped <= stop:
            span = stop - left_t
            local = 0.0 if span == 0 else (clipped - left_t) / span
            return [_lerp(left[channel], color[channel], local) for channel in range(4)]
    red, green, blue, alpha = STOPS[-1][1]
    return [red, green, blue, alpha]


def color_for(value: float, vmin: float, vmax: float) -> list[int]:
    if vmax == vmin:
        return rgba_at(0.5)
    return rgba_at((float(value) - vmin) / (vmax - vmin))


def values_saturate(values: list[float], vmin: float, vmax: float) -> bool:
    return any(item < vmin or item > vmax for item in values)
