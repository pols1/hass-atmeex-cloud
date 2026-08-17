# Atmeex Cloud Integration for Home Assistant

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
*   Optional humidifier control (if supported by the device).
*   **Climate Presets**: Support for Auto and Sleep modes.
*   **Optional Cool Mode**: You can optionally enable cooling mode (`HVACMode.COOL`) from the integration settings if your climate complex supports it.
*   **Sensors**: CO₂ (`co2_ppm`), indoor temperature and outdoor temperature.
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
You can configure the integration dynamically after setup:
1. Go to Settings → Devices & Services → Atmeex Cloud.
2. Click **Configure**.
3. Toggle whether you want to expose the **CO2 Sensor** or enable **Cool Mode**.

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
| **sensor** | `sensor.brizer_bedroom_co2` | CO₂ level, ppm (can be disabled in options) |
| **sensor** | `sensor.brizer_bedroom_indoor_temperature` | Room temperature |
| **sensor** | `sensor.brizer_bedroom_outdoor_temperature` | Outdoor temperature |

Online/offline state is not a separate entity: it drives the `available` property of the
climate entity, so an offline device greys out on the card.

`select.py` and `fan.py` are present in the repository but **not** enabled — `PLATFORMS` in
`const.py` currently lists only `CLIMATE` and `SENSOR`. Enable them there if you want the
damper position and humidification stage as standalone entities.

## Local channel (experimental, read-only for now)

The brizers do not listen on any port — verified by a full TCP scan (1–10000, every port
`closed`) and UDP probing. They are pure outbound clients: each opens one TCP connection to
`ws.iot.atmeex.com:3001` and talks plain JSON over it, with no TLS. So local access means
being on the receiving end of that connection, not connecting to the device.

With **Local channel** enabled, Home Assistant listens on port 3001 and **forwards
everything to the Atmeex cloud unchanged** — the vendor app keeps working, and the
integration additionally reads the live stream. That stream carries a `state` frame every
few seconds, so entities update in near real time instead of waiting for the 30-second
cloud poll, and it exposes room humidity and the humidifier's water-tank flag.

If the cloud is unreachable, the channel answers the device itself (the device stays silent
until the server acknowledges its `hello` with a time sync), so local readings survive a
vendor outage.

### Redirecting the traffic

The integration cannot redirect traffic to itself — that is a network change you make once:

* **DNS override (simplest):** point `ws.iot.atmeex.com` at your Home Assistant IP on your
  router, Pi-hole or AdGuard Home.
* **Or per-device NAT**, if you would rather not touch DNS for the whole network. On
  MikroTik, for one brizer:

  ```
  /ip firewall nat add chain=dstnat protocol=tcp src-address=<brizer IP> \
      dst-address=<cloud IP> dst-port=3001 \
      action=dst-nat to-addresses=<HA IP> to-ports=3001 place-before=0
  /ip firewall nat add chain=srcnat protocol=tcp src-address=<brizer IP> \
      dst-address=<HA IP> dst-port=3001 action=masquerade place-before=0
  ```

  The second rule is required when the brizer and Home Assistant share a bridge: without
  it the reply comes from the wrong source address and the device drops the session.

**Add a failsafe.** While the redirect is in place, a Home Assistant outage cuts the
brizers off from the cloud as well. On MikroTik, `/tool netwatch` can watch the port and
disable the rule when Home Assistant stops answering, so the devices fall back to the
vendor on their own.

A power cycle of the brizer is the reliable way to make it reconnect through the new path —
clearing connection tracking on the router does not close the socket on the device.

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
