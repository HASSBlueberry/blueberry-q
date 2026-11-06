"""Experimental client for the endpoints used by the myQ application."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

IDENTITY = 'https://partner-identity.myq-cloud.com'


class MyQCloudAuthError(ValueError):
    """The provider session requires a new sign-in."""



class MyQCloud:
    def __init__(self, session, data=None):
        self.session = session
        self.data = dict(data or {})
        self.lock = asyncio.Lock()

    def begin(self):
        self.verifier = secrets.token_urlsafe(48)
        self.state = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b'=').decode()
        return IDENTITY + '/connect/authorize?' + urlencode({
            'client_id': self.data.get('client_id', 'ANDROID_CGI_MYQ'),
            'redirect_uri': self.data.get('redirect_uri', 'com.myqops://android'),
            'response_type': 'code', 'scope': 'MyQ_Residential offline_access',
            'code_challenge': challenge, 'code_challenge_method': 'S256',
            'state': self.state, 'prompt': 'login', 'acr_values': 'unified_flow:v1 brand:myq',
        })

    async def finish(self, callback):
        parsed = urlsplit(callback)
        expected = urlsplit(self.data.get('redirect_uri', 'com.myqops://android'))
        if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path):
            raise ValueError('Unexpected OAuth redirect')
        params = parse_qs(parsed.query)
        if not secrets.compare_digest(params.get('state', [''])[0], self.state):
            raise ValueError('OAuth state mismatch')
        code = params.get('code', [''])[0]
        if not code:
            raise ValueError('Authorization did not return a code')
        await self._token({'grant_type': 'authorization_code', 'code': code, 'code_verifier': self.verifier})

    async def _token(self, form):
        fields = {'client_id': self.data.get('client_id', 'ANDROID_CGI_MYQ'),
                  'redirect_uri': self.data.get('redirect_uri', 'com.myqops://android'), **form}
        if self.data.get('client_secret'):
            fields['client_secret'] = self.data['client_secret']
        async with self.session.post(IDENTITY + '/connect/token', data=fields, timeout=30) as response:
            if response.status in (400, 401):
                raise MyQCloudAuthError('Provider token exchange was rejected')
            response.raise_for_status()
            payload = await response.json()
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
            headers = {'Authorization': 'Bearer ' + self.data['access_token'], 'BrandId': '1'}
            async with self.session.request(method, url, headers=headers, timeout=30) as response:
                response.raise_for_status()
                if method == 'PUT':
                    await response.read()
                    return None
                return await response.json()

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

    async def login_password(self, username, password):
        """Complete the provider form with an isolated cookie jar and bounded redirects."""
        from html.parser import HTMLParser
        from urllib.parse import urljoin
        from aiohttp import ClientSession, ClientTimeout

        class LoginForm(HTMLParser):
            def __init__(self):
                super().__init__()
                self.forms = []
                self.form = None
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'form':
                    self.form = {'action': attrs.get('action', ''), 'fields': {}}
                    self.forms.append(self.form)
                if tag == 'input' and self.form is not None and attrs.get('name'):
                    self.form['fields'][attrs['name']] = attrs.get('value', '')
            def handle_endtag(self, tag):
                if tag == 'form':
                    self.form = None

        url = self.begin()
        method, fields = 'GET', None
        submitted = False
        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            for _ in range(12):
                parsed = urlsplit(url)
                if parsed.scheme == 'com.myqops':
                    await self.finish(url)
                    return
                if parsed.scheme != 'https' or parsed.hostname != 'partner-identity.myq-cloud.com':
                    raise ValueError('Provider requires browser sign-in')
                async with session.request(method, url, data=fields, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        destination = response.headers.get('Location')
                        if not destination:
                            raise ValueError('Missing sign-in redirect')
                        url = urljoin(url, destination)
                        # Never forward password form bodies across redirects.
                        method, fields = 'GET', None
                        continue
                    response.raise_for_status()
                    page = await response.text()
                parser = LoginForm()
                parser.feed(page)
                form = next((form for form in parser.forms
                             if 'Password' in form['fields'] and 'Email' in form['fields']), None)
                if submitted or form is None:
                    raise ValueError('Provider requires browser sign-in or rejected credentials')
                destination = urljoin(url, form['action'])
                parsed = urlsplit(destination)
                if parsed.scheme != 'https' or parsed.hostname != 'partner-identity.myq-cloud.com':
                    raise ValueError('Unexpected password form host')
                fields = {**form['fields'], 'Email': username, 'Password': password}
                url, method, submitted = destination, 'POST', True
        raise ValueError('Too many provider redirects')
