"""Expose provider reported controller light and lock states."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN
from datetime import datetime, timezone
import time

FIELDS = {"light_state": "Garage light", "lock_state": "Controller lock", "remote_lock_state": "Remote control lock"}

async def async_setup_entry(hass, entry, async_add_entities):
    if entry.data.get("mode") != "cloud":
        return
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    api=hass.data[DOMAIN][entry.entry_id]['api']
    async_add_entities([MyQAuthState(coordinator,api,entry.entry_id,field,name) for field,name in {
        'oauth_status':'OAuth session', 'expires_at':'OAuth access token expiry',
        'oauth_refreshed_at':'OAuth last renewal', 'app_check_expires_at':'App Check expiry',
        'app_check_synced_at':'App Check last sync', 'devices_synced_at':'Device state last sync'}.items()])
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


class MyQAuthState(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = 'diagnostic'

    def __init__(self, coordinator, api, entry_id, field, name):
        super().__init__(coordinator)
        self.api, self.field = api, field
        self._attr_name=name
        self._attr_unique_id=f'myq_auth_{entry_id}_{field}'
        if field!='oauth_status':self._attr_device_class='timestamp'

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        if self.field=='oauth_status':
            if not self.coordinator.last_update_success:return 'update_failed'
            return 'expired' if self.api.data.get('expires_at',0)<=time.time() else 'ready'
        value=self.api.data.get(self.field)
        return datetime.fromtimestamp(value,timezone.utc) if value else None

    @property
    def extra_state_attributes(self):
        if self.field!='oauth_status':return {}
        return {'refresh_token_saved':bool(self.api.data.get('refresh_token')),
                'app_check_status':self.api.data.get('app_check_status','unknown'),
                'app_check_source':'BlueberryQDB' if self.api.data.get('app_bridge_url') else 'manual',
                'automatic_oauth_refresh':bool(self.api.data.get('refresh_token')),
                'credentials_storage':'Home Assistant config entry'}
