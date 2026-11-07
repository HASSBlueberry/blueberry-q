from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed
from aiohttp import ClientResponseError, ClientError
from datetime import timedelta
import logging
import asyncio
from .cloud_api import MyQCloud, MyQCloudAuthError
from .native_api import NativeMyQ

from .const import DOMAIN


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Retain existing account credentials and MQTT identities during upgrade."""
    if entry.version > 2:
        return False
    if entry.version < 2:
        data = dict(entry.data)
        data.setdefault('mode', 'cloud' if data.get('refresh_token') or data.get('access_token') else 'mqtt')
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    store = {}
    if entry.data.get('mode') in ('cloud','native'):
        native=entry.data.get('mode')=='native'
        client = (NativeMyQ if native else MyQCloud)(async_get_clientsession(hass), entry.data)
        def persist():
            if client.data != dict(entry.data):
                hass.config_entries.async_update_entry(entry, data=dict(client.data))
        if not native:client.on_credentials_changed=persist
        async def update():
            try:
                result = await client.devices()
                return result
            except MyQCloudAuthError as err:
                raise ConfigEntryAuthFailed('myQ session needs a new sign-in') from err
            except ClientResponseError as err:
                if err.status == 401:
                    raise ConfigEntryAuthFailed('myQ sign-in expired') from err
                raise UpdateFailed(f'myQ provider returned HTTP {err.status}') from err
            except (ClientError, TimeoutError, ValueError, KeyError) as err:
                raise UpdateFailed('myQ account refresh failed') from err
            finally:
                # A successful refresh can rotate credentials before device
                # discovery fails. Persist rotation even on that failure.
                persist()
        coordinator = DataUpdateCoordinator(hass, logging.getLogger(__name__), name='myQ phone controller' if native else 'myQ cloud',
                                            update_method=update, update_interval=timedelta(seconds=15 if native else 30))
        await coordinator.async_config_entry_first_refresh()
        store = {'api': client, 'coordinator': coordinator}
        async def follow_command():
            try:
                for delay in (2, 3, 5, 10, 10):
                    await asyncio.sleep(delay)
                    await coordinator.async_request_refresh()
            except asyncio.CancelledError:
                pass
        def schedule_follow():
            task=store.get('command_poll')
            if task and not task.done():task.cancel()
            store['command_poll']=hass.async_create_task(follow_command())
        if not native:coordinator.async_follow_command=schedule_follow
        def cancel_follow():
            task=store.get('command_poll')
            if task:task.cancel()
        entry.async_on_unload(cancel_follow)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = store
    await hass.config_entries.async_forward_entry_setups(entry, ["cover", "sensor"])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, ["cover", "sensor"])
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
