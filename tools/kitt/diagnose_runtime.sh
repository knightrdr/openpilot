#!/usr/bin/env bash
set -euo pipefail

cd /data/openpilot

echo "== commit =="
git log --oneline -3

echo
echo "== models and packages =="
./tools/kitt/check_models.sh

echo
echo "== KITT params =="
PYTHONPATH="/data/openpilot/kitt/site-packages:${PYTHONPATH:-}" /usr/local/venv/bin/python - <<'PY'
from openpilot.common.params import Params

p = Params()
for key in [
  "KittVoiceControl",
  "KittTrafficMonitor",
  "KittVoiceLastResult",
  "KittVoiceLastCommand",
  "KittTrafficState",
  "KittSpeechFile",
]:
  try:
    print(f"{key}: {p.get(key)}")
  except Exception as e:
    print(f"{key}: ERROR {e}")
PY

echo
echo "== processes =="
pgrep -af "soundd|kittvoiced|kitttrafficd|manager" || true
