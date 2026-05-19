import site
from pathlib import Path


KITT_SITE_PACKAGES = Path("/data/openpilot/kitt/site-packages")


def add_kitt_site_packages() -> None:
  if KITT_SITE_PACKAGES.exists():
    site.addsitedir(str(KITT_SITE_PACKAGES))
