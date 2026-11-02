"""Load user's actual weights and infer a synthetic image; no camera needed."""
from pathlib import Path
import numpy as np
from ultralytics import YOLO

model = YOLO(str(Path(__file__).resolve().parent / "best.pt"), task="detect")
expected = {"person", "bicycle", "car", "motorcycle", "bus", "truck", "traffic_light"}
assert set(model.names.values()) == expected, model.names
result = model.predict(np.zeros((480, 640, 3), dtype=np.uint8), imgsz=320,
                       device="cpu", verbose=False)[0]
assert result.orig_shape == (480, 640), result.orig_shape
assert result.boxes is not None
print("PASS: loaded best.pt, 7 labels, CPU inference at imgsz=320")
