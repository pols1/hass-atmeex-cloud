"""Диагностика интеграции: то, что HA отдаёт кнопкой «Скачать диагностику».

Файл диагностики прикладывают к issue, поэтому всё, что указывает на аккаунт,
человека или место, закрыто. Набор полей взят из OpenAPI облака (схемы Device,
DeviceCondition, User): кроме учётных данных это owner_id, MAC устройства и
network_name — имя Wi-Fi сети, к которой подключён бризер.

async_redact_data закрывает значения по ключу, но не ключи словарей, а
состояния локального канала хранятся по MAC. Поэтому здесь они переложены
на облачный id устройства — тот же, что в списке devices.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN
from .local_channel import normalize_mac

TO_REDACT = {
    # учётные данные
    CONF_EMAIL,
    CONF_PASSWORD,
    "access_token",
    "refresh_token",
    "token",
    # человек и аккаунт
    "phone",
    "owner_id",
    "user_id",
    # устройство и место: MAC однозначно указывает на экземпляр бризера,
    # network_name — на Wi-Fi сеть дома
    "mac",
    "network_name",
}


def _local_snapshot(channel: Any, devices: list[Any]) -> dict[str, Any] | None:
    """Состояние локального канала с MAC, заменёнными на id устройств."""
    if channel is None:
        return None

    ids = {
        normalize_mac(dev.get("mac") or ""): str(dev.get("id"))
        for dev in devices
        if isinstance(dev, dict) and dev.get("mac")
    }
    # Устройство на канале, которого нет в аккаунте, тоже показываем —
    # но под порядковой меткой, а не под MAC.
    seen = set(channel.connected) | set(channel.states) | set(channel.setpoints)
    for n, mac in enumerate(sorted(seen - ids.keys()), start=1):
        ids[mac] = f"unmatched_{n}"

    return {
        "port": channel.port,
        "connected": sorted(ids[mac] for mac, on in channel.connected.items() if on),
        "states": {ids[mac]: state for mac, state in channel.states.items()},
        "setpoints": {ids[mac]: setp for mac, setp in channel.setpoints.items()},
    }


def _diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}
    coordinator = runtime.get("coordinator")
    data = (coordinator.data if coordinator is not None else None) or {}
    devices = data.get("devices") or []

    coordinator_info = None
    if coordinator is not None:
        coordinator_info = {
            "last_update_success": coordinator.last_update_success,
            "last_exception": (
                repr(coordinator.last_exception) if coordinator.last_exception else None
            ),
            "update_interval": str(coordinator.update_interval),
        }

    # async_redact_data возвращает копии, так что живые данные координатора
    # не меняются.
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "coordinator": coordinator_info,
            "devices": devices,
            "local_channel": _local_snapshot(runtime.get("local"), devices),
        },
        TO_REDACT,
    )


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return _diagnostics(hass, entry)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device: общие сведения и данные только этого устройства."""
    wanted = {ident for domain, ident in device.identifiers if domain == DOMAIN}
    diag = _diagnostics(hass, entry)

    diag["devices"] = [
        dev for dev in diag["devices"] if isinstance(dev, dict) and str(dev.get("id")) in wanted
    ]
    local = diag["local_channel"]
    if local is not None:
        local["connected"] = [did for did in local["connected"] if did in wanted]
        local["states"] = {k: v for k, v in local["states"].items() if k in wanted}
        local["setpoints"] = {k: v for k, v in local["setpoints"].items() if k in wanted}
    return diag
