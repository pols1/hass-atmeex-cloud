"""Когда изменение записи конфигурации требует перезагрузки интеграции.

Home Assistant зовёт слушателя обновлений на ЛЮБОЕ изменение записи — и
настроек, и данных (config_entries._async_save_and_notify). Раньше слушатель
перезагружал интеграцию целиком всегда, а данные меняются при каждом продлении
токена: JWT живёт три часа, и интеграция сама себя перезапускала каждые
2 ч 59 мин. Сущности на долю секунды становились недоступными, автоматизации
с триггером «вернулся из unavailable» срабатывали и слали команды в облако,
локальный канал закрывался вместе с соединением бризера.

Вынесено отдельно от __init__, чтобы проверяться тестами без Home Assistant.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Настройки, которые применяются на ходу, без перезагрузки.
LIVE_OPTIONS = frozenset({"write_mode"})

# Данные, от которых зависит набор сущностей: найденный узел (датчик CO2,
# увлажнитель) должен появиться сразу, а не после следующего перезапуска.
# Токены сюда не входят намеренно. Пароль тоже: повторный вход перезагружает
# интеграцию сам.
RELOAD_DATA_KEYS = frozenset({"capabilities"})


def changed_keys(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    defaults: Mapping[str, Any] | None = None,
) -> set[str]:
    """Ключи, у которых изменилось действующее значение.

    Отсутствующий ключ равен своему значению по умолчанию. Форма настроек
    сохраняет все поля сразу, так что первое сохранение после обновления
    дописывает новые поля с умолчаниями — это не изменение.
    """
    defaults = defaults or {}
    return {
        key
        for key in set(before) | set(after)
        if before.get(key, defaults.get(key)) != after.get(key, defaults.get(key))
    }


def needs_reload(
    old_options: Mapping[str, Any],
    new_options: Mapping[str, Any],
    old_data: Mapping[str, Any],
    new_data: Mapping[str, Any],
    option_defaults: Mapping[str, Any] | None = None,
) -> bool:
    if changed_keys(old_options, new_options, option_defaults) - LIVE_OPTIONS:
        return True
    return bool(changed_keys(old_data, new_data) & RELOAD_DATA_KEYS)
