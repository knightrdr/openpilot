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

## Voice Control

KITT voice control uses the comma 3X microphone, openpilot's `rawAudioData` stream, offline Vosk speech recognition, and offline Piper text-to-speech.

The voice process is disabled by default. Enable `KITT Voice Commands` in settings, then install the small English Vosk model and Piper voice model on the device:

```bash
cd /data/openpilot
./tools/kitt/install_vosk_model.sh
./tools/kitt/install_piper_voice.sh
```

Initial supported phrases:

- `KITT, show settings`
- `KITT, what's my current speed?`

Spoken replies are routed through `soundd`, so normal openpilot alert sounds keep priority. KITT applies an original voice-style effect to Piper output for a deeper, more synthetic dashboard-computer tone without cloning any actor's voice.

To check whether the required voice models are installed on the device:

```bash
cd /data/openpilot
./tools/kitt/check_models.sh
```

## Traffic Monitor

KITT Traffic Monitor is disabled by default. Enable `KITT Traffic Monitor` in settings, then install the object detector model on the device:

```bash
cd /data/openpilot
./tools/kitt/install_traffic_model.sh
```

The monitor uses a COCO object detector for stop signs and traffic lights, then classifies traffic-light crops as red, yellow, or green by color. It only displays monitor indicators; it does not control braking or steering.

## Safety Rules

Treat changes to steering, braking, acceleration, CAN messages, safety hooks, and driver monitoring as safety-critical. Keep those changes isolated in focused commits and test them before road use.

## First Customization Targets

- KITT branding and UI text. The Software settings page identifies the fork as `KITT openpilot` and shows the comma 3X / Kia Stinger profile.
- Onroad KITT brake status badge showing driver brake pedal activity and openpilot-requested braking/deceleration.
- Monitor-only model stop/decel badge showing `modelV2.action.shouldStop` and negative model desired acceleration without changing control behavior.
- A feature flag namespace for future KITT options.
- Vehicle-specific configuration for the Kia Stinger.
- Custom alerts and sounds.
