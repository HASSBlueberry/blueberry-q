from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed
from aiohttp import ClientResponseError, ClientError
from datetime import timedelta
import logging
from .cloud_api import MyQCloud, MyQCloudAuthError

from .const import DOMAIN


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    store = {}
    if entry.data.get('mode') == 'cloud':
        client = MyQCloud(async_get_clientsession(hass), entry.data)
        async def update():
            try:
                result = await client.devices()
                if client.data != dict(entry.data):
                    hass.config_entries.async_update_entry(entry, data=client.data)
                return result
            except MyQCloudAuthError as err:
                raise ConfigEntryAuthFailed('myQ session needs a new sign-in') from err
            except ClientResponseError as err:
                if err.status == 401:
                    raise ConfigEntryAuthFailed('myQ sign-in expired') from err
                raise UpdateFailed(f'myQ provider returned HTTP {err.status}') from err
            except (ClientError, TimeoutError, ValueError, KeyError) as err:
                raise UpdateFailed('myQ account refresh failed') from err
        coordinator = DataUpdateCoordinator(hass, logging.getLogger(__name__), name='myQ cloud',
                                            update_method=update, update_interval=timedelta(seconds=60))
        await coordinator.async_config_entry_first_refresh()
        store = {'api': client, 'coordinator': coordinator}
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = store
    await hass.config_entries.async_forward_entry_setups(entry, ["cover", "sensor"])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, ["cover", "sensor"])
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
