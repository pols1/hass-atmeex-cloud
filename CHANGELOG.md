# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## Unreleased

### Fixed
- **Every entity name repeated the device name.** `has_entity_name` tells Home Assistant
  to put the device name in front itself, so the name property must carry only the part
  that identifies the reading — all three platforms baked the device name in as well.
  On a unit called "Бризер спальня" that produced "Бризер спальня Бризер спальня Humidity".
  Climate, being the device's primary feature, now has no name of its own, which is the
  documented way to say "call me after the device". Entity IDs are unchanged.
- **The outdoor temperature sensor could never have a value.** A7 does not report
  `temp_out` at all — not locally, not through the cloud — so the entity sat at `unknown`
  for ever. It is now created only where a reading actually arrives, the same way the CO₂
  sensor is.
- **"Indoor Temperature" was showing the intake air, not the room.** It reads `temp_in`,
  which is the air entering the unit; the room temperature lives in `temp_room` and is what
  the climate entity shows as current. The sensor is renamed to Intake Temperature and a
  proper Room Temperature sensor is added alongside it. Entity IDs and history are
  unaffected — only the displayed name changes.
- **Local frames were starving the cloud poll.** Pushing local data with
  `async_set_updated_data` also resets the coordinator's poll timer, and frames arrive
  every few seconds against a thirty-second interval — so with the local channel connected
  the cloud was never polled. Outdoor temperature, the online flag and settings changed
  from the vendor app would have quietly frozen. Data is now assigned and listeners
  notified without touching the schedule. Confirmed on a live install: polls resumed at
  exactly thirty-second spacing.

## [0.6.0] — 2026-08-19

### Added
- **Per-unit feature detection.** A7 ships in seven trims — Simple and Flow have neither
  humidifier nor CO₂ sensor, Start has the humidifier only, BabyCare and Forever have both —
  and the API never says which one you own (`model` is always `A7`). The integration now
  derives it from the readings: a missing CO₂ sensor reports exactly 0 ppm for ever, which
  air never does, and room humidity only arrives on units that actually humidify. So an
  always-zero CO₂ entity is no longer created on units without the sensor, and the humidity
  slider no longer appears where there is nothing to humidify. `CO₂ sensor` and `Humidifier`
  options are tri-state: auto (default), on, off.
- **Room humidity sensor**, created on units that have the humidifier. The field was in the
  cloud API all along but no entity was ever made for it.
- **Local channel (read-only).** Home Assistant can accept the brizers' own outbound
  connection and read their live stream, while forwarding it unchanged to the Atmeex
  cloud so the vendor app keeps working. Entities then update every few seconds instead
  of waiting for the 30-second cloud poll, and the stream carries room humidity and the
  humidifier water-tank flag. Off by default; enable it in the integration options and
  redirect the device channel to Home Assistant (see README). A NAT rule matching the
  device address is the reliable way: on the install this was built against, the brizers
  were never seen asking the router to resolve the channel hostname, so a DNS override
  alone never reached them.
- **Local command writing** with a selectable path (`Command path` option):
  `cloud_first` (default) sends through the cloud and falls back to the local channel when
  the cloud fails, `local_first` talks to the device directly, `cloud_only` never uses the
  local channel. The default is cloud-first on purpose: the vendor app reads state from the
  cloud, so writing past it would let the two views drift apart.

  `set_pwr_on`, `set_fan_speed` and `set_cool_mode` were observed on the wire, and
  `set_temp_room` was confirmed against a live device. The names for damper position and
  humidification stage follow the same pattern but have not been exercised yet; they are
  marked as inferred in the code.
- First tests in the repository: `tests/test_local_channel.py`, running on plain Python
  (no Home Assistant needed) against real captured frames, wired into CI.

### Changed
- The `enable_co2` option is replaced by tri-state `co2_sensor`. It used to default to on,
  which created a CO₂ entity stuck at zero on every unit without the sensor.
- `climate` no longer concludes that a humidifier exists just because a `hum_stg` key is
  present in the payload — that key arrives from trims without a humidifier too.

### Fixed
- **The standalone fallback could strand a device.** If the cloud was unreachable when a
  brizer connected, the channel answered it itself — and never retried the upstream. Since
  a brizer holds one connection for days, the device stayed invisible to the vendor cloud
  long after the outage ended: a day and a half, on the install this was found on. The
  upstream is now probed once a minute and the session dropped when the cloud returns, so
  the device reconnects through a proxied one.
- **Availability flapped between local and cloud data**, because the cloud marks a device
  offline while it has no telemetry for it. Any automation triggering on "came back from
  unavailable" then fired every few seconds. A device holding a live local connection is
  now treated as online regardless of what the cloud reports.
- Identical telemetry frames no longer publish a coordinator update; only the timestamp
  usually differs between them.
- **The channel connected to itself** once the device hostname was redirected to Home
  Assistant: the DNS override applies to Home Assistant too, so the upstream connection
  looped back in, was taken for a new device, and opened another upstream — hundreds of
  connections per second on the live install. The cloud address is now resolved via the
  REST API hostname when the device hostname points at us, and inbound connections from
  our own outbound socket are refused. The outgoing socket is bound before connecting so
  the guard cannot lose a race against the accept.
- The upstream is opened on the first frame rather than on every inbound socket. A health
  probe against the channel — such as the netwatch guard that protects it — used to make
  Home Assistant dial the vendor twice a minute, and the probe could outlast its own
  timeout, which made the guard pull the redirect from a healthy service.

### Protocol notes
The device channel is plain JSON over TCP on port 3001, without TLS. Objects arrive
concatenated with no delimiter and no length prefix. The device stays silent until the
server answers its `hello` with a time sync — that is why a naive echo of its own
settings gets no response. Commands are `{"id":"<MAC>:0","cmd":{"set_…": value}}`.

## [0.5.10] — 2026-08-17

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
