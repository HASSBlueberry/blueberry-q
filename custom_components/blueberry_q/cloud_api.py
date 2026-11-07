"""Experimental client for the endpoints used by the myQ application."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
import logging
import re
from urllib.parse import parse_qs, quote, urlencode, urlsplit
import aiohttp

IDENTITY = 'https://partner-identity.myq-cloud.com'
USER_AGENT = 'BlueberryQ/0.6.0'


class MyQCloudAuthError(ValueError):
    """The provider session requires a new sign-in."""



class OAuthCallbackError(ValueError):
    def __init__(self,reason):
        super().__init__(reason);self.reason=reason

class TokenExchangeError(MyQCloudAuthError):
    def __init__(self,reason,diagnostic='No token response recorded'):
        super().__init__('Provider token exchange failed');self.reason=reason
        self.diagnostic=diagnostic


TOKEN_ERRORS={'invalid_client':'oauth_client_rejected','invalid_grant':'oauth_code_expired',
              'access_denied':'oauth_access_denied','invalid_scope':'oauth_scope_rejected',
              'invalid_request':'oauth_request_invalid','unauthorized_client':'oauth_client_rejected',
              'unsupported_grant_type':'oauth_grant_type_rejected'}


def token_rejection(payload,status,source):
    payload=payload if isinstance(payload,dict) else {}
    error=payload.get('error')
    error=error if isinstance(error,str) and error in TOKEN_ERRORS else 'other'
    code=payload.get('code')
    code=str(code) if isinstance(code,(str,int,float)) and re.fullmatch(r'[0-9]{3}\.[0-9]{1,6}[a-z]?',str(code)) else 'none'
    reason='oauth_application_unauthorized' if code in ('401.122','401.122i') else TOKEN_ERRORS.get(error,'oauth_token_rejected')
    diagnostic=f'HTTP {status}, OAuth {error}, provider {code}, App Check {source}'
    logging.getLogger(__name__).warning('myQ token exchange rejected (%s)',diagnostic)
    return TokenExchangeError(reason,diagnostic)

class MyQCloudNetworkError(ValueError):
    """A provider request failed without exposing request or response secrets."""
    def __init__(self, reason):
        super().__init__('myQ provider request failed')
        self.reason = reason

def _network_reason(error, prefix):
    if isinstance(error, aiohttp.ClientSSLError):
        return f'{prefix}_tls_error'
    if isinstance(error, (aiohttp.ServerTimeoutError, asyncio.TimeoutError)):
        return f'{prefix}_timeout'
    if isinstance(error, aiohttp.ClientConnectorError):
        return f'{prefix}_connection_error'
    return f'{prefix}_network_error'

class MyQCloud:
    IDENTITY=IDENTITY
    def __init__(self, session, data=None):
        self.session = session
        self.data = dict(data or {})
        self.lock = asyncio.Lock()
        self._login=None
        self._login_timer=None

    def begin(self, force_login=True):
        self.verifier = secrets.token_urlsafe(48)
        self.state = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b'=').decode()
        parameters={
            'client_id': self.data.get('client_id', 'ANDROID_CGI_MYQ'),
            'redirect_uri': self.data.get('redirect_uri', 'com.myqops://android'),
            'response_type': 'code', 'scope': self.data.get('scope', 'MyQ_Residential offline_access'),
            'code_challenge': challenge, 'code_challenge_method': 'S256',
            'state': self.state, 'acr_values': 'unified_flow:v1 brand:myq',
        }
        if force_login:parameters['prompt']='login'
        return IDENTITY + '/connect/authorize?' + urlencode(parameters)

    async def finish(self, callback):
        if not getattr(self,'state',None) or not getattr(self,'verifier',None):
            raise OAuthCallbackError('oauth_session_lost')
        try:parsed=urlsplit(callback.strip())
        except (ValueError,AttributeError):raise OAuthCallbackError('invalid_callback') from None
        expected=urlsplit(self.data.get('redirect_uri','com.myqops://android'))
        if (parsed.scheme,parsed.netloc,parsed.path)!=(expected.scheme,expected.netloc,expected.path) or parsed.fragment:
            raise OAuthCallbackError('invalid_callback')
        params=parse_qs(parsed.query,keep_blank_values=True)
        if any(len(values)!=1 for key,values in params.items() if key in ('state','code','iss','error')):
            raise OAuthCallbackError('invalid_callback')
        if not secrets.compare_digest(params.get('state',[''])[0].encode(),self.state.encode()):
            raise OAuthCallbackError('oauth_state_mismatch')
        if 'iss' in params and params['iss'][0].rstrip('/')!=IDENTITY:
            raise OAuthCallbackError('oauth_issuer_mismatch')
        if params.get('error'):raise OAuthCallbackError('oauth_access_denied')
        code=params.get('code',[''])[0]
        if not code:raise OAuthCallbackError('invalid_callback')
        await self._token({'grant_type':'authorization_code','code':code,'code_verifier':self.verifier})

    async def _token(self, form):
        fields = {'client_id': self.data.get('client_id', 'ANDROID_CGI_MYQ'), **form}
        # MyQOAuthInterceptor builds refresh requests with only client_id,
        # grant_type and refresh_token. Scope and redirect belong to code exchange.
        if form.get('grant_type') == 'authorization_code':
            fields.update(scope=self.data.get('scope', 'MyQ_Residential offline_access'),
                          redirect_uri=self.data.get('redirect_uri', 'com.myqops://android'))
        if self.data.get('client_secret'):
            fields['client_secret'] = self.data['client_secret']
        # HA's shared session identifies itself as HomeAssistant. The live
        # identity endpoint rejects that agent before OAuth grant validation.
        headers = {'User-Agent': USER_AGENT}
        if self.data.get('app_bridge_url'):
            from .app_check import fetch_app_check, AppCheckBridgeError
            try:
                headers['Firebase-AppCheck-Token'] = await fetch_app_check(self.session, self.data)
            except AppCheckBridgeError as error:
                raise MyQCloudNetworkError(str(error)) from None
        elif self.data.get('app_check_token'):
            from .app_check import prepare_manual_token, AppCheckBridgeError
            try:
                self.data['app_check_token']=prepare_manual_token(self.data['app_check_token'])
            except AppCheckBridgeError as error:
                raise MyQCloudNetworkError(str(error)) from None
            headers['Firebase-AppCheck-Token'] = self.data['app_check_token']
        try:
            async with self.session.post(IDENTITY + '/connect/token', data=fields, headers=headers, timeout=30, allow_redirects=False) as response:
                if response.status == 429:
                    raise MyQCloudNetworkError('identity_rate_limited')
                if response.status >= 500:
                    raise MyQCloudNetworkError('identity_server_error')
                if response.status in (400,401,403):
                    try:
                        rejection=await response.json(content_type=None)
                    except (ValueError,AttributeError):
                        rejection=None
                    source='bridge' if self.data.get('app_bridge_url') else 'manual' if headers.get('Firebase-AppCheck-Token') else 'none'
                    raise token_rejection(rejection,response.status,source)
                response.raise_for_status()
                payload = await response.json()
        except (TokenExchangeError, MyQCloudNetworkError):
            raise
        except aiohttp.ClientResponseError:
            raise MyQCloudNetworkError('identity_http_error') from None
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise MyQCloudNetworkError(_network_reason(error, 'identity')) from None
        if not payload.get('access_token'):
            raise ValueError('Provider did not return an access token')
        self.data.update(access_token=payload['access_token'],
                         refresh_token=payload.get('refresh_token', self.data.get('refresh_token', '')),
                         expires_at=time.time() + int(payload.get('expires_in', 3600)))

    async def request(self, method, url):
        async with self.lock:
            if float(self.data.get('expires_at', 0)) < time.time() + 60:
                if not self.data.get('refresh_token'):
                    raise MyQCloudAuthError('Sign in to myQ again')
                await self._token({'grant_type': 'refresh_token', 'refresh_token': self.data['refresh_token']})
            headers = {'Authorization': 'Bearer ' + self.data['access_token'], 'BrandId': '1', 'User-Agent': USER_AGENT}
            try:
                async with self.session.request(method, url, headers=headers, timeout=30) as response:
                    if response.status == 429:
                        raise MyQCloudNetworkError('device_api_rate_limited')
                    if response.status >= 500:
                        raise MyQCloudNetworkError('device_api_server_error')
                    response.raise_for_status()
                    if method == 'PUT':
                        await response.read()
                        return None
                    return await response.json()
            except (MyQCloudNetworkError, aiohttp.ClientResponseError):
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                raise MyQCloudNetworkError(_network_reason(error, 'device_api')) from None

    async def devices(self):
        accounts = await self.request('GET', 'https://accounts.myq-cloud.com/api/v6.0/accounts')
        devices = {}
        for account in accounts.get('accounts', []):
            account_id = str(account['id'])
            result = await self.request('GET', 'https://devices.myq-cloud.com/api/v5.2/Accounts/' + quote(account_id, safe='') + '/Devices')
            for device in result.get('items', []):
                if device.get('device_family') == 'garagedoor' and device.get('serial_number'):
                    key = account_id + '/' + device['serial_number']
                    devices[key] = {**device, 'account_id': account_id}
        return devices

    async def command(self, device, action):
        if action not in ('open', 'close'):
            raise ValueError('Unsupported door command')
        account = quote(device['account_id'], safe='')
        serial = quote(device['serial_number'], safe='')
        await self.request('PUT', f'https://account-devices-gdo.myq-cloud.com/api/v5.2/Accounts/{account}/door_openers/{serial}/{action}')

    async def close_login(self):
        if self._login_timer:self._login_timer.cancel();self._login_timer=None
        login,self._login=self._login,None
        if login:await login.close()

    def _arm_login_timeout(self):
        if self._login_timer:self._login_timer.cancel()
        self._login_timer=asyncio.get_running_loop().call_later(600,lambda:asyncio.create_task(self.close_login()))

    async def _advance_login(self,operation):
        from .login import VerificationRequired,VerificationMethodRequired
        if self._login_timer:self._login_timer.cancel();self._login_timer=None
        try:
            await operation()
        except (VerificationRequired,VerificationMethodRequired):
            self._arm_login_timeout()
            raise
        except BaseException:
            await self.close_login()
            raise
        await self.close_login()

    async def login_password(self,username,password):
        """Continue the provider's own forms, pausing for its MFA challenge."""
        from .login import ProviderLogin
        await self.close_login()
        self._login=ProviderLogin(self,username,password)
        await self._advance_login(self._login.advance)

    async def submit_verification(self,code):
        from .login import BrowserRequired
        if not self._login:raise BrowserRequired('Verification session expired')
        await self._advance_login(lambda:self._login.submit_code(code))

    async def choose_verification_method(self,method):
        from .login import BrowserRequired
        if not self._login:raise BrowserRequired('Verification session expired')
        await self._advance_login(lambda:self._login.choose_method(method))
