from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfTemperature,
    CONCENTRATION_PARTS_PER_MILLION,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    devices_raw = coordinator.data
    devices = devices_raw.get("devices", [])

    entities = []

    for dev in devices:
        if not isinstance(dev, dict):
            continue
        if dev.get("type") != 1:
            continue

        did = dev.get("id")
        if did is None:
            continue

        try:
            did_int = int(did)
        except Exception:
            continue

        name = dev.get("name") or f"Atmeex {did_int}"

        enable_co2 = entry.options.get("enable_co2", True)

        if enable_co2:
            entities.append(
                AtmeexCo2Sensor(
                    coordinator=coordinator,
                    device_id=did_int,
                    device_name=name,
                )
            )

        entities.append(
            AtmeexTempInSensor(
                coordinator=coordinator,
                device_id=did_int,
                device_name=name,
            )
        )
        entities.append(
            AtmeexTempOutSensor(
                coordinator=coordinator,
                device_id=did_int,
                device_name=name,
            )
        )

    if entities:
        async_add_entities(entities)


class AtmeexBaseSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, device_id: int, device_name: str, key: str, name_suffix: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._device_name = device_name
        self._key = key
        self._attr_name = f"{device_name} {name_suffix}"
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_has_entity_name = True

    @property
    def _dev(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        for dev in data.get("devices", []):
            if isinstance(dev, dict) and int(dev.get("id", -1)) == self._device_id:
                return dev
        return {}

    @property
    def _state(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return data.get("states", {}).get(str(self._device_id), {}) or {}

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._dev
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._device_id))},
            manufacturer="Atmeex",
            model=dev.get("model") or "A7",
            name=self._device_name,
            sw_version=dev.get("fw_ver"),
        )

    @property
    def available(self) -> bool:
        dev = self._dev
        return bool(dev.get("online", True))


class AtmeexCo2Sensor(AtmeexBaseSensor):
    _attr_device_class = SensorDeviceClass.CO2
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = CONCENTRATION_PARTS_PER_MILLION

    def __init__(self, coordinator, device_id: int, device_name: str) -> None:
        super().__init__(coordinator, device_id, device_name, "co2_ppm", "CO2")

    @property
    def native_value(self) -> int | None:
        val = self._state.get("co2_ppm")
        return int(val) if isinstance(val, (int, float)) else None

class AtmeexTempInSensor(AtmeexBaseSensor):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, device_id: int, device_name: str) -> None:
        super().__init__(coordinator, device_id, device_name, "temp_in", "Indoor Temperature")

    @property
    def native_value(self) -> float | None:
        val = self._state.get("temp_in")
        return (val / 10.0) if isinstance(val, (int, float)) else None

class AtmeexTempOutSensor(AtmeexBaseSensor):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, device_id: int, device_name: str) -> None:
        super().__init__(coordinator, device_id, device_name, "temp_out", "Outdoor Temperature")

    @property
    def native_value(self) -> float | None:
        val = self._state.get("temp_out")
        return (val / 10.0) if isinstance(val, (int, float)) else None
