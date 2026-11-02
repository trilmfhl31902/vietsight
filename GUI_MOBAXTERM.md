# Cửa sổ GUI chạy trên Pi 4, hiển thị qua MobaXterm X11

Không cần mở trình duyệt, không cần tunnel cổng 8080. Chế độ này không phát âm thanh.

1. Chép `minhtri_x11_update.zip` vào `/home/pi/minhtri` bằng SFTP bên trái MobaXterm.
2. Dừng chương trình camera cũ bằng Ctrl+C.
3. Trong MobaXterm: chỉnh phiên SSH → **Advanced SSH settings** → bật
   **X11-forwarding**, lưu và kết nối lại. X server của MobaXterm phải đang chạy.
4. Trong terminal Pi, chạy:

```bash
cd ~/minhtri
unzip -o minhtri_x11_update.zip
sudo apt install -y python3-tk xauth
source .venv/bin/activate
echo "$DISPLAY"
python gui_x11.py --camera /dev/video1
```

`DISPLAY` cần có giá trị (thường dạng `localhost:10.0`), không tự gán biến này.
Nếu rỗng, kiểm tra X11-forwarding và kết nối SSH lại. Nếu vẫn không có,
kiểm tra cấu hình SSH server trên Pi có cho phép X11 forwarding hay không.

Cửa sổ xuất hiện trên Windows, nhưng mã và model chạy trên Pi. Nhấn **Bắt đầu**.
Giao diện có khung camera chia ba vùng, các nhãn tiếng Việt, FPS suy luận,
thời gian xử lý, nút **Dừng** và **Thoát**. Có thể đổi thiết bị camera khi đã dừng.

Nếu chậm qua X11:

```bash
python gui_x11.py --camera /dev/video1 --imgsz 256
```

Thử riêng giao diện, không dùng camera/model:

```bash
python gui_x11.py --demo
```

Gói cập nhật gồm gui_x11.py, gui.py (bộ xử lý nền dùng chung), main.py,
detection_logic.py, labels_vi.json và tài liệu này. Không mở máy chủ web khi chạy
gui_x11.py. Giữ best.pt và môi trường .venv có sẵn; không cần cài lại PyTorch.
Pillow đã nằm trong các phụ thuộc của Ultralytics.
