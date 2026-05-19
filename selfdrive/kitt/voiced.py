#!/usr/bin/env python3
import json
import os
import re
import time
import wave
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

import numpy as np

from cereal import messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.kitt.python_env import add_kitt_site_packages


add_kitt_site_packages()


COMMAND_PREFIXES = ("kitt", "kit", "kid")
COMMAND_COOLDOWN = 2.0
REPEAT_COMMAND_COOLDOWN = 12.0
SPEECH_FEEDBACK_MUTE_SECONDS = 4.0
VOSK_MODEL_PATH = "/data/openpilot/kitt/vosk-model"
PIPER_MODEL_PATH = "/data/openpilot/kitt/piper-voice/en_US-lessac-medium.onnx"
PIPER_SPEECH_DIR = "/tmp"
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


def param_value_to_str(value) -> str:
  if isinstance(value, bytes):
    return value.decode("utf-8", "replace")
  return str(value)


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


class OptionalPiperSpeaker:
  def __init__(self):
    self._voice = None
    self.available = False
    self.reason = "piper Python package is not installed"

    if find_spec("piper") is None:
      return

    if not os.path.isfile(PIPER_MODEL_PATH):
      self.reason = f"piper voice model not found at {PIPER_MODEL_PATH}"
      return

    try:
      from piper.voice import PiperVoice
      self._voice = PiperVoice.load(PIPER_MODEL_PATH)
      self.available = True
      self.reason = "ready"
    except Exception as e:
      self.reason = str(e)
      cloudlog.exception("kittvoiced failed to initialize piper")

  def speak(self, text: str) -> str | None:
    if not self.available or self._voice is None:
      return None

    speech_path = Path(PIPER_SPEECH_DIR) / f"kitt_speech_{int(time.monotonic() * 1000)}.wav"
    try:
      with wave.open(str(speech_path), "wb") as wav_file:
        self._voice.synthesize_wav(text, wav_file)
      return str(speech_path)
    except Exception:
      cloudlog.exception("kittvoiced failed to synthesize speech")
      return None


class KittVoice:
  def __init__(self):
    self.params = Params()
    self.sm = messaging.SubMaster(["rawAudioData", "carState"])
    self.rk = Ratekeeper(20)
    self.recognizer = OptionalVoskRecognizer()
    self.speaker = OptionalPiperSpeaker()
    self.last_command_time = 0.0
    self.last_phrase = ""
    self.last_phrase_time = 0.0
    self.muted_until = 0.0
    self.noise_floor = 0.0

    self._set_result(f"voice daemon started; recognizer={self.recognizer.reason}; speaker={self.speaker.reason}")

  def _set_result(self, result: str) -> None:
    self.params.put("KittVoiceLastResult", result)

  def _set_command(self, command: VoiceCommand) -> None:
    self.params.put("KittVoiceLastCommand", command.phrase)

  def _speak(self, text: str) -> None:
    speech_file = self.speaker.speak(text)
    if speech_file is not None:
      self.muted_until = time.monotonic() + SPEECH_FEEDBACK_MUTE_SECONDS
      self.params.put("KittSpeechFile", speech_file)

  def _execute(self, command: VoiceCommand) -> None:
    now = time.monotonic()
    if now - self.last_command_time < COMMAND_COOLDOWN:
      return
    normalized = normalize_phrase(command.phrase)
    if normalized == self.last_phrase and now - self.last_phrase_time < REPEAT_COMMAND_COOLDOWN:
      self._set_result(f"ignored repeated command: {command.phrase}")
      return
    self.last_command_time = now
    self.last_phrase = normalized
    self.last_phrase_time = now
    self._set_command(command)

    if command.action == "show_settings":
      self.params.put("KittUiCommand", "show_settings")
      self._set_result("showing settings")
      self._speak("Showing settings.")
      cloudlog.info(f"kittvoiced command: {command.phrase} -> show settings")
    elif command.action == "current_speed":
      speed_ms = self.sm["carState"].vEgoCluster if self.sm.valid["carState"] else 0.0
      is_metric = self.params.get_bool("IsMetric")
      speed = speed_ms * 3.6 if is_metric else speed_ms * 2.236936
      unit = "km/h" if is_metric else "mph"
      speed_text = f"Current speed is {round(speed)} {unit}."
      self._set_result(speed_text.lower())
      self._speak(speed_text)
      cloudlog.info(f"kittvoiced command: {command.phrase} -> current speed")

  def _handle_test_command(self) -> None:
    phrase_bytes = self.params.get("KittVoiceCommandInput")
    if not phrase_bytes:
      return

    phrase = param_value_to_str(phrase_bytes)
    self.params.remove("KittVoiceCommandInput")
    command = command_from_phrase(phrase)
    if command is None:
      self._set_result(f"unrecognized command: {phrase}")
      return

    self._execute(command)

  def _handle_audio(self) -> None:
    if time.monotonic() < self.muted_until:
      return

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
