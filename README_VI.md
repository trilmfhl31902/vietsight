# Nhận diện vật thể hỗ trợ người khiếm thị — Raspberry Pi 4 + USB camera

Model `best.pt` đi kèm có **7 lớp**: person, bicycle, car, motorcycle, bus,
truck, traffic_light. Mã dùng tên lớp từ model, không gán nhầm theo thứ tự COCO.
Model ghi phiên bản Ultralytics 8.4.138, nên requirements dùng đúng phiên bản này.

## Chép lên Raspberry Pi

Trong khung SFTP bên trái MobaXterm, mở `/home/pi/minhtri` và kéo các file sau
từ `D:\khuyettat` vào đó: `main.py`, `detection_logic.py`, `labels_vi.json`,
`requirements.txt`, `install_pi.sh`, `smoke_model.py`, `best.pt`, và thư mục `tests`.
Không cần chép `.test_deps`, `__pycache__` hoặc `pi_known_hosts`.
Nếu dùng gói `minhtri_pi.zip`, kéo gói vào thư mục rồi chạy `unzip -n minhtri_pi.zip`.
Không ghi đè model cũ nếu bạn muốn giữ nó; dùng `--model đường_dẫn_model` để chọn.

## Cài đặt lần đầu

Yêu cầu Raspberry Pi OS **64 bit**, Python có wheel PyTorch tương thích,
kết nối Internet lúc cài, và loa/tai nghe gắn vào **Pi**. Âm thanh không tự truyền
qua SSH về máy Windows. Không cần Internet khi nhận diện và đọc cảnh báo.

```bash
cd ~/minhtri
bash install_pi.sh
.venv/bin/python main.py --test-audio
.venv/bin/python main.py --list-labels
.venv/bin/python main.py
```

Dừng bằng `Ctrl+C`. Mặc định camera 0, ảnh camera 640×480, suy luận 320,
CPU 3 luồng, ngưỡng tin cậy 0.45. Không mở cửa sổ nên chạy được qua SSH.

Nếu có màn hình Pi hoặc đã bật X11 forwarding:

```bash
.venv/bin/python main.py --show
```

Kiểm tra nhanh 30 giây (không hiện cửa sổ):

```bash
.venv/bin/python main.py --duration 30
```

## Cách cảnh báo

- Khung hình chia thành ba phần bằng nhau theo chiều ngang: trái, giữa, phải.
  Camera phải hướng cùng chiều người dùng, không lật ảnh kiểu selfie.
- Mỗi vật được báo ở vùng chứa tâm hộp và các vùng chiếm ít nhất 20% chiều rộng
  hộp. Xe lớn trải ngang có thể được báo ở nhiều vùng, bao gồm vùng giữa.
- Cần xuất hiện ở hai lần suy luận liên tiếp để xác nhận; lớp mất khỏi vùng
  được bỏ ngay, không duy trì cảnh báo cũ trong hàng đợi.
- Giọng Việt eSpeak NG chạy offline, ví dụ: “Chính giữa có ô tô.
  Bên trái có người. Bên phải có xe máy.” Ưu tiên đọc vùng giữa trước.
- Nghỉ ít nhất 3 giây sau mỗi câu rồi đọc lại cảnh hiện tại nếu vẫn thấy vật.
  Chỉ giữ cảnh mới nhất. Câu đang phát có thể kéo dài vài giây.
- Nếu mất camera hoặc âm thanh lỗi, ghi lỗi và thoát với mã 1.
  Kết quả quá 3 giây bị loại bỏ, không dùng cảnh cũ để cảnh báo tiếp.
  Nếu suy luận quá chậm, chương trình sẽ ghi cảnh báo và bỏ kết quả đó.
- Không phát câu “đường an toàn” khi không thấy vật. Camera đơn không đo được
  khoảng cách chính xác, và model này không có lớp ổ gà, bậc thang, bàn, ghế.
  Đây là bản hỗ trợ nhận diện; cần thử có người hỗ trợ trước khi dùng khi di chuyển.

## Điều chỉnh và xử lý lỗi

```bash
# Liệt kê USB camera; thử từng thiết bị nếu có nhiều /dev/video*
v4l2-ctl --list-devices
ls -l /dev/video*
.venv/bin/python main.py --camera /dev/video0

# Kiểm tra âm thanh trực tiếp trên Pi
aplay -l
espeak-ng -v vi 'Bên trái có người'

# Giảm thời gian suy luận nếu Pi chậm
.venv/bin/python main.py --imgsz 256

# Chỉ in cảnh báo, phục vụ chẩn đoán
.venv/bin/python main.py --mute --duration 30

# Tăng ngưỡng nếu nhiều nhận diện sai
.venv/bin/python main.py --conf 0.60
```

Nếu không có quyền camera, kiểm tra `groups` có nhóm `video`. Quản trị viên có
thể thêm bằng `sudo usermod -aG video pi`, sau đó đăng xuất/đăng nhập lại.
Nếu không nghe tiếng, chọn đúng đầu ra âm thanh trong Raspberry Pi OS và kiểm tra
âm lượng; có thể loa đang nằm ở HDMI thay vì jack 3.5 mm/USB.

eSpeak báo lỗi trả về sẽ làm chương trình dừng, nhưng đầu ra bị mute hay chọn
sai thiết bị có thể không sinh mã lỗi: luôn thử `--test-audio` và nghe thực tế.
Nếu `pip` không tìm thấy wheel torch, kiểm tra `uname -m` và `python3 --version`;
không ép cài gói x86 trên Pi ARM.

## Tùy chọn NCNN cho Pi

Ultralytics khuyến nghị NCNN cho CPU Raspberry Pi. Có thể xuất từ chính model
của bạn (cần Internet để cài công cụ xuất ở lần đầu):

```bash
.venv/bin/python -c "from ultralytics import YOLO; YOLO('best.pt').export(format='ncnn', imgsz=320)"
.venv/bin/python main.py --model best_ncnn_model --imgsz 320
```

Tham khảo: https://docs.ultralytics.com/guides/raspberry-pi/
và https://docs.ultralytics.com/integrations/ncnn/.
Chưa đo FPS trên Pi của bạn; không giả định tốc độ thời gian thực.

## Kiểm tra logic

```bash
.venv/bin/python -m unittest discover -s tests -v
```

`labels_vi.json` cho phép đổi cách đọc tên lớp. File này dùng **tên lớp** làm khóa.
Muốn nhận thêm loại vật cần model được huấn luyện cho những lớp đó.
