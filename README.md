# Atmeex Cloud Integration for Home Assistant

*[Русская версия](README.ru.md)*

## Overview

Atmeex Cloud is a custom integration for [Home Assistant](https://www.home-assistant.io/) that connects your Atmeex (AirNanny) ventilation devices to the Home Assistant ecosystem.
It uses the official Atmeex Cloud REST API (https://api.iot.atmeex.com) to provide reliable control and monitoring of your brizers directly from Home Assistant dashboards and automations.

🧩 Written and maintained by Sergei Polunovskii: an own API client against the current
Atmeex Cloud REST API, built for current Home Assistant releases.

## Features
*   Auto-discovery of all devices linked to your Atmeex Cloud account.
*   Power on/off control.
*   Fan speed control (1–7).
*   Operation modes: ventilation, recirculation, mixed, and fresh-air intake.
*   Target temperature control (°C).
*   Humidifier control on the trims that have one, detected automatically.
*   **Climate Presets**: Support for Auto and Sleep modes.
*   **Optional Cool Mode**: You can optionally enable cooling mode (`HVACMode.COOL`) from the integration settings if your climate complex supports it.
*   **Sensors**: indoor and outdoor temperature always; CO₂ and room humidity on the trims
    that actually have those parts — the integration works out which (see Options).
*   **Config Flow Re-authentication**: Seamlessly handles expired cloud tokens by prompting re-login natively in HA.
*   Online/offline status displayed directly on the climate card.
*   Clean asynchronous I/O using Home Assistant’s shared aiohttp client session.

## Installation

Option 1 — via HACS (recommended)
1. Open HACS → Integrations → Custom repositories.
2. Add this repository:

https://github.com/pols1/hass-atmeex-cloud

Choose **Integration** as the repository type.

3. Find Atmeex Cloud in HACS and click Install.
4. Restart Home Assistant.

Option 2 — manual installation
1. Copy the folder:

`custom_components/atmeex_cloud`

into your Home Assistant configuration directory:

`/config/custom_components/`

2. Restart Home Assistant.

## Configuration
1. Go to Settings → Devices & Services → Add Integration.
2. Search for Atmeex Cloud.
3. Enter your Atmeex account credentials (email and password).
4. After successful login, all connected devices will appear automatically.

The integration uses an internal update coordinator with a 30-second polling interval.

## Options

Settings → Devices & Services → Atmeex Cloud → **Configure**.

### Which parts your unit actually has

A7 ships in seven trims and they differ inside:

| Trim | Humidifier | CO₂ sensor |
|---|---|---|
| Simple, Flow | — | — |
| Start | yes | — |
| BabyCare, Forever (3 colours) | yes | yes |

Neither the cloud API nor the device's own greeting reports the trim — `model` is always
`A7`. So the integration works it out from the readings, and **CO₂ sensor** and
**Humidifier** default to *auto*:

* **CO₂** — a missing sensor reports exactly `0 ppm` for ever. Air never does: outdoors is
  around 420 ppm, indoors 500–1500. Measured on a live Start: 1952 telemetry frames, all
  zero. So a persistent zero means there is no sensor, and no always-zero entity is created.
* **Humidifier** — room humidity only arrives on units that have one. The presence of the
  `hum_stg` field proves nothing: it is sent by trims that cannot humidify at all.

Detection only ever adds a part, never takes one away, so a momentary zero from a failing
sensor will not delete an entity together with its history. Set the option to *on* or *off*
to override — for a faulty sensor, or a non-standard build.

**Cool mode** stays manual: the A7 line does not cool, the option exists for other Atmeex
climate units.

### Command path

* `cloud_first` (default) — commands go through the cloud, falling back to the local channel
  when the cloud refuses them and the device is connected locally.
* `local_first` — straight to the device, using the cloud only when the device is not
  connected to Home Assistant.
* `cloud_only` — the local channel is never used for writing.

Cloud-first is the default for consistency rather than reliability: the vendor app reads
state from the cloud, so writing past it would let the two views drift apart.

## Compatibility

Tested against **Home Assistant 2026.8** (Python 3.14, Home Assistant OS); `hacs.json`
declares 2026.7 as the minimum (that is when `UnitOfRatio` landed). The integration follows current core APIs: shared aiohttp
session, `DataUpdateCoordinator`, config-entry re-auth, and an options flow that takes
`config_entry` from the core rather than storing it itself. See [CHANGELOG.md](CHANGELOG.md)
for version history.

The setup and options dialogs are translated — English and Russian ship in
`custom_components/atmeex_cloud/translations/`.

Every push is checked by [hassfest](https://developers.home-assistant.io/blog/2020/04/16/hassfest)
and the HACS validation action; see [.github/workflows/validate.yml](.github/workflows/validate.yml).

## Entities

Two platforms are loaded: `climate` and `sensor`.

| Entity type | Example | Description |
|---|---|---|
| **climate** | `climate.brizer_bedroom` | Main entity: on/off, fan speed 1–7, target temperature, presets (Auto, Sleep), humidifier slider, optional Cool mode |
| **sensor** | `sensor.brizer_bedroom_indoor_temperature` | Room temperature |
| **sensor** | `sensor.brizer_bedroom_outdoor_temperature` | Outdoor temperature |
| **sensor** | `sensor.brizer_bedroom_co2` | CO₂ level, ppm — only on trims with the sensor |
| **sensor** | `sensor.brizer_bedroom_humidity` | Room humidity, % — only on trims with the humidifier |

Online/offline state is not a separate entity: it drives the `available` property of the
climate entity, so an offline device greys out on the card.

`select.py` and `fan.py` are present in the repository but **not** enabled — `PLATFORMS` in
`const.py` currently lists only `CLIMATE` and `SENSOR`. Enable them there if you want the
damper position and humidification stage as standalone entities.

## Local channel

The brizers do not listen on any port — verified by a full TCP scan (1–10000, every port
`closed`) and UDP probing. They are pure outbound clients: each opens one TCP connection to
`ws.iot.atmeex.com:3001` and talks plain JSON over it, with no TLS. So local access means
being on the receiving end of that connection, not connecting to the device.

With **Local channel** enabled, Home Assistant listens on port 3001 and **forwards
everything to the Atmeex cloud unchanged** — the vendor app keeps working, and the
integration additionally reads the live stream. That stream carries a `state` frame every
few seconds, so entities update in near real time instead of waiting for the 30-second
cloud poll, and it exposes room humidity and the humidifier's water-tank flag.

If the cloud is unreachable, the channel answers the device itself — the device stays silent
until the server acknowledges its `hello` with a time sync — so readings keep coming through
a vendor outage. Once the cloud returns, the session is dropped so the device reconnects
through a proxied one.

Commands can also travel this way; see **Command path** under Options. Only `set_pwr_on`,
`set_fan_speed` and `set_cool_mode` were observed on the wire during the capture — the names
for damper, temperature and humidity stage are inferred from the setpoint fields and are
marked as such in the code.

### Redirecting the traffic

The integration cannot redirect traffic to itself — that is a network change you make once.
Two ways, and on the hardware this was built against only the first one actually worked.

**By address (recommended).** A NAT rule catches the device wherever it points, because it
matches the destination address rather than a name. On MikroTik, per brizer:

```
/ip firewall nat add chain=dstnat protocol=tcp src-address=<BRIZER_IP> \
    dst-address=<CLOUD_IP> dst-port=3001 \
    action=dst-nat to-addresses=<HA_IP> to-ports=3001 \
    comment="atmeex-local" place-before=0
/ip firewall nat add chain=srcnat protocol=tcp src-address=<BRIZER_IP> \
    dst-address=<HA_IP> dst-port=3001 action=masquerade comment="atmeex-local"
```

The second rule is required when the brizer and Home Assistant share a bridge: without it
the reply comes from the wrong source address and the device drops the session.

**By name.** A static DNS record pointing `ws.iot.atmeex.com` at Home Assistant is simpler
and works on any router, Pi-hole or AdGuard Home. It did not work here: with the record
live and enabled, a power-cycled brizer still went straight to the vendor. Why is not
settled — the device does send DNS queries to the router, but we never captured it asking
for this particular name, so it may be answering from a cached address. Try it if you
like, then power-cycle a device and check where it lands; if it still reaches the vendor,
use the NAT rule.

### The failsafe

Whichever redirect you choose, guard it. While it is in place, Home Assistant being down
takes the brizers off the cloud as well, and they stay off for as long as they hold the
connection — days. That is not hypothetical: it happened here for a day and a half before
the guard existed.

```
/tool netwatch add name=atmeex-ha-guard type=tcp-conn host=<HA_IP> port=3001 \
    interval=30s timeout=3s \
    up-script="/ip firewall nat enable [find comment~\"atmeex-local\"]" \
    down-script="/ip firewall nat disable [find comment~\"atmeex-local\"]"
```

Disable rather than remove, so recovery is instant. Verified end to end on the live setup:
the channel was stopped, the guard saw it within seven seconds, the rules went to disabled,
and both brizers held direct sessions to the vendor throughout; restarting the channel put
everything back just as quickly.

**Keep the probe cheap.** The guard opens a TCP connection to the local channel every thirty
seconds. If answering that probe makes Home Assistant do real work — as it did while the
channel dialled the cloud on every inbound socket — the probe can outlast its own timeout,
and the guard will declare a healthy service dead. A watchman who makes the owner run to the
cellar on every knock eventually decides nobody is home.

**RouterOS caveat.** `netwatch` with `type=tcp-conn` has no "N consecutive failures" setting
— the thresholds (`thr-loss-count` and friends) apply to ICMP probes only, `thr-tcp-conn-time`
is a latency ceiling on a *successful* connect, and `start-delay` covers startup rather than
later transitions. The first failed probe therefore trips the guard, which is why a Home
Assistant restart takes the redirect down. Soften it by re-checking inside the down-script:
wait out a typical restart, probe the port again, and disable the rules only if it is still
dead. The cost is honest — during a genuine outage the redirect now survives a couple of
minutes longer, so a device reconnecting in that window fails once before falling back.

**Restarting Home Assistant costs you the channel for a while.** The guard polls every
thirty seconds, so a restart that takes a couple of minutes is long enough for it to
disable the redirect — and the devices, having reconnected straight to the vendor, stay
there until their next reconnect, which can be a day away. Nothing breaks; the local
channel is simply idle in the meantime. Plan updates accordingly, or power-cycle a device
if you want it back immediately.

### What to expect during the switch

* Devices change over on their next reconnect, not immediately: each holds a single
  connection for days. A power cycle forces it; otherwise just wait.
* Removing an old redirect does not break an existing session either — connection tracking
  keeps the translation until the device reconnects.
* While the redirect is in place the vendor still sees the devices online, because Home
  Assistant forwards their traffic unchanged.
* A DNS override, if you use one, also applies to Home Assistant itself. The integration
  resolves the cloud address via the REST API hostname in that case and refuses connections
  arriving from its own outbound socket — otherwise the channel would connect to itself,
  take the result for a new device, and open another upstream, endlessly.

## Humidifier Control

If your device supports a humidifier, a humidity slider will appear under the climate card.
It has four fixed stages, automatically snapping to the nearest level:

| Slider position | Mode    |
|-----------------|---------|
| 0%              | Off     |
| 33%             | Stage 1 |
| 66%             | Stage 2 |
| 100%            | Stage 3 |

Intermediate values (e.g. 25%, 80%) are automatically rounded to the nearest valid stage.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| Integration fails to load | Old or corrupted files | Reinstall from HACS |
| Auth failed during setup | Wrong credentials | Verify your Atmeex Cloud email and password |
| Temperature shows −100 °C | The API did not return room temperature | Wait for the next update or restart Home Assistant |
| Second brizer missing | The API returned `null` for device condition | Fixed in recent releases |
| Entities go `unavailable`, log shows timeouts | The Atmeex cloud itself is down — the integration only talks to `api.iot.atmeex.com` | Check the cloud, not the integration: `curl -m 10 https://api.iot.atmeex.com/`. Since 0.5.10 the reason is spelled out in the UI instead of an empty `Unexpected error:` |
| **Configure** button does nothing | Fixed in 0.5.10 (options flow was incompatible with recent Home Assistant) | Update the integration |

You can check detailed logs in:
Settings → System → Logs → custom_components.atmeex_cloud

## Development

### Local setup

`git clone https://github.com/pols1/hass-atmeex-cloud.git`
`cd hass-atmeex-cloud`

All requests use Home Assistant’s shared async session (async_get_clientsession(hass)), ensuring clean resource management and no unclosed sessions.

### Tests

The local-channel tests need no Home Assistant install — `local_channel.py` deliberately
depends on nothing but the standard library, and the fixtures are real frames captured
from an A7:

```
python3 -m unittest discover -s tests -v
```

They run in CI on every push. Everything else (cloud API, entities, config flow) is still
verified against a live Home Assistant instance; `pytest-homeassistant-custom-component`
coverage for those parts is not written yet.

### Releasing a new version
1. Update the `version` field in `manifest.json`.
2. Add the release section to `CHANGELOG.md`.
3. Commit and push.
4. Tag and push the tag:

`git tag -a v0.5.10 -m "Release 0.5.10"`
`git push --tags`

5. Create a GitHub Release — HACS offers updates based on published releases, so an
   unreleased commit on `main` will not reach users.

## Credits
* 🧠 Development: [Sergei Polunovskii](https://github.com/pols1)
* 🌐 API & platform: [Atmeex / AirNanny Cloud](https://api.iot.atmeex.com/)
* 🧩 Framework: [Home Assistant](https://www.home-assistant.io/)

## License

Distributed under the [MIT License](LICENSE) — see the `LICENSE` file.
