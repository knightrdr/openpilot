#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="/data/openpilot/kitt/site-packages"

mkdir -p "$TARGET_DIR"

/usr/local/venv/bin/python -m pip install \
  --target "$TARGET_DIR" \
  --upgrade \
  vosk==0.3.45 \
  piper-tts==1.4.2 \
  onnxruntime==1.26.0 \
  opencv-python-headless

echo "Installed KITT Python packages at ${TARGET_DIR}"
