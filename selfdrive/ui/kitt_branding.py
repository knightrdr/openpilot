KITT_BRAND_NAME = "KITT openpilot"
KITT_VEHICLE_PROFILE = "2022 Kia Stinger 3.3L RWD"
KITT_DEVICE_PROFILE = "comma 3X"


def kitt_profile_description() -> str:
  return f"Custom KITT fork for {KITT_DEVICE_PROFILE} / {KITT_VEHICLE_PROFILE}."
