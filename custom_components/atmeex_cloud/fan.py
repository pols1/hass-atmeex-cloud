from homeassistant.components.fan import FanEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

class AtmeexFan(CoordinatorEntity, FanEntity):
    def __init__(self, coordinator, api, device):
        super().__init__(coordinator)
        self.api = api
        self._device_id = device["id"]
        self._attr_name = device["name"]

    @property
    def condition(self):
        return self.coordinator.data["states"].get(str(self._device_id), {})

    @property
    def is_on(self):
        return self.condition.get("pwr_on", False)

    @property
    def percentage(self):
        return self.condition.get("fan_speed")

    async def async_set_percentage(self, percentage):
        await self.api.set_fan_speed(self._device_id, int(percentage))
        await self.coordinator.async_request_refresh()
