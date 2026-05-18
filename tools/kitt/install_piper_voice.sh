#!/usr/bin/env bash
set -euo pipefail

VOICE_NAME="en_US-lessac-medium"
VOICE_BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium"
TARGET_DIR="/data/openpilot/kitt/piper-voice"

mkdir -p "$TARGET_DIR"

curl -L "${VOICE_BASE_URL}/${VOICE_NAME}.onnx" -o "${TARGET_DIR}/${VOICE_NAME}.onnx"
curl -L "${VOICE_BASE_URL}/${VOICE_NAME}.onnx.json" -o "${TARGET_DIR}/${VOICE_NAME}.onnx.json"

echo "Installed KITT Piper voice at ${TARGET_DIR}/${VOICE_NAME}.onnx"
