import math
import numpy as np
import os
import time
import wave


from cereal import car, messaging
from openpilot.common.basedir import BASEDIR
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.utils import retry
from openpilot.common.swaglog import cloudlog

from openpilot.system import micd
from openpilot.system.hardware import HARDWARE

SAMPLE_RATE = 48000
SAMPLE_BUFFER = 4096 # (approx 100ms)
MAX_VOLUME = 1.0
MIN_VOLUME = 0.1
KITT_SPEECH_VOLUME = 0.75
KITT_MAX_SPEECH_SECONDS = 8
KITT_SPEECH_PREFIX = "/tmp/kitt_speech_"
KITT_VOICE_DELAY_SECONDS = 0.018
KITT_VOICE_PITCH_RATIO = 0.92
SELFDRIVE_STATE_TIMEOUT = 5 # 5 seconds
FILTER_DT = 1. / (micd.SAMPLE_RATE / micd.FFT_SAMPLES)

AMBIENT_DB = 24 # DB where MIN_VOLUME is applied
DB_SCALE = 30 # AMBIENT_DB + DB_SCALE is where MAX_VOLUME is applied

VOLUME_BASE = 20
if HARDWARE.get_device_type() == "tizi":
  AMBIENT_DB = 30
  VOLUME_BASE = 10

AudibleAlert = car.CarControl.HUDControl.AudibleAlert


sound_list: dict[int, tuple[str, int | None, float]] = {
  # AudibleAlert, file name, play count (none for infinite)
  AudibleAlert.engage: ("engage.wav", 1, MAX_VOLUME),
  AudibleAlert.disengage: ("disengage.wav", 1, MAX_VOLUME),
  AudibleAlert.refuse: ("refuse.wav", 1, MAX_VOLUME),

  AudibleAlert.prompt: ("prompt.wav", 1, MAX_VOLUME),
  AudibleAlert.promptRepeat: ("prompt.wav", None, MAX_VOLUME),
  AudibleAlert.promptDistracted: ("prompt_distracted.wav", None, MAX_VOLUME),

  AudibleAlert.warningSoft: ("warning_soft.wav", None, MAX_VOLUME),
  AudibleAlert.warningImmediate: ("warning_immediate.wav", None, MAX_VOLUME),
}
if HARDWARE.get_device_type() == "tizi":
  sound_list.update({
    AudibleAlert.engage: ("engage_tizi.wav", 1, MAX_VOLUME),
    AudibleAlert.disengage: ("disengage_tizi.wav", 1, MAX_VOLUME),
  })

def check_selfdrive_timeout_alert(sm):
  ss_missing = time.monotonic() - sm.recv_time['selfdriveState']

  if ss_missing > SELFDRIVE_STATE_TIMEOUT:
    if sm['selfdriveState'].enabled and (ss_missing - SELFDRIVE_STATE_TIMEOUT) < 10:
      return True

  return False


def apply_kitt_voice_style(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
  if samples.size == 0:
    return samples

  styled = samples.astype(np.float32)
  original_size = styled.size

  # Slight synthetic pitch drop without changing the final buffer length.
  source_x = np.arange(styled.size)
  pitched_x = np.minimum(np.arange(styled.size) * KITT_VOICE_PITCH_RATIO, styled.size - 1)
  styled = np.interp(pitched_x, source_x, styled).astype(np.float32)

  # Radio/computer presence shaping: reduce low rumble and emphasize consonants.
  high_pass = np.empty_like(styled)
  high_pass[0] = styled[0]
  high_pass[1:] = styled[1:] - 0.88 * styled[:-1]
  styled = 0.62 * styled + 0.38 * high_pass

  # Mild machine modulation and short reflection for a dashboard-computer tone.
  t = np.arange(styled.size, dtype=np.float32) / sample_rate
  styled *= 0.92 + 0.08 * np.sin(2 * math.pi * 7.0 * t)
  styled += 0.045 * np.sin(2 * math.pi * 38.0 * t) * np.abs(styled)

  delay = int(sample_rate * KITT_VOICE_DELAY_SECONDS)
  if delay > 0 and styled.size > delay:
    styled[delay:] += 0.18 * styled[:-delay]

  # Controlled saturation keeps it assertive without clipping.
  styled = np.tanh(styled * 1.25).astype(np.float32)
  peak = float(np.max(np.abs(styled)))
  if peak > 0:
    styled = styled / max(peak, 1.0)

  if styled.size != original_size:
    target_x = np.linspace(0, styled.size - 1, original_size)
    styled = np.interp(target_x, np.arange(styled.size), styled).astype(np.float32)

  return np.clip(styled, -1.0, 1.0)


class Soundd:
  def __init__(self):
    self.params = Params()
    self.load_sounds()

    self.current_alert = AudibleAlert.none
    self.current_volume = MIN_VOLUME
    self.current_sound_frame = 0
    self.kitt_speech_data: np.ndarray | None = None
    self.kitt_speech_frame = 0

    self.selfdrive_timeout_alert = False

    self.spl_filter_weighted = FirstOrderFilter(0, 2.5, FILTER_DT, initialized=False)

  def load_sounds(self):
    self.loaded_sounds: dict[int, np.ndarray] = {}

    # Load all sounds
    for sound in sound_list:
      filename, play_count, volume = sound_list[sound]

      with wave.open(BASEDIR + "/selfdrive/assets/sounds/" + filename, 'r') as wavefile:
        assert wavefile.getnchannels() == 1
        assert wavefile.getsampwidth() == 2
        assert wavefile.getframerate() == SAMPLE_RATE

        length = wavefile.getnframes()
        self.loaded_sounds[sound] = np.frombuffer(wavefile.readframes(length), dtype=np.int16).astype(np.float32) / (2**16/2)

  def get_sound_data(self, frames): # get "frames" worth of data from the current alert sound, looping when required

    ret = np.zeros(frames, dtype=np.float32)
    volume = self.current_volume

    if self.current_alert != AudibleAlert.none:
      num_loops = sound_list[self.current_alert][1]
      sound_data = self.loaded_sounds[self.current_alert]
      written_frames = 0

      current_sound_frame = self.current_sound_frame % len(sound_data)
      loops = self.current_sound_frame // len(sound_data)

      while written_frames < frames and (num_loops is None or loops < num_loops):
        available_frames = sound_data.shape[0] - current_sound_frame
        frames_to_write = min(available_frames, frames - written_frames)
        ret[written_frames:written_frames+frames_to_write] = sound_data[current_sound_frame:current_sound_frame+frames_to_write]
        written_frames += frames_to_write
        self.current_sound_frame += frames_to_write

    elif self.kitt_speech_data is not None:
      volume = 1.0
      available_frames = self.kitt_speech_data.shape[0] - self.kitt_speech_frame
      frames_to_write = min(available_frames, frames)
      if frames_to_write > 0:
        ret[:frames_to_write] = self.kitt_speech_data[self.kitt_speech_frame:self.kitt_speech_frame + frames_to_write]
        self.kitt_speech_frame += frames_to_write

      if self.kitt_speech_frame >= self.kitt_speech_data.shape[0]:
        self.kitt_speech_data = None
        self.kitt_speech_frame = 0

    return ret * volume

  def callback(self, data_out: np.ndarray, frames: int, time, status) -> None:
    if status:
      cloudlog.warning(f"soundd stream over/underflow: {status}")
    data_out[:frames, 0] = self.get_sound_data(frames)

  def update_alert(self, new_alert):
    current_alert_played_once = self.current_alert == AudibleAlert.none or self.current_sound_frame > len(self.loaded_sounds[self.current_alert])
    if self.current_alert != new_alert and (new_alert != AudibleAlert.none or current_alert_played_once):
      self.current_alert = new_alert
      self.current_sound_frame = 0

  def get_audible_alert(self, sm):
    if sm.updated['selfdriveState']:
      new_alert = sm['selfdriveState'].alertSound.raw
      self.update_alert(new_alert)
    elif check_selfdrive_timeout_alert(sm):
      self.update_alert(AudibleAlert.warningImmediate)
      self.selfdrive_timeout_alert = True
    elif self.selfdrive_timeout_alert:
      self.update_alert(AudibleAlert.none)
      self.selfdrive_timeout_alert = False

  def calculate_volume(self, weighted_db):
    volume = ((weighted_db - AMBIENT_DB) / DB_SCALE) * (MAX_VOLUME - MIN_VOLUME) + MIN_VOLUME
    return math.pow(VOLUME_BASE, (np.clip(volume, MIN_VOLUME, MAX_VOLUME) - 1))

  def maybe_load_kitt_speech(self):
    if self.kitt_speech_data is not None or self.current_alert != AudibleAlert.none:
      return

    path = self.params.get("KittSpeechFile")
    if not path:
      return

    self.params.remove("KittSpeechFile")
    speech_path = path.decode("utf-8", "replace")
    if not speech_path.startswith(KITT_SPEECH_PREFIX):
      cloudlog.warning(f"ignoring invalid KITT speech path: {speech_path}")
      return

    try:
      with wave.open(speech_path, "r") as wavefile:
        if wavefile.getnchannels() != 1 or wavefile.getsampwidth() != 2:
          cloudlog.warning(f"ignoring unsupported KITT speech wav: {speech_path}")
          return

        source_rate = wavefile.getframerate()
        max_frames = min(wavefile.getnframes(), int(source_rate * KITT_MAX_SPEECH_SECONDS))
        samples = np.frombuffer(wavefile.readframes(max_frames), dtype=np.int16).astype(np.float32) / (2**16 / 2)

      if source_rate != SAMPLE_RATE and samples.size > 0:
        source_x = np.arange(samples.size)
        target_size = int(samples.size * SAMPLE_RATE / source_rate)
        target_x = np.linspace(0, samples.size - 1, target_size)
        samples = np.interp(target_x, source_x, samples).astype(np.float32)

      self.kitt_speech_data = apply_kitt_voice_style(samples) * KITT_SPEECH_VOLUME
      self.kitt_speech_frame = 0
    except Exception:
      cloudlog.exception(f"failed to load KITT speech wav: {speech_path}")
    finally:
      try:
        os.remove(speech_path)
      except OSError:
        pass

  @retry(attempts=10, delay=3)
  def get_stream(self, sd):
    # reload sounddevice to reinitialize portaudio
    sd._terminate()
    sd._initialize()
    return sd.OutputStream(channels=1, samplerate=SAMPLE_RATE, callback=self.callback, blocksize=SAMPLE_BUFFER)

  def soundd_thread(self):
    # sounddevice must be imported after forking processes
    import sounddevice as sd

    sm = messaging.SubMaster(['selfdriveState', 'soundPressure'])

    with self.get_stream(sd) as stream:
      rk = Ratekeeper(20)

      cloudlog.info(f"soundd stream started: {stream.samplerate=} {stream.channels=} {stream.dtype=} {stream.device=}, {stream.blocksize=}")
      while True:
        sm.update(0)

        if sm.updated['soundPressure'] and self.current_alert == AudibleAlert.none: # only update volume filter when not playing alert
          self.spl_filter_weighted.update(sm["soundPressure"].soundPressureWeightedDb)
          self.current_volume = self.calculate_volume(float(self.spl_filter_weighted.x))

        self.get_audible_alert(sm)
        self.maybe_load_kitt_speech()

        rk.keep_time()

        assert stream.active


def main():
  s = Soundd()
  s.soundd_thread()


if __name__ == "__main__":
  main()
