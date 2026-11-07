"""Account-free client for the signed-in official MyQ app UI relay."""
from urllib.parse import quote
from .app_check import bridge_url, AppCheckBridgeError
from .cloud_api import MyQCloudAuthError, MyQCloudNetworkError
import aiohttp

NATIVE_ERRORS={'native_calibration_required','native_phone_locked','native_open_myq','native_door_not_unique',
               'native_state_unknown','native_screen_unreadable','native_control_changed','native_binding_missing',
               'native_phone_changed','native_door_busy_or_unknown','native_control_wrong_action','native_command_pending',
               'native_sign_in_required','authorize_or_connect_phone','selected_phone_missing','phone_unauthorized','phone_offline'}
NATIVE_ERRORS.update({'native_confirmation_required','native_screen_obstructed','native_controller_busy','native_app_offline'})


class NativeMyQ:
    def __init__(self,session,data):
        self.session=session
        self.data={k:v for k,v in data.items() if k in ('mode','app_bridge_url','app_bridge_key','app_bridge_id')}

    async def request(self,method,path,payload=None):
        try:
            url=bridge_url(self.data['app_bridge_url'])
            async with self.session.request(method,url+path,json=payload,headers={'Authorization':'Bearer '+self.data['app_bridge_key']},timeout=60,allow_redirects=False) as response:
                if response.status in (401,403):raise MyQCloudNetworkError('app_bridge_unauthorized')
                result=await response.json()
                if response.status!=200:
                    error=result.get('error') if isinstance(result,dict) else None
                    if error=='native_sign_in_required':raise MyQCloudAuthError('Sign in inside the official MyQ app on the phone')
                    raise MyQCloudNetworkError(error if isinstance(error,str) and error in NATIVE_ERRORS else 'native_bridge_unavailable')
                if not isinstance(result,dict):raise ValueError
                return result
        except (MyQCloudNetworkError,MyQCloudAuthError):raise
        except (aiohttp.ClientError,TimeoutError,ValueError,KeyError,AppCheckBridgeError):
            raise MyQCloudNetworkError('native_bridge_unavailable') from None

    async def devices(self):
        result=await self.request('GET','/v1/native/devices')
        if not isinstance(result.get('devices'),dict) or not isinstance(result.get('bridge_id'),str):
            raise MyQCloudNetworkError('native_bridge_unavailable')
        self.data['app_bridge_id']=result['bridge_id']
        return result['devices']

    async def command(self,device,action):
        if action not in ('open','close'):raise ValueError('Unsupported action')
        binding_id=quote(device['native_binding_id'],safe='')
        return await self.request('POST','/v1/native/doors/'+binding_id+'/command',{'action':action})
