#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [ "$(uname -m)" != "aarch64" ]; then
  echo 'Cần Raspberry Pi OS 64-bit (aarch64) để dùng bản PyTorch này.' >&2
  exit 1
fi
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip espeak-ng alsa-utils libgl1 libglib2.0-0 v4l-utils
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python smoke_model.py
echo 'Hoàn tất. Kiểm tra loa: .venv/bin/python main.py --test-audio'
echo 'Chạy camera: .venv/bin/python main.py'
