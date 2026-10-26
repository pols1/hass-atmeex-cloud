"""Команда охлаждения уходит только туда, где охлаждение включено.

26.10.2026 восстановленное облако ответило на включение бризера
422 «Неподдерживаемая команда: u_cool_mode», и автоматизация гостиной
остановилась, не дойдя до скорости вентилятора.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "atmeex_cloud"
    / "hvac_commands.py"
)
_spec = importlib.util.spec_from_file_location("atmeex_hvac_commands", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

commands_for_mode = _mod.commands_for_mode
POWER = _mod.POWER
COOL = _mod.COOL


class WithoutCoolingTest(unittest.TestCase):
    """Обычная запись: охлаждение выключено, COOL в списке режимов нет."""

    def test_switching_on_does_not_mention_cooling(self) -> None:
        self.assertEqual(
            commands_for_mode("fan_only", cool_supported=False), [(POWER, True)]
        )

    def test_switching_off_does_not_mention_cooling(self) -> None:
        self.assertEqual(
            commands_for_mode("off", cool_supported=False), [(POWER, False)]
        )


class WithCoolingTest(unittest.TestCase):
    """Охлаждение включено хозяином записи — снимать его при включении нужно."""

    def test_switching_on_clears_cooling(self) -> None:
        self.assertEqual(
            commands_for_mode("fan_only", cool_supported=True),
            [(POWER, True), (COOL, False)],
        )

    def test_cool_mode_switches_on_first_then_cools(self) -> None:
        # Порядок важен: выключенный бризер команду охлаждения не примет.
        self.assertEqual(
            commands_for_mode("cool", cool_supported=True),
            [(POWER, True), (COOL, True)],
        )

    def test_cool_mode_asked_for_explicitly_is_sent_either_way(self) -> None:
        # Если режим COOL всё-таки запрошен, молча подменять его на обдув нельзя:
        # пусть облако ответит отказом, который увидит хозяин.
        self.assertEqual(
            commands_for_mode("cool", cool_supported=False),
            [(POWER, True), (COOL, True)],
        )

    def test_switching_off_is_the_same_with_cooling(self) -> None:
        self.assertEqual(commands_for_mode("off", cool_supported=True), [(POWER, False)])


class UnknownModeTest(unittest.TestCase):
    def test_anything_that_is_not_off_or_cool_switches_the_breezer_on(self) -> None:
        # В списке режимов только fan_only, off и (по настройке) cool,
        # но подстановка «включить» безопаснее отказа без команды.
        self.assertEqual(commands_for_mode("auto", cool_supported=False), [(POWER, True)])


if __name__ == "__main__":
    unittest.main()
