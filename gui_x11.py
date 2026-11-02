#!/usr/bin/env python3
"""Native, silent Tk GUI forwarded from Raspberry Pi through MobaXterm X11."""
import argparse
import io
import logging
import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk

from gui import Dashboard, run_detection

BASE = Path(__file__).resolve().parent


class Application:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.worker = None
        self.stop = threading.Event()
        self.dashboard = Dashboard(args.max_age)
        self.closing = self.stopping = False
        self.image = self.last_jpeg = None
        root.title("Minh Trí — Nhận diện camera USB (âm thanh tắt)")
        root.geometry("1040x760")
        root.minsize(850, 650)
        root.configure(bg="#101b22")
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#101b22")
        style.configure("TLabel", background="#101b22", foreground="#ecf4f7", font=("DejaVu Sans", 11))
        style.configure("Title.TLabel", font=("DejaVu Sans", 20, "bold"))
        style.configure("Muted.TLabel", foreground="#a4b7c2", font=("DejaVu Sans", 10))
        style.configure("Card.TFrame", background="#1d303b")
        style.configure("Card.TLabel", background="#1d303b", foreground="#ecf4f7")
        style.configure("TButton", font=("DejaVu Sans", 11), padding=(12, 8))
        shell = ttk.Frame(root, padding=20)
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 16))
        ttk.Label(header, text="Nhìn phía trước", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="ÂM THANH TẮT · X11", style="Muted.TLabel").pack(side="right")
        controls = ttk.Frame(shell)
        controls.pack(fill="x", pady=(0, 12))
        ttk.Label(controls, text="Camera:").pack(side="left", padx=(0, 8))
        self.source = tk.StringVar(value=args.camera)
        self.entry = ttk.Entry(controls, textvariable=self.source, width=25)
        self.entry.pack(side="left", padx=(0, 12))
        self.start_button = ttk.Button(controls, text="Bắt đầu", command=self.start)
        self.start_button.pack(side="left", padx=4)
        self.stop_button = ttk.Button(controls, text="Dừng", command=self.request_stop, state="disabled")
        self.stop_button.pack(side="left", padx=4)
        ttk.Button(controls, text="Thoát", command=self.close).pack(side="right")
        model_controls = ttk.Frame(shell)
        model_controls.pack(fill="x", pady=(0, 12))
        ttk.Label(model_controls, text="Model:").pack(side="left", padx=(0, 8))
        self.models = {Path(args.model).name: args.model}
        self.model_name = tk.StringVar(value=Path(args.model).name)
        self.model_select = ttk.Combobox(model_controls, textvariable=self.model_name,
                                        values=list(self.models), width=24, state="readonly")
        self.model_select.pack(side="left", padx=(0, 12))
        ttk.Label(model_controls, text="Ngưỡng tin cậy:").pack(side="left", padx=(0, 8))
        self.confidence = tk.StringVar(value=str(args.conf))
        self.conf_entry = ttk.Spinbox(model_controls, from_=0.05, to=0.95, increment=0.05,
                                      textvariable=self.confidence, width=6)
        self.conf_entry.pack(side="left")
        ttk.Label(model_controls, text="Dừng trước khi đổi model", style="Muted.TLabel").pack(side="right")
        content = ttk.Frame(shell)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, minsize=240)
        content.rowconfigure(0, weight=1)
        self.video = tk.Label(content, text="Nhấn Bắt đầu để mở camera", bg="#080e12", fg="#9fb8c6",
                              font=("DejaVu Sans", 13))
        self.video.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        sidebar = ttk.Frame(content)
        sidebar.grid(row=0, column=1, sticky="nsew")
        self.regions = {}
        for key, title in (("left", "← Bên trái"), ("center", "↑ Chính giữa"), ("right", "→ Bên phải")):
            card = ttk.Frame(sidebar, style="Card.TFrame", padding=16)
            card.pack(fill="x", pady=(0, 12))
            ttk.Label(card, text=title, style="Card.TLabel", font=("DejaVu Sans", 12, "bold")).pack(anchor="w")
            value = ttk.Label(card, text="Đang chờ", style="Card.TLabel", wraplength=210, justify="left")
            value.pack(anchor="w", pady=(12, 0))
            self.regions[key] = value
        self.metrics = ttk.Label(sidebar, text="", style="Muted.TLabel", wraplength=230, justify="left")
        self.metrics.pack(anchor="w", pady=8)
        self.status = ttk.Label(shell, text="Sẵn sàng · Không phát âm thanh", wraplength=950)
        self.status.pack(fill="x", pady=(14, 8))
        ttk.Label(shell, text="Camera hướng về phía trước. Không thấy vật không có nghĩa đường an toàn.",
                  style="Muted.TLabel", wraplength=950).pack(anchor="w")
        root.after(100, self.refresh)

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        source = self.source.get().strip()
        if not source:
            self.status.configure(text="Hãy nhập thiết bị camera, ví dụ /dev/video1")
            return
        try:
            confidence = float(self.confidence.get())
            if not 0 < confidence <= 1:
                raise ValueError()
        except ValueError:
            self.status.configure(text="Ngưỡng tin cậy phải là số lớn hơn 0 và không quá 1")
            return
        model_path = self.models[self.model_name.get()]
        if not self.args.demo and not Path(model_path).exists():
            self.status.configure(text=f"Thiếu model {model_path}. Chép file model vào ~/minhtri.")
            return
        self.args.model, self.args.conf = model_path, confidence
        self.args.camera = source
        self.stop = threading.Event()
        self.dashboard = Dashboard(self.args.max_age)
        self.last_jpeg = None
        self.stopping = False
        self.start_button.configure(state="disabled")
        self.entry.configure(state="disabled")
        self.model_select.configure(state="disabled")
        self.conf_entry.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.video.configure(image="", text="Đang nạp model và mở camera…")
        self.worker = threading.Thread(target=run_detection,
                                       args=(self.args, self.dashboard, self.stop), daemon=True)
        self.worker.start()

    def request_stop(self):
        if self.worker and self.worker.is_alive():
            self.stopping = True
            self.stop.set()
            self.stop_button.configure(state="disabled")
            self.status.configure(text="Đang dừng camera…")

    def close(self):
        self.closing = True
        self.start_button.configure(state="disabled")
        self.request_stop()

    def refresh(self):
        alive = self.worker is not None and self.worker.is_alive()
        if self.closing:
            if not alive:
                self.root.destroy()
                return
            self.root.after(100, self.refresh)
            return
        if self.worker:
            jpeg, data = self.dashboard.snapshot()
            if not self.stopping:
                message = data["message"]
                if data.get("demo"):
                    message = "DEMO — không dùng camera thật. " + message
                self.status.configure(text=message)
                for region, label in self.regions.items():
                    names = data["regions"].get(region, [])
                    label.configure(text=", ".join(names) or ("Chưa có nhãn xác nhận"
                                    if data["status"] == "running" else "Chưa có dữ liệu hiện tại"))
                self.metrics.configure(text=f"Model: {Path(self.args.model).name}\n"
                                       f"Ngưỡng: {self.args.conf:.2f}\n"
                                       f"Suy luận: {data['fps']} FPS\n"
                                       f"Thời gian: {data['latency_ms']} ms\n"
                                       f"Tuổi hình: {data['age_seconds']} s")
                if jpeg and jpeg is not self.last_jpeg:
                    from PIL import Image, ImageTk
                    image = Image.open(io.BytesIO(jpeg))
                    image.thumbnail((max(320, self.video.winfo_width()), max(240, self.video.winfo_height())))
                    self.image = ImageTk.PhotoImage(image)
                    self.video.configure(image=self.image, text="")
                    self.last_jpeg = jpeg
            if not alive:
                self.entry.configure(state="normal")
                self.model_select.configure(state="readonly")
                self.conf_entry.configure(state="normal")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                if self.stopping:
                    self.video.configure(image="", text="Đã dừng camera")
                    self.image = self.last_jpeg = None
                    self.status.configure(text="Đã dừng · Nhấn Bắt đầu để chạy lại")
                    for label in self.regions.values():
                        label.configure(text="Đã dừng")
                    self.metrics.configure(text="")
                    self.worker = None
                    self.stopping = False
        self.root.after(150, self.refresh)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", default="/dev/video1")
    parser.add_argument("--model", default=str(BASE / "best.pt"))
    parser.add_argument("--imgsz", type=int, default=320)
    parser.add_argument("--conf", type=float, default=.45)
    parser.add_argument("--threads", type=int, default=3)
    parser.add_argument("--max-age", type=float, default=3)
    parser.add_argument("--demo", action="store_true", help="Thử giao diện không dùng camera/model")
    args = parser.parse_args()
    if min(args.imgsz, args.threads, args.max_age) <= 0 or not 0 < args.conf <= 1:
        parser.error("Thông số không hợp lệ")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.name == "posix" and not os.environ.get("DISPLAY"):
        parser.exit(1, "Chưa có DISPLAY. Bật X11-forwarding trong phiên SSH MobaXterm rồi kết nối lại.\n")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        parser.exit(1, f"Không mở được giao diện X11: {exc}\nKiểm tra X server và X11-forwarding của MobaXterm.\n")
    Application(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
