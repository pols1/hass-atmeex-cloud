from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AtmeexApi, ApiAuthError, ApiError
from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Atmeex Cloud from a config entry."""
    session = async_get_clientsession(hass)

    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)
    if not email or not password:
        raise ConfigEntryAuthFailed("Missing email/password in config entry")

    access_token = entry.data.get("access_token")
    refresh_token = entry.data.get("refresh_token")

    def _save_tokens(tokens) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
            },
        )

    api = AtmeexApi(
        session=session,
        email=email,
        password=password,
        access_token=access_token,
        refresh_token=refresh_token,
        token_update_cb=_save_tokens,
    )

    async def async_update_data() -> dict[str, Any]:
        try:
            devices = await api.get_devices(with_condition=True)
        except ApiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ApiError as err:
            raise UpdateFailed(str(err)) from err
        except TimeoutError as err:
            # У asyncio.TimeoutError пустой str() — без явного текста в UI
            # и в логе оставалось загадочное "Unexpected error: ".
            raise UpdateFailed(
                f"Облако Atmeex не ответило за отведённое время ({api.base_url})"
            ) from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(
                f"Сеть до облака Atmeex недоступна: {type(err).__name__}: {err}"
            ) from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {type(err).__name__}: {err}") from err

        states: dict[str, Any] = {}
        for dev in devices:
            if isinstance(dev, dict):
                did = dev.get("id")
                cond = dev.get("condition")
                if did is not None and isinstance(cond, dict):
                    states[str(did)] = cond

        _LOGGER.debug(
            "Atmeex: coordinator devices = %s",
            [d.get("id") for d in devices if isinstance(d, dict)],
        )
        return {"devices": devices, "states": states}

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    await coordinator.async_config_entry_first_refresh()

    async def refresh_device(device_id: int | str) -> None:
        """
        Точечное обновление после команд.
        Сейчас просто дёргаем общий refresh (быстро и надёжно).
        """
        await coordinator.async_request_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "refresh_device": refresh_device,  # <-- ВОТ ЭТОГО НЕ ХВАТАЛО
    }

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "Atmeex Cloud: setup complete for %s, devices will be loaded by platforms",
        email,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
