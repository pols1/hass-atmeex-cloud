"""Определение комплектации конкретного бризера.

AIRNANNY A7 продаётся семью вариантами, и они различаются начинкой:

    Simple, Flow            без увлажнителя, без датчика CO2
    Start                   увлажнитель есть, датчика CO2 нет
    BabyCare, Forever ×3    увлажнитель и датчик CO2

Ни в облачном API, ни в кадре hello модификация не передаётся: `model`
всегда «A7». Поэтому комплектацию определяем по самим показаниям.

CO2: в воздухе не бывает 0 ppm — на улице около 420, в помещении 500-1500.
Работающий датчик ноль отдать не может, значит `co2_ppm == 0` во всех
показаниях означает, что датчика нет. Проверено на живом Start: 1952 кадра
телеметрии, во всех ровно ноль.

Влажность: датчик влажности входит в комплект увлажнителя, поэтому живое
значение `hum_room` — признак, что увлажнитель есть. Наличие самого ключа
`hum_stg` признаком НЕ является: он приходит и там, где увлажнять нечем.

Определение «прилипчивое»: увидели признак хоть раз — считаем, что узел
есть, и больше не отзываем. Иначе секундный ноль от неисправного датчика
удалял бы сущность вместе с историей. Обратное решение (узла нет) всегда
можно переопределить в настройках интеграции.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

CAP_CO2 = "co2"
CAP_HUMIDIFIER = "humidifier"

# Значения переключателей в настройках
MODE_AUTO = "auto"
MODE_ON = "on"
MODE_OFF = "off"
MODES = [MODE_AUTO, MODE_ON, MODE_OFF]


def detect_from_payload(payload: dict[str, Any] | None) -> dict[str, bool]:
    """Признаки, которые видны в одном кадре состояния (condition/state).

    Возвращает только положительные находки: отсутствие признака в одном
    кадре ничего не доказывает, поэтому False здесь не выставляется.
    """
    found: dict[str, bool] = {}
    if not isinstance(payload, dict):
        return found

    co2 = payload.get("co2_ppm")
    if isinstance(co2, (int, float)) and co2 > 0:
        found[CAP_CO2] = True

    hum = payload.get("hum_room")
    if isinstance(hum, (int, float)) and hum > 0:
        found[CAP_HUMIDIFIER] = True

    # Ненулевая ступень увлажнения — тоже доказательство: выставить её
    # можно только там, где увлажнитель физически есть.
    for key in ("hum_stg", "u_hum_stg"):
        stage = payload.get(key)
        if isinstance(stage, (int, float)) and stage > 0:
            found[CAP_HUMIDIFIER] = True

    return found


def merge(known: dict[str, bool] | None, found: dict[str, bool]) -> dict[str, bool]:
    """Слить новые находки с накопленными. Признак только добавляется."""
    result = dict(known or {})
    for cap, value in found.items():
        if value and not result.get(cap):
            result[cap] = True
    return result


def resolve(cap: str, detected: dict[str, bool] | None, mode: str) -> bool:
    """Итоговый ответ: есть узел или нет.

    `mode` — переключатель из настроек: auto доверяет показаниям,
    on и off перебивают их вручную (сломанный датчик, нестандартная сборка).
    """
    if mode == MODE_ON:
        return True
    if mode == MODE_OFF:
        return False
    return bool((detected or {}).get(cap))


def describe(detected: dict[str, bool] | None) -> str:
    """Строка для лога: что нашли по показаниям."""
    detected = detected or {}
    parts = [
        f"датчик CO2: {'есть' if detected.get(CAP_CO2) else 'не обнаружен'}",
        f"увлажнитель: {'есть' if detected.get(CAP_HUMIDIFIER) else 'не обнаружен'}",
    ]
    return ", ".join(parts)
