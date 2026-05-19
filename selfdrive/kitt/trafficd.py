#!/usr/bin/env python3
import os
import time
from dataclasses import dataclass

import cv2
import numpy as np
import onnxruntime as ort

from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog


MODEL_PATH = "/data/openpilot/kitt/yolov8n.onnx"
MODEL_INPUT_SIZE = 640
DETECTION_PERIOD = 1.0
STATE_TTL = 1.6
CONF_THRESHOLD = 0.28
IOU_THRESHOLD = 0.45
TRAFFIC_LIGHT_CLASS = 9
STOP_SIGN_CLASS = 11


@dataclass(frozen=True)
class Detection:
  cls: int
  conf: float
  box: tuple[int, int, int, int]


def letterbox(image: np.ndarray, size: int = MODEL_INPUT_SIZE) -> tuple[np.ndarray, float, tuple[int, int]]:
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
  pred = np.squeeze(output)
  if pred.ndim != 2:
    return []
  if pred.shape[0] < pred.shape[1]:
    pred = pred.T

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


def classify_light_color(frame_bgr: np.ndarray, box: tuple[int, int, int, int]) -> tuple[str | None, float]:
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
    self.rk = Ratekeeper(20)
    self.last_detection_t = 0.0
    self.last_state: dict[str, float | bool | str] = {}
    self.session: ort.InferenceSession | None = None
    self.input_name = ""

  def _set_status(self, status: str) -> None:
    self.params.put("KittTrafficState", {"status": status, "ts": time.monotonic()})

  def _load_model(self) -> bool:
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
      if VisionStreamType.VISION_STREAM_ROAD in streams:
        stream = VisionStreamType.VISION_STREAM_ROAD
        break
      if VisionStreamType.VISION_STREAM_WIDE_ROAD in streams:
        stream = VisionStreamType.VISION_STREAM_WIDE_ROAD
        break
      time.sleep(0.2)

    client = VisionIpcClient("camerad", stream, True)
    while not client.connect(False):
      time.sleep(0.1)
    cloudlog.info(f"kitttrafficd connected camera {client.width}x{client.height}")
    return client

  def _frame_to_bgr(self, client, buf) -> np.ndarray:
    yuv = np.frombuffer(buf.data, dtype=np.uint8, count=client.width * client.height * 3 // 2)
    yuv = yuv.reshape((client.height * 3 // 2, client.width))
    return cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_NV12)

  def _detect(self, frame_bgr: np.ndarray) -> dict[str, float | bool | str]:
    assert self.session is not None
    image, scale, pad = letterbox(frame_bgr)
    blob = image.transpose(2, 0, 1)[np.newaxis].astype(np.float32) / 255.0
    output = self.session.run(None, {self.input_name: blob})[0]
    detections = parse_yolov8(output, frame_bgr.shape[:2], scale, pad)

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
      buf = client.recv()
      if buf is None:
        continue

      now = time.monotonic()
      if now - self.last_detection_t >= DETECTION_PERIOD:
        self.last_detection_t = now
        try:
          state = self._detect(self._frame_to_bgr(client, buf))
          self.last_state = state
          self.params.put("KittTrafficState", state)
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
