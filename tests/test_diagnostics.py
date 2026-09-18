"""Тесты диагностики: в выгрузке не должно остаться ни одного значения,
указывающего на аккаунт, человека или место.

diagnostics.py импортирует homeassistant, которого в CI нет, поэтому здесь
подставлены минимальные заглушки его модулей. async_redact_data в заглушке —
копия homeassistant/components/diagnostics/util.py (HA core), чтобы тест
проверял настоящую семантику: точное совпадение ключа, рекурсия по dict и
list, пустые строки и None не трогаются.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import types
import unittest
from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "**REDACTED**"


def _async_redact_data(data: Any, to_redact: Iterable[Any]) -> Any:
    if not isinstance(data, (Mapping, list)):
        return data
    if isinstance(data, list):
        return [_async_redact_data(val, to_redact) for val in data]
    redacted = {**data}
    for key, value in redacted.items():
        if value is None:
            continue
        if isinstance(value, str) and not value:
            continue
        if key in to_redact:
            redacted[key] = REDACTED
        elif isinstance(value, Mapping):
            redacted[key] = _async_redact_data(value, to_redact)
        elif isinstance(value, list):
            redacted[key] = [_async_redact_data(item, to_redact) for item in value]
    return redacted


def _install_ha_stubs() -> None:
    def mod(name: str, **attrs: Any) -> None:
        m = sys.modules.get(name) or types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m

    mod("homeassistant")
    mod("homeassistant.components")
    mod(
        "homeassistant.components.diagnostics",
        async_redact_data=_async_redact_data,
        REDACTED=REDACTED,
    )
    mod("homeassistant.config_entries", ConfigEntry=object)
    mod(
        "homeassistant.const",
        CONF_EMAIL="email",
        CONF_PASSWORD="password",
        Platform=types.SimpleNamespace(CLIMATE="climate", SENSOR="sensor"),
    )
    mod("homeassistant.core", HomeAssistant=object)
    mod("homeassistant.helpers")
    mod("homeassistant.helpers.device_registry", DeviceEntry=object)


def _load_diagnostics() -> types.ModuleType:
    _install_ha_stubs()
    # Пакет регистрируем пустым, чтобы не исполнять __init__.py (он тянет HA
    # целиком), а относительные импорты .const и .local_channel работали.
    pkg_dir = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "atmeex_cloud"
    pkg = types.ModuleType("atmeex_diag_pkg")
    pkg.__path__ = [str(pkg_dir)]
    sys.modules["atmeex_diag_pkg"] = pkg
    import importlib

    return importlib.import_module("atmeex_diag_pkg.diagnostics")


diagnostics = _load_diagnostics()
DOMAIN = sys.modules["atmeex_diag_pkg.const"].DOMAIN

EMAIL = "someone@example.org"
PASSWORD = "hunter2-secret"
ACCESS = "eyJhbGciOiJIUzI1NiJ9.ACCESS-SECRET.sig"
REFRESH = "eyJhbGciOiJIUzI1NiJ9.REFRESH-SECRET.sig"
MAC_KNOWN = "84:1F:E8:A5:D7:A0"
MAC_STRAY = "B0:A7:32:5A:79:BC"
SSID = "HomeNet-Secret-SSID"
OWNER_ID = 987654


def _device() -> dict[str, Any]:
    # Поля по схеме Device/DeviceCondition из OpenAPI облака.
    return {
        "id": 12746,
        "name": "Бризер спальня",
        "mac": MAC_KNOWN,
        "type": 7,
        "room_id": 3,
        "owner_id": OWNER_ID,
        "online": True,
        "settings": {"u_pwr_on": True, "u_fan_speed": 3},
        "condition": {
            "temp_room": 213,
            "co2_ppm": 0,
            "firmware_version": "1.9/1.8/0.0",
            "network_name": SSID,
        },
    }


class _Channel:
    port = 3001

    def __init__(self) -> None:
        known = MAC_KNOWN.lower()
        stray = MAC_STRAY.lower()
        self.connected = {known: True, stray: True}
        self.states = {known: {"temp_room": 214}, stray: {"temp_room": 190}}
        self.setpoints = {known: {"u_fan_speed": 3}}


class _Coordinator:
    last_update_success = True
    last_exception = None
    update_interval = "0:00:30"

    def __init__(self, devices: list[dict[str, Any]]) -> None:
        self.data = {"devices": devices, "states": {}}


class _Entry:
    entry_id = "entry1"
    data = {
        "email": EMAIL,
        "password": PASSWORD,
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "capabilities": {"12746": {"co2": False}},
    }
    options = {"local_enabled": True, "local_port": 3001, "write_mode": "cloud_first"}


class _Device:
    identifiers = {(DOMAIN, "12746")}


class _Push:
    def status(self) -> dict[str, Any]:
        return {
            "connected": True,
            "connections": 2,
            "seconds_since_last_message": 4.9,
            "last_error": "соединение закрыто (код 1006)",
            "devices_seen": ["12746", "17428"],
        }


def _hass(local: bool = True, push: bool = True) -> Any:
    runtime: dict[str, Any] = {"coordinator": _Coordinator([_device()])}
    if local:
        runtime["local"] = _Channel()
    if push:
        runtime["push"] = _Push()
    return types.SimpleNamespace(data={DOMAIN: {"entry1": runtime}})


SECRETS = [EMAIL, PASSWORD, ACCESS, REFRESH, SSID, str(OWNER_ID)]
MACS = [MAC_KNOWN, MAC_STRAY]


class DiagnosticsTest(unittest.TestCase):
    def _assert_clean(self, diag: dict[str, Any]) -> str:
        dump = json.dumps(diag, ensure_ascii=False)
        for secret in SECRETS:
            self.assertNotIn(secret, dump)
        for mac in MACS:
            self.assertNotIn(mac.lower(), dump.lower())
        return dump

    def test_config_entry_has_no_secrets(self) -> None:
        hass = _hass()
        diag = asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, _Entry()))
        self._assert_clean(diag)
        self.assertEqual(diag["entry"]["data"]["access_token"], REDACTED)
        self.assertEqual(diag["entry"]["options"]["write_mode"], "cloud_first")
        dev = diag["devices"][0]
        self.assertEqual(dev["id"], 12746)
        self.assertEqual(dev["condition"]["temp_room"], 213)
        self.assertEqual(dev["condition"]["network_name"], REDACTED)

    def test_local_channel_keyed_by_device_id(self) -> None:
        diag = asyncio.run(diagnostics.async_get_config_entry_diagnostics(_hass(), _Entry()))
        local = diag["local_channel"]
        self.assertEqual(local["connected"], ["12746", "unmatched_1"])
        self.assertEqual(local["states"]["12746"], {"temp_room": 214})
        self.assertEqual(local["states"]["unmatched_1"], {"temp_room": 190})
        self.assertEqual(local["setpoints"], {"12746": {"u_fan_speed": 3}})

    def test_local_channel_absent(self) -> None:
        diag = asyncio.run(
            diagnostics.async_get_config_entry_diagnostics(_hass(local=False), _Entry())
        )
        self.assertIsNone(diag["local_channel"])

    def test_cloud_push_status(self) -> None:
        diag = asyncio.run(diagnostics.async_get_config_entry_diagnostics(_hass(), _Entry()))
        self._assert_clean(diag)
        self.assertTrue(diag["cloud_push"]["connected"])
        self.assertEqual(diag["cloud_push"]["devices_seen"], ["12746", "17428"])

    def test_cloud_push_absent(self) -> None:
        diag = asyncio.run(
            diagnostics.async_get_config_entry_diagnostics(_hass(push=False), _Entry())
        )
        self.assertIsNone(diag["cloud_push"])

    def test_live_data_not_mutated(self) -> None:
        hass = _hass()
        asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, _Entry()))
        dev = hass.data[DOMAIN]["entry1"]["coordinator"].data["devices"][0]
        self.assertEqual(dev["mac"], MAC_KNOWN)
        self.assertEqual(dev["condition"]["network_name"], SSID)
        self.assertEqual(_Entry.data["password"], PASSWORD)

    def test_device_diagnostics_only_that_device(self) -> None:
        diag = asyncio.run(
            diagnostics.async_get_device_diagnostics(_hass(), _Entry(), _Device())
        )
        self._assert_clean(diag)
        self.assertEqual([d["id"] for d in diag["devices"]], [12746])
        self.assertEqual(diag["local_channel"]["connected"], ["12746"])
        self.assertNotIn("unmatched_1", diag["local_channel"]["states"])
        self.assertEqual(diag["cloud_push"]["devices_seen"], ["12746"])

    def test_log_redaction_covers_diagnostics_keys(self) -> None:
        # То, что скрыто в файле диагностики, не должно утекать через отладочный лог.
        import importlib

        log_keys = importlib.import_module("atmeex_diag_pkg.redact").SENSITIVE_KEYS
        self.assertLessEqual(set(diagnostics.TO_REDACT), set(log_keys))

    def test_not_loaded_entry(self) -> None:
        hass = types.SimpleNamespace(data={})
        diag = asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, _Entry()))
        self._assert_clean(diag)
        self.assertEqual(diag["devices"], [])
        self.assertIsNone(diag["coordinator"])


if __name__ == "__main__":
    unittest.main()
