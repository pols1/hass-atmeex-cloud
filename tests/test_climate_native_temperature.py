"""Температурные свойства называются по-новому.

Core 2026.11 переименовал температурные свойства климатической сущности:
значение отдаётся в единицах прибора (`native_*`), а пересчёт в единицы
пользователя делает сам Home Assistant. Прежние имена он принимает до
2027.11 и на каждой загрузке пишет в журнал:

    custom_components.atmeex_cloud.climate::AtmeexClimateEntity provides the
    deprecated target_temperature … use native_target_temperature instead

Сам класс здесь не поднять — climate.py тянет Home Assistant. Поэтому
проверка читает исходный текст: имя, которое вернётся копипастой из старого
кода, она поймает.
"""

from __future__ import annotations

import pathlib
import re
import unittest

_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "atmeex_cloud"
    / "climate.py"
).read_text(encoding="utf-8")

# Что именно запрещено: объявление свойства или присваивание _attr_.
# Внутри комментариев старые имена упоминать можно — там и объясняется, почему.
_WITHOUT_COMMENTS = "\n".join(
    line for line in _SOURCE.splitlines() if not line.lstrip().startswith("#")
)

DEPRECATED = ("current_temperature", "target_temperature", "temperature_unit")


class DeprecatedNamesAreGoneTest(unittest.TestCase):
    def test_no_deprecated_property_is_declared(self) -> None:
        for name in DEPRECATED:
            with self.subTest(name=name):
                self.assertIsNone(
                    re.search(rf"^\s*def {name}\s*\(", _WITHOUT_COMMENTS, re.MULTILINE),
                    f"climate.py объявляет устаревшее свойство {name}",
                )

    def test_no_deprecated_attr_is_set(self) -> None:
        for name in DEPRECATED:
            with self.subTest(name=name):
                self.assertIsNone(
                    re.search(rf"^\s*_attr_{name}\s*=", _WITHOUT_COMMENTS, re.MULTILINE),
                    f"climate.py задаёт устаревшее _attr_{name}",
                )


class NativeNamesArePresentTest(unittest.TestCase):
    """Отрицательный контроль к предыдущему: пустой файл его бы прошёл."""

    def test_native_properties_are_declared(self) -> None:
        for name in ("native_current_temperature", "native_target_temperature"):
            with self.subTest(name=name):
                self.assertIsNotNone(
                    re.search(rf"^\s*def {name}\s*\(", _WITHOUT_COMMENTS, re.MULTILINE),
                    f"climate.py не объявляет {name}",
                )

    def test_native_unit_is_set(self) -> None:
        self.assertIsNotNone(
            re.search(r"^\s*_attr_native_temperature_unit\s*=", _WITHOUT_COMMENTS, re.MULTILINE)
        )


class StillOwnNamesTest(unittest.TestCase):
    """Переименование не коснулось границ и шага — они и были в единицах прибора."""

    def test_limits_and_step_keep_their_names(self) -> None:
        for name in ("_attr_min_temp", "_attr_max_temp", "_attr_target_temperature_step"):
            with self.subTest(name=name):
                self.assertIsNotNone(
                    re.search(rf"^\s*{name}\s*=", _WITHOUT_COMMENTS, re.MULTILINE),
                    f"{name} пропал из climate.py",
                )


if __name__ == "__main__":
    unittest.main()
