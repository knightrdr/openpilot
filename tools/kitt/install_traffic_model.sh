#!/usr/bin/env bash
set -euo pipefail

MODEL_URL="https://huggingface.co/webml/yolov8n/resolve/main/onnx/yolov8n.onnx"
TARGET_DIR="/data/openpilot/kitt"
TARGET_PATH="${TARGET_DIR}/yolov8n.onnx"

mkdir -p "$TARGET_DIR"
curl -L "$MODEL_URL" -o "$TARGET_PATH"

echo "Installed KITT traffic monitor model at ${TARGET_PATH}"
