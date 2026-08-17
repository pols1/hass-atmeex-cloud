from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)

PRESET_NONE = "none"
PRESET_AUTO = "auto"
PRESET_SLEEP = "sleep"
from homeassistant.const import (
    UnitOfTemperature,
    ATTR_TEMPERATURE,
    PRECISION_WHOLE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .capabilities import CAP_HUMIDIFIER, resolve
from .const import CONF_HUMIDIFIER, DATA_CAPABILITIES, DEFAULT_CAP_MODE, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Скорости вентилятора в UI
FAN_MODES = ["1", "2", "3", "4", "5", "6", "7"]

# Режимы заслонки
BRIZER_SWING_MODES = [
    "приточная вентиляция",  # 0
    "рециркуляция",          # 1
    "смешанный режим",       # 2
]

# Допустимые уровни целевой влажности (для «прилипания» слайдера)
HUM_ALLOWED = [0, 33, 66, 100]


def _quantize_humidity(val: int | float | None) -> int:
    """Ближайшее из 0/33/66/100."""
    if val is None:
        return 0
    v = max(0, min(100, int(round(val))))
    return min(HUM_ALLOWED, key=lambda x: abs(x - v))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Создание climate-entity по entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    api = data["api"]
    refresh_device: Callable[[int], Any] = data["refresh_device"]

    devices_raw = coordinator.data
    devices = devices_raw.get("devices", [])

    entities: list[AtmeexClimateEntity] = []

    dev_ids_for_log: list[int] = []

    for dev in devices:
        if not isinstance(dev, dict):
            continue
        if dev.get("type") != 1:  # только бризеры
            continue

        did = dev.get("id")
        if did is None:
            continue

        try:
            did_int = int(did)
        except Exception:
            continue

        name = dev.get("name") or f"Atmeex {did_int}"

        entities.append(
            AtmeexClimateEntity(
                coordinator=coordinator,
                api=api,
                entry_id=entry.entry_id,
                device_id=did_int,
                name=name,
                refresh_device=refresh_device,
            )
        )
        dev_ids_for_log.append(did_int)

    if entities:
        _LOGGER.debug(
            "Atmeex climate: async_setup_entry for %s, found %d devices: %s",
            entry.entry_id,
            len(dev_ids_for_log),
            dev_ids_for_log,
        )
        async_add_entities(entities)
    else:
        _LOGGER.warning(
            "Atmeex climate: no entities created for entry %s. devices_raw=%r, coordinator.data=%r",
            entry.entry_id,
            devices_raw,
            coordinator.data,
        )


class AtmeexClimateEntity(CoordinatorEntity, ClimateEntity):
    """
    Climate-сущность бризера A7:
    - температура
    - 7 скоростей вентилятора
    - режим заслонки
    - (опционально) ступенчатый увлажнитель
    """

    # Базовые фичи
    _base_supported = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.PRESET_MODE
    )

    _attr_preset_modes = [PRESET_NONE, PRESET_AUTO, PRESET_SLEEP]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_WHOLE
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 10
    _attr_max_temp = 30

    _attr_fan_modes = FAN_MODES
    _attr_swing_modes = BRIZER_SWING_MODES
    _attr_icon = "mdi:air-purifier"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        api,
        entry_id: str,
        device_id: int,
        name: str,
        refresh_device: Callable[[int], Any],
    ) -> None:
        super().__init__(coordinator)
        self.api = api
        self._entry_id = entry_id
        self._device_id = int(device_id)
        self._attr_name = name
        self._refresh_device = refresh_device
        self._attr_unique_id = f"{self._device_id}_climate"

    # ---------- вспомогательные ----------

    @property
    def device_id(self) -> int:
        return self._device_id

    @property
    def _dev(self) -> dict[str, Any]:
        """Сырой объект устройства из coordinator.data['devices']."""
        data = self.coordinator.data or {}
        for dev in data.get("devices", []):
            if isinstance(dev, dict) and int(dev.get("id", -1)) == self._device_id:
                return dev
        return {}

    @property
    def _state(self) -> dict[str, Any]:
        """Текущее состояние из coordinator.data['states']."""
        data = self.coordinator.data or {}
        return data.get("states", {}).get(str(self._device_id), {}) or {}

    @property
    def _settings(self) -> dict[str, Any]:
        """Набор u_* параметров из settings устройства."""
        return self._dev.get("settings", {}) or {}

    @property
    def _cond(self) -> dict[str, Any]:
        """
        Сводный dict вида:
        - сначала condition (датчики)
        - поверх settings (u_*)
        """
        merged: dict[str, Any] = {}
        merged.update(self._state)
        merged.update(self._settings)
        return merged

    async def _refresh(self) -> None:
        """Попросить coordinator обновить данные именно для этого девайса."""
        cb: Optional[Callable[[int], Any]] = self._refresh_device
        if cb is None:
            return
        res = cb(self._device_id)
        # на всякий случай поддержим async/sync
        if hasattr(res, "__await__"):
            await res  # type: ignore[func-returns-value]

    # ---------- device_info (чтобы entity БЫЛА привязана к device) ----------

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._dev
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._device_id))},
            manufacturer="Atmeex",
            model=dev.get("model") or "A7",
            name=dev.get("name") or f"Atmeex {self._device_id}",
            sw_version=dev.get("fw_ver"),
        )

    # ---------- доступность ----------

    @property
    def available(self) -> bool:
        dev = self._dev
        return bool(dev.get("online", True))

    # ---------- поддержка фич ----------

    @property
    def supported_features(self) -> int:
        features = self._base_supported
        if self._has_humidifier():
            features |= ClimateEntityFeature.TARGET_HUMIDITY
        return features

    def _has_humidifier(self) -> bool:
        """Есть ли увлажнитель у этой модификации A7.

        По наличию ключа hum_stg судить нельзя: он приходит и от Simple/Flow,
        где увлажнять нечем. Спрашиваем накопленную комплектацию, которую
        можно перебить переключателем в настройках.
        """
        entry = self.coordinator.config_entry
        if entry is None:
            return False
        detected = (entry.data.get(DATA_CAPABILITIES) or {}).get(str(self._device_id), {})
        return resolve(
            CAP_HUMIDIFIER,
            detected,
            entry.options.get(CONF_HUMIDIFIER, DEFAULT_CAP_MODE),
        )

    # ---------- HVAC ----------

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = [HVACMode.FAN_ONLY, HVACMode.OFF]
        # Проверяем настройки конфигурации
        if self.coordinator.config_entry and self.coordinator.config_entry.options.get("enable_cool", False):
            modes.append(HVACMode.COOL)
        return modes

    @property
    def hvac_mode(self) -> HVACMode:
        on = self._settings.get("u_pwr_on")
        if on is None:
            on = self._state.get("pwr_on")
        
        if not bool(on):
            return HVACMode.OFF
            
        cool = self._settings.get("u_cool_mode")
        if cool is None:
            cool = self._state.get("cool_mode")
            
        if bool(cool) and HVACMode.COOL in self.hvac_modes:
            return HVACMode.COOL
            
        return HVACMode.FAN_ONLY

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.api.set_power(self._device_id, False)
        elif hvac_mode == HVACMode.COOL:
            # Сначала включаем, затем переводим в COOL
            await self.api.set_power(self._device_id, True)
            await self.api.set_cool_mode(self._device_id, True)
        else:
            # FAN_ONLY: включаем и отключаем COOL
            await self.api.set_power(self._device_id, True)
            await self.api.set_cool_mode(self._device_id, False)
        await self._refresh()

    # ---------- Преднастройки (Presets) ----------

    @property
    def preset_mode(self) -> str | None:
        is_auto = self._settings.get("u_auto")
        if is_auto is None:
            is_auto = self._state.get("auto", False)
        
        is_night = self._settings.get("u_night")
        if is_night is None:
            is_night = self._state.get("night", False)

        if is_auto:
            return PRESET_AUTO
        if is_night:
            return PRESET_SLEEP
        return PRESET_NONE

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == PRESET_AUTO:
            await self.api.set_auto_mode(self._device_id, True)
            await self.api.set_night_mode(self._device_id, False)
        elif preset_mode == PRESET_SLEEP:
            await self.api.set_night_mode(self._device_id, True)
            await self.api.set_auto_mode(self._device_id, False)
        else: # PRESET_NONE
            await self.api.set_auto_mode(self._device_id, False)
            await self.api.set_night_mode(self._device_id, False)
        await self._refresh()

    # ---------- Температура ----------

    @property
    def current_temperature(self):
        val = self._state.get("temp_room")  # деци-°C
        return (val / 10) if isinstance(val, (int, float)) else None

    @property
    def target_temperature(self):
        # u_temp_room в деци-градусах
        val = self._settings.get("u_temp_room")
        if isinstance(val, (int, float)):
            return val / 10
        # запасной вариант — текущая / 20.0
        cur = self.current_temperature
        return cur if isinstance(cur, (int, float)) else 20.0

    async def async_set_temperature(self, **kwargs) -> None:
        t = kwargs.get(ATTR_TEMPERATURE)
        if t is None:
            return
        # если было выключено — включаем
        if not bool(self._settings.get("u_pwr_on") or self._state.get("pwr_on")):
            await self.api.set_power(self._device_id, True)
        await self.api.set_target_temperature(self._device_id, float(t))
        await self._refresh()

    # ---------- Влажность (слайдер с квантованием 0/33/66/100) ----------

    @property
    def current_humidity(self) -> int | None:
        """Текущая влажность из датчика."""
        val = self._state.get("hum_room")
        return int(val) if isinstance(val, (int, float)) else None

    @property
    def target_humidity(self) -> int | None:
        """Показываем одно из 0/33/66/100 (по текущей ступени hum_stg)."""
        if not self._has_humidifier():
            return None
        stg = self._cond.get("hum_stg")
        if not isinstance(stg, (int, float)):
            stg = 0
        stg = max(0, min(3, int(stg)))
        return HUM_ALLOWED[stg]

    async def async_set_humidity(self, humidity: int) -> None:
        """Принимаем любое число, квантируем в 0/33/66/100 → ступень 0..3."""
        if not self._has_humidifier():
            return
        q = _quantize_humidity(humidity)
        stage = HUM_ALLOWED.index(q)  # 0..3
        await self.api.set_humid_stage(self._device_id, stage)
        await self._refresh()

    # ---------- Вентилятор ----------

    @property
    def fan_mode(self) -> str | None:
        """
        Отображаем текущую скорость:
        - приоритет у u_fan_speed (настройка)
        - иначе fan_speed из состояния
        ВАЖНО: в API это 1..7, в UI — строки "1".."7".
        """
        spd = self._settings.get("u_fan_speed")
        if not isinstance(spd, (int, float)):
            spd = self._state.get("fan_speed")

        if isinstance(spd, (int, float)):
            spd = int(spd)

        if isinstance(spd, int) and 1 <= spd <= 7:
            # Возвращаем 1..7 напрямую как строку
            return str(spd)

        return None

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """
        fan_mode — это строка из FAN_MODES, т.е. "1".."7".
        На API отправляем целое 1..7 (БЕЗ смещения).
        """
        if fan_mode not in FAN_MODES:
            _LOGGER.warning("Unsupported fan_mode: %s", fan_mode)
            return

        try:
            speed = int(fan_mode)
        except Exception:
            _LOGGER.warning("Cannot parse fan_mode to int: %s", fan_mode)
            return

        # speed 1..7 — как ожидает API
        await self.api.set_fan_speed(self._device_id, speed)
        await self._refresh()

    # ---------- Swing = режим бризера (заслонка) ----------

    @property
    def swing_mode(self) -> str | None:
        """
        Режим заслонки:
        - берём u_damp_pos (целевой режим), если есть
        - иначе damp_pos из состояния
        Индекс 0..2 → BRIZER_SWING_MODES[index]
        """
        pos = self._settings.get("u_damp_pos")
        if not isinstance(pos, int):
            pos = self._state.get("damp_pos")

        if isinstance(pos, int) and 0 <= pos < len(BRIZER_SWING_MODES):
            return BRIZER_SWING_MODES[pos]
        return None

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """
        В UI выбирается одна из BRIZER_SWING_MODES,
        на API отправляем индекс 0..2.
        """
        if swing_mode not in BRIZER_SWING_MODES:
            _LOGGER.warning("Unsupported swing_mode: %s", swing_mode)
            return

        idx = BRIZER_SWING_MODES.index(swing_mode)
        await self.api.set_brizer_mode(self._device_id, idx)
        await self._refresh()

    # ---------- Атрибуты для UI/отладки ----------

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs: dict[str, Any] = {}

        attrs["device_id"] = self._device_id

        # Сырые структуры для дебага
        attrs["raw_state"] = self._state
        attrs["raw_settings"] = self._settings

        tr = self._state.get("temp_room")
        ut = self._settings.get("u_temp_room")

        if isinstance(tr, (int, float)):
            attrs["room_temp_c"] = round(tr / 10, 1)
        if isinstance(ut, (int, float)):
            attrs["target_temp_c"] = round(ut / 10, 1)

        attrs["has_humidifier"] = self._has_humidifier()

        return attrs