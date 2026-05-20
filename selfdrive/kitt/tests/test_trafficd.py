from types import SimpleNamespace

import numpy as np

from openpilot.selfdrive.kitt.trafficd import Detection, TrafficMonitor, classify_light_color, nms, traffic_candidate_stats


def test_nms_keeps_highest_overlap():
  detections = [
    Detection(11, 0.9, (10, 10, 40, 40)),
    Detection(11, 0.7, (12, 12, 42, 42)),
    Detection(9, 0.8, (100, 100, 130, 130)),
  ]
  kept = nms(detections, iou_threshold=0.4)
  assert len(kept) == 2
  assert kept[0].conf == 0.9


def test_classify_light_color_red():
  frame = np.zeros((60, 30, 3), dtype=np.uint8)
  frame[5:20, 8:22] = (0, 0, 255)
  color, score = classify_light_color(frame, (0, 0, 30, 60))
  assert color == "red_light"
  assert score > 0.5


def test_classify_light_color_yellow():
  frame = np.zeros((60, 30, 3), dtype=np.uint8)
  frame[22:37, 8:22] = (0, 255, 255)
  color, score = classify_light_color(frame, (0, 0, 30, 60))
  assert color == "yellow_light"
  assert score > 0.5


def test_classify_light_color_green():
  frame = np.zeros((60, 30, 3), dtype=np.uint8)
  frame[40:55, 8:22] = (0, 255, 0)
  color, score = classify_light_color(frame, (0, 0, 30, 60))
  assert color == "green_light"
  assert score > 0.5


def test_traffic_candidate_stats():
  output = np.zeros((1, 84, 3), dtype=np.float32)
  output[0, 4 + 9, 0] = 0.06
  output[0, 4 + 9, 1] = 0.4
  output[0, 4 + 11, 2] = 0.3

  stats = traffic_candidate_stats(output)

  assert stats["traffic_light_candidates"] == 2
  assert stats["stop_sign_candidates"] == 1
  assert round(stats["traffic_light_max_conf"], 2) == 0.4
  assert round(stats["stop_sign_max_conf"], 2) == 0.3


def test_detect_converts_bgr_to_rgb(monkeypatch):
  captured = {}

  def fake_parse(output, image_shape, scale, pad):
    return []

  def fake_stats(output):
    return {}

  class FakeSession:
    def run(self, _, inputs):
      blob = next(iter(inputs.values()))
      captured["pixel"] = blob[0, :, 0, 0].tolist()
      return [np.zeros((1, 84, 1), dtype=np.float32)]

  monkeypatch.setattr("openpilot.selfdrive.kitt.trafficd.parse_yolov8", fake_parse)
  monkeypatch.setattr("openpilot.selfdrive.kitt.trafficd.traffic_candidate_stats", fake_stats)

  monitor = TrafficMonitor()
  monitor.session = FakeSession()
  monitor.input_name = "images"
  frame_bgr = np.zeros((640, 640, 3), dtype=np.uint8)
  frame_bgr[0, 0] = (10, 20, 30)

  monitor._detect(frame_bgr)

  np.testing.assert_allclose(captured["pixel"], [30 / 255.0, 20 / 255.0, 10 / 255.0])
