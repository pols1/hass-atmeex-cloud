from __future__ import annotations

import logging
from homeassistant.const import Platform

DOMAIN = "atmeex_cloud"

# Локальный канал: приёмная сторона соединения, которое бризер сам открывает
# к облаку. Требует заворачивания трафика на Home Assistant — см. README.
CONF_LOCAL_ENABLED = "local_enabled"
CONF_LOCAL_PORT = "local_port"
DEFAULT_LOCAL_ENABLED = False
DEFAULT_LOCAL_PORT = 3001

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