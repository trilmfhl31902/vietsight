#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YOLO person gate + OhStem ultrasonic, CN212: ECHO=11, TRIG=8 (BCM).

Run: python3 detect_nguoi_sieu_am.py
The ultrasonic return cannot identify its reflector. A person in the camera's
central vertical region gates the reading; an intervening object can cause it.
"""
import argparse
import glob
import logging
import math
import os
from pathlib import Path
import sys
import threading
import time

MAX_DISTANCE_CM = 300.0
WARNING_DISTANCE_CM = 100.0
OUT_OF_RANGE = 'Ngo\xe0i ph\u1ea1m vi: tr\xean 300 cm'


class BeyondRange(ValueError):
    pass


def overlap(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = ((a[2] - a[0]) * (a[3] - a[1])
             + (b[2] - b[0]) * (b[3] - b[1]) - intersection)
    return intersection / union if union > 0 else 0


class PersonGate:
    def __init__(self):
        self.previous = None
        self.count = 0

    def update(self, people, width, height, fresh=True):
        # Use horizontal alignment only: seated/near people may be below the
        # crosshair. This remains a visual gate, not acoustic calibration.
        candidates = [box for box in people
                      if box[0] < width / 2 < box[2] and box[3] > box[1]]
        if not fresh or len(candidates) != 1:
            self.previous, self.count = None, 0
            return None
        box = candidates[0]
        self.count = self.count + 1 if self.previous is not None and overlap(box, self.previous) >= .3 else 1
        self.previous = box
        return box if self.count >= 2 else None


class Ultrasonic:
    def __init__(self, gpio):
        self.gpio, self.handle, self.claimed = gpio, None, []
        self.last_ping = 0.0
        self.last_pulse_us = None
        self.callback = None
        self.edge_lock = threading.Lock()
        self.pulse_done = threading.Event()
        self.armed = False
        self.rising_tick = self.pulse_ns = None
        self.arm_mono = self.arm_wall = 0
        labels = {'pinctrl-bcm2711', 'pinctrl-bcm2835', 'pinctrl-rp1'}
        for path in sorted(glob.glob('/dev/gpiochip*')):
            handle = None
            try:
                handle = gpio.gpiochip_open(int(path.split('gpiochip')[-1]))
                status, lines, name, label = gpio.gpio_get_chip_info(handle)
            except Exception:
                if handle is not None:
                    gpio.gpiochip_close(handle)
                continue
            if status >= 0 and label in labels and lines > 11:
                self.handle = handle
                break
            gpio.gpiochip_close(handle)
        if self.handle is None:
            raise RuntimeError('Khong tim thay GPIO Raspberry Pi')
        try:
            for pin in (11, 8):
                status, offset, flags, name, consumer = gpio.gpio_get_line_info(self.handle, pin)
                if status < 0 or flags & 1:
                    raise RuntimeError(f'GPIO{pin} dang ban: {consumer}; kiem tra SPI0')
            # Kernel edge timestamps avoid Python polling errors under YOLO load.
            gpio.gpio_claim_alert(self.handle, 11, gpio.BOTH_EDGES)
            self.claimed.append(11)
            self.callback = gpio.callback(self.handle, 11, gpio.BOTH_EDGES, self.edge)
            gpio.gpio_claim_output(self.handle, 8, 0)
            self.claimed.append(8)
            time.sleep(.2)
        except Exception:
            self.close()
            raise

    def edge(self, chip, pin, level, tick):
        if pin != 11 or level not in (0, 1):
            return
        with self.edge_lock:
            if not self.armed:
                return
            # lgpio uses ns. Handle both kernel monotonic and old epoch clocks.
            floor = (self.arm_mono if abs(tick - self.arm_mono) < abs(tick - self.arm_wall)
                     else self.arm_wall)
            if tick < floor:
                return  # Delayed notification from a previous trigger.
            if level == 1 and self.rising_tick is None:
                self.rising_tick = tick
            elif level == 0 and self.rising_tick is not None and tick > self.rising_tick:
                self.pulse_ns = tick - self.rising_tick
                self.armed = False
                self.pulse_done.set()

    def measure(self):
        self.last_pulse_us = None
        time.sleep(max(0, .06 - (time.monotonic() - self.last_ping)))
        if self.gpio.gpio_read(self.handle, 11) != 0:
            raise TimeoutError('ECHO bi giu muc cao; kiem tra day va nguon 3.3V')
        with self.edge_lock:
            self.pulse_done.clear()
            self.rising_tick = self.pulse_ns = None
            self.arm_mono, self.arm_wall = time.monotonic_ns(), time.time_ns()
            self.armed = True
        self.last_ping = time.monotonic()
        try:
            self.gpio.gpio_write(self.handle, 8, 1)
            try:
                deadline = time.monotonic() + .00001
                while time.monotonic() < deadline:
                    pass
            finally:
                self.gpio.gpio_write(self.handle, 8, 0)
            if not self.pulse_done.wait(.08):
                raise TimeoutError('Khong nhan duoc ECHO; kiem tra ECHO11 / TRIG8')
            with self.edge_lock:
                cm = self.pulse_ns * 1e-9 * 34300 / 2
                self.last_pulse_us = self.pulse_ns / 1000
        finally:
            with self.edge_lock:
                self.armed = False
        if cm < 3:
            raise ValueError('So do duoi pham vi 3 cm')
        if cm > MAX_DISTANCE_CM + .0001:
            raise BeyondRange(OUT_OF_RANGE)
        return cm

    def close(self):
        if self.handle is None:
            return
        with self.edge_lock:
            self.armed = False
        if self.callback is not None:
            self.callback.cancel()
            self.callback = None
        if 8 in self.claimed:
            try:
                self.gpio.gpio_write(self.handle, 8, 0)
            except Exception:
                pass
        for pin in self.claimed:
            try:
                self.gpio.gpio_free(self.handle, pin)
            except Exception:
                pass
        self.gpio.gpiochip_close(self.handle)
        self.handle = None


class Camera:
    def __init__(self, cv2, source, on_frame=None):
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.latest = (None, 0.0, 0)
        self.error = None
        self.on_frame = on_frame
        backend = cv2.CAP_V4L2 if os.name == 'posix' else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(source, backend)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(f'Khong mo duoc camera {source}')
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 15)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            while not self.stop.is_set():
                ok, frame = self.cap.read()
                if not ok:
                    raise RuntimeError('Mat ket noi camera')
                with self.lock:
                    self.latest = (frame, time.monotonic(), self.latest[2] + 1)
                    latest = self.latest
                if self.on_frame:
                    self.on_frame(*latest)
        except Exception as exc:
            self.error = exc
        finally:
            self.cap.release()

    def read(self):
        with self.lock:
            return self.latest

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)


class LiveState:
    """One latest frame/result, never an unbounded queue. No Tk calls here."""
    def __init__(self, max_age=1.5):
        self.lock = threading.Lock()
        self.max_age = max_age
        self.data = dict(phase='idle', message='S\u1eb5n s\xe0ng', frame=None,
                         frame_id=0, frame_time=0., people=[], target=None,
                         detected=0., revision=0, cm=None, measured=0.,
                         pulse_us=None, sensor_error='', ai_fps=0., latency_ms=0.)

    def phase(self, phase, message):
        with self.lock:
            self.data.update(phase=phase, message=message, cm=None,
                             target=None, measured=0., pulse_us=None, people=[], sensor_error='')
            self.data['revision'] += 1

    def frame(self, image, captured, frame_id):
        with self.lock:
            self.data.update(frame=image, frame_time=captured, frame_id=frame_id)

    def detection(self, people, target, captured, latency_ms, fps):
        with self.lock:
            old = self.data['target']
            if (target is None or old is None or overlap(old, target) < .3
                    or captured - self.data['detected'] > self.max_age):
                self.data.update(cm=None, measured=0., sensor_error='')
                self.data['revision'] += 1
            self.data.update(people=people, target=target, detected=captured,
                             latency_ms=latency_ms, ai_fps=fps)

    def reading(self, revision, cm=None, error='', now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            d = self.data
            if (revision != d['revision'] or d['phase'] != 'running'
                    or d['target'] is None or now - d['detected'] > self.max_age
                    or now - d['frame_time'] > self.max_age):
                return False
            d.update(cm=cm, measured=now, sensor_error=error)
            return True

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            d = self.data.copy()
        d['camera_fresh'] = d['frame'] is not None and now - d['frame_time'] <= self.max_age
        d['detection_fresh'] = now - d['detected'] <= self.max_age
        d['eligible'] = (d['phase'] == 'running' and d['camera_fresh']
                         and d['detection_fresh'] and d['target'] is not None)
        if (not d['eligible'] and d['phase'] != 'sensor_test') or now - d['measured'] > .5:
            d['cm'] = None
            d['sensor_error'] = ''
            d['pulse_us'] = None
        if not d['detection_fresh'] or not d['camera_fresh'] or d['phase'] != 'running':
            d['people'], d['target'] = [], None
        return d

    def test_reading(self, cm=None, pulse_us=None, error='', now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if self.data['phase'] != 'sensor_test':
                return
            self.data.update(cm=cm, pulse_us=pulse_us, sensor_error=error, measured=now)


class Engine:
    def __init__(self, args):
        self.args = args
        self.state = LiveState(args.max_age)
        self.stop = threading.Event()
        self.worker = None
        self.model = None

    def alive(self):
        return self.worker is not None and self.worker.is_alive()

    def start(self):
        if self.alive():
            return
        self.state = LiveState(self.args.max_age)
        self.stop = threading.Event()
        self.state.phase('loading', '\u0110ang kh\u1edfi \u0111\u1ed9ng\u2026')
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def request_stop(self):
        self.stop.set()
        if self.alive():
            self.state.phase('stopping', '\u0110ang gi\u1ea3i ph\xf3ng camera v\xe0 c\u1ea3m bi\u1ebfn\u2026')

    def sensor_loop(self, sensor):
        try:
            while not self.stop.is_set():
                d = self.state.snapshot()
                if d['eligible']:
                    try:
                        cm = sensor.measure()
                        self.state.reading(d['revision'], cm=cm)
                    except BeyondRange:
                        self.state.reading(d['revision'], error=OUT_OF_RANGE)
                    except (TimeoutError, ValueError) as exc:
                        self.state.reading(d['revision'], error=str(exc))
                self.stop.wait(.1)
        except Exception as exc:
            self.state.phase('error', f'L\u1ed7i c\u1ea3m bi\u1ebfn: {exc}')
            self.stop.set()

    def run(self):
        camera = sensor = sensor_thread = None
        try:
            if self.args.demo:
                self.demo()
                return
            if self.args.sensor_only:
                # Diagnose raw sensor with no camera, YOLO, or person gating.
                import lgpio
                sensor = Ultrasonic(lgpio)
                self.state.phase('sensor_test', '\u0110\u1eb7t m\u1eb7t ph\u1eb3ng \u0111\u1ed1i di\u1ec7n c\u1ea3m bi\u1ebfn \u0111\u1ec3 ki\u1ec3m tra')
                while not self.stop.is_set():
                    try:
                        cm = sensor.measure()
                        self.state.test_reading(cm, sensor.last_pulse_us)
                    except BeyondRange:
                        self.state.test_reading(pulse_us=sensor.last_pulse_us, error=OUT_OF_RANGE)
                    except (TimeoutError, ValueError) as exc:
                        self.state.test_reading(error=str(exc))
                    self.stop.wait(.2)
                return
            if not Path(self.args.model).is_file():
                raise RuntimeError(f'Thi\u1ebfu model: {self.args.model}')
            os.environ.setdefault('OMP_NUM_THREADS', str(self.args.threads))
            import cv2
            import lgpio
            import torch
            from ultralytics import YOLO
            logging.getLogger('ultralytics').setLevel(logging.ERROR)
            torch.set_num_threads(self.args.threads)
            cv2.setNumThreads(1)
            self.state.phase('loading', '\u0110ang n\u1ea1p m\xf4 h\xecnh nh\u1eadn di\u1ec7n ng\u01b0\u1eddi\u2026')
            if self.model is None:
                self.model = YOLO(self.args.model, task='detect')
            names = self.model.names
            entries = names.items() if isinstance(names, dict) else enumerate(names)
            person_ids = [key for key, name in entries if str(name).lower() == 'person']
            if not person_ids:
                raise RuntimeError('Model kh\xf4ng c\xf3 l\u1edbp person')
            if self.stop.is_set():
                return
            sensor = Ultrasonic(lgpio)
            source = int(self.args.camera) if self.args.camera.isdigit() else self.args.camera
            camera = Camera(cv2, source, self.state.frame)
            self.state.phase('running', '\u0110\u01b0a m\u1ed9t ng\u01b0\u1eddi v\xe0o \u0111\u01b0\u1eddng d\u1ecdc gi\u1eefa h\xecnh')
            sensor_thread = threading.Thread(target=self.sensor_loop, args=(sensor,), daemon=True)
            sensor_thread.start()
            gate = PersonGate()
            previous_id, last_frame = -1, time.monotonic()
            completed, fps = None, 0.
            while not self.stop.is_set():
                started = time.monotonic()
                if camera.error:
                    raise RuntimeError(str(camera.error))
                frame, captured, frame_id = camera.read()
                if frame is None or frame_id == previous_id:
                    if started - last_frame > 5:
                        raise RuntimeError('Camera kh\xf4ng c\xf3 h\xecnh m\u1edbi trong 5 gi\xe2y')
                    self.stop.wait(.01)
                    continue
                previous_id, last_frame = frame_id, started
                if started - captured > self.args.max_age:
                    gate.update([], 640, 480, fresh=False)
                    self.state.detection([], None, captured, 0, 0)
                    self.stop.wait(.01)
                    continue
                result = self.model.predict(frame, classes=person_ids, conf=self.args.conf,
                                            imgsz=self.args.imgsz, device='cpu', verbose=False,
                                            max_det=10)[0]
                people = [] if result.boxes is None else result.boxes.xyxy.tolist()
                height, width = frame.shape[:2]
                now = time.monotonic()
                target = gate.update(people, width, height,
                                     fresh=now - captured <= self.args.max_age)
                if completed is not None:
                    instant = 1 / max(.001, now - completed)
                    fps = instant if not fps else .75 * fps + .25 * instant
                completed = now
                if self.stop.is_set():
                    break
                self.state.detection(people, target, captured, (now - started) * 1000, fps)
                # Reserve CPU for the live preview; skip frames while YOLO is busy.
                self.stop.wait(max(0, 1 / self.args.ai_fps - (time.monotonic() - started)))
        except Exception as exc:
            self.state.phase('error', str(exc))
        finally:
            self.stop.set()
            # GPIO belongs to the sensor thread until it has finished measuring.
            if sensor_thread is not None:
                sensor_thread.join()
            try:
                if sensor is not None:
                    sensor.close()
            finally:
                if camera is not None:
                    camera.close()
                if self.state.snapshot()['phase'] != 'error':
                    self.state.phase('stopped', '\u0110\xe3 d\u1eebng \xb7 Nh\u1ea5n B\u1eaft \u0111\u1ea7u \u0111\u1ec3 ch\u1ea1y l\u1ea1i')

    def demo(self):
        """Synthetic local preview: no model, camera, or GPIO access."""
        from PIL import Image, ImageDraw
        if self.args.sensor_only:
            self.state.phase('sensor_test', 'M\xd4 PH\u1eceNG \xb7 Kh\xf4ng c\xf3 c\u1ea3m bi\u1ebfn th\u1eadt')
            while not self.stop.is_set():
                self.state.test_reading(200., 2 * 200 / 34300 * 1e6)
                self.stop.wait(.2)
            return
        self.state.phase('running', 'B\u1ea2N M\xd4 PH\u1eceNG \xb7 Kh\xf4ng s\u1eed d\u1ee5ng c\u1ea3m bi\u1ebfn th\u1eadt')
        started, frame_id = time.monotonic(), 0
        gate = PersonGate()
        while not self.stop.is_set():
            now = time.monotonic()
            t = now - started
            frame_id += 1
            image = Image.new('RGB', (640, 480), '#12222d')
            draw = ImageDraw.Draw(image)
            draw.polygon([(210, 240), (430, 240), (640, 480), (0, 480)], fill='#1d3441')
            for y in range(280, 481, 50):
                draw.line((0, y, 640, y), fill='#2a4554')
            for x in (40, 130, 510, 600):
                draw.line((x, 0, x, 480), fill='#24404d', width=2)
            # Cycle near / far / absent / no echo to exercise UI states.
            mode = int(t // 6) % 4
            box = (235, 75, 405, 455)
            if mode == 0:
                # Reproduce the reported seated person below the crosshair.
                box = (205, 260, 435, 480)
                draw.ellipse((284, 270, 356, 342), fill='#6996a6')
                draw.rounded_rectangle((235, 350, 405, 490), 28, fill='#416c80')
            elif mode != 2:
                draw.ellipse((282, 88, 358, 164), fill='#6996a6')
                draw.rounded_rectangle((257, 174, 383, 325), 28, fill='#416c80')
                draw.line((285, 310, 279, 437), fill='#6996a6', width=24)
                draw.line((354, 310, 361, 437), fill='#6996a6', width=24)
                draw.line((263, 194, 240, 295), fill='#6996a6', width=19)
                draw.line((376, 194, 400, 295), fill='#6996a6', width=19)
            self.state.frame(image, now, frame_id)
            people = [box] if mode != 2 else []
            target = gate.update(people, 640, 480)
            self.state.detection(people, target, now, 142, 4.0)
            d = self.state.snapshot()
            if mode in (0, 1):
                self.state.reading(d['revision'], cm=(76 if mode == 0 else 154) + 2 * math.sin(t), now=now)
            elif mode == 3:
                self.state.reading(d['revision'], error='Kh\xf4ng nh\u1eadn \u0111\u01b0\u1ee3c ECHO', now=now)
            self.stop.wait(1 / 15)


def describe(d):
    if d['phase'] == 'error':
        return 'L\u1ed6I K\u1ebeT N\u1ed0I', d['message'], '#fb7185'
    if d['phase'] == 'sensor_test':
        if d['cm'] is None:
            pulse = f"ECHO: {d['pulse_us']:.0f} \xb5s. " if d['pulse_us'] is not None else ''
            return 'KI\u1ec2M TRA SI\xcaU \xc2M', pulse + (d['sensor_error'] or '\u0110ang ch\u1edd ECHO\u2026'), '#fbbf70'
        return ('KI\u1ec2M TRA SI\xcaU \xc2M', f"ECHO: {d['pulse_us']:.0f} \xb5s. "
                '\u0110\xe2y l\xe0 s\u1ed1 \u0111o v\u1eadt ph\u1ea3n x\u1ea1, ch\u01b0a g\xe1n cho ng\u01b0\u1eddi.', '#52ddc2')
    if d['phase'] != 'running':
        return {'loading': '\u0110ANG KH\u1edeI \u0110\u1ed8NG', 'stopping': '\u0110ANG D\u1eeaNG',
                'stopped': '\u0110\xc3 D\u1eeaNG'}.get(d['phase'], 'S\u1eb4N S\xc0NG'), d['message'], '#8ca6b6'
    if not d['camera_fresh']:
        return '\u0110ANG CH\u1edc CAMERA', 'Ch\u01b0a c\xf3 h\xecnh \u1ea3nh m\u1edbi t\u1eeb camera.', '#fbbf70'
    if not d['detection_fresh']:
        return '\u0110ANG NH\u1eacN DI\u1ec6N', '\u0110ang x\u1eed l\xfd h\xecnh \u1ea3nh m\u1edbi. S\u1ed1 \u0111o c\u0169 \u0111\xe3 \u0111\u01b0\u1ee3c b\u1ecf.', '#8ca6b6'
    if not d['eligible']:
        return 'CH\u1edc NG\u01af\u1edcI \u1ede GI\u1eeeA', '\u0110\u01b0a m\u1ed9t ng\u01b0\u1eddi v\xe0o \u0111\u01b0\u1eddng d\u1ecdc gi\u1eefa \u1ea3nh \u0111\u1ec3 \u0111o.', '#8ca6b6'
    if d['sensor_error'] == OUT_OF_RANGE:
        return 'TR\xcaN 3 M\xc9T', 'Ngo\xe0i ph\u1ea1m vi \u0111o 300 cm. Kh\xf4ng c\u1ea3nh b\xe1o d\u01b0\u1edbi 1 m\xe9t.', '#8ca6b6'
    if d['cm'] is None:
        return 'CH\u01afA C\xd3 S\u1ed0 \u0110O', d['sensor_error'] or '\u0110\xe3 th\u1ea5y ng\u01b0\u1eddi. \u0110ang ch\u1edd ph\u1ea3n h\u1ed3i si\xeau \xe2m.', '#fbbf70'
    if d['cm'] < WARNING_DISTANCE_CM:
        return 'CH\xda \xdd \xb7 D\u01af\u1edaI 1 M\xc9T', 'C\xf3 ng\u01b0\u1eddi \u1edf gi\u1eefa \u1ea3nh v\xe0 s\u1ed1 \u0111o si\xeau \xe2m d\u01b0\u1edbi 100 cm.', '#ff9b6a'
    return 'T\u1eea 1 M\xc9T TR\u1ede L\xcaN', 'C\xf3 ng\u01b0\u1eddi \u1edf gi\u1eefa \u1ea3nh. \u0110ang c\u1eadp nh\u1eadt s\u1ed1 \u0111o si\xeau \xe2m.', '#52ddc2'


class Application:
    BG = '#0a121c'
    PANEL = '#121f2c'
    TEXT = '#edf5f8'
    MUTED = '#8ca6b6'
    ACCENT = '#52ddc2'

    def __init__(self, root, args):
        import tkinter as tk
        from tkinter import ttk
        from PIL import Image, ImageDraw, ImageTk
        self.tk, self.Image, self.ImageDraw, self.ImageTk = tk, Image, ImageDraw, ImageTk
        self.root, self.args, self.engine = root, args, Engine(args)
        self.closing = False
        self.photo = None
        self.last_render = None
        self.widget_values = {}
        self.last_ruler = None
        self.pending_mode = None
        root.title('Minh Tri | Nguoi & Khoang cach')
        root.geometry('1120x800')
        root.minsize(900, 720)
        root.configure(bg=self.BG)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Escape>', lambda event: self.close())
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('TCombobox', fieldbackground=self.PANEL, background=self.PANEL,
                        foreground=self.TEXT, arrowcolor=self.ACCENT, padding=6)
        style.map('TCombobox', fieldbackground=[('readonly', self.PANEL)],
                  foreground=[('readonly', self.TEXT)])
        shell = tk.Frame(root, bg=self.BG)
        shell.pack(fill='both', expand=True, padx=26, pady=22)
        header = tk.Frame(shell, bg=self.BG)
        header.pack(fill='x', pady=(0, 20))
        brand = tk.Frame(header, bg=self.BG)
        brand.pack(side='left')
        self.label(brand, 'M I N H  T R \xcd', size=11, color=self.ACCENT, bold=True).pack(anchor='w')
        self.label(brand, 'Nh\xecn r\xf5. \u0110o g\u1ea7n.', size=27, bold=True).pack(anchor='w', pady=(4, 0))
        self.label(brand, 'Nh\u1eadn di\u1ec7n ng\u01b0\u1eddi k\u1ebft h\u1ee3p c\u1ea3m bi\u1ebfn kho\u1ea3ng c\xe1ch', color=self.MUTED).pack(anchor='w', pady=(4, 0))
        self.badge = self.label(header, '\u25cf  \u0110ANG KH\u1edeI \u0110\u1ed8NG', size=10, color=self.ACCENT)
        self.badge.pack(side='right', anchor='n', pady=10)
        body = tk.Frame(shell, bg=self.BG)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=285)
        body.rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=self.PANEL, highlightbackground='#233343', highlightthickness=1)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 18))
        bar = tk.Frame(left, bg=self.PANEL)
        bar.pack(fill='x', padx=17, pady=13)
        self.label(bar, 'CAMERA TR\u1ef0C TI\u1ebeP', size=10, bold=True).pack(side='left')
        self.cam_tag = self.label(bar, '\u0110ang ch\u1edd', size=10, color=self.MUTED)
        self.cam_tag.pack(side='right')
        self.video = tk.Label(left, bg='#080f17', fg=self.MUTED, text='\u0110ang kh\u1edfi \u0111\u1ed9ng camera\u2026',
                              font=('DejaVu Sans', 12), width=1, height=1)
        self.video.pack(fill='both', expand=True, padx=1)
        self.label(left, '\u0110\u01b0a ng\u01b0\u1eddi v\xe0o \u0111\u01b0\u1eddng d\u1ecdc gi\u1eefa \u1ea3nh \xb7 Si\xeau \xe2m h\u01b0\u1edbng v\u1ec1 ph\xeda ng\u01b0\u1eddi',
                   size=9, color=self.MUTED, wrap=510).pack(anchor='w', padx=14, pady=13)
        right = tk.Frame(body, bg=self.BG, width=285)
        right.grid(row=0, column=1, sticky='nsew')
        right.pack_propagate(False)
        distance = tk.Frame(right, bg=self.PANEL, padx=20, pady=16)
        distance.pack(fill='x')
        self.label(distance, 'S\u1ed0 \u0110O SI\xcaU \xc2M', size=10, color=self.MUTED, bold=True).pack(anchor='w')
        digits = tk.Frame(distance, bg=self.PANEL)
        digits.pack(fill='x', pady=(10, 0))
        self.distance = self.label(digits, '\u2014', size=44, color=self.ACCENT, bold=True)
        self.distance.pack(side='left')
        self.label(digits, 'cm', size=17, color=self.MUTED).pack(side='left', anchor='s', padx=8, pady=14)
        self.label(distance, 'Ph\u1ea1m vi 3\u2013300 cm \xb7 C\u1ea3nh b\xe1o d\u01b0\u1edbi 100 cm', size=9,
                   color=self.MUTED, wrap=235).pack(anchor='w')
        self.ruler = tk.Canvas(distance, height=43, bg=self.PANEL, highlightthickness=0)
        self.ruler.pack(fill='x', pady=(10, 0))
        status = tk.Frame(right, bg=self.PANEL, padx=20, pady=18)
        status.pack(fill='x', pady=(14, 0))
        self.state_title = self.label(status, '\u0110ANG KH\u1edeI \u0110\u1ed8NG', size=11, color=self.ACCENT, bold=True, wrap=240)
        self.state_title.pack(anchor='w')
        self.state_detail = self.label(status, '\u0110ang n\u1ea1p m\xf4 h\xecnh\u2026', size=10, color=self.MUTED, wrap=240)
        self.state_detail.pack(anchor='w', pady=(9, 0))
        settings = tk.Frame(right, bg=self.BG)
        settings.pack(fill='x', pady=(16, 0))
        self.label(settings, 'CH\u1ebe \u0110\u1ed8 X\u1eec L\xdd', size=9, color=self.MUTED, bold=True).pack(anchor='w')
        self.mode = tk.StringVar(value={256: 'Nhanh \xb7 256', 320: 'C\xe2n b\u1eb1ng \xb7 320', 416: 'Chi ti\u1ebft \xb7 416'}.get(args.imgsz, 'C\xe2n b\u1eb1ng \xb7 320'))
        self.modes = {'Nhanh \xb7 256': 256, 'C\xe2n b\u1eb1ng \xb7 320': 320, 'Chi ti\u1ebft \xb7 416': 416}
        self.quality = ttk.Combobox(settings, textvariable=self.mode,
                                    values=list(self.modes), state='readonly', width=23)
        self.quality.pack(fill='x', pady=(7, 0))
        self.test_button = tk.Button(settings, text='Ki\u1ec3m tra si\xeau \xe2m', command=self.toggle_test,
                                     bg='#203748', fg=self.TEXT, activebackground='#2c4a60',
                                     activeforeground=self.TEXT, relief='flat',
                                     font=('DejaVu Sans', 10), pady=9, cursor='hand2')
        self.test_button.pack(fill='x', pady=(10, 0))
        self.toggle = tk.Button(right, text='D\u1eebng nh\u1eadn di\u1ec7n', command=self.toggle_run,
                                bg=self.ACCENT, fg='#07251f', activebackground='#88ecd7',
                                relief='flat', font=('DejaVu Sans', 11, 'bold'), pady=12, cursor='hand2')
        self.toggle.pack(fill='x', pady=(14, 0), side='bottom')
        metrics = tk.Frame(shell, bg=self.BG)
        metrics.pack(fill='x', pady=(18, 0))
        self.metric_values = []
        for index, title in enumerate(('NG\u01af\u1edcI TRONG H\xccNH', 'T\u1ed0C \u0110\u1ed8 AI', 'TH\u1edcI GIAN AI')):
            metrics.columnconfigure(index, weight=1, uniform='metric')
            card = tk.Frame(metrics, bg=self.PANEL, padx=16, pady=12)
            card.grid(row=0, column=index, sticky='ew', padx=(0 if index == 0 else 6, 0 if index == 2 else 6))
            self.label(card, title, size=8, color=self.MUTED, bold=True).pack(anchor='w')
            value = self.label(card, '\u2014', size=18, bold=True)
            value.pack(anchor='w', pady=(5, 0))
            self.metric_values.append(value)
        self.footer = self.label(shell,
            'L\u01b0u \xfd: si\xeau \xe2m c\xf3 th\u1ec3 \u0111o v\u1eadt ch\u1eafn tr\u01b0\u1edbc ng\u01b0\u1eddi.  \u2022  \xc2m thanh t\u1eaft  \u2022  Esc \u0111\u1ec3 tho\xe1t',
            size=9, color=self.MUTED)
        self.footer.pack(anchor='w', pady=(14, 0))
        root.after(120, self.engine.start)
        root.after(80, self.refresh)

    def label(self, parent, text, size=11, color=None, bold=False, wrap=0):
        return self.tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color or self.TEXT,
                             font=('DejaVu Sans', size, 'bold' if bold else 'normal'),
                             anchor='w', justify='left', wraplength=wrap, bd=0)

    def set_widget(self, widget, **values):
        # Avoid resending unchanged text/style over X11 on every preview frame.
        previous = self.widget_values.setdefault(widget, {})
        changed = {key: value for key, value in values.items() if previous.get(key) != value}
        if changed:
            widget.configure(**changed)
            previous.update(changed)

    def toggle_run(self):
        if self.engine.alive():
            self.engine.request_stop()
        else:
            self.args.imgsz = self.modes[self.mode.get()]
            self.last_render = None
            self.engine.start()

    def toggle_test(self):
        if self.pending_mode is not None:
            return
        self.pending_mode = not self.args.sensor_only
        self.engine.request_stop()
        self.root.after(80, self.finish_mode_change)

    def finish_mode_change(self):
        if self.closing:
            return
        if self.engine.alive():
            self.root.after(80, self.finish_mode_change)
            return
        self.args.sensor_only = self.pending_mode
        self.pending_mode = None
        self.last_render = None
        self.engine.start()

    def close(self):
        self.closing = True
        self.engine.request_stop()
        self.toggle.configure(state='disabled', text='\u0110ang \u0111\xf3ng\u2026')

    def draw_frame(self, d, color):
        frame = d['frame']
        if d['phase'] == 'sensor_test':
            self.set_widget(self.video, image='', text='KI\u1ec2M TRA RI\xcaNG C\u1ea2M BI\u1ebeN\n\n'
                            'Camera v\xe0 AI \u0111ang t\u1ea1m d\u1eebng\n\n'
                            '\u0110\u1eb7t t\u1ea5m b\xeca ph\u1eb3ng tr\u01b0\u1edbc hai m\u1eaft si\xeau \xe2m\n'
                            'L\u1ea7n l\u01b0\u1ee3t ki\u1ec3m tra \u1edf 50 cm, 100 cm, 200 cm\n\n'
                            '\u0110o t\u1eeb m\u1eb7t tr\u01b0\u1edbc c\u1ea3m bi\u1ebfn, kh\xf4ng \u0111o t\u1eeb camera')
            self.photo, self.last_render = None, None
            return
        if not d['camera_fresh'] or d['phase'] not in ('running', 'loading'):
            self.set_widget(self.video, image='', text='\u0110\xe3 d\u1eebng camera' if d['phase'] == 'stopped'
                                 else 'Kh\xf4ng c\xf3 h\xecnh \u1ea3nh hi\u1ec7n t\u1ea1i')
            self.photo, self.last_render = None, None
            return
        size = (max(100, self.video.winfo_width()), max(100, self.video.winfo_height()))
        key = (d['frame_id'], d['revision'], d['detection_fresh'], size, color)
        if key == self.last_render:
            return
        self.last_render = key
        # Pillow draws on a copy; the inference frame is never modified by UI.
        image = frame.copy() if self.args.demo else self.Image.fromarray(frame[:, :, ::-1])
        image.thumbnail(size, self.Image.Resampling.BILINEAR)
        draw = self.ImageDraw.Draw(image)
        iw, ih = image.size
        original_w, original_h = (frame.size if self.args.demo else (frame.shape[1], frame.shape[0]))
        sx, sy = iw / original_w, ih / original_h
        # Keep a valid box between AI updates. A fixed .5 s expiry hid almost
        # every box when inference itself took 427 ms on Pi 4.
        if d['detection_fresh']:
            for box in d['people']:
                coords = (int(box[0] * sx), int(box[1] * sy), int(box[2] * sx), int(box[3] * sy))
                chosen = d['target'] is not None and overlap(box, d['target']) > .99
                draw.rectangle(coords, outline=color if chosen else '#82a9bc', width=3 if chosen else 1)
                draw.text((coords[0] + 5, max(0, coords[1] - 14)), 'PERSON', fill=color if chosen else '#b3c9d4')
        cx, cy = iw // 2, ih // 2
        for y in range(0, ih, 22):
            draw.line((cx, y, cx, min(ih, y + 10)), fill='#385d70', width=1)
        draw.ellipse((cx - 27, cy - 27, cx + 27, cy + 27), outline='#385d70', width=1)
        draw.line((cx - 12, cy, cx + 12, cy), fill=color, width=2)
        draw.line((cx, cy - 12, cx, cy + 12), fill=color, width=2)
        if self.photo is not None and self.photo.width() == iw and self.photo.height() == ih:
            self.photo.paste(image)
        else:
            self.photo = self.ImageTk.PhotoImage(image)
            self.set_widget(self.video, image=self.photo, text='')

    def refresh(self):
        if self.closing:
            if not self.engine.alive():
                self.root.destroy()
                return
            self.root.after(80, self.refresh)
            return
        d = self.engine.state.snapshot()
        title, detail, color = describe(d)
        alive = self.engine.alive()
        self.set_widget(self.toggle, state='disabled' if d['phase'] == 'stopping' or self.pending_mode is not None else 'normal',
                              text='\u0110ang d\u1eebng\u2026' if d['phase'] == 'stopping' else
                              ('D\u1eebng nh\u1eadn di\u1ec7n' if alive else 'B\u1eaft \u0111\u1ea7u nh\u1eadn di\u1ec7n'))
        self.set_widget(self.quality, state='disabled' if alive else 'readonly')
        self.set_widget(self.test_button,
                        state='disabled' if self.pending_mode is not None or d['phase'] == 'stopping' else 'normal',
                        text='Tr\u1edf l\u1ea1i nh\u1eadn di\u1ec7n' if self.args.sensor_only else 'Ki\u1ec3m tra si\xeau \xe2m')
        self.set_widget(self.badge, text='\u25cf  M\xd4 PH\u1eceNG' if self.args.demo else
                             ('\u25cf  \u0110ANG CH\u1ea0Y' if d['phase'] == 'running' else '\u25cf  ' + title), fg=color)
        self.set_widget(self.cam_tag, text='T\u1ea1m d\u1eebng AI' if d['phase'] == 'sensor_test' else 'M\xd4 PH\u1eceNG' if self.args.demo else
                               ('\u25cf Tr\u1ef1c ti\u1ebfp' if d['camera_fresh'] and alive else 'Ch\u01b0a c\xf3 t\xedn hi\u1ec7u'))
        self.set_widget(self.distance, text=f"{d['cm']:.1f}" if d['cm'] is not None else
                        ('>300' if d['sensor_error'] == OUT_OF_RANGE else '\u2014'), fg=color)
        self.set_widget(self.state_title, text=title, fg=color)
        self.set_widget(self.state_detail, text=detail)
        self.set_widget(self.metric_values[0], text=str(len(d['people'])) if d['detection_fresh'] and d['camera_fresh'] and d['phase'] == 'running' else '\u2014')
        self.set_widget(self.metric_values[1], text=f"{d['ai_fps']:.1f} FPS" if d['detection_fresh'] and alive else '\u2014')
        self.set_widget(self.metric_values[2], text=f"{d['latency_ms']:.0f} ms" if d['detection_fresh'] and alive else '\u2014')
        self.draw_ruler(d['cm'], color)
        self.draw_frame(d, color)
        self.root.after(max(33, round(1000 / self.args.preview_fps)), self.refresh)

    def draw_ruler(self, cm, color):
        rw = max(1, self.ruler.winfo_width() - 2)
        key = (None if cm is None else round(cm, 1), color, rw)
        if key == self.last_ruler:
            return
        self.last_ruler = key
        self.ruler.delete('all')
        self.ruler.create_rectangle(0, 10, rw, 16, fill='#243746', outline='')
        warning_x = rw * WARNING_DISTANCE_CM / MAX_DISTANCE_CM
        self.ruler.create_line(warning_x, 3, warning_x, 23, fill='#ff9b6a', width=2)
        for x, label, anchor in ((0, '0', 'w'), (warning_x, '100', 'center'), (rw, '300 cm', 'e')):
            self.ruler.create_text(x, 34, text=label, fill=self.MUTED,
                                   font=('DejaVu Sans', 8), anchor=anchor)
        if cm is not None:
            x = min(rw - 4, max(4, cm / MAX_DISTANCE_CM * rw))
            self.ruler.create_rectangle(0, 10, x, 16, fill=color, outline='')
            self.ruler.create_oval(x - 4, 9, x + 4, 17, fill=self.TEXT, outline='')


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera', default='/dev/video1')
    parser.add_argument('--model', default=str(Path(__file__).with_name('yolov8n.pt')))
    parser.add_argument('--conf', type=float, default=.45)
    parser.add_argument('--imgsz', type=int, default=256)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--ai-fps', type=float, default=4)
    parser.add_argument('--preview-fps', type=float, default=10)
    parser.add_argument('--max-age', type=float, default=1.5)
    parser.add_argument('--no-show', action='store_true', help='Ch\u1ea1y terminal, kh\xf4ng m\u1edf giao di\u1ec7n')
    parser.add_argument('--sensor-only', action='store_true', help='Ki\u1ec3m tra ri\xeang si\xeau \xe2m, t\u1ea1m d\u1eebng camera v\xe0 AI')
    parser.add_argument('--demo', action='store_true', help='M\xf4 ph\u1ecfng giao di\u1ec7n, kh\xf4ng truy c\u1eadp camera/GPIO/model')
    args = parser.parse_args()
    if (not 0 < args.conf <= 1 or args.threads < 1 or args.imgsz not in (256, 320, 416)
            or not all(math.isfinite(v) and v > 0 for v in (args.max_age, args.ai_fps, args.preview_fps))):
        parser.error('Th\xf4ng s\u1ed1 kh\xf4ng h\u1ee3p l\u1ec7; imgsz ch\u1ecdn 256, 320 ho\u1eb7c 416')
    return args


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    args = arguments()
    if args.no_show:
        engine = Engine(args)
        engine.start()
        try:
            while engine.alive():
                d = engine.state.snapshot()
                title, detail, color = describe(d)
                cm = f" | {d['cm']:.1f} cm" if d['cm'] is not None else ''
                print(title + cm + ' | ' + detail, flush=True)
                engine.stop.wait(.5)
        finally:
            engine.request_stop()
            engine.worker.join()
        d = engine.state.snapshot()
        if d['phase'] == 'error':
            raise RuntimeError(d['message'])
        return
    if os.name == 'posix' and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise RuntimeError('Ch\u01b0a c\xf3 m\xe0n h\xecnh. B\u1eadt X11 forwarding trong MobaXterm '
                           'ho\u1eb7c ch\u1ea1y th\xeam --no-show \u0111\u1ec3 d\xf9ng terminal.')
    import tkinter as tk
    root = tk.Tk()
    app = Application(root, args)
    try:
        root.mainloop()
    finally:
        app.engine.request_stop()
        if app.engine.worker is not None:
            app.engine.worker.join()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n\u0110\xe3 d\u1eebng.')
    except Exception as exc:
        raise SystemExit(f'L\u1ed7i: {exc}')


