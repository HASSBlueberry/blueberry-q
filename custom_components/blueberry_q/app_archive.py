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
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason

def load_app_material(path):
    path = Path(path).expanduser()
    try:
        if not path.is_file():
            raise AppArchiveError('app_archive_missing')
        if path.stat().st_size > MAX_ARCHIVE:
            raise AppArchiveError('app_archive_large')
    except OSError:
        raise AppArchiveError('app_archive_unreadable') from None
    def inspect(archive):
        try:
            member = archive.getinfo('assets/myq-config.json')
        except KeyError:
            return None
        if member.file_size > MAX_CONFIG:
            raise AppArchiveError('app_config_large')
        config = json.loads(archive.read(member))
        rows = [row for row in config['environmentProviders'] if row.get('identifier') == 'Production']
        if len(rows) != 1:
            raise AppArchiveError('app_production_missing')
        credentials = rows[0]['credentialsProvider']
        fields = {'client_id': credentials['clientId'], 'redirect_uri': credentials['redirectURI'], 'scope': credentials['scope']}
        if not all(isinstance(value, str) and value.strip() for value in fields.values()):
            raise AppArchiveError('app_config_invalid')
        redirect = urlsplit(fields['redirect_uri'])
        if redirect.scheme != 'com.myqops' or redirect.netloc != 'android' or redirect.path or redirect.query or redirect.fragment:
            raise AppArchiveError('app_callback_unsupported')
        secret = credentials.get('clientSecret') or credentials.get('client_secret')
        if secret:
            if not isinstance(secret, str):
                raise AppArchiveError('app_config_invalid')
            fields['client_secret'] = secret
        return fields
    try:
        with zipfile.ZipFile(path) as archive:
            result = inspect(archive)
            if result:
                return result
            members = [item for item in archive.infolist() if item.filename.lower().endswith('.apk')]
            if len(members) > 32:
                raise AppArchiveError('app_archive_layout')
            members.sort(key=lambda item: (item.filename.rsplit('/', 1)[-1] != 'base.apk', item.filename))
            for member in members:
                if member.file_size > MAX_APK:
                    raise AppArchiveError('app_archive_large')
                with zipfile.ZipFile(io.BytesIO(archive.read(member))) as base:
                    result = inspect(base)
                    if result:
                        return result
    except AppArchiveError:
        raise
    except OSError:
        raise AppArchiveError('app_archive_unreadable') from None
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AppArchiveError('app_config_invalid') from None
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise AppArchiveError('app_archive_corrupt') from None
    raise AppArchiveError('app_config_missing')
