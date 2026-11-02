#!/usr/bin/env python3
"""USB camera -> local YOLO -> Vietnamese speech, no cloud services."""
import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time

from detection_logic import Detection, Stabilizer, sentence, summarize, VI_NAMES

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("minhtri")


class Speaker:
    """Only the most recent scene is retained; never queue old detections."""
    def __init__(self, interval, max_age, muted=False):
        self.interval, self.max_age, self.muted = interval, max_age, muted
        self.binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not muted and not self.binary:
            raise RuntimeError("Thiếu espeak-ng. Chạy: sudo apt install espeak-ng")
        self.lock = threading.Lock()
        self.latest = (frozenset(), 0.0)
        self.stop = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def publish(self, items, captured):
        with self.lock:
            self.latest = (items, captured)

    def speak(self, text):
        LOG.info("Âm thanh: %s", text)
        if not self.muted:
            subprocess.run([self.binary, "-v", "vi", "-s", "165", text],
                           check=True, timeout=20, stdin=subprocess.DEVNULL)

    def _run(self):
        try:
            self.speak("Đã bật nhận diện vật thể.")
            last_time = time.monotonic()
            while not self.stop.wait(0.1):
                now = time.monotonic()
                with self.lock:
                    items, captured = self.latest
                if items and now - captured <= self.max_age and now - last_time >= self.interval:
                    self.speak(sentence(items))
                    last_time = time.monotonic()
        except Exception as exc:
            self.error = exc
            LOG.exception("Không phát được âm thanh")

    def close(self):
        self.stop.set()
        self.thread.join(timeout=21)


class Camera:
    """Drain the USB stream in a separate thread to avoid buffered old frames."""
    def __init__(self, cv2, source, width, height):
        self.cv2 = cv2
        backend = cv2.CAP_V4L2 if os.name == "posix" else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(source, backend)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(f"Không mở được camera {source}. Kiểm tra /dev/video* và quyền video.")
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, 15)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.lock = threading.Lock()
        self.latest = (None, 0.0, 0)
        self.stop = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        failures = 0
        try:
            while not self.stop.is_set():
                ok, frame = self.cap.read()
                if not ok:
                    failures += 1
                    if failures >= 10:
                        self.error = "Mất kết nối camera USB"
                        return
                    self.stop.wait(0.1)
                    continue
                failures = 0
                with self.lock:
                    self.latest = (frame, time.monotonic(), self.latest[2] + 1)
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.cap.release()

    def read(self):
        with self.lock:
            return self.latest

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default=str(BASE / "best.pt"))
    p.add_argument("--camera", default="0", help="0, 1 hoặc /dev/video0")
    p.add_argument("--conf", type=float, default=0.45)
    p.add_argument("--imgsz", type=int, default=320)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--threads", type=int, default=3)
    p.add_argument("--interval", type=float, default=3.0, help="Khoảng nghỉ giữa các câu")
    p.add_argument("--max-age", type=float, default=3.0, help="Bỏ kết quả cũ hơn số giây này")
    p.add_argument("--confirm-frames", type=int, default=2)
    p.add_argument("--show", action="store_true", help="Cần màn hình Pi hoặc X11")
    p.add_argument("--mute", action="store_true", help="Chỉ in cảnh báo, không phát loa")
    p.add_argument("--test-audio", action="store_true")
    p.add_argument("--list-labels", action="store_true")
    p.add_argument("--labels", default=str(BASE / "labels_vi.json"))
    p.add_argument("--duration", type=float, default=0, help="Dừng sau N giây, 0: liên tục")
    a = p.parse_args()
    if not 0 < a.conf <= 1 or min(a.imgsz, a.width, a.height, a.threads,
                                 a.interval, a.max_age, a.confirm_frames) <= 0 or a.duration < 0:
        p.error("Các kích thước, thời gian và số khung phải dương; conf trong (0, 1].")
    return a


def main():
    a = arguments()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.test_audio:
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not binary:
            raise RuntimeError("Thiếu espeak-ng: sudo apt install espeak-ng")
        subprocess.run([binary, "-v", "vi", "-s", "165",
                        "Bên trái có người. Chính giữa có ô tô. Bên phải có xe máy."],
                       check=True, timeout=20)
        return
    if Path(a.labels).is_file():
        labels = json.loads(Path(a.labels).read_text(encoding="utf-8"))
        if not isinstance(labels, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                 for k, v in labels.items()):
            raise ValueError("labels_vi.json phải là ánh xạ tên lớp sang chuỗi tiếng Việt")
        VI_NAMES.update(labels)
    if not Path(a.model).exists():
        raise FileNotFoundError(f"Không có model: {a.model}")
    os.environ.setdefault("OMP_NUM_THREADS", str(a.threads))
    import cv2
    import torch
    from ultralytics import YOLO
    torch.set_num_threads(a.threads)
    cv2.setNumThreads(1)
    model = YOLO(a.model, task="detect")
    LOG.info("Nhãn model: %s", model.names)
    if a.list_labels:
        return
    cap = speaker = None
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    stabilizer = Stabilizer(a.confirm_frames)
    try:
        speaker = Speaker(a.interval, a.max_age, a.mute)
        source = int(a.camera) if a.camera.isdigit() else a.camera
        cap = Camera(cv2, source, a.width, a.height)
        started = time.monotonic()
        previous_id, last_scene = -1, None
        last_progress = started
        while not stopped.is_set():
            now = time.monotonic()
            if a.duration and now - started >= a.duration:
                break
            if speaker.error:
                raise RuntimeError("Hệ thống âm thanh lỗi") from speaker.error
            if cap.error:
                raise RuntimeError(cap.error)
            frame, captured, frame_id = cap.read()
            if frame is None or frame_id == previous_id:
                if now - last_progress > 5:
                    raise RuntimeError("Camera không cung cấp khung hình mới trong 5 giây")
                stopped.wait(0.01)
                continue
            previous_id, last_progress = frame_id, now
            if now - captured > a.max_age:
                speaker.publish(frozenset(), now)
                stabilizer.reset()
                continue
            result = model.predict(frame, imgsz=a.imgsz, conf=a.conf, device="cpu",
                                   verbose=False, max_det=30)[0]
            if time.monotonic() - captured > a.max_age:
                LOG.warning("Suy luận quá chậm, bỏ kết quả cũ. Thử --imgsz 256 hoặc NCNN.")
                speaker.publish(frozenset(), captured)
                stabilizer.reset()
                continue
            detections = []
            if result.boxes is not None:
                for box, cls, conf in zip(result.boxes.xyxy.tolist(),
                                          result.boxes.cls.tolist(), result.boxes.conf.tolist()):
                    detections.append(Detection(str(result.names[int(cls)]), conf, tuple(box)))
            scene = stabilizer.update(summarize(detections, frame.shape[1]))
            speaker.publish(scene, captured)
            if scene != last_scene:
                LOG.info("Cảnh: %s", sentence(scene) or "Không có nhãn đã xác nhận")
                last_scene = scene
            if a.show:
                preview = result.plot()
                h, w = preview.shape[:2]
                for x in (w // 3, 2 * w // 3):
                    cv2.line(preview, (x, 0), (x, h), (0, 255, 255), 2)
                for i, label in enumerate(("TRAI", "GIUA", "PHAI")):
                    cv2.putText(preview, label, (int(i * w / 3) + 10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                cv2.imshow("Minh Tri - USB camera", preview)
                if cv2.waitKey(1) & 0xff == ord("q"):
                    break
    finally:
        if speaker:
            speaker.publish(frozenset(), 0)
        if cap:
            cap.close()
        if speaker:
            speaker.close()
        if a.show:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        LOG.exception("Chương trình dừng vì lỗi")
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if binary:
            try:
                subprocess.run([binary, "-v", "vi", "Hệ thống nhận diện đã dừng do lỗi."],
                               check=True, timeout=15, stdin=subprocess.DEVNULL)
            except Exception:
                LOG.error("Không thể đọc thông báo lỗi qua loa")
        raise SystemExit(1)
