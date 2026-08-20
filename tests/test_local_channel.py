"""Тесты локального канала.

Фикстуры — настоящие кадры, снятые с бризера AirNanny A7 (прошивка 1.9/1.8/0.0)
перехватом 16.08.2026, включая склейку объектов без разделителя.

Модуль local_channel специально не зависит от homeassistant, поэтому тест
запускается без установленного HA:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import pathlib
import unittest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "atmeex_cloud"
    / "local_channel.py"
)
_spec = importlib.util.spec_from_file_location("atmeex_local_channel", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

AtmeexLocalChannel = _mod.AtmeexLocalChannel
normalize_mac = _mod.normalize_mac
split_json_objects = _mod.split_json_objects

DEVICE_ID = "84:1F:E8:A5:D7:A0:0"
MAC = "84:1f:e8:a5:d7:a0"

HELLO = (
    '{"id":"84:1F:E8:A5:D7:A0:0","hello":{"fw_ver":"1.9/1.8/0.0",'
    '"time":"2026-08-16 18:51:30","time_zone":"-00:00","init_req":false,'
    '"model":"A7","test":false}}'
)
SETP = (
    '{"id":"84:1F:E8:A5:D7:A0:0","setp":{"u_pwr_on":true,"u_fan_speed":3,'
    '"u_damp_pos":0,"u_hum_stg":0,"u_temp_room":100,"u_auto":false,'
    '"u_night":false,"u_night_start":"20:00","u_night_stop":"10:00"}}'
)
STATE = (
    '{"id":"84:1F:E8:A5:D7:A0:0","state":{"time":"2026-08-16 18:51:30",'
    '"pwr_on":true,"no_water":true,"co2_ppm":0,"temp_in":175,"temp_room":164,'
    '"fan_speed":3,"damp_pos":0,"hum_room":67,"hum_stg":0}}'
)
# В потоке объекты идут вплотную, без разделителя и без длины.
GLUED = HELLO + SETP + STATE + STATE


class TestFrameSplitting(unittest.TestCase):
    def test_splits_glued_objects(self):
        frames, rest = split_json_objects(GLUED)
        self.assertEqual(len(frames), 4)
        self.assertEqual(rest, "")
        kinds = [next(k for k in json.loads(f) if k != "id") for f in frames]
        self.assertEqual(kinds, ["hello", "setp", "state", "state"])

    def test_survives_arbitrary_chunking(self):
        """TCP режет поток где угодно — сборка не должна от этого зависеть."""
        for size in (1, 3, 7, 64, 500):
            buf, collected = "", []
            for i in range(0, len(GLUED), size):
                buf += GLUED[i : i + size]
                done, buf = split_json_objects(buf)
                collected += done
            self.assertEqual(len(collected), 4, f"размер куска {size}")
            self.assertEqual(buf, "", f"размер куска {size}")

    def test_keeps_incomplete_tail(self):
        frames, rest = split_json_objects(HELLO + '{"id":"x","sta')
        self.assertEqual(len(frames), 1)
        self.assertEqual(rest, '{"id":"x","sta')

    def test_braces_inside_strings_are_ignored(self):
        payload = '{"id":"a","hello":{"name":"} not a brace {"}}'
        frames, rest = split_json_objects(payload)
        self.assertEqual(frames, [payload])
        self.assertEqual(rest, "")

    def test_stray_open_brace_does_not_poison_the_stream(self):
        """Одна лишняя '{' иначе съедала бы все последующие кадры."""
        frames, rest = split_json_objects('{"broken": ')
        self.assertEqual(frames, [])
        self.assertEqual(rest, "", "мусорный хвост должен быть отброшен")

        frames, rest = split_json_objects('{"broken": ' + HELLO)
        self.assertEqual(len(frames), 1)
        self.assertEqual(next(k for k in json.loads(frames[0]) if k != "id"), "hello")

    def test_resync_keeps_valid_partial_frame(self):
        """Настоящий обрезанный кадр выбрасывать нельзя — он ещё дойдёт."""
        self.assertEqual(_mod.resync('{"id":"84:1F'), '{"id":"84:1F')
        self.assertEqual(_mod.resync('{"i'), '{"i')
        self.assertEqual(_mod.resync('{"nope":1'), "")


class TestMacNormalisation(unittest.TestCase):
    def test_strips_suffix_and_lowercases(self):
        self.assertEqual(normalize_mac(DEVICE_ID), MAC)

    def test_cloud_and_device_forms_match(self):
        """В /devices mac лежит без суффикса ':0' — ключ должен совпадать."""
        self.assertEqual(normalize_mac("84:1F:E8:A5:D7:A0"), normalize_mac(DEVICE_ID))

    def test_pads_short_octets(self):
        self.assertEqual(normalize_mac("8:1F:E8:A5:D7:A0:0"), "08:1f:e8:a5:d7:a0")


class TestChannelExchange(unittest.IsolatedAsyncioTestCase):
    """Канал без облака обязан сам ответить на hello, иначе бризер молчит."""

    PORT = 13901

    async def asyncSetUp(self):
        self.states: list[tuple[str, dict]] = []
        self.setpoints: list[tuple[str, dict]] = []
        self.channel = AtmeexLocalChannel(
            port=self.PORT,
            on_state=lambda mac, payload: self.states.append((mac, payload)),
            on_setp=lambda mac, payload: self.setpoints.append((mac, payload)),
            upstream=None,
        )
        await self.channel.async_start()

    async def asyncTearDown(self):
        await self.channel.async_stop()

    async def _read_frames(self, reader, count, timeout=5):
        acc, frames = "", []

        async def collect():
            nonlocal acc, frames
            while len(frames) < count:
                acc += (await reader.read(4096)).decode()
                frames, _ = split_json_objects(acc)

        try:
            await asyncio.wait_for(collect(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return [json.loads(f) for f in frames]

    async def test_answers_hello_and_records_telemetry(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.PORT)
        self.addCleanup(writer.close)

        writer.write(HELLO.encode())
        await writer.drain()

        replies = await self._read_frames(reader, 3)
        self.assertEqual(len(replies), 3, replies)

        # Первый кадр — синхронизация времени, ровно как у облака.
        self.assertIs(replies[0].get("hello"), True)
        self.assertEqual(replies[0].get("id"), DEVICE_ID)
        self.assertRegex(replies[0].get("time", ""), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(replies[0].get("time_zone"), "-00:00")

        self.assertEqual(
            sorted(json.dumps(r.get("cmd"), sort_keys=True) for r in replies[1:]),
            ['{"get_setp": true}', '{"get_state": true}'],
        )

        writer.write((SETP + STATE).encode())
        await writer.drain()
        for _ in range(50):
            if self.states and self.setpoints:
                break
            await asyncio.sleep(0.02)

        self.assertTrue(self.states, "телеметрия не дошла")
        self.assertTrue(self.setpoints, "уставки не дошли")
        self.assertEqual(self.states[0][0], MAC)

        stored = self.channel.states[MAC]
        self.assertEqual(stored["temp_room"], 164)   # десятые градуса -> 16.4 °C
        self.assertEqual(stored["hum_room"], 67)
        self.assertIs(stored["no_water"], True)
        self.assertIs(self.channel.connected[MAC], True)

    async def test_marks_device_offline_after_disconnect(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.PORT)
        writer.write(HELLO.encode())
        await writer.drain()
        await self._read_frames(reader, 1)
        self.assertIs(self.channel.connected.get(MAC), True)

        writer.close()
        await writer.wait_closed()
        for _ in range(50):
            if self.channel.connected.get(MAC) is False:
                break
            await asyncio.sleep(0.02)
        self.assertIs(self.channel.connected.get(MAC), False)

    async def test_garbage_does_not_kill_the_server(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.PORT)
        self.addCleanup(writer.close)
        writer.write(b'\x00\x01 not json at all {"broken": ')
        await writer.drain()
        writer.write(HELLO.encode())
        await writer.drain()
        replies = await self._read_frames(reader, 3)
        self.assertEqual(len(replies), 3, "после мусора канал должен продолжать работать")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestCapabilityDetection(unittest.TestCase):
    """Комплектация A7 определяется по показаниям — модель в API не приходит."""

    def setUp(self):
        import importlib.util

        path = (
            pathlib.Path(__file__).resolve().parents[1]
            / "custom_components"
            / "atmeex_cloud"
            / "capabilities.py"
        )
        spec = importlib.util.spec_from_file_location("atmeex_capabilities", path)
        self.caps = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.caps)

    def test_zero_co2_means_no_sensor(self):
        """1952 кадра с живого Start: co2_ppm всегда ровно 0."""
        found = self.caps.detect_from_payload(json.loads(STATE)["state"])
        self.assertNotIn(self.caps.CAP_CO2, found)

    def test_real_co2_reading_proves_sensor(self):
        found = self.caps.detect_from_payload({"co2_ppm": 612})
        self.assertTrue(found.get(self.caps.CAP_CO2))

    def test_humidity_reading_proves_humidifier(self):
        found = self.caps.detect_from_payload(json.loads(STATE)["state"])
        self.assertTrue(found.get(self.caps.CAP_HUMIDIFIER))

    def test_humidity_stage_also_proves_humidifier(self):
        self.assertTrue(
            self.caps.detect_from_payload({"u_hum_stg": 2}).get(self.caps.CAP_HUMIDIFIER)
        )

    def test_bare_keys_prove_nothing(self):
        """Ключи hum_stg и co2_ppm приходят и от моделей без этих узлов."""
        found = self.caps.detect_from_payload({"co2_ppm": 0, "hum_room": 0, "hum_stg": 0})
        self.assertEqual(found, {})

    def test_detection_is_sticky(self):
        known = {self.caps.CAP_CO2: True}
        merged = self.caps.merge(known, {})
        self.assertTrue(merged[self.caps.CAP_CO2], "признак нельзя отзывать")

    def test_manual_override_wins(self):
        self.assertTrue(self.caps.resolve(self.caps.CAP_CO2, {}, self.caps.MODE_ON))
        self.assertFalse(
            self.caps.resolve(self.caps.CAP_CO2, {"co2": True}, self.caps.MODE_OFF)
        )
        self.assertTrue(
            self.caps.resolve(self.caps.CAP_CO2, {"co2": True}, self.caps.MODE_AUTO)
        )
        self.assertFalse(self.caps.resolve(self.caps.CAP_CO2, {}, self.caps.MODE_AUTO))


def _load(name: str):
    import importlib.util

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "custom_components"
        / "atmeex_cloud"
        / f"{name}.py"
    )
    spec = importlib.util.spec_from_file_location(f"atmeex_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPayloadDiffers(unittest.TestCase):
    """Устройство шлёт state каждые ~5 с; публиковать неизменное незачем."""

    def setUp(self):
        self.dm = _load("data_merge")
        self.data = {
            "devices": [{"id": 17428, "settings": {"u_fan_speed": 2}}],
            "states": {"17428": {"temp_room": 164, "pwr_on": True, "time": "20:25:39"}},
        }

    def test_same_values_with_new_timestamp_are_not_a_change(self):
        frame = {"temp_room": 164, "pwr_on": True, "time": "20:25:44"}
        self.assertFalse(self.dm.payload_differs(self.data, "state", "17428", frame))

    def test_real_change_is_detected(self):
        frame = {"temp_room": 165, "pwr_on": True, "time": "20:25:44"}
        self.assertTrue(self.dm.payload_differs(self.data, "state", "17428", frame))

    def test_new_field_is_a_change(self):
        self.assertTrue(
            self.dm.payload_differs(self.data, "state", "17428", {"hum_room": 66})
        )

    def test_settings_are_compared_against_the_device(self):
        self.assertFalse(
            self.dm.payload_differs(self.data, "setp", "17428", {"u_fan_speed": 2})
        )
        self.assertTrue(
            self.dm.payload_differs(self.data, "setp", "17428", {"u_fan_speed": 3})
        )

    def test_unknown_device_counts_as_change(self):
        self.assertTrue(
            self.dm.payload_differs(self.data, "state", "99999", {"temp_room": 164})
        )


class TestStandaloneRecovery(unittest.IsolatedAsyncioTestCase):
    """Аварийный режим обязан быть временным.

    Бризер держит соединение сутками. Если облако легло в момент его
    подключения, а мы навсегда остались отвечать сами, устройство исчезает
    из приложения вендора до следующего передёрга питания — что и случилось
    18.08.2026 на живой гостиной.
    """

    PORT = 13902
    UPSTREAM_PORT = 13903

    async def test_session_is_dropped_when_cloud_returns(self):
        _mod.UPSTREAM_RETRY_INTERVAL = 0.2  # не ждать минуту в тесте
        self.addCleanup(setattr, _mod, "UPSTREAM_RETRY_INTERVAL", 60)

        channel = AtmeexLocalChannel(
            port=self.PORT, upstream=("127.0.0.1", self.UPSTREAM_PORT)
        )
        await channel.async_start()
        self.addCleanup(lambda: asyncio.ensure_future(channel.async_stop()))

        # Облако лежит: апстрим не слушает, канал должен обслужить сам.
        reader, writer = await asyncio.open_connection("127.0.0.1", self.PORT)
        writer.write(HELLO.encode())
        await writer.drain()

        acc, frames = "", []
        while len(frames) < 1:
            acc += (await asyncio.wait_for(reader.read(4096), timeout=5)).decode()
            frames, _ = split_json_objects(acc)
        self.assertIs(json.loads(frames[0]).get("hello"), True, "канал не ответил сам")

        # Облако вернулось.
        async def accept(r, w):
            w.close()

        cloud = await asyncio.start_server(accept, "127.0.0.1", self.UPSTREAM_PORT)
        self.addCleanup(cloud.close)

        # Сессию должны разорвать, чтобы бризер переподключился уже через облако.
        for _ in range(100):
            if reader.at_eof():
                break
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(reader.read(1), timeout=0.1)
        self.assertTrue(
            reader.at_eof(), "автономная сессия не разорвана после возвращения облака"
        )


class TestLocalCommands(unittest.IsolatedAsyncioTestCase):
    """Отправка команд в устройство по локальному каналу."""

    PORT = 13903

    async def asyncSetUp(self):
        self.channel = AtmeexLocalChannel(port=self.PORT, upstream=None)
        await self.channel.async_start()
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.PORT)
        self.writer.write(HELLO.encode())
        await self.writer.drain()
        # ответ на hello + два запроса — вычитываем, чтобы не мешали
        acc, frames = "", []
        while len(frames) < 3:
            acc += (await asyncio.wait_for(self.reader.read(4096), timeout=5)).decode()
            frames, _ = split_json_objects(acc)

    async def asyncTearDown(self):
        self.writer.close()
        await self.channel.async_stop()

    async def _receive(self, count):
        acc, frames = "", []
        while len(frames) < count:
            acc += (await asyncio.wait_for(self.reader.read(4096), timeout=5)).decode()
            frames, _ = split_json_objects(acc)
        return [json.loads(f) for f in frames]

    async def test_translates_cloud_params_into_channel_commands(self):
        ok = await self.channel.async_send_params(
            MAC, {"u_pwr_on": True, "u_fan_speed": 4}
        )
        self.assertTrue(ok)

        frames = await self._receive(4)  # две команды + get_setp + get_state
        cmds = [f["cmd"] for f in frames]
        self.assertIn({"set_pwr_on": True}, cmds)
        self.assertIn({"set_fan_speed": 4}, cmds)
        # адресация обязана быть той же, что использует облако
        self.assertTrue(all(f["id"] == DEVICE_ID for f in frames))
        # после записи просим устройство отчитаться, а не ждём телеметрии
        self.assertIn({"get_setp": True}, cmds)
        self.assertIn({"get_state": True}, cmds)

    async def test_refuses_when_device_is_not_connected(self):
        self.assertFalse(
            await self.channel.async_send_params("00:00:00:00:00:00", {"u_pwr_on": True})
        )

    async def test_unknown_parameter_does_not_break_the_rest(self):
        ok = await self.channel.async_send_params(
            MAC, {"u_fan_speed": 2, "u_something_new": 5}
        )
        self.assertTrue(ok, "известный параметр должен уйти несмотря на неизвестный")
        cmds = [f["cmd"] for f in await self._receive(3)]
        self.assertIn({"set_fan_speed": 2}, cmds)
        self.assertNotIn("u_something_new", json.dumps(cmds))

    async def test_is_connected_tracks_the_session(self):
        self.assertTrue(self.channel.is_connected(MAC))
        self.assertFalse(self.channel.is_connected("00:00:00:00:00:00"))


class TestCommanderPolicy(unittest.IsolatedAsyncioTestCase):
    """Куда уходит команда: облако первично, локальный канал подхватывает."""

    def setUp(self):
        self.commander_mod = _load("commander")
        self.api_calls: list[tuple] = []
        self.local_calls: list[tuple] = []
        outer = self

        class FakeApi:
            def __init__(self, fail=False):
                self.fail = fail

            async def set_device_params(self, device_id, **params):
                outer.api_calls.append((device_id, params))
                if self.fail:
                    raise RuntimeError("cloud is down")
                return {"ok": True}

        class FakeChannel:
            def __init__(self, connected=True):
                self._connected = connected

            def is_connected(self, mac):
                return self._connected

            async def async_send_params(self, mac, params):
                outer.local_calls.append((mac, params))
                return True

        self.FakeApi = FakeApi
        self.FakeChannel = FakeChannel

    def _commander(self, api, channel, mode):
        return self.commander_mod.AtmeexCommander(
            api,
            channel_getter=lambda: channel,
            mac_getter=lambda did: MAC,
            mode=mode,
        )

    async def test_cloud_first_uses_the_cloud_when_it_answers(self):
        c = self._commander(self.FakeApi(), self.FakeChannel(), "cloud_first")
        await c.set_fan_speed(17428, 3)
        self.assertEqual(self.api_calls, [(17428, {"u_fan_speed": 3})])
        self.assertEqual(self.local_calls, [], "локальный путь не нужен, облако живо")

    async def test_falls_back_to_local_when_the_cloud_fails(self):
        c = self._commander(self.FakeApi(fail=True), self.FakeChannel(), "cloud_first")
        await c.set_power(17428, True)
        self.assertEqual(len(self.api_calls), 1, "сначала пробуем облако")
        self.assertEqual(self.local_calls, [(MAC, {"u_pwr_on": True})])

    async def test_cloud_only_raises_instead_of_going_local(self):
        c = self._commander(self.FakeApi(fail=True), self.FakeChannel(), "cloud_only")
        with self.assertRaises(Exception):
            await c.set_power(17428, True)
        self.assertEqual(self.local_calls, [], "режим cloud_only обязан остаться в облаке")

    async def test_local_first_skips_the_cloud_entirely(self):
        c = self._commander(self.FakeApi(), self.FakeChannel(), "local_first")
        await c.set_target_temperature(17428, 20.5)
        self.assertEqual(self.local_calls, [(MAC, {"u_temp_room": 205})])
        self.assertEqual(self.api_calls, [], "локальный путь сработал, облако не трогаем")

    async def test_local_first_falls_back_to_cloud_when_device_is_offline(self):
        c = self._commander(
            self.FakeApi(), self.FakeChannel(connected=False), "local_first"
        )
        await c.set_humid_stage(17428, 2)
        self.assertEqual(self.local_calls, [])
        self.assertEqual(self.api_calls, [(17428, {"u_hum_stg": 2})])

    async def test_temperature_is_sent_in_tenths(self):
        c = self._commander(self.FakeApi(), self.FakeChannel(), "cloud_first")
        await c.set_target_temperature(17428, 18.0)
        self.assertEqual(self.api_calls[-1][1], {"u_temp_room": 180})


class TestSelfConnectionGuard(unittest.IsolatedAsyncioTestCase):
    """Подмена DNS действует и на сам Home Assistant.

    Если резолвить имя облака в лоб, канал соединяется сам с собой, принимает
    это за новое устройство и открывает ещё одно встречное — рекурсия без дна.
    Это случилось на живом доме 19.08.2026: сотни соединений в секунду.
    """

    PORT = 13904

    async def test_own_upstream_connection_is_rejected(self):
        channel = AtmeexLocalChannel(
            port=self.PORT, upstream=("127.0.0.1", self.PORT)  # облако = мы сами
        )
        await channel.async_start()
        self.addCleanup(lambda: asyncio.ensure_future(channel.async_stop()))

        reader, writer = await asyncio.open_connection("127.0.0.1", self.PORT)
        self.addCleanup(writer.close)
        writer.write(HELLO.encode())
        await writer.drain()

        # Канал открыл встречное «в облако» — то есть к себе же. Оно должно
        # быть распознано и закрыто, а не обслужено как новое устройство.
        for _ in range(100):
            await asyncio.sleep(0.02)
            if channel._own_ports:
                break

        await asyncio.sleep(0.3)
        # Ровно одно настоящее устройство, никакой лавины.
        self.assertLessEqual(
            len(channel._writers), 2, "соединения размножаются — защита не сработала"
        )

    async def test_poisoned_name_falls_back_to_the_api_hostname(self):
        """Когда имя канала указывает на нас, берём адрес по имени REST-API."""
        channel = AtmeexLocalChannel(port=self.PORT)
        own = channel._local_addresses()
        self.assertIn("127.0.0.1", own, "свои адреса должны определяться")

        async def fake_getaddrinfo(host, port, **kwargs):
            # имитируем отравленный резолв: имя ведёт на нас же
            return [(None, None, None, None, ("127.0.0.1", port))]

        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo
        loop.getaddrinfo = fake_getaddrinfo
        try:
            host, port = await channel._resolve_upstream()
        finally:
            loop.getaddrinfo = original

        self.assertEqual(host, _mod.UPSTREAM_FALLBACK_HOST)
        self.assertEqual(port, _mod.UPSTREAM_PORT)

    async def test_explicit_address_is_trusted_as_is(self):
        channel = AtmeexLocalChannel(port=self.PORT, upstream=("10.9.9.9", 3001))
        self.assertEqual(await channel._resolve_upstream(), ("10.9.9.9", 3001))


class TestLazyUpstream(unittest.IsolatedAsyncioTestCase):
    """Проверка порта не должна дёргать облако.

    netwatch стережёт этот же канал и раз в 30 секунд открывает соединение,
    ничего в него не отправляя. Если открывать встречное сразу на входящем,
    мы будем впустую соединяться с вендором дважды в минуту.
    """

    PORT = 13905
    UPSTREAM_PORT = 13906

    async def asyncSetUp(self):
        self.upstream_hits = 0

        async def on_upstream(reader, writer):
            self.upstream_hits += 1
            writer.close()

        self.cloud = await asyncio.start_server(
            on_upstream, "127.0.0.1", self.UPSTREAM_PORT
        )
        self.channel = AtmeexLocalChannel(
            port=self.PORT, upstream=("127.0.0.1", self.UPSTREAM_PORT)
        )
        await self.channel.async_start()

    async def asyncTearDown(self):
        await self.channel.async_stop()
        self.cloud.close()

    async def test_port_probe_does_not_reach_the_cloud(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.PORT)
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.3)
        self.assertEqual(
            self.upstream_hits, 0, "пустая проверка порта ушла в облако"
        )

    async def test_real_device_still_gets_the_cloud(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.PORT)
        self.addCleanup(writer.close)
        writer.write(HELLO.encode())
        await writer.drain()
        for _ in range(50):
            if self.upstream_hits:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(
            self.upstream_hits, 1, "кадр от устройства обязан открыть встречное"
        )


class TestOutdoorSensorDetection(unittest.TestCase):
    """Уличный датчик: A7 поле temp_out не отдаёт вовсе.

    Сущность создавалась всегда и вечно висела в unknown. Теперь она
    появляется только там, где значение действительно приходит.
    """

    def setUp(self):
        self.caps = _load("capabilities")

    def test_a7_frame_gives_no_outdoor_capability(self):
        """Настоящий кадр с живого A7: temp_out в нём отсутствует."""
        state = json.loads(STATE)["state"]
        self.assertNotIn("temp_out", state, "фикстура должна отражать реальный кадр")
        found = self.caps.detect_from_payload(state)
        self.assertNotIn(self.caps.CAP_OUTDOOR_TEMP, found)

    def test_real_outdoor_reading_enables_it(self):
        found = self.caps.detect_from_payload({"temp_out": -85})
        self.assertTrue(found.get(self.caps.CAP_OUTDOOR_TEMP))

    def test_zero_outdoor_is_still_a_reading(self):
        """Ноль градусов — нормальная температура, в отличие от нуля ppm CO2."""
        found = self.caps.detect_from_payload({"temp_out": 0})
        self.assertTrue(found.get(self.caps.CAP_OUTDOOR_TEMP))
