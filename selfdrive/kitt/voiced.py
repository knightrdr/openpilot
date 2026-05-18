#!/usr/bin/env python3
import json
import os
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


@dataclass(frozen=True)
class VoiceCommand:
  action: str
  phrase: str


def normalize_phrase(phrase: str) -> str:
  normalized = " ".join(phrase.lower().replace(".", " ").replace(",", " ").split())
  return normalized.replace("k i t t", "kitt")


def command_from_phrase(phrase: str) -> VoiceCommand | None:
  normalized = normalize_phrase(phrase)
  if not normalized:
    return None

  words = normalized.split()
  if words[0] in COMMAND_PREFIXES:
    words = words[1:]
  command = " ".join(words)

  if command in ("bookmark", "bookmark this", "mark this", "save this"):
    return VoiceCommand("bookmark", phrase)

  if command in ("status", "system status", "what is your status"):
    return VoiceCommand("status", phrase)

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
      self._recognizer = KaldiRecognizer(Model(VOSK_MODEL_PATH), 16000)
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
    self.pm = messaging.PubMaster(["bookmarkButton"])
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

    if command.action == "bookmark":
      msg = messaging.new_message("bookmarkButton")
      msg.valid = True
      self.pm.send("bookmarkButton", msg)
      self._set_result("bookmark sent")
      cloudlog.info(f"kittvoiced command: {command.phrase} -> bookmark")
    elif command.action == "status":
      speed_ms = self.sm["carState"].vEgo if self.sm.valid["carState"] else 0.0
      self._set_result(f"system online; speed={speed_ms:.1f} m/s")
      cloudlog.info(f"kittvoiced command: {command.phrase} -> status")

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
