# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [0.5.10] — unreleased

Compatibility fixes for Home Assistant 2026.x, and a move to this repository.

### Repository
- The project now lives in `pols1/hass-atmeex-cloud`, a standalone repository with a clean
  history. The previous location, `pols1/atmeex_hacs`, was a GitHub fork and has been
  archived. Re-add the custom repository in HACS to keep receiving updates.

### Added
- `strings.json` plus English and Russian translations — the config and options dialogs
  previously showed raw keys such as `cannot_connect`.
- CI: hassfest and HACS validation run on every push.
- `LICENSE` (MIT). The README had always claimed MIT, but no license file was committed.
- `integration_type: hub` in the manifest — now a required key.

### Fixed
- **Integration options could not be opened at all.** `AtmeexOptionsFlowHandler.__init__`
  assigned `self.config_entry`, but Home Assistant made `OptionsFlow.config_entry` a
  read-only property (deprecated in 2024.11, setter removed in later releases). Pressing
  **Configure** raised `AttributeError: property 'config_entry' … has no setter` and the
  dialog never rendered. The handler now relies on the core-provided attribute.
- **Cloud outages reported as `Unexpected error: ` with no text.** `asyncio.TimeoutError`
  has an empty `str()`, so every timeout surfaced as a truncated message in the UI and in
  the log. Timeouts and `aiohttp.ClientError` are now caught explicitly and reported with
  a readable reason; the generic branch includes the exception class name.

### Changed
- `manifest.json`: dropped `aiohttp` and `async-timeout` from `requirements`. Both ship
  with Home Assistant core, and declaring them makes hassfest fail the manifest check.
- `AtmeexApi` exposes a read-only `base_url` property, used in error messages.
- The CO₂ sensor uses `UnitOfRatio.PARTS_PER_MILLION` instead of the deprecated
  `CONCENTRATION_PARTS_PER_MILLION`, which Home Assistant warned about on every start and
  removes in 2027.8. The new enum landed in 2026.7, so `hacs.json` now declares that as
  the minimum version.

### Notes
- Verified against Home Assistant 2026.8.2 (Python 3.14, HAOS 18.2).

## [0.5.9] — 2026-03-24

### Fixed
- Icons moved to `brand/` to comply with the Home Assistant 2026.3 Local Brands layout.
- Icon and logo also copied into the `custom_components` folder so the UI renders them.

## [0.5.5] — 2026-03-23

### Fixed
- Off-by-one bug in `fan_mode` indexing (UI shows 1–7, the API counts from 0).

### Added
- Proper 256×256 Atmeex logo for HACS.

## [0.5.4] — 2026-03-20

### Fixed
- Removed a malformed `icon.png` that caused HTTP 500 in the Home Assistant UI.

## [0.5.2] — 2026-03-20

### Added
- Optional **Cool mode** toggle in the config flow, for complexes that support cooling.

## [0.5.1] — 2026-03-20

### Fixed
- Removed a faulty `PRESET_AUTO` import that raised `ImportError` on startup.

## [0.5.0] — 2026-03-20

### Added
- Sensor platform: CO₂, indoor temperature, outdoor temperature.
- HVAC presets (Auto, Sleep).
- Re-authentication flow in the config flow for expired cloud tokens.

## [0.4.1] — 2025-12-22

### Changed
- API client updates.

## [0.4.0] — 2025-12-12

First release of the rewritten integration: own `api.py` against the current Atmeex Cloud
REST API (`https://api.iot.atmeex.com`), JWT access token with `refresh_token` re-auth,
Home Assistant's shared aiohttp session, and a `DataUpdateCoordinator` with a 30-second
poll interval.

## Earlier

`beta-0.2.2` … `0.3.9.b*` (2025-10 … 2025-12) — early iterations, before the integration
was rewritten against the current Atmeex Cloud API. No changelog was kept.
