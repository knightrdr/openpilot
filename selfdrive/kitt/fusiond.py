#!/usr/bin/env python3
import ast
import json
import time

from cereal import messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper


MODEL_DECEL_THRESHOLD = -0.5
MODEL_STRONG_DECEL_THRESHOLD = -1.2
OBJECT_CONF_THRESHOLD = 0.15
OBJECT_WEAK_CONF_THRESHOLD = 0.05
LEAD_RELEVANT_DISTANCE = 45.0
LEAD_CLOSING_SPEED = -0.4


def decode_param_value(value) -> dict:
  if not value:
    return {}
  if isinstance(value, dict):
    return value
  if isinstance(value, bytes):
    value = value.decode("utf-8", "replace")
  if not isinstance(value, str):
    value = str(value)

  try:
    return json.loads(value)
  except json.JSONDecodeError:
    try:
      parsed = ast.literal_eval(value)
      return parsed if isinstance(parsed, dict) else {}
    except (SyntaxError, ValueError):
      return {}


def _max_object_conf(traffic_state: dict) -> float:
  keys = ("stop_sign_conf", "red_light_conf", "yellow_light_conf", "green_light_conf",
          "traffic_light_max_conf", "stop_sign_max_conf")
  return max((float(traffic_state.get(key, 0.0) or 0.0) for key in keys), default=0.0)


def lead_context(radar_state) -> dict[str, bool | float | str]:
  lead = radar_state.leadOne
  lead_present = bool(lead.status)
  lead_distance = float(lead.dRel) if lead_present else 0.0
  lead_v_rel = float(lead.vRel) if lead_present else 0.0
  lead_radar = bool(lead.radar) if lead_present else False
  lead_relevant = lead_present and lead_distance <= LEAD_RELEVANT_DISTANCE and lead_v_rel <= LEAD_CLOSING_SPEED
  return {
    "lead_present": lead_present,
    "lead_relevant": lead_relevant,
    "lead_distance": lead_distance,
    "lead_v_rel": lead_v_rel,
    "lead_radar": lead_radar,
  }


def build_fusion_state(model_action, traffic_state: dict, v_ego: float, radar_state=None) -> dict[str, bool | float | str]:
  desired_accel = float(model_action.desiredAcceleration)
  model_stop = bool(model_action.shouldStop)
  model_decel = desired_accel <= MODEL_DECEL_THRESHOLD
  strong_model_decel = desired_accel <= MODEL_STRONG_DECEL_THRESHOLD
  lead = lead_context(radar_state) if radar_state is not None else {
    "lead_present": False,
    "lead_relevant": False,
    "lead_distance": 0.0,
    "lead_v_rel": 0.0,
    "lead_radar": False,
  }

  object_stop = bool(traffic_state.get("stop_sign") or traffic_state.get("red_light") or traffic_state.get("yellow_light"))
  object_go = bool(traffic_state.get("green_light"))
  object_conf = _max_object_conf(traffic_state)
  object_candidate = object_stop or object_conf >= OBJECT_WEAK_CONF_THRESHOLD
  strong_object = object_stop or object_conf >= OBJECT_CONF_THRESHOLD

  if (model_stop or strong_model_decel) and strong_object:
    level = "high"
    label = "CONFIRMED STOP CONTROL"
  elif model_decel and lead["lead_relevant"] and not object_candidate:
    level = "low"
    label = "LEAD VEHICLE DECEL"
  elif model_stop or strong_model_decel:
    level = "medium"
    label = "E2E STOP INTENT" if model_stop else "E2E STRONG DECEL"
  elif model_decel and object_candidate:
    level = "medium"
    label = "POSSIBLE STOP CONTROL"
  elif object_candidate:
    level = "low"
    label = "OBJECT CANDIDATE"
  elif model_decel:
    level = "low"
    label = "E2E DECEL"
  else:
    level = "none"
    label = "NO STOP CONTROL"

  return {
    "status": "ready",
    "ts": time.monotonic(),
    "level": level,
    "label": label,
    "model_stop": model_stop,
    "model_decel": model_decel,
    "strong_model_decel": strong_model_decel,
    "desired_accel": desired_accel,
    "v_ego": float(v_ego),
    "object_stop": object_stop,
    "object_go": object_go,
    "object_candidate": object_candidate,
    "object_conf": object_conf,
    **lead,
  }


class KittTrafficFusion:
  def __init__(self):
    self.params = Params()
    self.sm = messaging.SubMaster(["modelV2", "carState", "radarState"], poll="modelV2")
    self.rk = Ratekeeper(20)
    self.summary: dict[str, float | int | bool | str] = {
      "status": "ready",
      "started_ts": time.monotonic(),
      "last_ts": 0.0,
      "frames": 0,
      "last_label": "NO STOP CONTROL",
      "high_count": 0,
      "medium_count": 0,
      "low_count": 0,
      "lead_vehicle_decel_count": 0,
      "possible_stop_control_count": 0,
      "confirmed_stop_control_count": 0,
      "max_object_conf": 0.0,
      "min_desired_accel": 99.0,
      "model_stop_seen": False,
      "lead_relevant_seen": False,
    }

  def _publish_state(self, state: dict[str, bool | float | str]) -> None:
    self.params.put("KittTrafficFusionState", state)
    self.params.put("KittTrafficFusionLastState", state)

    level = str(state.get("level", "none"))
    label = str(state.get("label", "NO STOP CONTROL"))
    self.summary["last_ts"] = float(state.get("ts", time.monotonic()))
    self.summary["frames"] = int(self.summary["frames"]) + 1
    self.summary["last_label"] = label
    if level in ("high", "medium", "low"):
      self.summary[f"{level}_count"] = int(self.summary.get(f"{level}_count", 0)) + 1
    if label == "LEAD VEHICLE DECEL":
      self.summary["lead_vehicle_decel_count"] = int(self.summary["lead_vehicle_decel_count"]) + 1
    elif label == "POSSIBLE STOP CONTROL":
      self.summary["possible_stop_control_count"] = int(self.summary["possible_stop_control_count"]) + 1
    elif label == "CONFIRMED STOP CONTROL":
      self.summary["confirmed_stop_control_count"] = int(self.summary["confirmed_stop_control_count"]) + 1

    self.summary["max_object_conf"] = max(float(self.summary["max_object_conf"]), float(state.get("object_conf", 0.0) or 0.0))
    self.summary["min_desired_accel"] = min(float(self.summary["min_desired_accel"]), float(state.get("desired_accel", 0.0) or 0.0))
    self.summary["model_stop_seen"] = bool(self.summary["model_stop_seen"] or state.get("model_stop", False))
    self.summary["lead_relevant_seen"] = bool(self.summary["lead_relevant_seen"] or state.get("lead_relevant", False))
    self.params.put("KittTrafficFusionDriveSummary", self.summary)

  def run(self) -> None:
    while True:
      self.sm.update(0)
      if self.sm.updated["modelV2"]:
        traffic_state = decode_param_value(self.params.get("KittTrafficState"))
        fusion_state = build_fusion_state(self.sm["modelV2"].action, traffic_state, self.sm["carState"].vEgo, self.sm["radarState"])
        self._publish_state(fusion_state)
      self.rk.keep_time()


def main() -> None:
  KittTrafficFusion().run()


if __name__ == "__main__":
  main()
