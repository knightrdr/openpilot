from types import SimpleNamespace

from openpilot.selfdrive.kitt.fusiond import KittTrafficFusion, build_fusion_state, decode_param_value


def action(accel=-0.1, stop=False):
  return SimpleNamespace(desiredAcceleration=accel, shouldStop=stop)


def radar(status=False, d_rel=0.0, v_rel=0.0, radar=True):
  lead = SimpleNamespace(status=status, dRel=d_rel, vRel=v_rel, radar=radar)
  return SimpleNamespace(leadOne=lead)


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


def test_fusion_identifies_relevant_lead_decel():
  state = build_fusion_state(action(accel=-0.7), {}, 12.0, radar(status=True, d_rel=20.0, v_rel=-1.0))
  assert state["level"] == "low"
  assert state["label"] == "LEAD VEHICLE DECEL"
  assert state["lead_present"] is True
  assert state["lead_relevant"] is True
  assert state["lead_radar"] is True


def test_object_candidate_overrides_lead_decel_label():
  state = build_fusion_state(action(accel=-0.7), {"stop_sign": True}, 12.0, radar(status=True, d_rel=20.0, v_rel=-1.0))
  assert state["label"] == "POSSIBLE STOP CONTROL"


def test_publish_state_updates_fusion_summary():
  puts = {}

  class FakeParams:
    def put(self, key, value):
      puts[key] = value

  fusion = KittTrafficFusion()
  fusion.params = FakeParams()
  state = {
    "ts": 456.0,
    "level": "medium",
    "label": "POSSIBLE STOP CONTROL",
    "object_conf": 0.12,
    "desired_accel": -0.8,
    "model_stop": False,
    "lead_relevant": False,
  }

  fusion._publish_state(state)

  assert puts["KittTrafficFusionState"] == state
  assert puts["KittTrafficFusionLastState"] == state
  summary = puts["KittTrafficFusionDriveSummary"]
  assert summary["frames"] == 1
  assert summary["medium_count"] == 1
  assert summary["possible_stop_control_count"] == 1
  assert summary["max_object_conf"] == 0.12
  assert summary["min_desired_accel"] == -0.8
