from cereal import car
from cereal import messaging
from cereal.messaging import SubMaster, PubMaster
from openpilot.selfdrive.ui.soundd import SAMPLE_RATE, SELFDRIVE_STATE_TIMEOUT, apply_kitt_voice_style, check_selfdrive_timeout_alert, param_value_to_str

import numpy as np
import time

AudibleAlert = car.CarControl.HUDControl.AudibleAlert


class TestSoundd:
  def test_check_selfdrive_timeout_alert(self):
    sm = SubMaster(['selfdriveState'])
    pm = PubMaster(['selfdriveState'])

    for _ in range(100):
      cs = messaging.new_message('selfdriveState')
      cs.selfdriveState.enabled = True

      pm.send("selfdriveState", cs)

      time.sleep(0.01)

      sm.update(0)

      assert not check_selfdrive_timeout_alert(sm)

    for _ in range(SELFDRIVE_STATE_TIMEOUT * 110):
      sm.update(0)
      time.sleep(0.01)

    assert check_selfdrive_timeout_alert(sm)

  # TODO: add test with micd for checking that soundd actually outputs sounds

  def test_kitt_voice_style(self):
    t = np.arange(SAMPLE_RATE // 4, dtype=np.float32) / SAMPLE_RATE
    samples = 0.4 * np.sin(2 * np.pi * 220 * t)

    styled = apply_kitt_voice_style(samples)

    assert styled.shape == samples.shape
    assert np.all(np.isfinite(styled))
    assert np.max(np.abs(styled)) <= 1.0
    assert not np.allclose(styled, samples)

  def test_param_value_to_str(self):
    assert param_value_to_str(b"/tmp/kitt_speech_test.wav") == "/tmp/kitt_speech_test.wav"
    assert param_value_to_str("/tmp/kitt_speech_test.wav") == "/tmp/kitt_speech_test.wav"

