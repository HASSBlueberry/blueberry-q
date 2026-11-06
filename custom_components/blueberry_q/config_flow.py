import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from aiohttp import ClientError

from .cloud_api import MyQCloud

from .const import DOMAIN


class BlueberryQConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input:
            if user_input['mode'] == 'cloud':
                return await self.async_step_cloud()
            return await self.async_step_mqtt()
        return self.async_show_form(step_id='user', data_schema=vol.Schema({
            vol.Required('mode', default='cloud'): vol.In({'cloud': 'myQ provider sign-in', 'mqtt': 'Existing MQTT relay'})
        }))

    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context['entry_id'])
        return await self.async_step_cloud()

    async def async_step_cloud(self, user_input=None):
        if user_input:
            self._cloud = MyQCloud(async_get_clientsession(self.hass), user_input)
            self._auth_url = self._cloud.begin()
            return await self.async_step_callback()
        return self.async_show_form(step_id='cloud', data_schema=vol.Schema({
            vol.Required('client_id', default='ANDROID_CGI_MYQ'): str,
            vol.Required('redirect_uri', default='com.myqops://android'): str,
            vol.Optional('client_secret', default=''): str,
        }))

    async def async_step_callback(self, user_input=None):
        errors = {}
        if user_input:
            try:
                await self._cloud.finish(user_input['callback_url'])
                devices = await self._cloud.devices()
                if not devices:
                    errors['base'] = 'no_devices'
                    self._auth_url = self._cloud.begin()
                else:
                    account_ids = sorted({d['account_id'] for d in devices.values()})
                    await self.async_set_unique_id('cloud:' + ','.join(account_ids))
                    if getattr(self, '_reauth_entry', None):
                        if self._reauth_entry.unique_id != self.unique_id:
                            return self.async_abort(reason='wrong_account')
                        self.hass.config_entries.async_update_entry(self._reauth_entry, data={'mode': 'cloud', **self._cloud.data})
                        await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                        return self.async_abort(reason='reauth_successful')
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title='myQ cloud', data={'mode': 'cloud', **self._cloud.data})
            except (ClientError, TimeoutError, ValueError, KeyError):
                errors['base'] = 'cloud_auth_failed'
                self._auth_url = self._cloud.begin()
        return self.async_show_form(step_id='callback', data_schema=vol.Schema({
            vol.Required('callback_url'): str,
        }), description_placeholders={'auth_url': self._auth_url}, errors=errors)

    async def async_step_mqtt(self, user_input=None):
        if user_input:
            serial = user_input["serial"].strip()
            opener_id = user_input["opener_id"].replace(":", "").replace(" ", "")
            try:
                if len(serial) != 10 or "/" in serial:
                    raise ValueError
                int(serial, 16)
                raw_id = bytes.fromhex(opener_id)
                if len(raw_id) != 6:
                    raise ValueError
            except ValueError:
                return self.async_show_form(step_id="mqtt", data_schema=self._schema(),
                                            errors={"base": "invalid_opener_id"})
            await self.async_set_unique_id(serial.lower())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input.get("name") or "MyQ Garage Door",
                data={"serial": serial, "opener_id": opener_id.lower(),
                      "name": user_input.get("name") or "MyQ Garage Door"},
            )
        return self.async_show_form(step_id="mqtt", data_schema=self._schema())

    @staticmethod
    def _schema():
        return vol.Schema({
            vol.Required("serial"): str,
            vol.Required("opener_id"): str,
            vol.Optional("name", default="MyQ Garage Door"): str,
        })
