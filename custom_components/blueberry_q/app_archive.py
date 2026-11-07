"""Read MyQ production OAuth configuration without executing an APK."""
import io
import json
from pathlib import Path
from urllib.parse import urlsplit
import zipfile

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_APK = 160 * 1024 * 1024
MAX_CONFIG = 1024 * 1024

class AppArchiveError(ValueError):
    """An archive does not contain supported production configuration."""

def load_app_material(path):
    path = Path(path).expanduser()
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
        raise AppArchiveError('App archive is missing or too large')
    def inspect(archive):
        try:
            member = archive.getinfo('assets/myq-config.json')
        except KeyError:
            return None
        if member.file_size > MAX_CONFIG:
            raise AppArchiveError('App configuration is too large')
        config = json.loads(archive.read(member))
        rows = [row for row in config['environmentProviders'] if row.get('identifier') == 'Production']
        if len(rows) != 1:
            raise AppArchiveError('Production environment is missing or ambiguous')
        credentials = rows[0]['credentialsProvider']
        fields = {'client_id': credentials['clientId'], 'redirect_uri': credentials['redirectURI'], 'scope': credentials['scope']}
        if not all(isinstance(value, str) and value.strip() for value in fields.values()):
            raise AppArchiveError('Invalid OAuth configuration')
        redirect = urlsplit(fields['redirect_uri'])
        if redirect.scheme != 'com.myqops' or redirect.netloc != 'android' or redirect.path or redirect.query or redirect.fragment:
            raise AppArchiveError('Unsupported app callback')
        secret = credentials.get('clientSecret') or credentials.get('client_secret')
        if secret:
            if not isinstance(secret, str):
                raise AppArchiveError('Invalid OAuth credential')
            fields['client_secret'] = secret
        return fields
    try:
        with zipfile.ZipFile(path) as archive:
            result = inspect(archive)
            if result:
                return result
            bases = [item for item in archive.infolist() if item.filename.rsplit('/', 1)[-1] == 'base.apk']
            if len(bases) != 1 or bases[0].file_size > MAX_APK:
                raise AppArchiveError('Expected one base APK')
            with zipfile.ZipFile(io.BytesIO(archive.read(bases[0]))) as base:
                result = inspect(base)
                if result:
                    return result
    except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as error:
        raise AppArchiveError('Cannot read supported MyQ app configuration') from None
    raise AppArchiveError('MyQ app configuration was not found')
