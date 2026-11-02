#!/usr/bin/env python3
"""Check the actual best.pt and USB image on Pi; no GUI, sound or downloads."""
import argparse
import hashlib
import json
from pathlib import Path
import time

BASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=str(BASE / 'best.pt'))
    parser.add_argument('--camera', default='/dev/video1')
    parser.add_argument('--image', help='Kiểm tra ảnh có sẵn thay cho camera')
    args = parser.parse_args()
    model_path = Path(args.model)
    if not model_path.is_file():
        parser.error(f'Không có model {model_path}')
    import cv2
    import torch
    from ultralytics import YOLO
    torch.set_num_threads(3)
    cv2.setNumThreads(1)
    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            parser.error(f'Không đọc được ảnh {args.image}')
    else:
        source = int(args.camera) if args.camera.isdigit() else args.camera
        cap = cv2.VideoCapture(source)
        frame = None
        try:
            if not cap.isOpened():
                raise RuntimeError(f'Không mở được {source}. Đóng GUI camera trước khi kiểm tra.')
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            deadline, count = time.monotonic() + 8, 0
            while time.monotonic() < deadline:
                ok, image = cap.read()
                if ok:
                    frame = image
                    count += 1
                    if count >= 10:
                        break
                else:
                    time.sleep(.1)
            if frame is None:
                raise RuntimeError('Camera không trả về ảnh')
        finally:
            cap.release()
    if not cv2.imwrite(str(BASE / 'anh_kiemtra_best.jpg'), frame):
        raise RuntimeError('Không lưu được ảnh kiểm tra')
    model = YOLO(str(model_path), task='detect')
    report = {'model': str(model_path.resolve()),
              'sha256': hashlib.sha256(model_path.read_bytes()).hexdigest(),
              'labels': model.names, 'image_shape': list(frame.shape), 'checks': []}
    for size in (320, 640):
        # This very low threshold is diagnostic ONLY, not an operational alert.
        result = model.predict(frame, imgsz=size, conf=.001, max_det=300,
                               device='cpu', verbose=False)[0]
        top = {}
        for cls, score in zip(result.boxes.cls.tolist(), result.boxes.conf.tolist()):
            name = str(result.names[int(cls)])
            top[name] = max(top.get(name, 0), round(score, 5))
        report['checks'].append({'imgsz': size, 'highest_score_by_class': top,
                                  'person_above_0_45': top.get('person', 0) >= .45})
    text = json.dumps(report, ensure_ascii=False, indent=2)
    (BASE / 'ketqua_best.json').write_text(text, encoding='utf-8')
    print(text)
    print('\nĐã lưu anh_kiemtra_best.jpg và ketqua_best.json.')
    print('Ngưỡng 0.001 chỉ để chẩn đoán. Điểm thấp không phải nhận diện đáng tin cậy.')


if __name__ == '__main__':
    main()
