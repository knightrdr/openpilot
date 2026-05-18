#!/usr/bin/env python3
import json
import os
import re
import time
from dataclasses import dataclass
from importlib.util import find_spec

import numpy as np

from cereal import messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog


COMMAND_PREFIXES = ("kitt", "kit", "kid")
COMMAND_COOLDOWN = 2.0
VOSK_MODEL_PATH = "/data/openpilot/kitt/vosk-model"
RECOGNIZER_GRAMMAR = json.dumps([
  "kitt show settings",
  "kitt what is my current speed",
  "kitt whats my current speed",
  "show settings",
  "what is my current speed",
  "whats my current speed",
  "[unk]",
])


@dataclass(frozen=True)
class VoiceCommand:
  action: str
  phrase: str


def normalize_phrase(phrase: str) -> str:
  normalized = re.sub(r"[^a-z0-9 ]+", " ", phrase.lower())
  normalized = " ".join(normalized.split())
  return normalized.replace("k i t t", "kitt")


def command_from_phrase(phrase: str) -> VoiceCommand | None:
  normalized = normalize_phrase(phrase)
  if not normalized:
    return None

  words = normalized.split()
  if words[0] in COMMAND_PREFIXES:
    words = words[1:]
  command = " ".join(words)

  if command in ("show settings", "open settings", "settings"):
    return VoiceCommand("show_settings", phrase)

  if command in ("what is my current speed", "what s my current speed", "whats my current speed", "current speed", "my current speed"):
    return VoiceCommand("current_speed", phrase)

  return None


class OptionalVoskRecognizer:
  def __init__(self):
    self._recognizer = None
    self.available = False
    self.reason = "vosk Python package is not installed"

    if find_spec("vosk") is None:
      return

    if not os.path.isdir(VOSK_MODEL_PATH):
      self.reason = f"vosk model not found at {VOSK_MODEL_PATH}"
      return

    try:
      from vosk import KaldiRecognizer, Model
      self._recognizer = KaldiRecognizer(Model(VOSK_MODEL_PATH), 16000, RECOGNIZER_GRAMMAR)
      self.available = True
      self.reason = "ready"
    except Exception as e:
      self.reason = str(e)
      cloudlog.exception("kittvoiced failed to initialize vosk")

  def accept_audio(self, audio_data: bytes) -> str | None:
    if not self.available or self._recognizer is None:
      return None

    if not self._recognizer.AcceptWaveform(audio_data):
      return None

    try:
      result = json.loads(self._recognizer.Result())
    except json.JSONDecodeError:
      return None

    text = result.get("text", "")
    return text if text else None


class KittVoice:
  def __init__(self):
    self.params = Params()
    self.sm = messaging.SubMaster(["rawAudioData", "carState"])
    self.rk = Ratekeeper(20)
    self.recognizer = OptionalVoskRecognizer()
    self.last_command_time = 0.0
    self.noise_floor = 0.0

    self._set_result(f"voice daemon started; recognizer={self.recognizer.reason}")

  def _set_result(self, result: str) -> None:
    self.params.put("KittVoiceLastResult", result)

  def _set_command(self, command: VoiceCommand) -> None:
    self.params.put("KittVoiceLastCommand", command.phrase)

  def _execute(self, command: VoiceCommand) -> None:
    now = time.monotonic()
    if now - self.last_command_time < COMMAND_COOLDOWN:
      return
    self.last_command_time = now
    self._set_command(command)

    if command.action == "show_settings":
      self.params.put("KittUiCommand", "show_settings")
      self._set_result("showing settings")
      cloudlog.info(f"kittvoiced command: {command.phrase} -> show settings")
    elif command.action == "current_speed":
      speed_ms = self.sm["carState"].vEgoCluster if self.sm.valid["carState"] else 0.0
      is_metric = self.params.get_bool("IsMetric")
      speed = speed_ms * 3.6 if is_metric else speed_ms * 2.236936
      unit = "km/h" if is_metric else "mph"
      self._set_result(f"current speed is {round(speed)} {unit}")
      cloudlog.info(f"kittvoiced command: {command.phrase} -> current speed")

  def _handle_test_command(self) -> None:
    phrase_bytes = self.params.get("KittVoiceCommandInput")
    if not phrase_bytes:
      return

    phrase = phrase_bytes.decode("utf-8", "replace")
    self.params.remove("KittVoiceCommandInput")
    command = command_from_phrase(phrase)
    if command is None:
      self._set_result(f"unrecognized command: {phrase}")
      return

    self._execute(command)

  def _handle_audio(self) -> None:
    if not self.sm.updated["rawAudioData"]:
      return

    raw_audio = self.sm["rawAudioData"]
    if raw_audio.sampleRate != 16000:
      self._set_result(f"unsupported sample rate: {raw_audio.sampleRate}")
      return

    audio_bytes = bytes(raw_audio.data)
    samples = np.frombuffer(audio_bytes, dtype=np.int16)
    if len(samples) == 0:
      return

    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
    self.noise_floor = 0.98 * self.noise_floor + 0.02 * rms

    text = self.recognizer.accept_audio(audio_bytes)
    if text is None:
      return

    command = command_from_phrase(text)
    if command is None:
      self._set_result(f"heard but ignored: {text}")
      return

    self._execute(command)

  def run(self) -> None:
    while True:
      self.sm.update(0)
      self._handle_test_command()
      self._handle_audio()
      self.rk.keep_time()


def main() -> None:
  KittVoice().run()


if __name__ == "__main__":
  main()
