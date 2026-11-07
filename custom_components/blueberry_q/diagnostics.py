"""Allowlisted device and authentication metadata, with no account identifiers."""
from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    store=hass.data.get(DOMAIN,{}).get(entry.entry_id,{})
    api=store.get('api')
    coordinator=store.get('coordinator')
    fields=('door_state','light_state','online','last_update','last_status',
            'sensor_comm_error','dps_no_communication')
    return {
        'mode':entry.data.get('mode'),
        'auth':{key:api.data.get(key) for key in (
            'expires_at','oauth_refreshed_at','app_check_expires_at',
            'app_check_synced_at','app_check_status','devices_synced_at')} if api else {},
        'devices':[{'family':device.get('device_family'),
                    'state_fields':sorted(device.get('state',{})),
                    'state':{key:device.get('state',{}).get(key) for key in fields
                             if key in device.get('state',{})}}
                   for device in (coordinator.data or {}).values()] if coordinator else [],
    }
