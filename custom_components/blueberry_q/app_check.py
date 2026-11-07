"""Read attestation from an explicitly configured BlueberryQDB service."""
import time
import base64
import json
import re
from urllib.parse import urlsplit
import aiohttp


class AppCheckBridgeError(ValueError):
    """Never include service response bodies or connection credentials."""


def prepare_manual_token(value):
    """Validate formatting and unverified expiry before spending an OAuth code."""
    try:
        token = value.strip()
        if token.lower().startswith('firebase-appcheck-token:'):
            token = token.split(':', 1)[1].strip()
        if token.startswith('"'):
            token = json.loads(token)
        if not isinstance(token, str) or len(token) > 8192 or not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token):
            raise ValueError
        payload = token.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        issuer = claims.get('iss')
        if issuer and urlsplit(issuer).hostname != 'firebaseappcheck.googleapis.com':
            raise ValueError
        expiry = claims.get('exp')
        if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
            raise ValueError
        if not time.time() + 30 < expiry < time.time() + 8 * 86400:
            raise AppCheckBridgeError('app_check_token_expired')
        return token
    except AppCheckBridgeError:
        raise
    except (ValueError, AttributeError, TypeError):
        raise AppCheckBridgeError('app_check_token_invalid') from None


def bridge_url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        if parsed.path not in ('', '/') or parsed.query or parsed.fragment or not parsed.port and parsed.netloc.endswith(':'):
            raise ValueError
        return value.rstrip('/')
    except (ValueError, TypeError, AttributeError):
        raise AppCheckBridgeError('invalid_app_bridge_url') from None


async def fetch_app_check(session, settings):
    url = bridge_url(settings['app_bridge_url'])
    key = settings.get('app_bridge_key', '')
    if not key:
        raise AppCheckBridgeError('app_bridge_key_required')
    try:
        async with session.get(url + '/v1/app-check', headers={'Authorization': 'Bearer ' + key},
                               timeout=30, allow_redirects=False) as response:
            if response.status != 200:
                raise AppCheckBridgeError('app_check_unavailable')
            payload = await response.json()
        token, expiry = payload.get('token'), payload.get('expires_at')
        if not isinstance(token, str) or not 20 <= len(token) <= 8192 or any(c.isspace() for c in token):
            raise ValueError
        if not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or expiry <= time.time() + 30:
            raise ValueError
        return token
    except AppCheckBridgeError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, AttributeError):
        raise AppCheckBridgeError('app_check_unavailable') from None

PHONE_ERRORS = {
    'phone_login_in_progress','phone_login_timeout','phone_login_session_missing',
    'phone_callback_mismatch','phone_chrome_not_ready','phone_browser_unavailable',
    'phone_browser_disconnected','phone_browser_command_failed','phone_adb_unavailable',
    'phone_browser_forward_failed','phone_browser_target_invalid','invalid_authorize_url',
    'authorize_or_connect_phone','selected_phone_missing','phone_unauthorized',
    'phone_offline','phone_no_permissions','select_one_phone','invalid_app_check_token',
}


async def phone_request(session, settings, method, path, payload=None):
    url = bridge_url(settings['app_bridge_url'])
    key = settings.get('app_bridge_key')
    if not key:
        raise AppCheckBridgeError('app_bridge_key_required')
    try:
        async with session.request(method,url+path,json=payload,
                                   headers={'Authorization':'Bearer '+key},
                                   timeout=30,allow_redirects=False) as response:
            if response.status != 200:
                try:
                    error = (await response.json()).get('error')
                except (ValueError,AttributeError):
                    error = None
                raise AppCheckBridgeError(error if isinstance(error,str) and error in PHONE_ERRORS else 'phone_bridge_unavailable')
            return await response.json()
    except AppCheckBridgeError:
        raise
    except (aiohttp.ClientError,TimeoutError,ValueError,TypeError):
        raise AppCheckBridgeError('phone_bridge_unavailable') from None


async def wait_phone_callback(session, settings, session_id):
    import asyncio
    if not isinstance(session_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',session_id):
        raise AppCheckBridgeError('phone_login_session_missing')
    deadline = time.monotonic()+600
    while time.monotonic() < deadline:
        result = await phone_request(session,settings,'GET','/v1/oauth/sessions/'+session_id)
        if not isinstance(result,dict):
            raise AppCheckBridgeError('phone_bridge_unavailable')
        if result.get('status') == 'ready':
            callback = result.get('callback')
            if not isinstance(callback,str) or len(callback)>16000:
                raise AppCheckBridgeError('phone_callback_mismatch')
            return callback
        if result.get('status') != 'waiting':
            error = result.get('error')
            raise AppCheckBridgeError(error if isinstance(error,str) and error in PHONE_ERRORS else 'phone_bridge_unavailable')
        await asyncio.sleep(2)
    raise AppCheckBridgeError('phone_login_timeout')
