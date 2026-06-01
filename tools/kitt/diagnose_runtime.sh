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
  "IsOnroad",
  "ControlsReady",
  "KittVoiceControl",
  "KittTrafficMonitor",
  "KittVoiceLastResult",
  "KittVoiceLastCommand",
  "KittTrafficDebugCapture",
  "KittTrafficDebugCaptureLast",
  "KittTrafficState",
  "KittTrafficLastState",
  "KittTrafficDriveSummary",
  "KittTrafficFusionState",
  "KittTrafficFusionLastState",
  "KittTrafficFusionDriveSummary",
  "KittTrafficCamera",
  "KittTrafficLastCamera",
  "KittSpeechFile",
]:
  try:
    print(f"{key}: {p.get(key)}")
  except Exception as e:
    print(f"{key}: ERROR {e}")
PY

echo
echo "== KITT debug captures =="
if [ -d /data/openpilot/kitt/debug_captures ]; then
  find /data/openpilot/kitt/debug_captures -maxdepth 1 -type f | sort | tail -20
else
  echo "No debug capture directory"
fi

echo
echo "== processes =="
ps -eo pid,ppid,stat,args | grep -E "selfdrive.kitt|selfdrive.ui.soundd|manager.py|camerad" | grep -v grep || true
