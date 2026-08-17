"""Тесты локального канала.

Фикстуры — настоящие кадры, снятые с бризера AirNanny A7 (прошивка 1.9/1.8/0.0)
перехватом 16.08.2026, включая склейку объектов без разделителя.

Модуль local_channel специально не зависит от homeassistant, поэтому тест
запускается без установленного HA:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
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
