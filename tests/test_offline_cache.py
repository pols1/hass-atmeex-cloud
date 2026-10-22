"""Состав устройств переживает недоступность облака.

22.10.2026 вендор перестал пускать аккаунт, а затем перенёс API на другой
адрес. Интеграция падала на входе, и локальный канал не поднимался вовсе —
хотя заведён ровно для таких случаев. Кэш состава устройств позволяет
создать те же сущности без облака.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "atmeex_cloud"
    / "offline_cache.py"
)
_spec = importlib.util.spec_from_file_location("atmeex_offline_cache", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

cache_payload = _mod.cache_payload
cache_fingerprint = _mod.cache_fingerprint
restore = _mod.restore
merge_seed = _mod.merge_seed

# Ответ облака как есть: с телеметрией, владельцем и сессией.
CLOUD_DEVICES = [
    {
        "id": 12746,
        "mac": "B0:A7:32:5A:79:BC",
        "type": 1,
        "name": "Бризер спальня",
        "owner_id": "d4f1e0c2-owner",
        "socket_id": "o4rrmomga6gzh6zt0jal3",
        "fw_ver": "1.9/1.8/0.0",
        "model": "A7",
        "condition": {"time": "2026-10-22 10:00:00", "temp_room": 201, "pwr_on": True},
        "online": True,
        "pivot": {"user_id": "d4f1e0c2-owner", "type": "ownership"},
        "settings": {"u_pwr_on": True, "u_fan_speed": 2},
        "scenarios": [],
    },
    {
        "id": 17428,
        "mac": "84:1F:E8:A5:D7:A0",
        "type": 1,
        "name": "Бризер",
        "owner_id": "d4f1e0c2-owner",
        "fw_ver": "1.9/1.8/0.0",
        "model": "A7",
        "condition": {"time": "2026-10-22 10:00:01", "temp_room": 190},
        "online": True,
        "settings": {"u_pwr_on": False},
    },
]


class CachePayloadTest(unittest.TestCase):
    def test_keeps_only_what_entities_need(self) -> None:
        (first, second) = cache_payload(CLOUD_DEVICES)
        self.assertEqual(
            first,
            {
                "id": 12746,
                "mac": "B0:A7:32:5A:79:BC",
                "type": 1,
                "name": "Бризер спальня",
                "fw_ver": "1.9/1.8/0.0",
                "model": "A7",
            },
        )
        self.assertEqual(second["id"], 17428)

    def test_owner_session_and_telemetry_are_not_cached(self) -> None:
        # Владелец в кэше не нужен, а телеметрия протухнет за минуты.
        for dev in cache_payload(CLOUD_DEVICES):
            for unwanted in ("owner_id", "pivot", "socket_id", "condition", "online"):
                self.assertNotIn(unwanted, dev)

    def test_rubbish_is_skipped(self) -> None:
        self.assertEqual(cache_payload([1, "x", {}, {"name": "без id"}]), [])
        self.assertEqual(cache_payload(None), [])


class FingerprintTest(unittest.TestCase):
    """Запись конфигурации пишется на диск — трогать её на каждый опрос нельзя."""

    def test_same_devices_same_fingerprint(self) -> None:
        self.assertEqual(
            cache_fingerprint(CLOUD_DEVICES), cache_fingerprint(CLOUD_DEVICES)
        )

    def test_changing_telemetry_does_not_change_fingerprint(self) -> None:
        moved = [dict(d) for d in CLOUD_DEVICES]
        moved[0]["condition"] = {"time": "2026-10-22 10:05:00", "temp_room": 205}
        moved[0]["online"] = False
        self.assertEqual(cache_fingerprint(CLOUD_DEVICES), cache_fingerprint(moved))

    def test_order_does_not_change_fingerprint(self) -> None:
        self.assertEqual(
            cache_fingerprint(CLOUD_DEVICES), cache_fingerprint(CLOUD_DEVICES[::-1])
        )

    def test_rename_changes_fingerprint(self) -> None:
        renamed = [dict(d) for d in CLOUD_DEVICES]
        renamed[0]["name"] = "Спальня"
        self.assertNotEqual(cache_fingerprint(CLOUD_DEVICES), cache_fingerprint(renamed))

    def test_new_device_changes_fingerprint(self) -> None:
        more = [*CLOUD_DEVICES, {"id": 999, "mac": "02:00:00:00:00:09", "name": "Третий"}]
        self.assertNotEqual(cache_fingerprint(CLOUD_DEVICES), cache_fingerprint(more))

    def test_cached_payload_matches_its_source(self) -> None:
        # Так сравнивается сохранённое с новым ответом облака.
        self.assertEqual(
            cache_fingerprint(cache_payload(CLOUD_DEVICES)),
            cache_fingerprint(CLOUD_DEVICES),
        )


class RestoreTest(unittest.TestCase):
    def test_devices_come_back(self) -> None:
        data = restore(cache_payload(CLOUD_DEVICES))
        self.assertEqual([d["id"] for d in data["devices"]], [12746, 17428])
        self.assertEqual(data["devices"][0]["name"], "Бризер спальня")

    def test_restored_devices_are_offline_until_someone_speaks_to_them(self) -> None:
        data = restore(cache_payload(CLOUD_DEVICES))
        for dev in data["devices"]:
            self.assertIs(dev["online"], False)
            self.assertEqual(dev["condition"], {})
        self.assertEqual(data["states"], {})

    def test_empty_cache_gives_empty_data(self) -> None:
        self.assertEqual(restore(None), {"devices": [], "states": {}})
        self.assertEqual(restore([]), {"devices": [], "states": {}})


class MergeSeedTest(unittest.TestCase):
    """Ручное задание состава: когда облако умерло раньше, чем мы запомнили."""

    SEED = [
        {"id": 12746, "mac": "B0:A7:32:5A:79:BC", "name": "Бризер спальня"},
        {"id": 17428, "mac": "84:1F:E8:A5:D7:A0", "name": "Бризер"},
    ]

    def test_seed_on_empty_cache(self) -> None:
        merged = merge_seed(None, self.SEED)
        self.assertEqual([d["id"] for d in merged], [12746, 17428])
        self.assertEqual(merged[0]["mac"], "B0:A7:32:5A:79:BC")

    def test_seed_updates_matching_device_and_keeps_the_rest(self) -> None:
        merged = merge_seed(
            cache_payload(CLOUD_DEVICES), [{"id": 12746, "name": "Спальня"}]
        )
        by_id = {d["id"]: d for d in merged}
        self.assertEqual(by_id[12746]["name"], "Спальня")
        # MAC из прежнего кэша не потерялся, второе устройство не тронуто
        self.assertEqual(by_id[12746]["mac"], "B0:A7:32:5A:79:BC")
        self.assertEqual(by_id[17428]["name"], "Бризер")

    def test_seed_without_id_is_ignored(self) -> None:
        merged = merge_seed(self.SEED, [{"mac": "02:00:00:00:00:01"}, "мусор"])
        self.assertEqual(len(merged), 2)

    def test_seeded_cache_restores(self) -> None:
        data = restore(merge_seed(None, self.SEED))
        self.assertEqual(len(data["devices"]), 2)
        self.assertIs(data["devices"][0]["online"], False)


if __name__ == "__main__":
    unittest.main()
