from openpilot.selfdrive.kitt.voiced import command_from_phrase, normalize_phrase


def test_normalize_phrase():
  assert normalize_phrase("K.I.T.T., show settings") == "kitt show settings"
  assert normalize_phrase("  KITT,   what's my current speed?  ") == "kitt what s my current speed"


def test_show_settings_commands():
  assert command_from_phrase("KITT show settings").action == "show_settings"
  assert command_from_phrase("show settings").action == "show_settings"
  assert command_from_phrase("KITT open settings").action == "show_settings"


def test_current_speed_commands():
  assert command_from_phrase("KITT what is my current speed").action == "current_speed"
  assert command_from_phrase("KITT what's my current speed").action == "current_speed"
  assert command_from_phrase("whats my current speed").action == "current_speed"
  assert command_from_phrase("current speed").action == "current_speed"


def test_unknown_command():
  assert command_from_phrase("KITT accelerate") is None
