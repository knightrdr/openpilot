#!/usr/bin/env bash
set -euo pipefail

paths=(
  "/data/openpilot/kitt/vosk-model"
  "/data/openpilot/kitt/piper-voice/en_US-lessac-medium.onnx"
  "/data/openpilot/kitt/piper-voice/en_US-lessac-medium.onnx.json"
  "/data/openpilot/kitt/yolov8n.onnx"
)

for path in "${paths[@]}"; do
  if [ -e "$path" ]; then
    echo "OK      $path"
  else
    echo "MISSING $path"
  fi
done

PYTHONPATH="/data/openpilot/kitt/site-packages:${PYTHONPATH:-}" /usr/local/venv/bin/python - <<'PY'
mods = ["vosk", "piper", "onnxruntime", "cv2"]
for mod in mods:
  try:
    __import__(mod)
    print(f"OK      python:{mod}")
  except Exception as e:
    print(f"MISSING python:{mod} ({e})")
PY
