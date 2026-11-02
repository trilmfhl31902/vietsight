#!/usr/bin/env python3
"""Silent USB detection dashboard, served locally and viewed through SSH."""
import argparse
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from detection_logic import Detection, Stabilizer, VI_NAMES, sentence, summarize
from main import Camera

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("gui")


class Dashboard:
    def __init__(self, max_age=3):
        self.lock = threading.Lock()
        self.max_age = max_age
        self.jpeg = None
        self.data = {"status": "loading", "message": "Đang nạp model và mở camera…",
                     "regions": {"left": [], "center": [], "right": []},
                     "detections": [], "latency_ms": 0, "fps": 0, "demo": False}
        self.captured = 0

    def update(self, jpeg, data, captured):
        with self.lock:
            self.jpeg, self.data, self.captured = jpeg, data, captured

    def fail(self, message):
        with self.lock:
            self.data = {**self.data, "status": "error", "message": message,
                         "regions": {"left": [], "center": [], "right": []},
                         "detections": []}

    def snapshot(self):
        with self.lock:
            data = dict(self.data)
            age = time.monotonic() - self.captured if self.captured else None
            data["age_seconds"] = round(age, 1) if age is not None else None
            if data["status"] == "running" and age is not None and age > self.max_age:
                data.update(status="stale", message="Hình đã cũ — đang chờ khung xử lý mới",
                            regions={"left": [], "center": [], "right": []}, detections=[])
            return self.jpeg, data


def handler_for(dashboard):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                body = (BASE / "dashboard.html").read_bytes()
                content_type, code = "text/html; charset=utf-8", 200
            elif path == "/api/status":
                _, data = dashboard.snapshot()
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                content_type, code = "application/json; charset=utf-8", 200
            elif path == "/frame.jpg":
                body, _ = dashboard.snapshot()
                code, content_type = (200, "image/jpeg") if body else (503, "text/plain")
                body = body or b"Waiting for first frame"
            else:
                body, content_type, code = b"Not found", "text/plain", 404
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    return Handler


def run_detection(a, dashboard, stop):
    cap = None
    try:
        import cv2
        cv2.setNumThreads(1)
        if a.demo:
            import numpy as np
            frame = np.full((480, 640, 3), (30, 36, 40), dtype=np.uint8)
            cv2.putText(frame, "DEMO - NO CAMERA", (125, 230),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (70, 215, 180), 2)
            model = None
        else:
            os.environ.setdefault("OMP_NUM_THREADS", str(a.threads))
            import torch
            from ultralytics import YOLO
            torch.set_num_threads(a.threads)
            model = YOLO(a.model, task="detect")
            if (BASE / "labels_vi.json").is_file():
                VI_NAMES.update(json.loads((BASE / "labels_vi.json").read_text(encoding="utf-8")))
            source = int(a.camera) if a.camera.isdigit() else a.camera
            cap = Camera(cv2, source, 640, 480)
        stabilizer = Stabilizer(2)
        previous = -1
        last_frame = time.monotonic()
        while not stop.is_set():
            if cap:
                if cap.error:
                    raise RuntimeError(cap.error)
                frame, captured, frame_id = cap.read()
                if frame is None or frame_id == previous:
                    if time.monotonic() - last_frame > 5:
                        raise RuntimeError("Camera không trả hình mới trong 5 giây")
                    stop.wait(0.02)
                    continue
                previous, last_frame = frame_id, time.monotonic()
            else:
                captured = time.monotonic()
            started = time.monotonic()
            detections = []
            if model is not None:
                result = model.predict(frame, imgsz=a.imgsz, conf=a.conf, device="cpu",
                                       verbose=False, max_det=30)[0]
                if result.boxes is not None:
                    for box, cls, confidence in zip(result.boxes.xyxy.tolist(),
                                                    result.boxes.cls.tolist(),
                                                    result.boxes.conf.tolist()):
                        detections.append(Detection(str(result.names[int(cls)]), confidence, tuple(box)))
                preview = result.plot()
            else:
                preview = frame.copy()
            elapsed = time.monotonic() - started
            scene = stabilizer.update(summarize(detections, frame.shape[1]))
            fresh = time.monotonic() - captured <= a.max_age
            if not fresh:
                scene = frozenset()
                stabilizer.reset()
            h, w = preview.shape[:2]
            for x in (w // 3, 2 * w // 3):
                cv2.line(preview, (x, 0), (x, h), (70, 220, 180), 2)
            for i, label in enumerate(("TRAI", "GIUA", "PHAI")):
                cv2.putText(preview, label, (int(i * w / 3) + 12, 28),
                            cv2.FONT_HERSHEY_SIMPLEX, .65, (70, 220, 180), 2)
            ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if not ok:
                raise RuntimeError("Không mã hóa được hình camera")
            regions = {r: sorted({VI_NAMES.get(n, n) for rr, n in scene if rr == r})
                       for r in ("left", "center", "right")}
            data = {"status": "running" if fresh else "stale",
                    "message": sentence(scene) or ("Chưa có vật thể được xác nhận" if fresh
                               else "Suy luận quá chậm — thử --imgsz 256"),
                    "regions": regions,
                    "detections": [{"name": VI_NAMES.get(d.name, d.name),
                                    "confidence": round(d.confidence * 100)} for d in detections] if fresh else [],
                    "latency_ms": round(elapsed * 1000),
                    "fps": round(1 / max(elapsed, .001), 1) if not a.demo else 0,
                    "demo": a.demo, "camera": a.camera, "imgsz": a.imgsz}
            dashboard.update(encoded.tobytes(), data, captured)
            if a.demo:
                stop.wait(.2)
    except Exception as exc:
        LOG.exception("Nhận diện dừng")
        dashboard.fail(str(exc))
    finally:
        if cap:
            cap.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--camera", default="/dev/video1")
    p.add_argument("--model", default=str(BASE / "best.pt"))
    p.add_argument("--imgsz", type=int, default=320)
    p.add_argument("--conf", type=float, default=.45)
    p.add_argument("--threads", type=int, default=3)
    p.add_argument("--max-age", type=float, default=3)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--demo", action="store_true", help="Giao diện mẫu, không dùng camera/model")
    a = p.parse_args()
    if min(a.imgsz, a.threads, a.max_age) <= 0 or not 0 < a.conf <= 1 or not 1 <= a.port <= 65535:
        p.error("Thông số không hợp lệ")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dashboard, stop = Dashboard(a.max_age), threading.Event()
    server = ThreadingHTTPServer((a.host, a.port), handler_for(dashboard))
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    server.timeout = .5
    worker = threading.Thread(target=run_detection, args=(a, dashboard, stop), daemon=True)
    worker.start()
    LOG.info("Giao diện: http://%s:%s — âm thanh TẮT", a.host, a.port)
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
