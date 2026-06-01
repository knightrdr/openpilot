#!/usr/bin/env python3
import os
import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cereal import messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.kitt.python_env import add_kitt_site_packages


add_kitt_site_packages()


MODEL_PATH = "/data/openpilot/kitt/yolov8n.onnx"
MODEL_INPUT_SIZE = 640
DETECTION_PERIOD = 1.0
STATE_TTL = 1.6
CONF_THRESHOLD = 0.15
DEBUG_CONF_THRESHOLD = 0.05
IOU_THRESHOLD = 0.45
TRAFFIC_LIGHT_CLASS = 9
STOP_SIGN_CLASS = 11
MODEL_DECEL_CAPTURE_THRESHOLD = -0.5
DEBUG_CAPTURE_DIR = Path("/data/openpilot/kitt/debug_captures")
DEBUG_CAPTURE_PERIOD = 8.0
DEBUG_CAPTURE_LIMIT = 30


@dataclass(frozen=True)
class Detection:
  cls: int
  conf: float
  box: tuple[int, int, int, int]


def normalize_yolov8_prediction(output: np.ndarray) -> np.ndarray | None:
  pred = np.squeeze(output)
  if pred.ndim != 2:
    return None

  # Ultralytics ONNX commonly returns [84, anchors], but some runtimes or
  # tests use [anchors, 84]. Normalize to one prediction per row.
  if pred.shape[1] < 6 or (pred.shape[0] <= 200 and pred.shape[0] < pred.shape[1]):
    pred = pred.T
  return pred


def letterbox(image: np.ndarray, size: int = MODEL_INPUT_SIZE) -> tuple[np.ndarray, float, tuple[int, int]]:
  import cv2

  h, w = image.shape[:2]
  scale = min(size / h, size / w)
  new_w, new_h = int(round(w * scale)), int(round(h * scale))
  resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
  canvas = np.full((size, size, 3), 114, dtype=np.uint8)
  pad_x = (size - new_w) // 2
  pad_y = (size - new_h) // 2
  canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
  return canvas, scale, (pad_x, pad_y)


def nms(detections: list[Detection], iou_threshold: float = IOU_THRESHOLD) -> list[Detection]:
  if not detections:
    return []

  boxes = np.array([d.box for d in detections], dtype=np.float32)
  scores = np.array([d.conf for d in detections], dtype=np.float32)
  order = scores.argsort()[::-1]
  keep: list[Detection] = []

  while order.size > 0:
    i = int(order[0])
    keep.append(detections[i])
    if order.size == 1:
      break

    rest = order[1:]
    x1 = np.maximum(boxes[i, 0], boxes[rest, 0])
    y1 = np.maximum(boxes[i, 1], boxes[rest, 1])
    x2 = np.minimum(boxes[i, 2], boxes[rest, 2])
    y2 = np.minimum(boxes[i, 3], boxes[rest, 3])
    inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    area_i = np.maximum(0, boxes[i, 2] - boxes[i, 0]) * np.maximum(0, boxes[i, 3] - boxes[i, 1])
    area_rest = np.maximum(0, boxes[rest, 2] - boxes[rest, 0]) * np.maximum(0, boxes[rest, 3] - boxes[rest, 1])
    iou = inter / np.maximum(area_i + area_rest - inter, 1e-6)
    order = rest[iou <= iou_threshold]

  return keep


def parse_yolov8(output: np.ndarray, image_shape: tuple[int, int], scale: float, pad: tuple[int, int]) -> list[Detection]:
  pred = normalize_yolov8_prediction(output)
  if pred is None:
    return []

  h, w = image_shape
  pad_x, pad_y = pad
  detections: list[Detection] = []
  for row in pred:
    class_scores = row[4:]
    cls = int(np.argmax(class_scores))
    if cls not in (TRAFFIC_LIGHT_CLASS, STOP_SIGN_CLASS):
      continue

    conf = float(class_scores[cls])
    if conf < CONF_THRESHOLD:
      continue

    cx, cy, bw, bh = row[:4]
    x1 = int((cx - bw / 2 - pad_x) / scale)
    y1 = int((cy - bh / 2 - pad_y) / scale)
    x2 = int((cx + bw / 2 - pad_x) / scale)
    y2 = int((cy + bh / 2 - pad_y) / scale)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w - 1, x2), min(h - 1, y2)
    if x2 <= x1 or y2 <= y1:
      continue
    detections.append(Detection(cls, conf, (x1, y1, x2, y2)))

  return nms(detections)


def traffic_candidate_stats(output: np.ndarray) -> dict[str, float | int]:
  pred = normalize_yolov8_prediction(output)
  if pred is None:
    return {
      "top_class": -1,
      "top_class_conf": 0.0,
      "top_traffic_class": -1,
      "top_traffic_class_conf": 0.0,
      "traffic_light_candidates": 0,
      "stop_sign_candidates": 0,
      "traffic_light_max_conf": 0.0,
      "stop_sign_max_conf": 0.0,
    }

  class_scores = pred[:, 4:]
  if class_scores.shape[1] <= max(TRAFFIC_LIGHT_CLASS, STOP_SIGN_CLASS):
    return {
      "top_class": -1,
      "top_class_conf": 0.0,
      "top_traffic_class": -1,
      "top_traffic_class_conf": 0.0,
      "traffic_light_candidates": 0,
      "stop_sign_candidates": 0,
      "traffic_light_max_conf": 0.0,
      "stop_sign_max_conf": 0.0,
    }

  top_idx = int(np.argmax(class_scores))
  _, top_class = divmod(top_idx, class_scores.shape[1])
  top_class_conf = float(class_scores.reshape(-1)[top_idx])
  traffic_scores = class_scores[:, TRAFFIC_LIGHT_CLASS]
  stop_scores = class_scores[:, STOP_SIGN_CLASS]
  traffic_light_max = float(np.max(traffic_scores)) if traffic_scores.size else 0.0
  stop_sign_max = float(np.max(stop_scores)) if stop_scores.size else 0.0
  if traffic_light_max >= stop_sign_max:
    top_traffic_class = TRAFFIC_LIGHT_CLASS
    top_traffic_class_conf = traffic_light_max
  else:
    top_traffic_class = STOP_SIGN_CLASS
    top_traffic_class_conf = stop_sign_max

  return {
    "top_class": int(top_class),
    "top_class_conf": top_class_conf,
    "top_traffic_class": top_traffic_class,
    "top_traffic_class_conf": top_traffic_class_conf,
    "traffic_light_candidates": int(np.count_nonzero(traffic_scores >= DEBUG_CONF_THRESHOLD)),
    "stop_sign_candidates": int(np.count_nonzero(stop_scores >= DEBUG_CONF_THRESHOLD)),
    "traffic_light_max_conf": traffic_light_max,
    "stop_sign_max_conf": stop_sign_max,
  }


def classify_light_color(frame_bgr: np.ndarray, box: tuple[int, int, int, int]) -> tuple[str | None, float]:
  import cv2

  x1, y1, x2, y2 = box
  crop = frame_bgr[y1:y2, x1:x2]
  if crop.size == 0:
    return None, 0.0

  hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
  sat = hsv[:, :, 1]
  val = hsv[:, :, 2]
  bright = (sat > 70) & (val > 90)
  if not np.any(bright):
    return None, 0.0

  hue = hsv[:, :, 0]
  masks = {
    "red_light": bright & ((hue < 10) | (hue > 170)),
    "yellow_light": bright & (hue >= 18) & (hue <= 38),
    "green_light": bright & (hue >= 45) & (hue <= 90),
  }
  scores = {name: float(np.count_nonzero(mask)) / float(np.count_nonzero(bright)) for name, mask in masks.items()}
  best_name, best_score = max(scores.items(), key=lambda item: item[1])
  return (best_name, best_score) if best_score > 0.18 else (None, best_score)


class TrafficMonitor:
  def __init__(self):
    self.params = Params()
    self.sm = messaging.SubMaster(["modelV2", "carState", "radarState"])
    self.rk = Ratekeeper(20)
    self.last_detection_t = 0.0
    self.last_capture_t = 0.0
    self.last_state: dict[str, float | bool | str] = {}
    self.summary: dict[str, float | int | bool | str] = {
      "status": "ready",
      "started_ts": time.monotonic(),
      "last_ts": 0.0,
      "frames": 0,
      "stop_sign_seen": False,
      "red_light_seen": False,
      "yellow_light_seen": False,
      "green_light_seen": False,
      "max_stop_sign_conf": 0.0,
      "max_red_light_conf": 0.0,
      "max_yellow_light_conf": 0.0,
      "max_green_light_conf": 0.0,
      "max_top_class_conf": 0.0,
      "max_top_class": -1,
      "max_top_traffic_class_conf": 0.0,
      "max_top_traffic_class": -1,
      "max_stop_sign_candidates": 0,
      "max_traffic_light_candidates": 0,
      "max_stop_sign_candidate_conf": 0.0,
      "max_traffic_light_candidate_conf": 0.0,
    }
    self.session = None
    self.input_name = ""

  def _prune_captures(self) -> None:
    captures = sorted(DEBUG_CAPTURE_DIR.glob("kitt_capture_*.jpg"))
    for path in captures[:-DEBUG_CAPTURE_LIMIT]:
      try:
        path.unlink()
        meta_path = path.with_suffix(".json")
        if meta_path.exists():
          meta_path.unlink()
      except OSError:
        pass

  def _set_status(self, status: str) -> None:
    state = {"status": status, "ts": time.monotonic()}
    self.params.put("KittTrafficState", state)
    self.params.put("KittTrafficLastState", state)

  def _load_model(self) -> bool:
    try:
      import onnxruntime as ort
    except ImportError:
      self._set_status("missing dependency: onnxruntime")
      return False

    if not os.path.isfile(MODEL_PATH):
      self._set_status(f"missing model: {MODEL_PATH}")
      return False

    providers = ["CPUExecutionProvider"]
    self.session = ort.InferenceSession(MODEL_PATH, providers=providers)
    self.input_name = self.session.get_inputs()[0].name
    self._set_status("ready")
    return True

  def _connect_camera(self):
    from msgq.visionipc import VisionIpcClient, VisionStreamType

    while True:
      streams = VisionIpcClient.available_streams("camerad", block=False)
      if VisionStreamType.VISION_STREAM_WIDE_ROAD in streams:
        stream = VisionStreamType.VISION_STREAM_WIDE_ROAD
        break
      if VisionStreamType.VISION_STREAM_ROAD in streams:
        stream = VisionStreamType.VISION_STREAM_ROAD
        break
      time.sleep(0.2)

    client = VisionIpcClient("camerad", stream, True)
    while not client.connect(False):
      time.sleep(0.1)
    self.params.put("KittTrafficCamera", str(stream))
    self.params.put("KittTrafficLastCamera", str(stream))
    cloudlog.info(f"kitttrafficd connected camera {stream=} {client.width}x{client.height}")
    return client

  def _maybe_capture_debug_frame(self, frame_bgr: np.ndarray, state: dict[str, float | bool | str]) -> None:
    if not self.params.get_bool("KittTrafficDebugCapture"):
      return
    if not self.sm.updated["modelV2"]:
      return

    model_action = self.sm["modelV2"].action
    desired_accel = float(model_action.desiredAcceleration)
    should_stop = bool(model_action.shouldStop)
    if not should_stop and desired_accel > MODEL_DECEL_CAPTURE_THRESHOLD:
      return

    now = time.monotonic()
    if now - self.last_capture_t < DEBUG_CAPTURE_PERIOD:
      return
    self.last_capture_t = now

    try:
      import cv2

      DEBUG_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
      capture_id = int(time.time() * 1000)
      image_path = DEBUG_CAPTURE_DIR / f"kitt_capture_{capture_id}.jpg"
      meta_path = image_path.with_suffix(".json")
      cv2.imwrite(str(image_path), frame_bgr)

      lead = self.sm["radarState"].leadOne
      metadata = {
        "ts": now,
        "wall_time_ms": capture_id,
        "image_path": str(image_path),
        "desired_accel": desired_accel,
        "should_stop": should_stop,
        "v_ego": float(self.sm["carState"].vEgo),
        "lead_present": bool(lead.status),
        "lead_distance": float(lead.dRel) if lead.status else 0.0,
        "lead_v_rel": float(lead.vRel) if lead.status else 0.0,
        "lead_radar": bool(lead.radar) if lead.status else False,
        "traffic_state": state,
      }
      meta_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
      self.params.put("KittTrafficDebugCaptureLast", metadata)
      self._prune_captures()
    except Exception:
      cloudlog.exception("kitttrafficd failed to save debug capture")

  def _publish_state(self, state: dict[str, float | bool | str]) -> None:
    self.params.put("KittTrafficState", state)
    self.params.put("KittTrafficLastState", state)
    self.summary["last_ts"] = float(state.get("ts", time.monotonic()))
    self.summary["frames"] = int(self.summary["frames"]) + 1
    for key in ("stop_sign", "red_light", "yellow_light", "green_light"):
      self.summary[f"{key}_seen"] = bool(self.summary.get(f"{key}_seen", False) or state.get(key, False))
      self.summary[f"max_{key}_conf"] = max(float(self.summary.get(f"max_{key}_conf", 0.0)), float(state.get(f"{key}_conf", 0.0) or 0.0))
    top_conf = float(state.get("top_class_conf", 0.0) or 0.0)
    if top_conf > float(self.summary["max_top_class_conf"]):
      self.summary["max_top_class_conf"] = top_conf
      self.summary["max_top_class"] = int(state.get("top_class", -1) or -1)
    top_traffic_conf = float(state.get("top_traffic_class_conf", 0.0) or 0.0)
    if top_traffic_conf > float(self.summary["max_top_traffic_class_conf"]):
      self.summary["max_top_traffic_class_conf"] = top_traffic_conf
      self.summary["max_top_traffic_class"] = int(state.get("top_traffic_class", -1) or -1)
    self.summary["max_stop_sign_candidates"] = max(int(self.summary["max_stop_sign_candidates"]), int(state.get("stop_sign_candidates", 0) or 0))
    self.summary["max_traffic_light_candidates"] = max(int(self.summary["max_traffic_light_candidates"]), int(state.get("traffic_light_candidates", 0) or 0))
    self.summary["max_stop_sign_candidate_conf"] = max(float(self.summary["max_stop_sign_candidate_conf"]), float(state.get("stop_sign_max_conf", 0.0) or 0.0))
    self.summary["max_traffic_light_candidate_conf"] = max(float(self.summary["max_traffic_light_candidate_conf"]), float(state.get("traffic_light_max_conf", 0.0) or 0.0))
    self.params.put("KittTrafficDriveSummary", self.summary)

  def _frame_to_bgr(self, client, buf) -> np.ndarray:
    import cv2

    yuv = np.frombuffer(buf.data, dtype=np.uint8, count=client.width * client.height * 3 // 2)
    yuv = yuv.reshape((client.height * 3 // 2, client.width))
    return cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_NV12)

  def _detect(self, frame_bgr: np.ndarray) -> dict[str, float | bool | str]:
    import cv2

    assert self.session is not None
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    image, scale, pad = letterbox(frame_rgb)
    blob = image.transpose(2, 0, 1)[np.newaxis].astype(np.float32) / 255.0
    output = self.session.run(None, {self.input_name: blob})[0]
    detections = parse_yolov8(output, frame_bgr.shape[:2], scale, pad)
    stats = traffic_candidate_stats(output)

    state: dict[str, float | bool | str] = {
      "status": "ready",
      "ts": time.monotonic(),
      "stop_sign": False,
      "red_light": False,
      "yellow_light": False,
      "green_light": False,
      "stop_sign_conf": 0.0,
      "red_light_conf": 0.0,
      "yellow_light_conf": 0.0,
      "green_light_conf": 0.0,
      **stats,
    }

    for detection in detections:
      if detection.cls == STOP_SIGN_CLASS:
        state["stop_sign"] = True
        state["stop_sign_conf"] = max(float(state["stop_sign_conf"]), detection.conf)
      elif detection.cls == TRAFFIC_LIGHT_CLASS:
        color_name, color_conf = classify_light_color(frame_bgr, detection.box)
        if color_name is not None:
          state[color_name] = True
          state[f"{color_name}_conf"] = max(float(state[f"{color_name}_conf"]), detection.conf * color_conf)

    return state

  def run(self) -> None:
    if not self._load_model():
      while True:
        self.rk.keep_time()

    client = self._connect_camera()
    while True:
      self.sm.update(0)
      buf = client.recv()
      if buf is None:
        continue

      now = time.monotonic()
      if now - self.last_detection_t >= DETECTION_PERIOD:
        self.last_detection_t = now
        try:
          frame_bgr = self._frame_to_bgr(client, buf)
          state = self._detect(frame_bgr)
          self.last_state = state
          self._publish_state(state)
          self._maybe_capture_debug_frame(frame_bgr, state)
        except Exception:
          cloudlog.exception("kitttrafficd detection failed")

      if self.last_state and now - float(self.last_state.get("ts", 0.0)) > STATE_TTL:
        self.params.remove("KittTrafficState")
        self.last_state = {}

      self.rk.keep_time()


def main() -> None:
  TrafficMonitor().run()


if __name__ == "__main__":
  main()
