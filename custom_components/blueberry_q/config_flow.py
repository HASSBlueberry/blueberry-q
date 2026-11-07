import asyncio
import voluptuous as vol
from homeassistant.helpers import selector
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from aiohttp import ClientError,ClientResponseError
from .cloud_api import MyQCloud,MyQCloudAuthError,MyQCloudNetworkError,OAuthCallbackError,TokenExchangeError
from .login import BrowserRequired,InvalidCredentials,ProviderHTTPError,ProviderNetworkError,VerificationRequired,VerificationMethodRequired
from .const import DOMAIN

class BlueberryQConfigFlow(config_entries.ConfigFlow,domain=DOMAIN):
    VERSION=1

    def async_remove(self):
        if getattr(self,'_cloud',None):asyncio.create_task(self._cloud.close_login())

    async def async_step_user(self,user_input=None):
        if not self.show_advanced_options:return await self.async_step_login(user_input)
        if user_input:
            return await {'cloud':self.async_step_cloud,'password':self.async_step_login,'mqtt':self.async_step_mqtt}[user_input['mode']]()
        return self.async_show_form(step_id='user',data_schema=vol.Schema({
            vol.Required('mode',default='password'):vol.In({'password':'myQ email and password','cloud':'Provider browser sign-in, including Amazon when offered','mqtt':'Existing MQTT relay'})}))

    async def _auth_operation(self,operation,step):
        try:await operation()
        except VerificationRequired as error:
            return await self.async_step_verification(_error='verification_invalid' if error.invalid else None)
        except VerificationMethodRequired as error:
            self._verification_methods=error.options
            return await self.async_step_verification_method()
        except InvalidCredentials:return await self.async_step_login(_error='invalid_auth')
        except BrowserRequired:
            await self._cloud.close_login()
            self._auth_url=self._cloud.begin()
            return await self.async_step_callback(_error='browser_required')
        except TokenExchangeError as error:
            self._auth_url=self._cloud.begin()
            return await self.async_step_callback(_error=error.reason)
        except ProviderNetworkError as error:
            reason=('verification_'+error.reason) if step=='verification' else error.reason
            return await self.async_step_login(_error=reason)
        except ProviderHTTPError as error:
            reason='identity_rate_limited' if error.status==429 else 'identity_server_error' if error.status>=500 else 'identity_http_error'
            return await self.async_step_login(_error=('verification_'+reason) if step=='verification' else reason)
        except MyQCloudNetworkError as error:
            self._auth_url=self._cloud.begin()
            return await self.async_step_callback(_error=error.reason)
        except (ClientError,TimeoutError):
            return await self.async_step_login(_error='verification_session_lost' if step=='verification' else 'cannot_connect')
        except (ValueError,KeyError):return await self.async_step_login(_error='cloud_auth_failed')
        return await self.async_step_discover()

    def _client_settings(self,values=None):
        previous=getattr(self,'_reauth_entry',None)
        settings={key:value for key,value in (previous.data.items() if previous else []) if key in ('client_id','redirect_uri','client_secret')}
        for key,value in (values or {}).items():
            if key in ('client_id','redirect_uri','client_secret') and value:settings[key]=value
        return settings

    async def async_step_login(self,user_input=None,_error=None):
        errors={'base':_error} if _error else {}
        if user_input:
            if getattr(self,'_cloud',None):await self._cloud.close_login()
            self._cloud=MyQCloud(async_get_clientsession(self.hass),self._client_settings(user_input))
            if user_input.get('use_browser'):
                self._auth_url=self._cloud.begin()
                return await self.async_step_callback()
            if not user_input.get('username') or not user_input.get('password'):errors['base']='missing_credentials'
            else:return await self._auth_operation(lambda:self._cloud.login_password(user_input['username'],user_input['password']),'login')
        fields={vol.Optional('username'):str,vol.Optional('password'):selector.TextSelector({'type':'password'}),vol.Optional('use_browser',default=False):bool}
        if self.show_advanced_options:fields[vol.Optional('client_secret',default='')]=selector.TextSelector({'type':'password'})
        return self.async_show_form(step_id='login',data_schema=vol.Schema(fields),errors=errors)

    async def async_step_verification(self,user_input=None,_error=None):
        if user_input:
            return await self._auth_operation(lambda:self._cloud.submit_verification(user_input['code']),'verification')
        return self.async_show_form(step_id='verification',data_schema=vol.Schema({vol.Required('code'):selector.TextSelector({'type':'password'})}),errors={'base':_error} if _error else {})

    async def async_step_verification_method(self,user_input=None):
        if user_input:return await self._auth_operation(lambda:self._cloud.choose_verification_method(user_input['method']),'verification')
        return self.async_show_form(step_id='verification_method',data_schema=vol.Schema({vol.Required('method'):vol.In(self._verification_methods)}))

    async def async_step_reauth(self,entry_data):
        self._reauth_entry=self.hass.config_entries.async_get_entry(self.context['entry_id'])
        return await self.async_step_login()

    async def async_step_cloud(self,user_input=None):
        if user_input or not self.show_advanced_options:
            if getattr(self,'_cloud',None):await self._cloud.close_login()
            self._cloud=MyQCloud(async_get_clientsession(self.hass),self._client_settings(user_input))
            self._auth_url=self._cloud.begin()
            return await self.async_step_callback()
        return self.async_show_form(step_id='cloud',data_schema=vol.Schema({
            vol.Required('client_id',default='ANDROID_CGI_MYQ'):str,
            vol.Required('redirect_uri',default='com.myqops://android'):str,
            vol.Optional('client_secret',default=''):selector.TextSelector({'type':'password'})}))

    async def async_step_callback(self,user_input=None,_error=None):
        errors={'base':_error} if _error else {}
        if user_input:
            if user_input.get('restart_login'):
                await self._cloud.close_login();self._auth_url=self._cloud.begin()
            else:
                try:await self._cloud.finish(user_input.get('callback_url',''))
                except OAuthCallbackError as error:errors['base']=error.reason
                except TokenExchangeError as error:errors['base']=error.reason
                except MyQCloudNetworkError as error:errors['base']=error.reason
                except (ClientError,TimeoutError):errors['base']='identity_network_error'
                except (ValueError,KeyError):errors['base']='cloud_auth_failed'
                else:return await self.async_step_discover()
        return self.async_show_form(step_id='callback',data_schema=vol.Schema({
            vol.Optional('callback_url'):str,vol.Optional('restart_login',default=False):bool}),
            description_placeholders={'auth_url':self._auth_url},errors=errors)

    async def async_step_discover(self,user_input=None):
        errors={}
        try:devices=await self._cloud.devices()
        except MyQCloudAuthError:errors['base']='account_auth_failed'
        except MyQCloudNetworkError as error:errors['base']=error.reason
        except ClientResponseError as error:errors['base']='account_access_denied' if error.status in (401,403) else 'discovery_failed'
        except (ClientError,TimeoutError,ValueError,KeyError):errors['base']='discovery_failed'
        else:
            if devices:return await self._complete_cloud(devices)
            errors['base']='no_devices'
        if user_input and user_input.get('restart_login'):return await self.async_step_login()
        return self.async_show_form(step_id='discover',data_schema=vol.Schema({vol.Optional('restart_login',default=False):bool}),errors=errors)

    async def _complete_cloud(self,devices):
        account_ids=sorted({d['account_id'] for d in devices.values()})
        await self.async_set_unique_id('cloud:'+','.join(account_ids))
        if getattr(self,'_reauth_entry',None):
            if self._reauth_entry.unique_id!=self.unique_id:return self.async_abort(reason='wrong_account')
            self.hass.config_entries.async_update_entry(self._reauth_entry,data={'mode':'cloud',**self._cloud.data})
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason='reauth_successful')
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title='myQ cloud',data={'mode':'cloud',**self._cloud.data})

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
