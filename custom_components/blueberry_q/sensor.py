"""Expose provider reported controller light and lock states."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN

FIELDS = {"light_state": "Garage light", "lock_state": "Controller lock", "remote_lock_state": "Remote control lock"}

async def async_setup_entry(hass, entry, async_add_entities):
    if entry.data.get("mode") != "cloud":
        return
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    seen = set()
    def discover():
        entities = []
        for key, device in coordinator.data.items():
            for field, name in FIELDS.items():
                if field in device.get("state", {}) and (key, field) not in seen:
                    seen.add((key, field))
                    entities.append(MyQControllerState(coordinator, key, field, name))
        if entities:
            async_add_entities(entities)
    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))

class MyQControllerState(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    def __init__(self, coordinator, key, field, name):
        super().__init__(coordinator)
        self.key, self.field = key, field
        self._attr_name = name
        self._attr_unique_id = "myq_cloud_" + key + "_" + field
    @property
    def native_value(self):
        return self.coordinator.data.get(self.key, {}).get("state", {}).get(self.field)
    @property
    def available(self):
        device = self.coordinator.data.get(self.key, {})
        return super().available and device.get("state", {}).get("online") is True and self.field in device.get("state", {})
    @property
    def device_info(self):
        return DeviceInfo(identifiers={(DOMAIN, self.key)}, manufacturer="Chamberlain Group")
