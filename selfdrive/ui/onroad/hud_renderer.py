import json
import time
import pyray as rl
from dataclasses import dataclass
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.selfdrive.ui.onroad.exp_button import ExpButton
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

# Constants
SET_SPEED_NA = 255
KM_TO_MILE = 0.621371
CRUISE_DISABLED_CHAR = '–'


@dataclass(frozen=True)
class UIConfig:
  header_height: int = 300
  border_size: int = 30
  button_size: int = 192
  set_speed_width_metric: int = 200
  set_speed_width_imperial: int = 172
  set_speed_height: int = 204
  wheel_icon_size: int = 144
  brake_badge_width: int = 300
  brake_badge_height: int = 64
  model_badge_width: int = 340
  model_badge_height: int = 64
  traffic_badge_width: int = 220
  traffic_badge_height: int = 58
  traffic_badge_gap: int = 14


@dataclass(frozen=True)
class FontSizes:
  current_speed: int = 176
  speed_unit: int = 66
  max_speed: int = 40
  set_speed: int = 90
  brake_badge: int = 36
  model_badge: int = 34
  traffic_badge: int = 28


@dataclass(frozen=True)
class Colors:
  WHITE = rl.WHITE
  DISENGAGED = rl.Color(145, 155, 149, 255)
  OVERRIDE = rl.Color(145, 155, 149, 255)  # Added
  ENGAGED = rl.Color(128, 216, 166, 255)
  DISENGAGED_BG = rl.Color(0, 0, 0, 153)
  OVERRIDE_BG = rl.Color(145, 155, 149, 204)
  ENGAGED_BG = rl.Color(128, 216, 166, 204)
  GREY = rl.Color(166, 166, 166, 255)
  DARK_GREY = rl.Color(114, 114, 114, 255)
  BLACK_TRANSLUCENT = rl.Color(0, 0, 0, 166)
  WHITE_TRANSLUCENT = rl.Color(255, 255, 255, 200)
  BORDER_TRANSLUCENT = rl.Color(255, 255, 255, 75)
  HEADER_GRADIENT_START = rl.Color(0, 0, 0, 114)
  HEADER_GRADIENT_END = rl.BLANK
  KITT_BRAKE_BG = rl.Color(225, 38, 38, 215)
  KITT_OP_BRAKE_BG = rl.Color(240, 145, 35, 215)
  KITT_MODEL_STOP_BG = rl.Color(175, 40, 215, 215)
  KITT_MODEL_DECEL_BG = rl.Color(64, 116, 220, 205)
  KITT_STOP_SIGN_BG = rl.Color(215, 30, 45, 220)
  KITT_RED_LIGHT_BG = rl.Color(220, 25, 25, 220)
  KITT_YELLOW_LIGHT_BG = rl.Color(220, 170, 20, 220)
  KITT_GREEN_LIGHT_BG = rl.Color(40, 175, 80, 220)


UI_CONFIG = UIConfig()
FONT_SIZES = FontSizes()
COLORS = Colors()


class HudRenderer(Widget):
  def __init__(self):
    super().__init__()
    """Initialize the HUD renderer."""
    self.is_cruise_set: bool = False
    self.is_cruise_available: bool = True
    self.set_speed: float = SET_SPEED_NA
    self.speed: float = 0.0
    self.v_ego_cluster_seen: bool = False
    self.driver_brake_active: bool = False
    self.openpilot_brake_active: bool = False
    self.model_stop_active: bool = False
    self.model_decel_active: bool = False
    self.traffic_state: dict[str, object] = {}
    self._last_traffic_read_t = 0.0

    self._params = Params()
    self._font_semi_bold: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)
    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)

    self._exp_button: ExpButton = ExpButton(UI_CONFIG.button_size, UI_CONFIG.wheel_icon_size)

  def _update_state(self) -> None:
    """Update HUD state based on car state and controls state."""
    sm = ui_state.sm
    if sm.recv_frame["carState"] < ui_state.started_frame:
      self.is_cruise_set = False
      self.set_speed = SET_SPEED_NA
      self.speed = 0.0
      return

    controls_state = sm['controlsState']
    car_state = sm['carState']
    car_control = sm['carControl']
    model_action = sm['modelV2'].action

    v_cruise_cluster = car_state.vCruiseCluster
    self.set_speed = (
      controls_state.vCruiseDEPRECATED if v_cruise_cluster == 0.0 else v_cruise_cluster
    )
    self.is_cruise_set = 0 < self.set_speed < SET_SPEED_NA
    self.is_cruise_available = self.set_speed != -1

    if self.is_cruise_set and not ui_state.is_metric:
      self.set_speed *= KM_TO_MILE

    v_ego_cluster = car_state.vEgoCluster
    self.v_ego_cluster_seen = self.v_ego_cluster_seen or v_ego_cluster != 0.0
    v_ego = v_ego_cluster if self.v_ego_cluster_seen else car_state.vEgo
    speed_conversion = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self.speed = max(0.0, v_ego * speed_conversion)

    self.driver_brake_active = car_state.brakePressed
    self.openpilot_brake_active = car_control.longActive and car_control.actuators.accel < -0.05 and not self.driver_brake_active
    self.model_stop_active = model_action.shouldStop
    self.model_decel_active = model_action.desiredAcceleration < -0.5 and not self.model_stop_active
    self._update_traffic_state()

  def _render(self, rect: rl.Rectangle) -> None:
    """Render HUD elements to the screen."""
    # Draw the header background
    rl.draw_rectangle_gradient_v(
      int(rect.x),
      int(rect.y),
      int(rect.width),
      UI_CONFIG.header_height,
      COLORS.HEADER_GRADIENT_START,
      COLORS.HEADER_GRADIENT_END,
    )

    if self.is_cruise_available:
      self._draw_set_speed(rect)

    self._draw_current_speed(rect)
    self._draw_brake_status(rect)
    self._draw_model_stop_status(rect)
    self._draw_traffic_status(rect)

    button_x = rect.x + rect.width - UI_CONFIG.border_size - UI_CONFIG.button_size
    button_y = rect.y + UI_CONFIG.border_size
    self._exp_button.render(rl.Rectangle(button_x, button_y, UI_CONFIG.button_size, UI_CONFIG.button_size))

  def user_interacting(self) -> bool:
    return self._exp_button.is_pressed

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    """Draw the MAX speed indicator box."""
    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45

    set_speed_rect = rl.Rectangle(x, y, set_speed_width, UI_CONFIG.set_speed_height)
    rl.draw_rectangle_rounded(set_speed_rect, 0.35, 10, COLORS.BLACK_TRANSLUCENT)
    rl.draw_rectangle_rounded_lines_ex(set_speed_rect, 0.35, 10, 6, COLORS.BORDER_TRANSLUCENT)

    max_color = COLORS.GREY
    set_speed_color = COLORS.DARK_GREY
    if self.is_cruise_set:
      set_speed_color = COLORS.WHITE
      if ui_state.status == UIStatus.ENGAGED:
        max_color = COLORS.ENGAGED
      elif ui_state.status == UIStatus.DISENGAGED:
        max_color = COLORS.DISENGAGED
      elif ui_state.status == UIStatus.OVERRIDE:
        max_color = COLORS.OVERRIDE

    max_text = tr("MAX")
    max_text_width = measure_text_cached(self._font_semi_bold, max_text, FONT_SIZES.max_speed).x
    rl.draw_text_ex(
      self._font_semi_bold,
      max_text,
      rl.Vector2(x + (set_speed_width - max_text_width) / 2, y + 27),
      FONT_SIZES.max_speed,
      0,
      max_color,
    )

    set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.set_speed))
    speed_text_width = measure_text_cached(self._font_bold, set_speed_text, FONT_SIZES.set_speed).x
    rl.draw_text_ex(
      self._font_bold,
      set_speed_text,
      rl.Vector2(x + (set_speed_width - speed_text_width) / 2, y + 77),
      FONT_SIZES.set_speed,
      0,
      set_speed_color,
    )

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    """Draw the current vehicle speed and unit."""
    speed_text = str(round(self.speed))
    speed_text_size = measure_text_cached(self._font_bold, speed_text, FONT_SIZES.current_speed)
    speed_pos = rl.Vector2(rect.x + rect.width / 2 - speed_text_size.x / 2, 180 - speed_text_size.y / 2)
    rl.draw_text_ex(self._font_bold, speed_text, speed_pos, FONT_SIZES.current_speed, 0, COLORS.WHITE)

    unit_text = tr("km/h") if ui_state.is_metric else tr("mph")
    unit_text_size = measure_text_cached(self._font_medium, unit_text, FONT_SIZES.speed_unit)
    unit_pos = rl.Vector2(rect.x + rect.width / 2 - unit_text_size.x / 2, 290 - unit_text_size.y / 2)
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, FONT_SIZES.speed_unit, 0, COLORS.WHITE_TRANSLUCENT)

  def _update_traffic_state(self) -> None:
    now = time.monotonic()
    if now - self._last_traffic_read_t < 0.25:
      return
    self._last_traffic_read_t = now

    state = self._params.get("KittTrafficState")
    if not state:
      self.traffic_state = {}
      return

    try:
      if isinstance(state, dict):
        self.traffic_state = state
      elif isinstance(state, bytes):
        self.traffic_state = json.loads(state.decode("utf-8", "replace"))
      else:
        self.traffic_state = json.loads(str(state))
    except (AttributeError, json.JSONDecodeError):
      self.traffic_state = {}

  def _draw_brake_status(self, rect: rl.Rectangle) -> None:
    """Draw KITT brake/decel status when braking is active."""
    if self.driver_brake_active:
      text = tr("DRIVER BRAKE")
      bg_color = COLORS.KITT_BRAKE_BG
    elif self.openpilot_brake_active:
      text = tr("OP BRAKE")
      bg_color = COLORS.KITT_OP_BRAKE_BG
    else:
      return

    badge_rect = rl.Rectangle(
      rect.x + rect.width / 2 - UI_CONFIG.brake_badge_width / 2,
      rect.y + 335,
      UI_CONFIG.brake_badge_width,
      UI_CONFIG.brake_badge_height,
    )
    rl.draw_rectangle_rounded(badge_rect, 0.35, 10, bg_color)
    text_size = measure_text_cached(self._font_semi_bold, text, FONT_SIZES.brake_badge)
    text_pos = rl.Vector2(
      badge_rect.x + (badge_rect.width - text_size.x) / 2,
      badge_rect.y + (badge_rect.height - text_size.y) / 2,
    )
    rl.draw_text_ex(self._font_semi_bold, text, text_pos, FONT_SIZES.brake_badge, 0, COLORS.WHITE)

  def _draw_model_stop_status(self, rect: rl.Rectangle) -> None:
    """Draw monitor-only model stop/decel intent."""
    if self.model_stop_active:
      text = tr("MODEL STOP")
      bg_color = COLORS.KITT_MODEL_STOP_BG
    elif self.model_decel_active:
      text = tr("MODEL DECEL")
      bg_color = COLORS.KITT_MODEL_DECEL_BG
    else:
      return

    badge_rect = rl.Rectangle(
      rect.x + rect.width / 2 - UI_CONFIG.model_badge_width / 2,
      rect.y + 410,
      UI_CONFIG.model_badge_width,
      UI_CONFIG.model_badge_height,
    )
    rl.draw_rectangle_rounded(badge_rect, 0.35, 10, bg_color)
    text_size = measure_text_cached(self._font_semi_bold, text, FONT_SIZES.model_badge)
    text_pos = rl.Vector2(
      badge_rect.x + (badge_rect.width - text_size.x) / 2,
      badge_rect.y + (badge_rect.height - text_size.y) / 2,
    )
    rl.draw_text_ex(self._font_semi_bold, text, text_pos, FONT_SIZES.model_badge, 0, COLORS.WHITE)

  def _draw_traffic_status(self, rect: rl.Rectangle) -> None:
    """Draw monitor-only stop-sign and traffic-light detections."""
    if not self.traffic_state:
      return

    detections = [
      ("stop_sign", tr("STOP SIGN"), COLORS.KITT_STOP_SIGN_BG),
      ("red_light", tr("RED LIGHT"), COLORS.KITT_RED_LIGHT_BG),
      ("yellow_light", tr("YELLOW LIGHT"), COLORS.KITT_YELLOW_LIGHT_BG),
      ("green_light", tr("GREEN LIGHT"), COLORS.KITT_GREEN_LIGHT_BG),
    ]
    active = [(text, color) for key, text, color in detections if self.traffic_state.get(key)]
    if not active:
      return

    count = len(active)
    total_width = count * UI_CONFIG.traffic_badge_width + (count - 1) * UI_CONFIG.traffic_badge_gap
    x = rect.x + rect.width / 2 - total_width / 2
    y = rect.y + 485

    for idx, (text, color) in enumerate(active):
      badge_rect = rl.Rectangle(
        x + idx * (UI_CONFIG.traffic_badge_width + UI_CONFIG.traffic_badge_gap),
        y,
        UI_CONFIG.traffic_badge_width,
        UI_CONFIG.traffic_badge_height,
      )
      rl.draw_rectangle_rounded(badge_rect, 0.32, 10, color)
      text_size = measure_text_cached(self._font_semi_bold, text, FONT_SIZES.traffic_badge)
      text_pos = rl.Vector2(
        badge_rect.x + (badge_rect.width - text_size.x) / 2,
        badge_rect.y + (badge_rect.height - text_size.y) / 2,
      )
      rl.draw_text_ex(self._font_semi_bold, text, text_pos, FONT_SIZES.traffic_badge, 0, COLORS.WHITE)
