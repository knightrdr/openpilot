# KITT Openpilot Fork

KITT is a private feature fork of comma.ai openpilot for a comma 3X installed in a 2022 Kia Stinger 3.3L RWD.

## Baseline

- Upstream: `https://github.com/commaai/openpilot.git`
- Starting tag: `v0.11.0`
- Development branch: `kitt/c3x-main`
- Public install branch: `kitt-install`

## Install Strategy

KITT uses a private-development/public-install workflow:

1. Build and test custom features on `kitt/c3x-main`.
2. Keep private or experimental work off the install branch.
3. Merge only install-ready commits into `kitt-install`.
4. Push `kitt-install` to a public GitHub fork or public mirror for comma 3X installation.

This keeps daily development private while preserving a simple install path for the device.

Expected comma 3X custom software URL:

```text
https://installer.comma.ai/knightrdr/kitt-install
```

## Safety Rules

Treat changes to steering, braking, acceleration, CAN messages, safety hooks, and driver monitoring as safety-critical. Keep those changes isolated in focused commits and test them before road use.

## First Customization Targets

- KITT branding and UI text. The Software settings page identifies the fork as `KITT openpilot` and shows the comma 3X / Kia Stinger profile.
- A feature flag namespace for future KITT options.
- Vehicle-specific configuration for the Kia Stinger.
- Custom alerts and sounds.
