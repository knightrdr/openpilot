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
  assert stats["top_class"] == 9
  assert round(stats["top_class_conf"], 2) == 0.4
  assert stats["top_traffic_class"] == 9
  assert round(stats["top_traffic_class_conf"], 2) == 0.4
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


def test_publish_state_updates_drive_summary(monkeypatch):
  puts = {}

  class FakeParams:
    def put(self, key, value):
      puts[key] = value

  monitor = TrafficMonitor()
  monitor.params = FakeParams()
  state = {
    "ts": 123.0,
    "stop_sign": True,
    "red_light": False,
    "yellow_light": False,
    "green_light": False,
    "stop_sign_conf": 0.3,
    "red_light_conf": 0.0,
    "yellow_light_conf": 0.0,
    "green_light_conf": 0.0,
    "stop_sign_candidates": 2,
    "traffic_light_candidates": 1,
    "stop_sign_max_conf": 0.4,
    "traffic_light_max_conf": 0.2,
    "top_class": 2,
    "top_class_conf": 0.7,
    "top_traffic_class": 11,
    "top_traffic_class_conf": 0.4,
  }

  monitor._publish_state(state)

  assert puts["KittTrafficState"] == state
  assert puts["KittTrafficLastState"] == state
  summary = puts["KittTrafficDriveSummary"]
  assert summary["frames"] == 1
  assert summary["stop_sign_seen"] is True
  assert summary["max_stop_sign_conf"] == 0.3
  assert summary["max_stop_sign_candidates"] == 2
  assert summary["max_top_class"] == 2
  assert summary["max_top_class_conf"] == 0.7
  assert summary["max_top_traffic_class"] == 11
  assert summary["max_top_traffic_class_conf"] == 0.4


def test_debug_capture_only_on_model_decel(monkeypatch, tmp_path):
  puts = {}
  writes = []

  class FakeParams:
    def get_bool(self, key):
      return True

    def put(self, key, value):
      puts[key] = value

  class FakeSM(dict):
    updated = {"modelV2": True}

  fake_sm = FakeSM({
    "modelV2": SimpleNamespace(action=SimpleNamespace(desiredAcceleration=-0.8, shouldStop=False)),
    "carState": SimpleNamespace(vEgo=4.0),
    "radarState": SimpleNamespace(leadOne=SimpleNamespace(status=False, dRel=0.0, vRel=0.0, radar=False)),
  })

  class FakeCV2:
    @staticmethod
    def imwrite(path, frame):
      writes.append(path)
      return True

  monkeypatch.setattr("openpilot.selfdrive.kitt.trafficd.DEBUG_CAPTURE_DIR", tmp_path)
  monkeypatch.setattr("openpilot.selfdrive.kitt.trafficd.time.time", lambda: 1000.0)
  monkeypatch.setattr("openpilot.selfdrive.kitt.trafficd.time.monotonic", lambda: 20.0)
  monkeypatch.setitem(__import__("sys").modules, "cv2", FakeCV2)

  monitor = TrafficMonitor()
  monitor.params = FakeParams()
  monitor.sm = fake_sm

  monitor._maybe_capture_debug_frame(np.zeros((4, 4, 3), dtype=np.uint8), {"status": "ready"})

  assert writes
  assert puts["KittTrafficDebugCaptureLast"]["desired_accel"] == -0.8
  assert puts["KittTrafficDebugCaptureLast"]["image_path"].endswith(".jpg")
  assert (tmp_path / "kitt_capture_1000000.json").exists()
