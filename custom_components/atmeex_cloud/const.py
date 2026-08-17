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

# Комплектация устройства. У A7 семь модификаций: датчик CO2 стоит только
# в старших, увлажнитель — во всех кроме Simple и Flow. Модель в API не
# передаётся, поэтому узлы определяются по показаниям (см. capabilities.py),
# а эти переключатели позволяют перебить определение вручную.
CONF_CO2_SENSOR = "co2_sensor"
CONF_HUMIDIFIER = "humidifier"
DEFAULT_CAP_MODE = "auto"

# Накопленные признаки комплектации, ключ — id устройства в облаке.
# Живут в entry.data, чтобы не переопределяться заново после перезапуска.
DATA_CAPABILITIES = "capabilities"

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