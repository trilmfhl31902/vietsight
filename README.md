# Minh Trí — nhận diện vật thể hỗ trợ người khiếm thị

Minh Trí dùng camera USB và YOLO để nhận diện một số vật thể phía trước, chia vị trí thành bên trái, chính giữa và bên phải, rồi đọc cảnh báo bằng tiếng Việt. Mô hình và xử lý chạy cục bộ trên Raspberry Pi; không cần dịch vụ đám mây.

## Tính năng

- Nhận diện 7 lớp trong model `best.pt`: người, xe đạp, ô tô, xe máy, xe buýt, xe tải và đèn giao thông.
- Cảnh báo theo ba vùng ngang của hình ảnh. Vật thể lớn có thể xuất hiện ở nhiều vùng.
- Chế độ dòng lệnh `main.py` phát giọng Việt bằng eSpeak NG.
- Giao diện Tk qua X11 (`gui_x11.py`) hiển thị camera, nhãn và chỉ số xử lý; giao diện này không phát âm thanh.
- Model YOLO chạy trên CPU. Có thể đổi model, camera, ngưỡng tin cậy và kích thước suy luận bằng tham số dòng lệnh.

## Yêu cầu

- Raspberry Pi OS 64-bit (`aarch64`) trên Raspberry Pi 4.
- Camera USB được hệ thống nhận diện.
- Loa hoặc tai nghe nối trực tiếp với Raspberry Pi nếu dùng cảnh báo giọng nói.
- Kết nối Internet trong lúc cài các gói Python. Sau khi cài xong, nhận diện và phát giọng nói hoạt động ngoại tuyến.

`requirements.txt` ghim PyTorch CPU và Ultralytics vào phiên bản phù hợp với model. Không cài wheel PyTorch dành cho x86 lên Raspberry Pi ARM.

## Cài đặt

```bash
cd ~/minhtri
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip espeak-ng alsa-utils libgl1 libglib2.0-0 v4l-utils
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Các lệnh trên giả định bạn đã chép repo vào `~/minhtri`. Có thể dùng `install_pi.sh` để tự động cài gói hệ thống và Python; script cũng gọi kiểm tra trong thư mục `tests/`, nhưng thư mục này hiện không có trong repo. Vì vậy hãy dùng các lệnh cài đặt thủ công ở trên nếu script dừng tại bước kiểm tra đó.

## Chạy nhận diện có âm thanh

Thử loa trước, sau đó chạy chương trình:

```bash
.venv/bin/python main.py --test-audio
.venv/bin/python main.py --list-labels
.venv/bin/python main.py
```

Dừng bằng `Ctrl+C`. Mặc định chương trình mở camera `0`, dùng ảnh 640×480, suy luận ở kích thước 320, chạy CPU với 3 luồng và ngưỡng tin cậy 0.45. Chương trình không mở cửa sổ nên có thể chạy qua SSH.

Một số tùy chọn thường dùng:

```bash
# Chọn camera khác
.venv/bin/python main.py --camera /dev/video1

# Chạy 30 giây
.venv/bin/python main.py --duration 30

# Chỉ in cảnh báo ra terminal, không phát âm thanh
.venv/bin/python main.py --mute --duration 30

# Tăng ngưỡng để giảm nhận diện nhầm
.venv/bin/python main.py --conf 0.60

# Giảm tải suy luận trên Pi
.venv/bin/python main.py --imgsz 256

# Hiện cửa sổ xem trước trên màn hình Pi hoặc qua X11
.venv/bin/python main.py --show
```

Xem tất cả tùy chọn bằng `.venv/bin/python main.py --help`.

## Giao diện MobaXterm qua X11

Giao diện Tk hiển thị trên máy Windows, trong khi model và camera vẫn chạy trên Pi. Bật X11-forwarding trong phiên SSH MobaXterm và bảo đảm X server của MobaXterm đang chạy:

```bash
cd ~/minhtri
sudo apt-get install -y python3-tk xauth
source .venv/bin/activate
python gui_x11.py --camera /dev/video1
```

`DISPLAY` phải được MobaXterm thiết lập tự động. Nếu chưa có, hãy kết nối lại SSH sau khi bật X11-forwarding. Có thể thử bố cục giao diện mà không cần camera hoặc model bằng:

```bash
python gui_x11.py --demo
```

Giao diện có nút **Bắt đầu**, **Dừng**, **Thoát**, chọn camera, ngưỡng tin cậy và model. Xem thêm [hướng dẫn MobaXterm](GUI_MOBAXTERM.md).

## Cách tạo cảnh báo

- Hình ảnh được chia thành ba vùng bằng nhau theo chiều ngang. Camera cần hướng cùng chiều người dùng, không lật như camera selfie.
- Một nhãn phải xuất hiện trong hai lần suy luận liên tiếp mới được xác nhận; nhãn biến mất sẽ bị xóa ngay.
- Vật được gán vào vùng chứa tâm hộp và các vùng mà hộp chiếm ít nhất 20% chiều rộng.
- Câu đọc ưu tiên vùng chính giữa, sau đó bên trái và bên phải. Mặc định cách nhau ít nhất 3 giây; chỉ cảnh mới nhất được giữ để đọc.
- Có thể sửa tên đọc trong `labels_vi.json`; khóa trong file là tên lớp của model.

## Chẩn đoán nhanh

```bash
# Liệt kê camera trên Raspberry Pi
v4l2-ctl --list-devices
ls -l /dev/video*

# Xem thiết bị âm thanh và thử eSpeak NG
aplay -l
espeak-ng -v vi 'Bên trái có người'

# Xem danh sách nhãn model
.venv/bin/python main.py --list-labels
```

Nếu không mở được camera, kiểm tra thiết bị `/dev/video*` và quyền nhóm `video` (`groups`). Nếu không có tiếng, kiểm tra đầu ra âm thanh và âm lượng trên Pi; âm thanh không tự truyền qua SSH về Windows.

## Cấu trúc chính

| Tệp | Mục đích |
| --- | --- |
| `main.py` | Nhận diện từ camera và cảnh báo giọng nói |
| `detection_logic.py` | Chia vùng, ổn định nhãn và tạo câu tiếng Việt |
| `gui_x11.py`, `gui.py` | Giao diện Tk qua X11 và bộ xử lý hình ảnh dùng chung |
| `best.pt` | Model YOLO tùy chỉnh gồm 7 lớp |
| `labels_vi.json` | Tên tiếng Việt dùng khi đọc cảnh báo |
| `requirements.txt` | Các gói Python và phiên bản được ghim |
| `install_pi.sh` | Script cài đặt dành cho Raspberry Pi OS 64-bit |
| `GUI_MOBAXTERM.md` | Hướng dẫn chi tiết cho giao diện MobaXterm/X11 |

Các tệp `vip.py`, `vip2.py`, `loa.py`, `testsieuam.py` và các tệp kiểm tra model là những chương trình/thử nghiệm riêng; luồng sử dụng chính được mô tả ở trên.

## Giới hạn an toàn

Đây là công cụ hỗ trợ nhận diện, không phải thiết bị dẫn đường hay hệ thống tránh va chạm. Camera đơn không đo chính xác khoảng cách; model hiện không nhận diện ổ gà, bậc thang, bàn hoặc ghế. Không thấy vật thể trong hình không có nghĩa là đường an toàn. Hãy thử nghiệm có người hỗ trợ và không dựa vào hệ thống này như phương tiện duy nhất khi di chuyển.
