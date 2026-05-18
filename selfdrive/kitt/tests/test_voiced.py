from openpilot.selfdrive.kitt.voiced import command_from_phrase, normalize_phrase


def test_normalize_phrase():
  assert normalize_phrase("K.I.T.T., bookmark this") == "kitt bookmark this"
  assert normalize_phrase("  KITT   status  ") == "kitt status"


def test_bookmark_commands():
  assert command_from_phrase("KITT bookmark this").action == "bookmark"
  assert command_from_phrase("bookmark").action == "bookmark"
  assert command_from_phrase("KITT save this").action == "bookmark"


def test_status_commands():
  assert command_from_phrase("KITT status").action == "status"
  assert command_from_phrase("what is your status").action == "status"


def test_unknown_command():
  assert command_from_phrase("KITT accelerate") is None
