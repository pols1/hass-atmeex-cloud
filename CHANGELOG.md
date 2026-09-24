# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [0.8.2] — 2026-09-24

### Fixed
- **0.8.1 cut healthy sessions.** Its watchdog treated two minutes of silence from the
  cloud as a dead link. The threshold came from a captured session where the longest such
  gap was 8.1 s — but that capture was made of short connections, in which the cloud speaks
  right after connect. On a live long-lived session the cloud stayed silent for over two
  minutes at a time, so the watchdog dropped the device every few minutes. Silence is now
  only a distant backstop (15 minutes).
- **A dead link is detected by what the cloud knows, not by its silence.** Every state frame
  carries a timestamp set by the unit itself, and the cloud stores that same timestamp. When
  a unit is on the local channel and its frames keep arriving while the cloud's copy stops
  advancing — more than five minutes apart — the channel is not passing the stream on. The
  session is dropped with a warning and the unit reconnects through a fresh link. This is
  the comparison that found the original fault by hand.

## [0.8.1] — 2026-09-24

### Fixed
- **The local channel could keep a unit to itself while its link to the cloud was dead.**
  Found on a live install: a bedroom unit ran for two days against its schedule while Home
  Assistant and the vendor both believed it was off. The channel had lost its connection to
  the cloud without noticing — it went on serving the device and feeding readings to Home
  Assistant, so nothing looked wrong, but the cloud no longer saw the unit and commands from
  it, the schedule included, went nowhere. Not one line of log said so. The channel now
  watches for silence from the cloud: the cloud polls each device constantly (in a captured
  session the longest gap between its frames was 8.1 s across 66 connections), so two
  minutes without a single byte means the link is gone. The device session is then dropped
  with a warning naming the reason, and the unit reconnects through a fresh link. A clean
  close by the cloud is treated the same way — before, it left the same silent half-dead
  state.
- Diagnostics report, per device, how long ago a frame arrived **from the cloud**. A growing
  number is the symptom above, visible before anyone notices a schedule was skipped.

## [0.8.0] — 2026-09-19

Live updates from the vendor's cloud WebSocket with nothing to set up on the network; the
integration no longer reloads itself every three hours; and commands over the local
channel finally reach the device.

### Added
- **Cloud push.** The integration now keeps the same live connection to the Atmeex cloud
  that the vendor app uses (`wss://ws.iot.atmeex.com`). Readings arrive every few seconds
  instead of every 30, and a change made in the app — or by Home Assistant itself — shows
  up within a second. Nothing to configure on the network; on by default, can be turned off
  in the options (`Cloud push`). Polling stays on as a fallback, and a device heard from
  over push in the last minute is treated as online whatever the cloud's `online` flag
  says. The endpoint is not in the vendor's Swagger; how it behaves was measured over a
  three-hour recording: authorisation by the `Authorization` header only, the token
  checked at the handshake only, a `{"type":"unauthorized"}` frame for an expired one, no
  pings from the server, and occasional stalls and drops without a close frame. The
  client uses aiohttp's heartbeat to notice a hung connection, reconnects with backoff,
  and asks for a fresh token when the server rejects one.
- **The command path switches without a reload.** Changing `Command path` in the options
  now applies at once. Before, every option change reloaded the integration, which also
  closed the local channel — the very connection `local_first` needs.

### Fixed
- **The integration reloaded itself every three hours.** Home Assistant calls a config
  entry's update listener on any change to the entry, including saving refreshed tokens,
  and the listener reloaded unconditionally. The access token lives three hours, so every
  2 h 59 min all entities went `unavailable` for a fraction of a second, the local channel
  dropped its device connections, and automations triggered by "came back from
  unavailable" fired and sent commands to the cloud. Found by matching the entry's
  `modified_at` against the moment of such a blip, then seen repeating at the same
  interval for a day. The listener now reloads only when an option changes, other than the
  command path, or when a newly detected unit feature needs new entities. An option missing
  from the stored entry counts as its default, so the first save after an upgrade, which
  writes every field, does not trigger a reload by itself. Confirmed on the live install:
  sixteen hours and several token refreshes without a single blip.
- **Commands over the local channel were never executed.** The cloud ends every frame it
  sends to the device with a newline; the device writes to the cloud with no separator at
  all, and the protocol notes had generalised that to both directions. The channel sent its
  commands without the newline, and the unit ignored them — `set_damp_pos` was seen being
  refused over the local channel while the byte-identical cloud frame worked. Every frame
  now ends with a newline and goes out on its own, and `set_damp_pos` works locally on a
  live unit. The same missing newline was in the channel's reply to the device's `hello`, so
  its standalone mode (answering the device while the cloud is down) most likely never woke
  a device either; that is fixed too, but has not been seen through a real outage yet.
- **Humidification could not be switched off from Home Assistant.** Stages 0–3 are shown as
  0/33/66/100 %, but the climate entity kept Home Assistant's default humidity range of
  30–99, so a target of 0 was rejected before it reached the integration.

### Changed
- `iot_class` is now `cloud_push`.
- The local channel no longer asks for `get_setp`/`get_state` after a command: the cloud does
  not either, and the unit reports its setpoints in reply to a command and its state every
  few seconds anyway.
- With debug logging on, the local channel logs the cloud's commands to the device (polling
  excluded) — that is how the real command names were captured.

## [0.7.0] — 2026-09-18

A diagnostics download to attach to issues, and debug logs that no longer point at the
owner, the device or the home network.

### Added
- **Diagnostics download.** The integration and each device now offer Home Assistant's
  "Download diagnostics": config entry data and options, coordinator status, the device
  payload from the cloud and, when the local channel is on, what it currently holds per
  device. Meant to be attached to issues instead of a debug log. Credentials (email,
  password, access and refresh token) are redacted, and so is anything that points at a
  person or a place: `owner_id`, `user_id`, `phone`, the device MAC and `network_name` —
  the Wi-Fi network the unit is connected to. The local channel keys its data by MAC, so
  in the download it is keyed by the cloud device id instead.

### Security
- **Debug logs no longer show the owner, the device MAC or the Wi-Fi network.** 0.6.2 hid
  credentials; the `/devices` response logged at debug level still carried `owner_id`, the
  MAC and `network_name`. These — plus `user_id` and `phone` — are now redacted in logs the
  same way as in diagnostics, and a test keeps the two lists from drifting apart. The local
  channel's own debug messages still name devices by MAC; that is left as is on purpose,
  since matching a device to its connection is what those messages are for.

## [0.6.2] — 2026-09-18

A security fix: debug logs no longer carry the account's tokens.

### Security
- **Debug logging wrote live tokens into home-assistant.log.** With debug enabled for the
  integration — the usual first step when filing a bug — the raw `/auth/signin` response
  was logged for both the password sign-in and the refresh-token path, `access_token` and
  `refresh_token` included, so a log pasted into an issue handed over the account. Every
  response body the API client logs or puts into an error message now goes through
  redaction first: token, password and email values become `**REDACTED**`, keys and other
  fields stay, so the log still shows what the server sent. The "missing tokens" error no
  longer prints the one token that did arrive. If you have shared a debug log before,
  treat the tokens in it as exposed and change the Atmeex account password (whether the
  cloud also revokes tokens issued before the change is not documented).

## [0.6.1] — 2026-08-20

Four fixes found by looking at what the entities actually showed on a live install,
the day after 0.6.0 shipped.

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
