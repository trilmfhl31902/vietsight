"""Logic independent of camera, YOLO and audio (also used by tests)."""
from dataclasses import dataclass
from collections import Counter

VI_NAMES = {
    "person": "người", "bicycle": "xe đạp", "car": "ô tô",
    "motorcycle": "xe máy", "motorbike": "xe máy", "bus": "xe buýt",
    "truck": "xe tải", "traffic_light": "đèn giao thông",
    "traffic light": "đèn giao thông",
}
REGIONS = ("left", "center", "right")
VI_REGIONS = {"left": "Bên trái", "center": "Chính giữa", "right": "Bên phải"}


@dataclass(frozen=True)
class Detection:
    name: str
    confidence: float
    box: tuple


def regions_for_box(box, width):
    """Report every third overlapped by >=20% of a box's visible width.

    A wide vehicle must not disappear from the central warning merely because
    its center falls in a side region. Always include the box center as well.
    """
    x1, _, x2, _ = box
    x1, x2 = max(0.0, x1), min(float(width), x2)
    if width <= 0 or x2 <= x1:
        return ()
    center = min(2, int(((x1 + x2) / 2) / (width / 3)))
    return tuple(region for i, region in enumerate(REGIONS)
                 if i == center or
                 max(0, min(x2, (i + 1) * width / 3) - max(x1, i * width / 3))
                 / (x2 - x1) >= 0.2)


def summarize(detections, width):
    found = {r: set() for r in REGIONS}
    for det in detections:
        for region in regions_for_box(det.box, width):
            found[region].add(det.name)
    return frozenset((r, name) for r, names in found.items() for name in names)


def sentence(items):
    parts = []
    # Center first because it is directly in the path of the camera.
    for region in ("center", "left", "right"):
        names = sorted({VI_NAMES.get(n, n) for r, n in items if r == region})
        if names:
            parts.append(f"{VI_REGIONS[region]} có {', '.join(names)}")
    return ". ".join(parts) + ("." if parts else "")


class Stabilizer:
    """Require consecutive fresh observations; clear absent labels immediately."""
    def __init__(self, frames=2):
        self.frames = frames
        self.counts = Counter()

    def update(self, items):
        self.counts = Counter({k: self.counts[k] + 1 for k in items})
        return frozenset(k for k in items if self.counts[k] >= self.frames)

    def reset(self):
        self.counts.clear()
