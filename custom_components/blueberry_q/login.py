"""Resumable provider forms with one cookie session for password and MFA."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import aiohttp

class BrowserRequired(ValueError):
    pass

class InvalidCredentials(ValueError):
    pass

class ProviderNetworkError(ValueError):
    def __init__(self,reason):super().__init__('myQ identity request failed');self.reason=reason

class ProviderHTTPError(ValueError):
    def __init__(self,status):super().__init__('myQ identity endpoint returned an HTTP error');self.status=status

class VerificationRequired(ValueError):
    def __init__(self,invalid=False):
        super().__init__('Enter the verification code requested by myQ')
        self.invalid=invalid

class VerificationMethodRequired(ValueError):
    def __init__(self,options):
        super().__init__('Choose a verification delivery method')
        self.options=options

class ProviderForms(HTMLParser):
    def __init__(self):
        super().__init__();self.forms=[];self.form=None;self.select=None;self.option=None
    def handle_starttag(self,tag,attrs):
        row=dict(attrs)
        if tag=='form':
            self.form={'action':row.get('action',''),'fields':{},'inputs':{},'choices':{},'submit':{}}
            self.forms.append(self.form)
        if self.form is None:return
        name=row.get('name')
        if tag=='input' and name and 'disabled' not in row:
            kind=row.get('type','text').lower();self.form['inputs'][name]=row
            if kind=='radio':self.form['choices'].setdefault(name,{})[row.get('value','')]=row.get('aria-label',row.get('value',''))
            if kind in ('submit','button'):
                if row.get('value','').lower() not in ('cancel','back','resend') and not self.form['submit']:self.form['submit'][name]=row.get('value','')
            elif kind not in ('checkbox','radio') or 'checked' in row:self.form['fields'][name]=row.get('value','')
        if tag=='button' and name and row.get('type','submit')=='submit' and 'disabled' not in row:
            if row.get('value','').lower() not in ('cancel','back','resend') and not self.form['submit']:self.form['submit'][name]=row.get('value','')
        if tag=='select' and name:
            self.select=name;self.form['choices'][name]={}
        if tag=='option' and self.select is not None:self.option={'value':row.get('value',''),'text':'','selected':'selected' in row}
    def handle_data(self,data):
        if self.option is not None:self.option['text']+=data
    def handle_endtag(self,tag):
        if tag=='option' and self.form is not None and self.select and self.option is not None:
            value=self.option['value'];self.form['choices'][self.select][value]=self.option['text'].strip() or value
            if self.option['selected']:self.form['fields'][self.select]=value
            self.option=None
        if tag=='select':self.select=None
        if tag=='form':self.form=None;self.select=None;self.option=None

def credential_fields(form):
    visible={name:row for name,row in form['inputs'].items() if row.get('type','text').lower()!='hidden'}
    password=next((name for name,row in visible.items() if row.get('type','').lower()=='password' and 'code' not in name.lower()),None)
    username=next((name for name,row in visible.items() if row.get('type')=='email' or name.lower().split('.')[-1] in ('email','username')),None)
    return username,password

def code_field(form):
    for name,row in form['inputs'].items():
        if row.get('type','text').lower() in ('hidden','submit','button'):continue
        compact=''.join(c for c in name.lower() if c.isalnum())
        if row.get('autocomplete')=='one-time-code' or compact in ('code','inputcode','verificationcode','securitycode','twofactorcode','mfacode','otp','otpcode','inputtwofactorcode','inputverificationcode'):
            return name
    return None

class ProviderLogin:
    def __init__(self,cloud,username,password):
        self.cloud=cloud;self.username=username;self.password=password
        self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        self.url=cloud.begin();self.method='GET';self.fields=None
        self.pending=None;self.code_name=None;self.method_name=None;self.submitted=False
    async def close(self):
        self.password=None;self.username=None;self.fields=None;self.pending=None
        await self.session.close()
    def destination(self,url):
        parsed=urlsplit(url);identity=urlsplit(self.cloud.IDENTITY)
        if parsed.scheme!='https' or parsed.hostname!=identity.hostname or parsed.port not in (None,443) or parsed.username or parsed.password:
            raise BrowserRequired('Provider requires browser sign-in')
        return url
    def prepare(self,form,values):
        self.url=self.destination(urljoin(self.url,form['action']))
        self.fields={**form['fields'],**form['submit'],**values};self.method='POST';self.pending=None
    async def submit_code(self,code):
        if self.pending is None or self.code_name is None:raise BrowserRequired('Verification session expired')
        if not isinstance(code,str) or not code.strip():raise VerificationRequired(invalid=True)
        self.prepare(self.pending,{self.code_name:code.strip()})
        return await self.advance()
    async def choose_method(self,value):
        if self.pending is None or self.method_name is None:raise BrowserRequired('Verification session expired')
        if value not in self.pending['choices'][self.method_name]:raise ValueError('Unknown verification method')
        self.prepare(self.pending,{self.method_name:value})
        return await self.advance()
    async def advance(self):
        expected=urlsplit(self.cloud.data.get('redirect_uri','com.myqops://android'))
        for _ in range(16):
            parsed=urlsplit(self.url)
            if (parsed.scheme,parsed.netloc,parsed.path)==(expected.scheme,expected.netloc,expected.path):
                await self.cloud.finish(self.url);return
            self.destination(self.url)
            try:
                request=self.session.request(self.method,self.url,data=self.fields,allow_redirects=False)
                async with request as response:
                    # Never keep a password or OTP body across prompts or redirects.
                    self.fields=None
                    if response.status in (301,302,303,307,308):
                        location=response.headers.get('Location')
                        if not location:raise BrowserRequired('Missing provider redirect')
                        self.url=urljoin(self.url,location);self.method='GET';continue
                    if response.status==429 or response.status>=500:raise ProviderHTTPError(response.status)
                    page=await response.text()
            except aiohttp.ClientSSLError:raise ProviderNetworkError('identity_tls_error') from None
            except aiohttp.ServerTimeoutError:raise ProviderNetworkError('identity_timeout') from None
            except aiohttp.ClientConnectorError:raise ProviderNetworkError('identity_connection_error') from None
            except aiohttp.ClientError:raise ProviderNetworkError('identity_network_error') from None
            except TimeoutError:raise ProviderNetworkError('identity_timeout') from None
            parser=ProviderForms();parser.feed(page)
            for form in parser.forms:
                name=code_field(form)
                if name:
                    self.destination(urljoin(self.url,form['action']))
                    self.pending=form;invalid=self.code_name is not None;self.code_name=name
                    raise VerificationRequired(invalid)
            for form in parser.forms:
                if form['choices'] and any(word in (self.url+' '+form['action']).lower() for word in ('verify','verification','twofactor','2fa','mfa','sendcode','challenge')):
                    self.destination(urljoin(self.url,form['action']))
                    self.pending=form;self.method_name=next(iter(form['choices']))
                    raise VerificationMethodRequired(form['choices'][self.method_name])
            credentials=next(((form,*credential_fields(form)) for form in parser.forms if all(credential_fields(form))),None)
            if credentials:
                if self.submitted:raise InvalidCredentials('Provider rejected sign-in credentials')
                form,user,password=credentials
                self.prepare(form,{user:self.username,password:self.password});self.submitted=True
                self.password=None;self.username=None
                continue
            if response.status>=400:raise ProviderHTTPError(response.status)
            raise BrowserRequired('Provider requires browser verification')
        raise BrowserRequired('Too many provider redirects')
