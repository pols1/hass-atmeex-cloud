from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .capabilities import MODES as CAP_MODES
from .const import (
    CONF_CO2_SENSOR,
    CONF_HUMIDIFIER,
    CONF_LOCAL_ENABLED,
    CONF_LOCAL_PORT,
    DEFAULT_CAP_MODE,
    DEFAULT_LOCAL_ENABLED,
    DEFAULT_LOCAL_PORT,
    DOMAIN,
)
from .api import AtmeexApi, ApiError

_LOGGER = logging.getLogger(__name__)


async def _test_credentials(
    hass: HomeAssistant,
    email: str,
    password: str,
) -> None:
    """Пробная авторизация и запрос устройств для проверки логина/пароля."""
    session = async_get_clientsession(hass)
    api = AtmeexApi(session, email, password)

    # Минимальная проверка: логинимся и получаем список устройств.
    # В твоём AtmeexApi login обычно вызывается внутри get_devices,
    # поэтому здесь достаточно одного вызова.
    devices = await api.get_devices()
    if devices is None:
        raise ApiError("Empty devices list")


class AtmeexCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Мастер настройки интеграции Atmeex Cloud."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        # config_entry не передаём: с HA 2024.11 ядро само проставляет
        # OptionsFlow.config_entry, а с 2026.x у свойства убран сеттер.
        return AtmeexOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Первый шаг мастера — ввод email/пароля."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]

            # делаем unique_id по email, чтобы не создать дубликат
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            try:
                await _test_credentials(self.hass, email, password)
            except ApiError as err:
                _LOGGER.error("Error communicating with Atmeex API: %s", err)
                # тут можно было бы различать invalid_auth / cannot_connect,
                # но сервер часто отдаёт 500, поэтому делаем один тип ошибки
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Atmeex auth")
                errors["base"] = "unknown"
            else:
                # всё ок – создаём запись конфига и сохраняем email/пароль
                return self.async_create_entry(
                    title=email,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                    },
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle re-authentication if refresh token fails."""
        self._reauth_email = entry_data.get(CONF_EMAIL, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm re-authentication with new password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            try:
                await _test_credentials(self.hass, self._reauth_email, password)
            except ApiError as err:
                _LOGGER.error("Error communicating with Atmeex API during reauth: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Atmeex reauth")
                errors["base"] = "unknown"
            else:
                existing_entry = await self.async_set_unique_id(self._reauth_email.lower())
                if existing_entry:
                    self.hass.config_entries.async_update_entry(
                        existing_entry,
                        data={
                            **existing_entry.data,
                            CONF_PASSWORD: password,
                            # Clear old tokens safely
                            "access_token": "",
                            "refresh_token": "",
                        },
                    )
                    await self.hass.config_entries.async_reload(existing_entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"email": getattr(self, "_reauth_email", "")},
        )

class AtmeexOptionsFlowHandler(config_entries.OptionsFlow):
    """Мастер настройки опций интеграции."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    # Комплектация определяется по показаниям (см.
                    # capabilities.py); эти два переключателя нужны,
                    # чтобы перебить определение вручную.
                    vol.Optional(
                        CONF_CO2_SENSOR,
                        default=options.get(CONF_CO2_SENSOR, DEFAULT_CAP_MODE),
                    ): vol.In(CAP_MODES),
                    vol.Optional(
                        CONF_HUMIDIFIER,
                        default=options.get(CONF_HUMIDIFIER, DEFAULT_CAP_MODE),
                    ): vol.In(CAP_MODES),
                    vol.Optional(
                        "enable_cool",
                        default=options.get("enable_cool", False),
                    ): bool,
                    vol.Optional(
                        CONF_LOCAL_ENABLED,
                        default=options.get(
                            CONF_LOCAL_ENABLED, DEFAULT_LOCAL_ENABLED
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_LOCAL_PORT,
                        default=options.get(CONF_LOCAL_PORT, DEFAULT_LOCAL_PORT),
                    ): vol.All(int, vol.Range(min=1, max=65535)),
                }
            ),
        )