import numpy as np

from openpilot.selfdrive.kitt.trafficd import Detection, classify_light_color, nms


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
