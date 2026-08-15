from __future__ import annotations

import logging
from homeassistant.const import Platform

DOMAIN = "atmeex_cloud"

# Логгер интеграции (его импортируют climate/fan/select)
LOGGER = logging.getLogger(__package__)

# Платформы, которые поднимаем
PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    # если позже снова подключим отдельный fan/select, их можно сюда добавить:
    # Platform.FAN,
    # Platform.SELECT,
]