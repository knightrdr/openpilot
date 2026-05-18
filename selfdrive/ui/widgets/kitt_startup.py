import math
import time
from collections.abc import Callable

import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget


STARTUP_DURATION = 5.0


class KittStartupOverlay(Widget):
  def __init__(self, on_complete: Callable[[], None]):
    super().__init__()
    self._on_complete = on_complete
    self._start_time = 0.0
    self._dismissed = False
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)
    self._font_regular = gui_app.font(FontWeight.DISPLAY_REGULAR)

  def show_event(self):
    super().show_event()
    self._start_time = time.monotonic()
    self._dismissed = False

  def _update_state(self):
    if self._dismissed:
      return

    elapsed = time.monotonic() - self._start_time
    if elapsed >= STARTUP_DURATION or ui_state.started:
      self._dismissed = True
      self._on_complete()

  def _render(self, rect: rl.Rectangle) -> None:
    elapsed = max(0.0, time.monotonic() - self._start_time)
    progress = min(elapsed / STARTUP_DURATION, 1.0)

    rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height), rl.BLACK)
    self._draw_scan_grid(rect, elapsed)
    self._draw_scanner(rect, elapsed)
    self._draw_title(rect, progress)
    self._draw_status(rect, elapsed)
    self._draw_bottom_meter(rect, progress)

  def _draw_scan_grid(self, rect: rl.Rectangle, elapsed: float) -> None:
    red_dim = rl.Color(90, 0, 0, 42)
    center_y = rect.y + rect.height * 0.52
    for i in range(13):
      y = center_y - 210 + i * 35
      fade = 1.0 - min(abs(y - center_y) / 240, 1.0)
      color = rl.Color(red_dim.r, red_dim.g, red_dim.b, int(38 * fade))
      rl.draw_line(int(rect.x + 170), int(y), int(rect.x + rect.width - 170), int(y), color)

    pulse_alpha = int(70 + 45 * math.sin(elapsed * 4.2))
    rl.draw_rectangle_lines_ex(
      rl.Rectangle(rect.x + 170, center_y - 210, rect.width - 340, 420),
      2,
      rl.Color(180, 0, 0, pulse_alpha),
    )

  def _draw_scanner(self, rect: rl.Rectangle, elapsed: float) -> None:
    scanner_rect = rl.Rectangle(rect.x + 220, rect.y + rect.height * 0.42, rect.width - 440, 96)
    rl.draw_rectangle_rounded(scanner_rect, 0.18, 10, rl.Color(24, 0, 0, 235))
    rl.draw_rectangle_rounded_lines_ex(scanner_rect, 0.18, 10, 3, rl.Color(170, 0, 0, 180))

    sweep = (math.sin(elapsed * 3.2) + 1.0) / 2.0
    beam_center = scanner_rect.x + 54 + sweep * (scanner_rect.width - 108)
    for i in range(13):
      distance = abs(i - 6)
      alpha = max(0, 220 - distance * 34)
      width = 18 + (6 - distance) * 5
      x = beam_center + (i - 6) * 16 - width / 2
      color = rl.Color(255, 16, 16, alpha)
      rl.draw_rectangle_rounded(
        rl.Rectangle(x, scanner_rect.y + 18, width, scanner_rect.height - 36),
        0.35,
        8,
        color,
      )

    glow = int(95 + 55 * math.sin(elapsed * 7.0))
    rl.draw_rectangle_gradient_h(
      int(scanner_rect.x + 8),
      int(scanner_rect.y + 30),
      int(scanner_rect.width - 16),
      36,
      rl.Color(60, 0, 0, 0),
      rl.Color(255, 0, 0, glow),
    )

  def _draw_title(self, rect: rl.Rectangle, progress: float) -> None:
    title = "K.I.T.T."
    subtitle = "KNIGHT INDUSTRIES TWO THOUSAND"
    alpha = int(255 * min(progress / 0.35, 1.0))

    title_size = 92
    title_width = measure_text_cached(self._font_bold, title, title_size).x
    title_pos = rl.Vector2(rect.x + rect.width / 2 - title_width / 2, rect.y + 122)
    rl.draw_text_ex(self._font_bold, title, title_pos, title_size, 0, rl.Color(245, 245, 245, alpha))

    subtitle_size = 30
    subtitle_width = measure_text_cached(self._font_medium, subtitle, subtitle_size).x
    subtitle_pos = rl.Vector2(rect.x + rect.width / 2 - subtitle_width / 2, rect.y + 220)
    rl.draw_text_ex(self._font_medium, subtitle, subtitle_pos, subtitle_size, 0, rl.Color(255, 48, 48, alpha))

  def _draw_status(self, rect: rl.Rectangle, elapsed: float) -> None:
    status_lines = (
      "COMMA 3X INTERFACE READY",
      "KIA STINGER PROFILE LOADED",
      "VISION SYSTEMS ONLINE",
      "DRIVER ASSIST STANDBY",
    )

    x = rect.x + rect.width / 2 - 330
    y = rect.y + rect.height * 0.64
    for idx, line in enumerate(status_lines):
      line_elapsed = elapsed - 1.0 - idx * 0.45
      if line_elapsed <= 0:
        continue
      alpha = int(255 * min(line_elapsed / 0.3, 1.0))
      text_color = rl.Color(235, 235, 235, alpha)
      indicator_color = rl.Color(255, 26, 26, alpha)
      line_y = y + idx * 50
      rl.draw_rectangle(int(x), int(line_y + 12), 18, 18, indicator_color)
      rl.draw_text_ex(self._font_regular, line, rl.Vector2(x + 42, line_y), 31, 0, text_color)

  def _draw_bottom_meter(self, rect: rl.Rectangle, progress: float) -> None:
    meter = rl.Rectangle(rect.x + 300, rect.y + rect.height - 126, rect.width - 600, 16)
    rl.draw_rectangle_rounded(meter, 0.5, 6, rl.Color(55, 0, 0, 220))
    fill = rl.Rectangle(meter.x, meter.y, meter.width * progress, meter.height)
    rl.draw_rectangle_rounded(fill, 0.5, 6, rl.Color(240, 18, 18, 245))

    text = "SYSTEM START"
    text_size = 26
    text_width = measure_text_cached(self._font_medium, text, text_size).x
    rl.draw_text_ex(
      self._font_medium,
      text,
      rl.Vector2(rect.x + rect.width / 2 - text_width / 2, meter.y + 34),
      text_size,
      0,
      rl.Color(190, 190, 190, 210),
    )
