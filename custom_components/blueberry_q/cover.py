"""Home Assistant cover for a MyQ device relayed through the MQTT integration."""
from __future__ import annotations

import logging
from aiohttp import ClientError

from homeassistant.components import mqtt
from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .protocol import DeviceTopics, command_payload, decode_state

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.data.get('mode') in ('cloud','native'):
        store = hass.data[DOMAIN][entry.entry_id]
        seen = set()
        def discover():
            keys = {key for key,device in store['coordinator'].data.items()
                    if device.get('device_family')=='garagedoor' or device.get('native_binding_id')} - seen
            seen.update(keys)
            if keys:
                async_add_entities([MyQCloudCover(store['coordinator'], store['api'], key) for key in keys])
        discover()
        entry.async_on_unload(store['coordinator'].async_add_listener(discover))
        return
    async_add_entities([MyQCover(entry.data["serial"], entry.data["opener_id"],
                                entry.data["name"])])


class MyQCover(CoverEntity):
    _attr_has_entity_name = True
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_device_class = "garage"

    def __init__(self, serial: str, opener_id: str, name: str):
        self._serial = serial
        self._opener_id = bytes.fromhex(opener_id)
        self._topics = DeviceTopics(serial)
        self._state = "unknown"
        self._unsubscribe = None
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{serial.lower()}_door"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial.lower())},
            name=self._attr_name,
            manufacturer="Chamberlain Group",
            model="MyQ Security 2.0 MQTT bridge",
        )

    @property
    def available(self):
        return self._state != "unknown"

    @property
    def is_closed(self):
        return self._state == "closed" if self._state in ("open", "closed") else None

    @property
    def is_opening(self):
        return self._state == "opening"

    @property
    def is_closing(self):
        return self._state == "closing"

    @property
    def current_cover_position(self):
        if self._state == "open":
            return 100
        if self._state == "closed":
            return 0
        return None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._unsubscribe = await mqtt.async_subscribe(
            self.hass, self._topics.telemetry, self._handle_state, qos=1, encoding=None
        )

    async def async_will_remove_from_hass(self):
        if self._unsubscribe:
            self._unsubscribe()

    def _handle_state(self, message):
        try:
            payload = message.payload
            if isinstance(payload, str):
                payload = payload.encode("latin-1")
            self._state = decode_state(bytes(payload))
            self.async_write_ha_state()
        except (TypeError, ValueError):
            _LOGGER.debug("Ignoring unrecognized MyQ telemetry on %s", self._topics.telemetry)

    async def async_open_cover(self, **kwargs):
        await self._send("open")

    async def async_close_cover(self, **kwargs):
        await self._send("close")

    async def _send(self, action):
        payload = command_payload(self._opener_id, action)
        try:
            await mqtt.async_publish(self.hass, self._topics.command, payload, qos=1, retain=False)
        except Exception as err:
            raise HomeAssistantError(f"MyQ MQTT command failed: {err}") from err


class MyQCloudCover(CoordinatorEntity, CoverEntity):
    _attr_has_entity_name = True
    _attr_device_class = "garage"
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(self, coordinator, api, key):
        super().__init__(coordinator)
        self.api = api
        self.key = key
        self._attr_unique_id = ('myq_native_' if api.data.get('mode')=='native' else 'myq_cloud_') + key
        self._attr_name = coordinator.data[key].get('name', 'Garage door')

    @property
    def device(self):
        return self.coordinator.data.get(self.key, {})

    @property
    def available(self):
        return super().available and self.device.get('state', {}).get('online') is True

    @property
    def is_closed(self):
        state = self.device.get('state', {}).get('door_state')
        return state == 'closed' if state in ('closed', 'open') else None

    @property
    def is_opening(self):
        return self.device.get('state', {}).get('door_state') == 'opening'

    @property
    def is_closing(self):
        return self.device.get('state', {}).get('door_state') == 'closing'

    @property
    def extra_state_attributes(self):
        state=self.device.get('state',{})
        return {key:state[key] for key in ('last_update','last_status','sensor_comm_error','dps_no_communication') if key in state}

    @property
    def device_info(self):
        return DeviceInfo(identifiers={(DOMAIN, self.key)}, name=self._attr_name,
                          manufacturer='Chamberlain Group', model='MyQ native phone controller' if self.api.data.get('mode')=='native' else 'myQ cloud opener')

    async def _command(self, action):
        try:
            await self.api.command(self.device, action)
        except (ValueError, TimeoutError, ClientError) as err:
            if self.api.data.get('mode')=='native':
                raise HomeAssistantError('MyQ phone controller could not complete the action. '+getattr(err,'reason','Check the phone screen.')) from err
            raise HomeAssistantError('myQ provider rejected the door command') from err
        await self.coordinator.async_request_refresh()
        follow=getattr(self.coordinator,'async_follow_command',None)
        if follow:follow()

    async def async_open_cover(self, **kwargs):
        await self._command('open')

    async def async_close_cover(self, **kwargs):
        await self._command('close')
