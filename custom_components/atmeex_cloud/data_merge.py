"""Слияние данных облака и локального канала.

Вынесено отдельно от __init__, чтобы проверяться тестами без установленного
Home Assistant.
"""

from __future__ import annotations

from datetime import datetime, timezone
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

# Насколько снимок в облаке может отставать от того, что устройство прислало
# нам, прежде чем считать, что поток до облака не доходит. Устройство шлёт
# состояние каждые ~5 с, облако сохраняет его метку — значит при исправном
# канале разрыв держится в секундах. Пять минут — запас на медленный обмен.
CLOUD_LAG_LIMIT = 300


def _frame_time(payload: Any) -> float | None:
    """Метка времени кадра «ГГГГ-ММ-ДД ЧЧ:ММ:СС» в секундах, если она есть."""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("time")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except ValueError:
        return None


def cloud_lags_behind_device(
    cloud_condition: Any, local_state: Any, limit: float = CLOUD_LAG_LIMIT
) -> float | None:
    """На сколько секунд снимок облака отстал от кадров устройства.

    Обе метки ставит само устройство: в кадре, который мы читаем с канала, и
    в том же кадре, дошедшем до облака. Поэтому их можно сравнивать напрямую.
    Возвращает отставание, если оно больше limit, иначе None.

    Так 24.09.2026 нашлась авария: встречное соединение канала умерло молча,
    устройство продолжало слать нам кадры, а в облаке его снимок замер — и
    команды оттуда, включая расписание, до бризера не доходили двое суток.
    """
    device_time = _frame_time(local_state)
    cloud_time = _frame_time(cloud_condition)
    if device_time is None or cloud_time is None:
        return None
    lag = device_time - cloud_time
    return lag if lag > limit else None

