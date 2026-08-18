"""Слияние данных облака и локального канала.

Вынесено отдельно от __init__, чтобы проверяться тестами без установленного
Home Assistant.
"""

from __future__ import annotations

from typing import Any


# Поля, по которым нельзя судить об изменении: они меняются в каждом кадре.
VOLATILE_FIELDS = frozenset({"time", "created_at", "server_time"})


def payload_differs(
    data: dict[str, Any], key: str, did: str, payload: dict[str, Any]
) -> bool:
    """Есть ли в кадре что-то новое, кроме метки времени."""
    if key == "setp":
        current = next(
            (
                dev.get("settings") or {}
                for dev in data.get("devices") or []
                if isinstance(dev, dict) and str(dev.get("id")) == did
            ),
            {},
        )
    else:
        current = (data.get("states") or {}).get(did) or {}

    return any(
        field not in VOLATILE_FIELDS and current.get(field) != value
        for field, value in payload.items()
    )
