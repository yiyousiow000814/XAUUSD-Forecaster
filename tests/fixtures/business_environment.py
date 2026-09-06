"""Injected only in generated rehearsal sources, never a production adapter.

The generator binds this file to one explicit fixture configuration. No missing
configuration may fall back to process credentials or the interactive user hive.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import winreg
import subprocess

CONFIGURATION = Path('__CONFIG_PATH__')
EXPECTED_DIGEST = '__CONFIG_DIGEST__'
FIXTURE_ROOT = Path('__FIXTURE_ROOT__').resolve()
SOURCE_ROOT = FIXTURE_ROOT / 'source'
GIT_EXECUTABLE = '__GIT_PATH__'


def _configuration():
    supplied = os.environ.get('XAUUSD_FIXTURE_CONFIGURATION')
    # Compare the locator lexically before any filesystem access. An untrusted
    # UNC/reparse spelling must not trigger lookup merely to reject it.
    if not supplied or os.path.normcase(os.path.abspath(supplied)) != os.path.normcase(os.path.abspath(CONFIGURATION)):
        raise RuntimeError('FIXTURE_CONFIGURATION_REQUIRED')
    with CONFIGURATION.open('rb') as stream:
        raw = stream.read(32769)
    if len(raw) > 32768 or hashlib.sha256(raw).hexdigest() != EXPECTED_DIGEST:
        raise RuntimeError('FIXTURE_CONFIGURATION_IDENTITY_MISMATCH')
    document = json.loads(raw)
    if document.get('schema_version') != 1 or not isinstance(document.get('values'), dict):
        raise RuntimeError('FIXTURE_CONFIGURATION_SCHEMA_INVALID')
    return document


DOCUMENT = _configuration()
sys.dont_write_bytecode = True


def environment_value(name):
    if name not in DOCUMENT['values']:
        raise RuntimeError('FIXTURE_ENVIRONMENT_KEY_UNDECLARED:' + name)
    return str(DOCUMENT['values'][name])


# Explicit process-only values replace inherited decoys. This is not a claim
# that clearing process environment alone contains persistent Windows secrets.
for _name, _value in DOCUMENT['values'].items():
    os.environ[_name] = str(_value)
os.environ['GIT_CONFIG_NOSYSTEM'] = '1'
os.environ['GIT_CONFIG_GLOBAL'] = os.devnull
os.environ['GIT_OPTIONAL_LOCKS'] = '0'

_git_commands = {subprocess.list2cmdline([GIT_EXECUTABLE, *args]) for args in (
    ('rev-parse', 'HEAD'),
    ('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}'),
    ('rev-parse', 'origin/main'), ('status', '--porcelain', '--', '.'),
)}


def _guard(event, arguments):
    # System timezone discovery (dateutil/pandas) is read-only machine metadata,
    # not a credential source. Deny opening the interactive user's hive and all
    # registry mutation; generated credential owner never falls back here.
    if (event in {'winreg.OpenKey', 'winreg.ConnectRegistry'} and
            any(isinstance(value, int) and value & 0xffffffff == (int(winreg.HKEY_CURRENT_USER) & 0xffffffff)
                for value in arguments[:2])) or event in {
                'winreg.CreateKey', 'winreg.DeleteKey', 'winreg.DeleteValue',
                'winreg.SetValue', 'winreg.SaveKey', 'winreg.LoadKey'}:
        raise RuntimeError('FIXTURE_PERSISTENT_CONFIGURATION_DENIED')
    if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo'}:
        # Until a rehearsal explicitly declares its owned ports there is no
        # network authority, including production localhost:8765.
        address = arguments[1] if event != 'socket.getaddrinfo' else arguments[:2]
        host, port = address[:2]
        allowed = DOCUMENT.get('loopback_ports', [])
        if host not in {'127.0.0.1', '::1', 'localhost'} or port not in allowed or port == 8765:
            raise RuntimeError('FIXTURE_NETWORK_TARGET_DENIED')
    if event == 'subprocess.Popen':
        # A new unguarded Python/native child is not implicitly trusted. The
        # full lifecycle must declare its actual child boundary before launch.
        executable, command, cwd, _ = arguments
        if (executable not in (None, GIT_EXECUTABLE) or command not in _git_commands or
                cwd is None or Path(cwd).resolve() != SOURCE_ROOT):
            raise RuntimeError('FIXTURE_UNDECLARED_BUSINESS_CHILD')
    if event == 'open' and isinstance(arguments[0], (str, bytes, os.PathLike)):
        target = Path(os.fsdecode(arguments[0])).resolve()
        mode, flags = arguments[1:3]
        writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        if writing and not target.is_relative_to(FIXTURE_ROOT) and str(target) != os.devnull:
            raise RuntimeError('FIXTURE_WRITE_TARGET_DENIED')
        if (not target.is_relative_to(FIXTURE_ROOT) and any(part.casefold() in {
                'xauusd-forecaster', 'xauusd-forecaster-runtime', 'xauusd-forecaster.local',
            } for part in target.parts)):
            raise RuntimeError('FIXTURE_PRODUCTION_READ_DENIED')


sys.addaudithook(_guard)
