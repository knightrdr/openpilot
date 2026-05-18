#!/usr/bin/env bash
set -euo pipefail

MODEL_NAME="vosk-model-small-en-us-0.15"
MODEL_URL="https://alphacephei.com/vosk/models/${MODEL_NAME}.zip"
TARGET_DIR="/data/openpilot/kitt"
TMP_DIR="/tmp/kitt-vosk-model"

mkdir -p "$TARGET_DIR"
rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"

cd "$TMP_DIR"
curl -L "$MODEL_URL" -o "${MODEL_NAME}.zip"
unzip -q "${MODEL_NAME}.zip"
rm -rf "${TARGET_DIR}/vosk-model"
mv "$MODEL_NAME" "${TARGET_DIR}/vosk-model"

echo "Installed KITT Vosk model at ${TARGET_DIR}/vosk-model"
