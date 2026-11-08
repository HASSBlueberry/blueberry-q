import asyncio
import re
import time
import logging
from contextlib import suppress
from pathlib import Path
from .app_archive import load_app_material, AppArchiveError
import voluptuous as vol
from homeassistant.helpers import selector
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from aiohttp import ClientError,ClientResponseError
from .cloud_api import MyQCloud,MyQCloudAuthError,MyQCloudNetworkError,OAuthCallbackError,TokenExchangeError
from .login import BrowserRequired,InvalidCredentials,ProviderHTTPError,ProviderNetworkError,VerificationRequired,VerificationMethodRequired
from .const import DOMAIN
from .app_check import bridge_url, AppCheckBridgeError, prepare_manual_token, phone_request, wait_phone_callback, fetch_oauth_material
from .native_api import NativeMyQ

AUTH_SETTINGS=('client_id','redirect_uri','client_secret','scope','app_check_token','app_bridge_url','app_bridge_key','app_bridge_id')

class BlueberryQConfigFlow(config_entries.ConfigFlow,domain=DOMAIN):
    VERSION=2

    def async_remove(self):
        if getattr(self,'_phone_task',None) and not self._phone_task.done() and not self._phone_task.cancelling():self._phone_task.cancel()
        if getattr(self,'_cloud',None):asyncio.create_task(self._cloud.close_login())

    async def async_step_user(self,user_input=None):
        if user_input:
            if user_input.get('mode')=='phone':return await self.async_step_phone_login()
            return await self.async_step_credentials()
        return self.async_show_form(step_id='user',data_schema=vol.Schema({
            vol.Required('mode',default='credentials'):vol.In({
                'credentials':'OAuth tokens with App Check',
                'phone':'OAuth sign-in through BlueberryQDB with App Check'})}))

    async def async_step_native(self,user_input=None):
        errors={}
        if not getattr(self,'_setup_settings',None) and not getattr(self,'_reauth_entry',None) and not getattr(self,'_reconfigure_entry',None):
            entries=self._async_current_entries()
            connected=[e for e in entries if e.data.get('app_bridge_url')]
            if len(connected)==1:
                self._setup_settings={k:v for k,v in connected[0].data.items() if k in ('app_bridge_url','app_bridge_key','app_bridge_id')}
        if user_input is not None:
            try:
                settings=self._client_settings(user_input)
                client=NativeMyQ(async_get_clientsession(self.hass),{'mode':'native',**settings})
                devices=await client.devices()
                if not devices or not any(d.get('state',{}).get('online') for d in devices.values()):
                    raise MyQCloudNetworkError('native_state_unknown')
                await self.async_set_unique_id('native:'+client.data['app_bridge_id'])
                previous=getattr(self,'_reauth_entry',None) or getattr(self,'_reconfigure_entry',None)
                if previous:
                    if previous.unique_id!=self.unique_id:return self.async_abort(reason='wrong_account')
                    self.hass.config_entries.async_update_entry(previous,data={'mode':'native',**client.data})
                    await self.hass.config_entries.async_reload(previous.entry_id)
                    return self.async_abort(reason='reauth_successful' if getattr(self,'_reauth_entry',None) else 'reconfigure_successful')
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title='MyQ phone controller',data={'mode':'native',**client.data})
            except MyQCloudAuthError:errors['base']='native_sign_in_required'
            except MyQCloudNetworkError as error:errors['base']=error.reason
            except AppCheckBridgeError as error:errors['base']=str(error)
        defaults=self._client_settings()
        return self.async_show_form(step_id='native',data_schema=vol.Schema({
            vol.Required('app_bridge_url',default=defaults.get('app_bridge_url','')):str,
            vol.Optional('app_bridge_key'):selector.TextSelector({'type':'password'})}),errors=errors)

    async def async_step_phone_login(self,user_input=None,_error=None):
        errors={'base':_error} if _error else {}
        if user_input is not None:
            self._phone_error=None
            self._token_diagnostic=None
            try:
                settings=self._client_settings(user_input)
                if not settings.get('app_bridge_url'):raise AppCheckBridgeError('invalid_app_bridge_url')
                if not settings.get('app_bridge_key'):raise AppCheckBridgeError('app_bridge_key_required')
                self._phone_import_token=prepare_manual_token(user_input['app_check_token']) if user_input.get('app_check_token') else None
            except AppCheckBridgeError as error:errors['base']=str(error)
            else:
                if getattr(self,'_cloud',None):await self._cloud.close_login()
                self._setup_settings=settings
                self._cloud=MyQCloud(async_get_clientsession(self.hass),settings)
                self._phone_task=None
                self._phone_error=None
                self._token_diagnostic=None
                self._phone_phase='checking_app_check'
                return await self.async_step_phone_wait()
        defaults=self._client_settings()
        fields={vol.Required('app_bridge_url',default=defaults.get('app_bridge_url','')):str,
                vol.Optional('app_bridge_key'):selector.TextSelector({'type':'password'}),
                vol.Optional('app_check_token'):selector.TextSelector({'type':'password'})}
        diagnostic=getattr(self,'_token_diagnostic',None) or ''
        if getattr(self,'_phone_error',None) and not getattr(self,'_token_diagnostic',None):
            diagnostic='No provider token response was received. Last failure, '+self._phone_error+'.'
        return self.async_show_form(step_id='phone_login',data_schema=vol.Schema(fields),errors=errors,
            description_placeholders={'token_diagnostic':diagnostic,'phone_status':self._phone_status_text()})

    def _phone_status_text(self):
        return {'checking_app_check':'Checking or refreshing App Check',
                'opening_chrome':'Preparing Chrome on the phone',
                'waiting_for_user':'Waiting for you to complete sign-in on the phone',
                'callback_captured':'Phone callback captured',
                'exchanging_token':'Exchanging the OAuth authorization code',
                'discovering_devices':'Signed in and discovering devices'}.get(getattr(self,'_phone_phase',None),'Ready to start phone sign-in')

    async def _ensure_phone_app_check(self,timeout=180):
        deadline=time.monotonic()+timeout
        requested=False
        while True:
            try:
                await self._cloud.app_check_token()
                return
            except MyQCloudNetworkError as error:
                if error.reason!='app_check_unavailable' or time.monotonic()>=deadline:raise
                if not requested:
                    requested=True
                    with suppress(AppCheckBridgeError):
                        await phone_request(async_get_clientsession(self.hass),self._cloud.data,'POST','/v1/app-check/refresh',{})
                await asyncio.sleep(3)

    async def _phone_authenticate(self):
        session=async_get_clientsession(self.hass)
        settings=self._cloud.data
        session_id=None
        try:
            if self._phone_import_token:
                await phone_request(session,settings,'POST','/v1/app-check',{'token':self._phone_import_token})
                self._phone_import_token=None
            self._phone_phase='checking_app_check'
            await self._ensure_phone_app_check()
            settings.pop('app_check_token',None)
            authorize_url=self._cloud.begin(force_login=False)
            self._phone_phase='opening_chrome'
            start_task=asyncio.create_task(phone_request(session,settings,'POST','/v1/oauth/sessions',{'authorize_url':authorize_url}))
            try:
                result=await asyncio.shield(start_task)
            except asyncio.CancelledError:
                # A cancellation can arrive after QDB created the session but
                # before its response reached HA. Obtain its ID for cleanup.
                with suppress(AppCheckBridgeError,ClientError,TimeoutError):
                    result=await start_task
                    session_id=result.get('id') if isinstance(result,dict) else None
                raise
            session_id=result.get('id') if isinstance(result,dict) else None
            callback=await wait_phone_callback(session,settings,session_id,on_phase=lambda phase:setattr(self,'_phone_phase',phase))
            self._phone_phase='checking_app_check'
            await self._ensure_phone_app_check(timeout=60)
            self._phone_phase='exchanging_token'
            await self._cloud.finish(callback)
            self._token_diagnostic='OAuth token exchange succeeded.'
            self._phone_phase='discovering_devices'
        except TokenExchangeError as error:
            self._phone_error=error.reason
            self._token_diagnostic=error.diagnostic
        except (AppCheckBridgeError,MyQCloudNetworkError,OAuthCallbackError) as error:
            self._phone_error=getattr(error,'reason',None) or str(error)
        except (ClientError,TimeoutError,ValueError,KeyError):self._phone_error='phone_bridge_unavailable'
        finally:
            if self._phone_error:
                logging.getLogger(__name__).warning('Phone sign-in stopped during %s (%s)',getattr(self,'_phone_phase','not_started'),self._phone_error)
            self._phone_import_token=None
            if isinstance(session_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{32,128}',session_id):
                with suppress(AppCheckBridgeError,ClientError,TimeoutError):
                    await asyncio.shield(phone_request(session,settings,'DELETE','/v1/oauth/sessions/'+session_id))

    async def async_step_phone_wait(self,user_input=None):
        if getattr(self,'_phone_task',None) is None:
            self._phone_task=self.hass.async_create_task(self._phone_authenticate())
        if not self._phone_task.done():
            return self.async_show_progress(step_id='phone_wait',progress_action='phone_login',progress_task=self._phone_task,
                description_placeholders={'phone_status':self._phone_status_text()})
        return self.async_show_progress_done(next_step_id='phone_finish')

    async def async_step_phone_finish(self,user_input=None):
        if not self._phone_task.done():return await self.async_step_phone_wait()
        if self._phone_task.cancelled():return await self.async_step_phone_login(_error='phone_login_cancelled')
        self._phone_task.result()
        if self._phone_error:return await self.async_step_phone_login(_error=self._phone_error)
        return await self.async_step_discover()

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
            self._token_diagnostic=error.diagnostic
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
        previous=getattr(self,'_reauth_entry',None) or getattr(self,'_reconfigure_entry',None)
        settings={key:value for key,value in (previous.data.items() if previous else []) if key in AUTH_SETTINGS}
        settings.update(getattr(self,'_setup_settings',{}))
        for key,value in (values or {}).items():
            if key in AUTH_SETTINGS and value:settings[key]=value.strip() if isinstance(value,str) else value
        if (values or {}).get('use_app_bridge') is False:
            for key in ('app_bridge_url','app_bridge_key','app_bridge_id'):settings.pop(key,None)
        if settings.get('app_bridge_url'):settings['app_bridge_url']=bridge_url(settings['app_bridge_url'])
        return settings

    @staticmethod
    def _attestation_fields():
        return {vol.Optional('app_check_token'):selector.TextSelector({'type':'password'}),
                vol.Optional('app_bridge_url'):str,
                vol.Optional('app_bridge_key'):selector.TextSelector({'type':'password'}),
                vol.Optional('use_app_bridge',default=True):bool}

    async def async_step_hassio(self,discovery_info):
        info=discovery_info.config
        try:
            self._setup_settings={'app_bridge_url':bridge_url(info['app_bridge_url']),
                                  'app_bridge_key':info['app_bridge_key'],'app_bridge_id':info['bridge_id']}
        except (KeyError,AppCheckBridgeError):return self.async_abort(reason='invalid_discovery')
        for entry in self._async_current_entries():
            if entry.data.get('app_bridge_id')==info['bridge_id'] or entry.data.get('app_bridge_url')==self._setup_settings['app_bridge_url']:
                self.hass.config_entries.async_update_entry(entry,data={**entry.data,**self._setup_settings})
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason='already_configured')
        await self.async_set_unique_id('qdb:'+info['bridge_id'])
        # Account identity is established by discovery after sign-in. The app's
        # identity must not replace the account unique ID or create a duplicate.
        return await self.async_step_user()

    async def async_step_reconfigure(self,user_input=None):
        self._reconfigure_entry=self.hass.config_entries.async_get_entry(self.context['entry_id'])
        # Existing non-cloud entries remain operational until explicitly migrated.
        return await self.async_step_credentials(user_input)

    async def async_step_credentials(self,user_input=None):
        errors={}
        previous=getattr(self,'_reconfigure_entry',None) or getattr(self,'_reauth_entry',None)
        if user_input is not None:
            try:settings={**{k:v for k,v in (previous.data.items() if previous else []) if k not in AUTH_SETTINGS},**self._client_settings(user_input)}
            except AppCheckBridgeError:
                return self.async_show_form(step_id='credentials',data_schema=self._credentials_schema(),errors={'base':'invalid_app_bridge_url'})
            if user_input.get('sync_oauth_from_bridge'):
                try:
                    imported=await fetch_oauth_material(async_get_clientsession(self.hass),settings)
                    settings.update(imported)
                except AppCheckBridgeError as error:
                    return self.async_show_form(step_id='credentials',data_schema=self._credentials_schema(),errors={'base':str(error)})
            if user_input.get('use_phone_login'):
                self._setup_settings={k:v for k,v in settings.items() if k in AUTH_SETTINGS}
                return await self.async_step_phone_login()
            for key in ('access_token','refresh_token'):
                if user_input.get(key):settings[key]=user_input[key].strip()
            if user_input.get('expires_at') is not None:settings['expires_at']=user_input['expires_at']
            if user_input.get('refresh_token'):settings['expires_at']=0
            if not settings.get('access_token') and not settings.get('refresh_token'):
                errors['base']='missing_oauth_credentials'
            elif not settings.get('app_bridge_url') and not settings.get('app_check_token'):
                errors['base']='app_check_required'
            else:
                settings.setdefault('expires_at',0)
                if getattr(self,'_cloud',None):await self._cloud.close_login()
                self._cloud=MyQCloud(async_get_clientsession(self.hass),settings)
                return await self.async_step_discover()
        return self.async_show_form(step_id='credentials',data_schema=self._credentials_schema(),errors=errors)

    def _credentials_schema(self):
        fields={vol.Optional('access_token'):selector.TextSelector({'type':'password'}),
                vol.Optional('refresh_token'):selector.TextSelector({'type':'password'}),
                vol.Optional('expires_at'):vol.All(vol.Coerce(float),vol.Range(min=0)),
                vol.Optional('client_id'):str,vol.Optional('redirect_uri'):str,
                vol.Optional('scope'):str,vol.Optional('client_secret'):selector.TextSelector({'type':'password'})}
        fields[vol.Optional('use_phone_login',default=False)]=bool
        fields[vol.Optional('sync_oauth_from_bridge',default=False)]=bool
        fields.update(self._attestation_fields())
        return vol.Schema(fields)

    async def async_step_login(self,user_input=None,_error=None):
        errors={'base':_error} if _error else {}
        if user_input:
            if getattr(self,'_cloud',None):await self._cloud.close_login()
            try:settings=self._client_settings(user_input)
            except AppCheckBridgeError:
                return await self.async_step_login(_error='invalid_app_bridge_url')
            self._cloud=MyQCloud(async_get_clientsession(self.hass),settings)
            if user_input.get('use_browser'):
                self._auth_url=self._cloud.begin()
                return await self.async_step_callback()
            if not user_input.get('username') or not user_input.get('password'):errors['base']='missing_credentials'
            else:return await self._auth_operation(lambda:self._cloud.login_password(user_input['username'],user_input['password']),'login')
        fields={vol.Optional('username'):str,vol.Optional('password'):selector.TextSelector({'type':'password'}),vol.Optional('use_browser',default=False):bool}
        fields[vol.Optional('client_secret',default='')]=selector.TextSelector({'type':'password'})
        fields.update(self._attestation_fields())
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
        return await self.async_step_credentials()

    async def async_step_cloud(self,user_input=None):
        errors={}
        default_app=self.hass.config.path('.storage',DOMAIN,'myq.apks')
        bundled=next(iter(sorted(Path(__file__).resolve().parents[2].glob('*.apks'))),None)
        if not Path(default_app).is_file() and bundled:default_app=str(bundled)
        if user_input is not None:
            default_app=str(user_input.get('app_archive') or default_app).strip()
            if default_app.startswith('/root/config/'):
                default_app=self.hass.config.path(default_app[len('/root/config/'):])
            try:
                settings=await self.hass.async_add_executor_job(load_app_material,default_app)
            except AppArchiveError as error:
                errors['base']=error.reason
            else:
                if getattr(self,'_cloud',None):await self._cloud.close_login()
                try:settings.update(self._client_settings(user_input))
                except AppCheckBridgeError:
                    return self.async_show_form(step_id='cloud',data_schema=vol.Schema({vol.Required('app_archive',default=default_app):str,**self._attestation_fields()}),errors={'base':'invalid_app_bridge_url'})
                self._cloud=MyQCloud(async_get_clientsession(self.hass),settings)
                self._auth_url=self._cloud.begin()
                return await self.async_step_callback()
        return self.async_show_form(step_id='cloud',data_schema=vol.Schema({
            vol.Required('app_archive',default=default_app):str,**self._attestation_fields()}),errors=errors)

    async def async_step_callback(self,user_input=None,_error=None):
        errors={'base':_error} if _error else {}
        if user_input:
            if user_input.get('restart_login'):
                await self._cloud.close_login();self._auth_url=self._cloud.begin()
                self._token_diagnostic='No token response recorded'
            else:
                if user_input.get('app_check_token') and not self._cloud.data.get('app_bridge_url'):
                    self._cloud.data['app_check_token']=user_input['app_check_token'].strip()
                try:await self._cloud.finish(user_input.get('callback_url',''))
                except OAuthCallbackError as error:errors['base']=error.reason
                except TokenExchangeError as error:
                    errors['base']=error.reason
                    self._token_diagnostic=error.diagnostic
                except MyQCloudNetworkError as error:errors['base']=error.reason
                except (ClientError,TimeoutError):errors['base']='identity_network_error'
                except (ValueError,KeyError):errors['base']='cloud_auth_failed'
                else:return await self.async_step_discover()
        fields={vol.Optional('callback_url'):str,vol.Optional('restart_login',default=False):bool}
        if not self._cloud.data.get('app_bridge_url'):
            fields[vol.Optional('app_check_token')]=selector.TextSelector({'type':'password'})
        return self.async_show_form(step_id='callback',data_schema=vol.Schema(fields),
            description_placeholders={'auth_url':self._auth_url,'token_diagnostic':getattr(self,'_token_diagnostic','No token response recorded')},errors=errors)

    async def async_step_discover(self,user_input=None):
        errors={}
        try:devices=await self._cloud.devices()
        except TokenExchangeError as error:
            errors['base']=error.reason
            self._token_diagnostic=error.diagnostic
        except MyQCloudAuthError:errors['base']='account_auth_failed'
        except MyQCloudNetworkError as error:errors['base']=error.reason
        except ClientResponseError as error:errors['base']='account_access_denied' if error.status in (401,403) else 'discovery_failed'
        except (ClientError,TimeoutError,ValueError,KeyError):errors['base']='discovery_failed'
        else:
            if devices:return await self._complete_cloud(devices)
            errors['base']='no_devices'
        if user_input and user_input.get('restart_login'):return await self.async_step_user()
        return self.async_show_form(step_id='discover',data_schema=vol.Schema({vol.Optional('restart_login',default=False):bool}),errors=errors,
            description_placeholders={'token_diagnostic':getattr(self,'_token_diagnostic','No token response recorded')})

    async def _complete_cloud(self,devices):
        account_ids=sorted({d['account_id'] for d in devices.values()})
        await self.async_set_unique_id('cloud:'+','.join(account_ids))
        previous=getattr(self,'_reauth_entry',None) or getattr(self,'_reconfigure_entry',None)
        if previous:
            if previous.unique_id!=self.unique_id:return self.async_abort(reason='wrong_account')
            self.hass.config_entries.async_update_entry(previous,data={'mode':'cloud',**self._cloud.data})
            await self.hass.config_entries.async_reload(previous.entry_id)
            return self.async_abort(reason='reauth_successful' if getattr(self,'_reauth_entry',None) else 'reconfigure_successful')
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
