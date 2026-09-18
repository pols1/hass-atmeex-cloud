"""Push-канал облака: разбор сообщений и переподключение.

Кадры сняты трёхчасовой записью 18.09.2026 с живого аккаунта; MAC и id
устройств заменены вымышленными, остальное — как пришло.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import pathlib
import sys
import unittest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "atmeex_cloud"
    / "cloud_push.py"
)
_spec = importlib.util.spec_from_file_location("atmeex_cloud_push", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
# dataclass ищет свой модуль в sys.modules — без регистрации загрузка падает.
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

AtmeexCloudPush = _mod.AtmeexCloudPush
parse_message = _mod.parse_message
Unauthorized = _mod.Unauthorized
BACKOFF_MIN = _mod.BACKOFF_MIN
BACKOFF_MAX = _mod.BACKOFF_MAX

# Снимок при подключении: pwr_on и no_water числами.
SNAPSHOT_CONDITION = json.dumps({"type": "condition", "data": [
    {"condition": {"time": "2026-09-18 16:19:10", "pwr_on": 0, "no_water": 1, "co2_ppm": 0,
                   "temp_in": 187, "temp_room": 200, "fan_speed": 1, "damp_pos": 0,
                   "hum_room": 51, "hum_stg": 0, "firmware_version": "1.9/1.8/0.0",
                   "server_time": "2026-09-18 16:19:11", "device_id": 101,
                   "created_at": "2026-09-18 16:19:12"},
     "id": 101, "mac": "02:00:00:00:00:01:0"},
    {"condition": {"time": "2026-09-18 16:19:07", "pwr_on": 1, "no_water": 1, "co2_ppm": 0,
                   "temp_in": 171, "temp_room": 160, "fan_speed": 3, "damp_pos": 0,
                   "hum_room": 56, "hum_stg": 0, "firmware_version": "1.9/1.8/0.0",
                   "server_time": "2026-09-18 16:19:07", "device_id": 102,
                   "created_at": "2026-09-18 16:19:08"},
     "id": 102, "mac": "02:00:00:00:00:02:0"},
]})

SNAPSHOT_SETTINGS = json.dumps({"type": "settings", "data": [
    {"mac": "02:00:00:00:00:01:0", "id": 101, "settings": {
        "id": 11744, "device_id": 101, "u_pwr_on": False, "u_fan_speed": 1, "u_damp_pos": 0,
        "u_hum_stg": 0, "u_temp_room": 100, "u_auto": False, "u_night": False,
        "u_cool_mode": False, "u_night_start": "20:00", "u_night_stop": "10:00",
        "u_time_zone": None}},
]})

# Живой кадр: pwr_on и no_water уже булевы.
LIVE_CONDITION = json.dumps({"type": "condition", "data": [
    {"condition": {"time": "2026-09-18 16:19:15", "pwr_on": False, "no_water": True,
                   "co2_ppm": 0, "temp_in": 187, "temp_room": 200, "fan_speed": 1,
                   "damp_pos": 0, "hum_room": 51, "hum_stg": 0,
                   "firmware_version": "1.9/1.8/0.0", "server_time": "2026-09-18 16:19:16"},
     "mac": "02:00:00:00:00:01:0", "id": 101},
]})

# Уставка, изменённая в приложении: полей меньше, чем в снимке.
LIVE_SETTINGS = json.dumps({"type": "settings", "data": [
    {"settings": {"u_pwr_on": True, "u_fan_speed": 2, "u_damp_pos": 0, "u_hum_stg": 0,
                  "u_temp_room": 100, "u_auto": False, "u_night": False,
                  "u_night_start": "20:00", "u_night_stop": "10:00",
                  "server_time": "2026-09-18 16:38:53"},
     "mac": "02:00:00:00:00:02:0", "id": 102},
]})

UNAUTHORIZED = '{"type": "unauthorized", "data": null}'


class ParseMessageTest(unittest.TestCase):
    def test_snapshot_condition(self) -> None:
        updates = parse_message(SNAPSHOT_CONDITION)
        self.assertEqual([u.device_id for u in updates], ["101", "102"])
        self.assertTrue(all(u.kind == "state" for u in updates))
        self.assertEqual(updates[0].mac, "02:00:00:00:00:01:0")
        self.assertEqual(updates[0].payload["temp_room"], 200)

    def test_zero_one_become_bools(self) -> None:
        first, second = parse_message(SNAPSHOT_CONDITION)
        self.assertIs(first.payload["pwr_on"], False)
        self.assertIs(first.payload["no_water"], True)
        self.assertIs(second.payload["pwr_on"], True)

    def test_live_condition_keeps_bools(self) -> None:
        (update,) = parse_message(LIVE_CONDITION)
        self.assertIs(update.payload["pwr_on"], False)
        self.assertIs(update.payload["no_water"], True)

    def test_settings_are_setpoints(self) -> None:
        (snap,) = parse_message(SNAPSHOT_SETTINGS)
        (live,) = parse_message(LIVE_SETTINGS)
        self.assertEqual((snap.kind, live.kind), ("setp", "setp"))
        self.assertEqual(live.device_id, "102")
        self.assertEqual(live.payload["u_fan_speed"], 2)
        self.assertNotIn("u_cool_mode", live.payload, "живой кадр неполный — сливать, не заменять")

    def test_unauthorized_raises(self) -> None:
        with self.assertRaises(Unauthorized):
            parse_message(UNAUTHORIZED)

    def test_unknown_or_malformed_is_ignored(self) -> None:
        self.assertEqual(parse_message('{"type": "stats", "data": [{"x": 1}]}'), [])
        self.assertEqual(parse_message('{"type": "condition", "data": null}'), [])
        self.assertEqual(parse_message('{"type": "condition", "data": [1, "x", {"id": 5}]}'), [])
        self.assertEqual(parse_message("[]"), [])

    def test_broken_json_is_value_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_message('{"type": "condi')

    def test_device_id_falls_back_to_payload(self) -> None:
        msg = {"type": "condition", "data": [{"condition": {"device_id": 7, "temp_room": 1}}]}
        (update,) = parse_message(msg)
        self.assertEqual(update.device_id, "7")
        self.assertEqual(update.mac, "")


class _Stop(Exception):
    """Остановить цикл из подставного sleep после нужного числа пауз."""


class _Harness:
    """Подставные соединения: каждое — список кадров или исключение."""

    def __init__(self, sessions: list, sleeps_allowed: int) -> None:
        self.sessions = list(sessions)
        self.tokens_requested: list[str | None] = []
        self.tokens_used: list[str] = []
        self.delays: list[float] = []
        self.updates: list = []
        self.clock = 1000.0
        self.sleeps_allowed = sleeps_allowed
        self._token_no = 0

    async def token_getter(self, rejected: str | None) -> str:
        self.tokens_requested.append(rejected)
        self._token_no += 1
        return f"token-{self._token_no}"

    @contextlib.asynccontextmanager
    async def connector(self, url: str, token: str, heartbeat: float):
        self.tokens_used.append(token)
        session = self.sessions.pop(0) if self.sessions else OSError("нет сети")
        if isinstance(session, Exception):
            raise session

        async def frames():
            for frame in session:
                yield frame

        yield frames()

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self.clock += delay
        if len(self.delays) >= self.sleeps_allowed:
            raise _Stop

    def push(self, **kw) -> AtmeexCloudPush:
        return AtmeexCloudPush(
            token_getter=self.token_getter,
            on_update=kw.pop("on_update", self.updates.append),
            connector=self.connector,
            sleep=self.sleep,
            monotonic=lambda: self.clock,
            jitter=lambda a, b: 0.0,
            **kw,
        )


class ReconnectLoopTest(unittest.IsolatedAsyncioTestCase):
    async def run_until_stop(self, push: AtmeexCloudPush) -> None:
        with self.assertRaises(_Stop):
            await push._run()
        self.assertFalse(push.connected)

    async def test_delivers_updates_and_reconnects_after_drop(self) -> None:
        h = _Harness(
            [
                [("text", SNAPSHOT_CONDITION), ("text", SNAPSHOT_SETTINGS), ("closed", 1006)],
                [("text", LIVE_CONDITION), ("error", TimeoutError("No PONG"))],
            ],
            sleeps_allowed=2,
        )
        push = h.push()
        await self.run_until_stop(push)
        self.assertEqual(push.connections, 2)
        self.assertEqual([u.kind for u in h.updates], ["state", "state", "setp", "state"])
        # После сессии с данными пауза сбрасывается к минимуму.
        self.assertEqual(h.delays, [BACKOFF_MIN, BACKOFF_MIN])
        self.assertIn("No PONG", push.last_error)

    async def test_backoff_grows_without_data_and_is_capped(self) -> None:
        h = _Harness([OSError("DNS")] * 12, sleeps_allowed=10)
        with self.assertLogs(_mod._LOGGER, level="WARNING"):
            await self.run_until_stop(h.push())
        self.assertEqual(h.delays[:4], [5.0, 10.0, 20.0, 40.0])
        self.assertEqual(max(h.delays), BACKOFF_MAX)

    async def test_unauthorized_asks_for_a_new_token(self) -> None:
        h = _Harness(
            [[("text", UNAUTHORIZED)], [("text", LIVE_CONDITION), ("closed", 1000)]],
            sleeps_allowed=2,
        )
        await self.run_until_stop(h.push())
        # Второй раз токен запрашивается с пометкой «этот отвергнут».
        self.assertEqual(h.tokens_requested[:2], [None, "token-1"])
        self.assertEqual(h.tokens_used[:2], ["token-1", "token-2"])

    async def test_connection_that_only_says_unauthorized_is_not_data(self) -> None:
        h = _Harness([[("text", UNAUTHORIZED)]] * 3, sleeps_allowed=3)
        with self.assertLogs(_mod._LOGGER, level="WARNING") as logs:
            await self.run_until_stop(h.push())
        self.assertIn("сервер отверг токен", logs.output[0])
        self.assertEqual(h.delays, [5.0, 10.0, 20.0])

    async def test_handler_error_does_not_break_the_connection(self) -> None:
        seen = []

        def on_update(update):
            seen.append(update.device_id)
            if update.device_id == "101":
                raise RuntimeError("сбой обработчика")

        h = _Harness([[("text", SNAPSHOT_CONDITION), ("closed", 1000)]], sleeps_allowed=1)
        with self.assertLogs(_mod._LOGGER, level="ERROR"):
            await self.run_until_stop(h.push(on_update=on_update))
        self.assertEqual(seen, ["101", "102"])

    async def test_garbage_frame_is_skipped(self) -> None:
        h = _Harness([[("text", "not json"), ("text", LIVE_CONDITION), ("closed", 1000)]], 1)
        await self.run_until_stop(h.push())
        self.assertEqual(len(h.updates), 1)

    async def test_fresh_state_expires(self) -> None:
        h = _Harness([[("text", SNAPSHOT_CONDITION), ("text", LIVE_CONDITION), ("closed", 1000)]], 1)
        push = h.push()
        await self.run_until_stop(push)
        # Снимок и живой кадр слились; после паузы переподключения прошло 5 с.
        state = push.fresh_state("101", max_age=60)
        self.assertEqual(state["temp_room"], 200)
        self.assertIs(state["no_water"], True)
        self.assertIsNone(push.fresh_state("101", max_age=1))
        self.assertIsNone(push.fresh_state("999", max_age=60))
        self.assertEqual(push.status()["devices_seen"], ["101", "102"])

    async def test_warns_once_after_repeated_failures(self) -> None:
        h = _Harness([OSError("DNS")] * 6, sleeps_allowed=5)
        with self.assertLogs(_mod._LOGGER, level="WARNING") as logs:
            await self.run_until_stop(h.push())
        self.assertEqual(len([r for r in logs.records if r.levelname == "WARNING"]), 1)

    async def test_stop_cancels_a_sleeping_loop(self) -> None:
        started = asyncio.Event()

        async def slow_sleep(delay: float) -> None:
            started.set()
            await asyncio.sleep(3600)

        h = _Harness([OSError("DNS")], sleeps_allowed=99)
        push = AtmeexCloudPush(
            token_getter=h.token_getter, on_update=h.updates.append,
            connector=h.connector, sleep=slow_sleep, jitter=lambda a, b: 0.0,
        )
        push.start()
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(push.async_stop(), 1)
        self.assertFalse(push.connected)


if __name__ == "__main__":
    unittest.main()
