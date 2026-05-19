from types import SimpleNamespace

from openpilot.selfdrive.kitt.fusiond import build_fusion_state, decode_param_value


def action(accel=-0.1, stop=False):
  return SimpleNamespace(desiredAcceleration=accel, shouldStop=stop)


def test_decode_param_value_json_and_repr():
  assert decode_param_value(b'{"status": "ready", "stop_sign": true}')["stop_sign"] is True
  assert decode_param_value("{'status': 'ready', 'red_light': True}")["red_light"] is True
  assert decode_param_value(None) == {}


def test_fusion_high_when_model_and_object_agree():
  state = build_fusion_state(action(accel=-1.5), {"stop_sign": True, "stop_sign_conf": 0.4}, 5.0)
  assert state["level"] == "high"
  assert state["label"] == "CONFIRMED STOP CONTROL"


def test_fusion_medium_for_model_stop_only():
  state = build_fusion_state(action(stop=True), {}, 5.0)
  assert state["level"] == "medium"
  assert state["label"] == "E2E STOP INTENT"


def test_fusion_low_for_model_decel_only():
  state = build_fusion_state(action(accel=-0.7), {}, 5.0)
  assert state["level"] == "low"
  assert state["label"] == "E2E DECEL"


def test_fusion_medium_for_decel_and_weak_candidate():
  state = build_fusion_state(action(accel=-0.7), {"traffic_light_max_conf": 0.06}, 5.0)
  assert state["level"] == "medium"
  assert state["label"] == "POSSIBLE STOP CONTROL"
