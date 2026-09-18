"""Когда изменение записи конфигурации перезагружает интеграцию.

Главный случай — продление токена. JWT живёт три часа; до исправления каждое
продление перезагружало интеграцию: сущности мигали в unavailable, а
автоматизации с триггером «вернулся из unavailable» слали команды в облако.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "atmeex_cloud"
    / "reload_policy.py"
)
_spec = importlib.util.spec_from_file_location("atmeex_reload_policy", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

needs_reload = _mod.needs_reload

OPTIONS = {
    "co2_sensor": "auto",
    "humidifier": "auto",
    "enable_cool": False,
    "local_enabled": True,
    "write_mode": "cloud_first",
    "local_port": 3001,
}
DATA = {
    "email": "user@example.com",
    "password": "secret",
    "access_token": "old-access",
    "refresh_token": "old-refresh",
    "capabilities": {"12746": {"humidifier": True}},
}


class NeedsReloadTest(unittest.TestCase):
    def test_token_refresh_does_not_reload(self) -> None:
        # Ровно то, что происходило каждые 2 ч 59 мин.
        new_data = {**DATA, "access_token": "new-access", "refresh_token": "new-refresh"}
        self.assertFalse(needs_reload(OPTIONS, OPTIONS, DATA, new_data))

    def test_tokens_cleared_does_not_reload(self) -> None:
        # Повторный вход обнуляет токены и перезагружает интеграцию сам.
        new_data = {**DATA, "password": "new", "access_token": "", "refresh_token": ""}
        self.assertFalse(needs_reload(OPTIONS, OPTIONS, DATA, new_data))

    def test_new_capability_reloads(self) -> None:
        # Найден увлажнитель у второго бризера — нужна новая сущность влажности.
        new_data = {
            **DATA,
            "capabilities": {"12746": {"humidifier": True}, "17428": {"humidifier": True}},
        }
        self.assertTrue(needs_reload(OPTIONS, OPTIONS, DATA, new_data))

    def test_first_capability_reloads(self) -> None:
        data = {k: v for k, v in DATA.items() if k != "capabilities"}
        self.assertTrue(needs_reload(OPTIONS, OPTIONS, data, DATA))

    def test_write_mode_applies_live(self) -> None:
        new_options = {**OPTIONS, "write_mode": "local_first"}
        self.assertFalse(needs_reload(OPTIONS, new_options, DATA, DATA))

    def test_other_option_reloads(self) -> None:
        for key, value in (("local_port", 3002), ("local_enabled", False), ("co2_sensor", "on")):
            with self.subTest(key=key):
                self.assertTrue(needs_reload(OPTIONS, {**OPTIONS, key: value}, DATA, DATA))

    def test_write_mode_with_other_option_reloads(self) -> None:
        new_options = {**OPTIONS, "write_mode": "local_first", "local_port": 3002}
        self.assertTrue(needs_reload(OPTIONS, new_options, DATA, DATA))

    def test_new_option_key_reloads(self) -> None:
        # Новая настройка, которой раньше не было в записи.
        self.assertTrue(needs_reload(OPTIONS, {**OPTIONS, "cloud_push": False}, DATA, DATA))

    def test_nothing_changed(self) -> None:
        self.assertFalse(needs_reload(OPTIONS, dict(OPTIONS), DATA, dict(DATA)))


if __name__ == "__main__":
    unittest.main()
