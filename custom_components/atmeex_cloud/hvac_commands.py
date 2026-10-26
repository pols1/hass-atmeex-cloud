"""Какие команды отправить при смене режима работы.

26.10.2026 восстановленное облако Atmeex отклонило команду охлаждения:

    PUT /devices/36/params failed 422:
    {"message": "Неподдерживаемая команда: u_cool_mode"}

Интеграция отправляла `u_cool_mode: false` при каждом включении бризера —
«включить и снять охлаждение», — даже когда охлаждение выключено в настройках
записи и режима COOL нет в списке доступных. Отказ облака поднимался из
climate.set_hvac_mode наружу, и автоматизация гостиной останавливалась на
первом же действии, не доходя до скорости. Признак был виден и в самих
настройках, которые отдаёт облако: ключа `u_cool_mode` в них больше нет.

Поэтому команду охлаждения отправляем только там, где охлаждение включено
хозяином записи. Остальные команды облако принимает: `u_pwr_on` и
`u_fan_speed` проверены живым вызовом 26.10.2026.

Модуль не зависит от Home Assistant и проверяется тестами на голом Python.
"""

from __future__ import annotations

# Значения HVACMode из Home Assistant: там это строковое перечисление,
# поэтому сравниваем со строками и обходимся без импорта.
MODE_OFF = "off"
MODE_FAN_ONLY = "fan_only"
MODE_COOL = "cool"

POWER = "power"
COOL = "cool"


def commands_for_mode(mode: str, *, cool_supported: bool) -> list[tuple[str, bool]]:
    """Последовательность (команда, значение) для перехода в режим."""
    if mode == MODE_OFF:
        return [(POWER, False)]

    if mode == MODE_COOL:
        return [(POWER, True), (COOL, True)]

    commands = [(POWER, True)]
    if cool_supported:
        commands.append((COOL, False))
    return commands
