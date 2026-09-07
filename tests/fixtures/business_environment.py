"""Injected only in generated rehearsal sources, never a production adapter.

The generator binds this file to one explicit fixture configuration. No missing
configuration may fall back to process credentials or the interactive user hive.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import winreg
import subprocess
import socket
import urllib.request
from urllib.parse import unquote, urlsplit

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
    try:
        serialized = raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise RuntimeError('FIXTURE_CONFIGURATION_ENCODING_INVALID') from error
    document = json.loads(serialized)
    if document.get('schema_version') != 1 or not isinstance(document.get('values'), dict):
        raise RuntimeError('FIXTURE_CONFIGURATION_SCHEMA_INVALID')
    return document


DOCUMENT = _configuration()
sys.dont_write_bytecode = True
CODE_ROOTS = {SOURCE_ROOT}
PREFLIGHT_ROOT = None
if DOCUMENT.get('runtime_root'):
    runtime = Path(DOCUMENT['runtime_root']).resolve()
    if not runtime.is_relative_to(FIXTURE_ROOT):
        raise RuntimeError('FIXTURE_RUNTIME_ROOT_UNDECLARED')
    CODE_ROOTS.add(runtime)
if DOCUMENT.get('values', {}).get('PREFLIGHT_API_PORT'):
    revision = DOCUMENT['values'].get('TARGET_SOURCE_REVISION', '')
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise RuntimeError('FIXTURE_PREFLIGHT_REVISION_UNDECLARED')
    PREFLIGHT_ROOT = Path(DOCUMENT['repository_root']) / '.local/runtime-preflight' / revision
    if not PREFLIGHT_ROOT.is_relative_to(FIXTURE_ROOT):
        raise RuntimeError('FIXTURE_PREFLIGHT_ROOT_UNDECLARED')
    CODE_ROOTS.add(PREFLIGHT_ROOT)


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
    if event == 'sqlite3.connect':
        database = os.fsdecode(arguments[0])
        if database != ':memory:':
            if database.startswith('file:'):
                uri = urlsplit(database)
                if uri.netloc:
                    raise RuntimeError('FIXTURE_DATABASE_TARGET_DENIED')
                database = unquote(uri.path)
                if len(database) > 3 and database[0] == '/' and database[2] == ':':
                    database = database[1:]
            if not Path(database).resolve().is_relative_to(FIXTURE_ROOT):
                raise RuntimeError('FIXTURE_DATABASE_TARGET_DENIED')
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
                cwd is None or Path(cwd).resolve() not in CODE_ROOTS):
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

# Route the real legacy API's bare Git command to the already verified exact
# executable. The audit hook still checks the complete bounded arguments/cwd.
_run = subprocess.run
def _run_with_exact_git(args, *positional, **keywords):
    if isinstance(args, (tuple, list)) and args and args[0] == 'git':
        args = [GIT_EXECUTABLE, *args[1:]]
    return _run(args, *positional, **keywords)
subprocess.run = _run_with_exact_git


def _owned_existing_path(value):
    # Reject a foreign lexical locator before resolving any part of it. These
    # roots are sealed fixture inputs, not permission to follow a reparse point.
    path = Path(os.path.abspath(value))
    if path == FIXTURE_ROOT or not path.is_relative_to(FIXTURE_ROOT):
        raise RuntimeError('FIXTURE_QUALIFICATION_PATH_UNDECLARED')
    for ancestor in reversed((path, *path.parents)):
        if not ancestor.is_relative_to(FIXTURE_ROOT):
            continue
        if ancestor.is_symlink() or ancestor.is_junction():
            raise RuntimeError('FIXTURE_QUALIFICATION_REPARSE_DENIED')
    if path.resolve(strict=True) != path:
        raise RuntimeError('FIXTURE_QUALIFICATION_PATH_UNDECLARED')
    return path


def _require_target_revision(root):
    expected = DOCUMENT['values'].get('TARGET_SOURCE_REVISION', '')
    if not re.fullmatch('[0-9a-f]{40}', expected):
        raise RuntimeError('FIXTURE_QUALIFICATION_REVISION_UNDECLARED')
    CODE_ROOTS.add(root)
    observed = subprocess.run([GIT_EXECUTABLE, 'rev-parse', 'HEAD'], cwd=root,
        check=True, capture_output=True, text=True, timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
    if observed != expected:
        raise RuntimeError('FIXTURE_QUALIFICATION_REVISION_MISMATCH')


def _argument_pairs(arguments, allowed, repeated=()):
    if len(arguments) % 2:
        raise RuntimeError('FIXTURE_QUALIFICATION_ARGUMENTS_UNDECLARED')
    result = {}
    for name, value in zip(arguments[::2], arguments[1::2]):
        if name not in allowed or name in result and name not in repeated:
            raise RuntimeError('FIXTURE_QUALIFICATION_ARGUMENTS_UNDECLARED')
        result.setdefault(name, []).append(value)
    return result


def _qualification_entrypoint():
    entry = Path(os.path.abspath(sys.argv[0]))
    values = DOCUMENT['values']
    if entry.name == 'build_release_validation_fixtures.py':
        parent_value = values.get('VALIDATION_WORKSPACE_PARENT', '')
        parent = Path(os.path.abspath(parent_value))
        workspace_name = entry.parent.parent.name
        if (not parent_value or parent == FIXTURE_ROOT or not parent.is_relative_to(FIXTURE_ROOT)
                or not re.fullmatch('aurum-release-validation-[0-9a-f]{32}', workspace_name)):
            raise RuntimeError('FIXTURE_QUALIFICATION_ARGUMENTS_UNDECLARED')
        # The command line selects a bounded workspace name, not a filesystem
        # authority. Resolve only the entry reconstructed from sealed inputs.
        root = parent / workspace_name
        declared_entry = root / 'scripts/build_release_validation_fixtures.py'
        if (entry != declared_entry
                or sys.argv[1:] != ['--output', str(root / '.release-validation-fixtures')]):
            raise RuntimeError('FIXTURE_QUALIFICATION_ARGUMENTS_UNDECLARED')
        declared_entry = _owned_existing_path(declared_entry)
        _require_target_revision(root)
        return declared_entry
    if entry.name in {'bootstrap_news_projection.py', 'retained_news_bootstrap.py'}:
        retained = entry.name == 'retained_news_bootstrap.py'
        declared_caller = values.get('BOOTSTRAP_CALLER_PATH', '')
        if (retained and (not declared_caller or entry != Path(os.path.abspath(declared_caller)))
                or not retained and entry != SOURCE_ROOT / 'scripts/bootstrap_news_projection.py'):
            raise RuntimeError('FIXTURE_QUALIFICATION_PATH_UNDECLARED')
        declared_entry = (Path(os.path.abspath(declared_caller)) if retained
                          else SOURCE_ROOT / 'scripts/bootstrap_news_projection.py')
        expected = json.loads(values.get('BOOTSTRAP_ARGUMENTS_JSON', 'null'))
        if not isinstance(expected, list) or sys.argv[1:] != expected:
            raise RuntimeError('FIXTURE_QUALIFICATION_ARGUMENTS_UNDECLARED')
        pairs = _argument_pairs(expected, {'--config', '--version-host', '--state-file',
            '--source-database', '--max-cycles', '--retry-seconds', '--token-env'})
        if not {'--config', '--version-host', '--state-file'} <= pairs.keys():
            raise RuntimeError('FIXTURE_QUALIFICATION_ARGUMENTS_UNDECLARED')
        declarations = json.loads(values.get('PROVIDER_HTTP_REQUESTS_JSON', '[]'))
        if not any(row == {'origin': pairs['--version-host'][0], 'method': 'POST',
                           'path_query': '/api/news-index'} for row in declarations):
            raise RuntimeError('FIXTURE_QUALIFICATION_ORIGIN_UNDECLARED')
        if retained:
            caller = _owned_existing_path(declared_entry)
            with caller.open('rb') as stream:
                raw = stream.read(65537)
            if (not raw or len(raw) > 65536
                    or hashlib.sha256(raw).hexdigest() != values.get('BOOTSTRAP_CALLER_SHA256')):
                raise RuntimeError('FIXTURE_QUALIFICATION_SCRIPT_MISMATCH')
            if '--source-database' in pairs or not values.get('BOOTSTRAP_STATE_ROOT'):
                raise RuntimeError('FIXTURE_QUALIFICATION_PATH_UNDECLARED')
            state_root = _owned_existing_path(values['BOOTSTRAP_STATE_ROOT'])
        else:
            state_root = Path(DOCUMENT['runtime_root']) / '.local/forward'
        state = Path(os.path.abspath(pairs['--state-file'][0]))
        if (state.parent != state_root or state.suffix != '.json'
                or '--source-database' in pairs and Path(os.path.abspath(pairs['--source-database'][0]))
                    != state_root / 'forward-evidence.sqlite3'):
            raise RuntimeError('FIXTURE_QUALIFICATION_PATH_UNDECLARED')
        declared_entry = _owned_existing_path(declared_entry)
        _owned_existing_path(pairs['--config'][0])
        _owned_existing_path(state_root)
        if state.is_symlink() or state.is_junction():
            raise RuntimeError('FIXTURE_QUALIFICATION_REPARSE_DENIED')
        if retained:
            _owned_existing_path(state.with_name(state.stem + '-generation.capture'))
        if '--source-database' in pairs:
            _owned_existing_path(pairs['--source-database'][0])
        _require_target_revision(SOURCE_ROOT)
        return declared_entry
    if entry.name == 'check_deferred_projection_parity.py':
        bundle_value = values.get('CONTROL_BUNDLE_ROOT', '')
        bundle = Path(os.path.abspath(bundle_value))
        if (not bundle_value or bundle == FIXTURE_ROOT or not bundle.is_relative_to(FIXTURE_ROOT)
                or entry != bundle / 'check_deferred_projection_parity.py'):
            raise RuntimeError('FIXTURE_QUALIFICATION_PATH_UNDECLARED')
        pairs = _argument_pairs(sys.argv[1:], {'--runtime-root', '--producer-root', '--version-id',
            '--git-sha', '--producer-revision', '--required-after', '--observe-attempt', '--route'}, {'--route'})
        expected = {'--runtime-root': DOCUMENT['runtime_root'], '--producer-root': DOCUMENT['runtime_root'],
            '--version-id': values.get('TARGET_WORKER_VERSION'), '--git-sha': values.get('TARGET_SOURCE_REVISION'),
            '--producer-revision': values.get('TARGET_SOURCE_REVISION')}
        if (set(pairs) != set(expected) | {'--required-after', '--observe-attempt', '--route'}
                or any(pairs.get(key) != [value] for key, value in expected.items())
                or not re.fullmatch('[0-9a-f]{32}', pairs['--observe-attempt'][0])
                or len(pairs['--route']) != len(set(pairs['--route']))
                or not set(pairs['--route']) <= {'/api/audit-briefs', '/api/audit-stories',
                    '/api/audit-decisions', '/api/news-evidence'}):
            raise RuntimeError('FIXTURE_QUALIFICATION_ARGUMENTS_UNDECLARED')
        declared_entry = _owned_existing_path(bundle / 'check_deferred_projection_parity.py')
        original = _owned_existing_path(SOURCE_ROOT / 'scripts/check_deferred_projection_parity.py')
        # Installed scripts do not have a Git checkout. Bind their actual bytes
        # to the exact target source instead of inventing a bundle Git identity.
        with declared_entry.open('rb') as stream:
            actual = stream.read(131073)
        with original.open('rb') as stream:
            wanted = stream.read(131073)
        if not actual or len(actual) > 131072 or actual != wanted:
            raise RuntimeError('FIXTURE_QUALIFICATION_SCRIPT_MISMATCH')
        _require_target_revision(SOURCE_ROOT)
        _require_target_revision(_owned_existing_path(DOCUMENT['runtime_root']))
        return declared_entry
    return None


def _loopback_origin(name):
    target = urlsplit(DOCUMENT['values'].get(name, ''))
    if (target.scheme != 'http' or target.hostname != '127.0.0.1'
            or target.port not in DOCUMENT.get('loopback_ports', []) or target.port == 8765
            or target.path not in ('', '/') or target.query or target.fragment or target.username is not None):
        raise RuntimeError('FIXTURE_HTTP_TARGET_UNDECLARED')
    return f'http://127.0.0.1:{target.port}'


class _NoFixtureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise RuntimeError('FIXTURE_HTTP_REDIRECT_DENIED')


def _mapped_urlopen(url, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *, context=None):
    request = url if isinstance(url, urllib.request.Request) else urllib.request.Request(url, data=data)
    if isinstance(url, urllib.request.Request) and data is not None:
        request = urllib.request.Request(url.full_url, data=data, headers=dict(url.header_items()),
                                         method=getattr(url, 'method', None))
    original = urlsplit(request.full_url)
    if (original.username is not None or original.fragment or '\\' in request.full_url
            or any(ord(character) < 33 for character in request.full_url)):
        raise RuntimeError('FIXTURE_HTTP_TARGET_UNDECLARED')
    origin = f'{original.scheme}://{original.netloc}'
    path_query = original.path + ('?' + original.query if original.query else '')
    headers = dict(request.header_items())
    lower = {name.lower(): value for name, value in headers.items()}
    if 'x-fixture-requested-origin' in lower:
        raise RuntimeError('FIXTURE_HTTP_HEADER_UNDECLARED')
    method = request.get_method()
    if original.scheme == 'https':
        declarations = json.loads(DOCUMENT['values'].get('PROVIDER_HTTP_REQUESTS_JSON', '[]'))
        exact = sum(row == {'origin': origin, 'method': method, 'path_query': path_query}
                    for row in declarations)
        # Only the existing consumer's nonce is dynamic; identities and every
        # other part of the Observe request remain exact, not a route wildcard.
        observe = (method == 'GET' and request.data is None and any(
            row == {'origin': origin, 'method': 'GET', 'path_query': original.path}
            for row in declarations) and original.path in {
                '/api/audit-briefs', '/api/audit-stories', '/api/audit-decisions', '/api/news-evidence'}
            and re.fullmatch('__release_observe=[0-9a-f]{32}' + (
                '&mode=all&limit=1' if original.path == '/api/news-evidence' else ''), original.query)
            and lower.get('cloudflare-workers-version-overrides') == 'aurum-signal-room="' +
                DOCUMENT['values'].get('TARGET_WORKER_VERSION', '') + '"'
            and lower.get('cache-control') == 'no-cache' and lower.get('pragma') == 'no-cache')
        if exact != 1 and not observe:
            raise RuntimeError('FIXTURE_HTTP_TARGET_UNDECLARED')
        headers['X-Fixture-Requested-Origin'] = origin
        mapped = _loopback_origin('WORKER_LOOPBACK_BASE_URL') + path_query
    elif origin == 'http://127.0.0.1:8765':
        if (method != 'GET' or request.data is not None or original.query
                or original.path not in {'/api/health', '/api/status', '/api/critical-status', '/api/audit',
                    '/api/audit-briefs', '/api/audit-stories', '/api/audit-decisions'}):
            raise RuntimeError('FIXTURE_HTTP_TARGET_UNDECLARED')
        mapped = _loopback_origin('LOCAL_API_BASE_URL') + original.path
    else:
        if (original.scheme != 'http' or original.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or original.port not in DOCUMENT.get('loopback_ports', []) or original.port == 8765):
            raise RuntimeError('FIXTURE_HTTP_TARGET_UNDECLARED')
        mapped = request.full_url
    forwarded = urllib.request.Request(mapped, data=request.data, headers=headers, method=method)
    # Do not consult the interactive user's proxy settings. A redirect is not
    # new fixture authority and must not forward credentials to another target.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoFixtureRedirect())
    return opener.open(forwarded, timeout=timeout)


if DOCUMENT['values'].get('PROVIDER_HTTP_REQUESTS_JSON'):
    urllib.request.urlopen = _mapped_urlopen

QUALIFICATION_ENTRYPOINT = _qualification_entrypoint()

# Explicit old-code external configuration adaptation only. The original source
# and its Git identity stay intact; this separately hashed adapter is part of
# the declared execution environment, not an unmodified production execution.
if sys.argv[0] == '-c':
    # These are the two existing controller preflight producers, not arbitrary
    # inline Python. Bind the full code/argument vector before either can run.
    if PREFLIGHT_ROOT is None or len(sys.orig_argv) != 5 or sys.orig_argv[1] != '-c':
        raise RuntimeError('FIXTURE_INLINE_COMMAND_UNDECLARED')
    owner = (SOURCE_ROOT / 'scripts/control_center_runtime_supervision.ps1').read_text(encoding='utf-8')
    bodies = {name: re.findall(r'\$' + name + r" = @'\n(.*?)\n'@", owner, re.DOTALL)
              for name in ('copy', 'migration')}
    if any(len(rows) != 1 for rows in bodies.values()):
        raise RuntimeError('FIXTURE_INLINE_SOURCE_UNDECLARED')
    state = Path(DOCUMENT['runtime_root']) / '.local'
    allowed = (
        (bodies['copy'][0], str(state / 'forward/forward-evidence.sqlite3'),
         str(state / 'preflight/forward-evidence.sqlite3')),
        (bodies['migration'][0], str(PREFLIGHT_ROOT), str(state / 'preflight/forward-evidence.sqlite3')),
    )
    observed = (sys.orig_argv[2].replace('\r\n', '\n'), *sys.orig_argv[3:])
    if observed not in allowed:
        raise RuntimeError('FIXTURE_INLINE_COMMAND_UNDECLARED')
elif QUALIFICATION_ENTRYPOINT is not None:
    pass
elif DOCUMENT.get('legacy_configuration_revision'):
    # Bind the adapter to the executable entrypoint, not mutable runtime-root
    # placement: target code intentionally runs outside the old code checkout.
    # Select an exact declared entrypoint lexically before any resolve/stat.
    # Unknown UNC/reparse paths must not cause a filesystem lookup to reject them.
    entrypoints = {
        os.path.normcase(os.path.abspath(root / 'scripts' / name)): root / 'scripts' / name
        for root in CODE_ROOTS for name in (
            'run_dashboard_api.py', 'run_dashboard_sync.py', 'run_forward_collector.py',
            'run_news_annotator.py', 'run_live_broadcast_publisher.py', 'check_production_shape.py',
            'run_evidence_repair_v2.py',
        )
    }
    entrypoint = entrypoints.get(os.path.normcase(os.path.abspath(sys.argv[0])))
    if entrypoint is None or entrypoint.resolve() != entrypoint:
        raise RuntimeError('FIXTURE_LEGACY_ENTRYPOINT_UNDECLARED')
    code_root = entrypoint.parent.parent
    if entrypoint.name == 'check_production_shape.py' and sys.argv[1:] == [
            '--status-url', 'http://127.0.0.1:8765/api/critical-status', '--allow-pending-generation-decision']:
        base = urlsplit(DOCUMENT['values'].get('LOCAL_API_BASE_URL', ''))
        if (base.scheme != 'http' or base.hostname != '127.0.0.1' or
                base.port not in DOCUMENT.get('loopback_ports', []) or base.port == 8765 or
                base.path not in ('', '/') or base.query or base.fragment or base.username):
            raise RuntimeError('FIXTURE_STATUS_TARGET_UNDECLARED')
        # The real checker and its result remain unchanged; remap only its
        # existing exact production URL before argparse or any socket call.
        sys.argv[2] = f'http://127.0.0.1:{base.port}/api/critical-status'
    observed = subprocess.run([GIT_EXECUTABLE, 'rev-parse', 'HEAD'], cwd=code_root,
        check=True, capture_output=True, text=True, timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
    if observed == DOCUMENT['legacy_configuration_revision']:
        sys.path.insert(0, str(code_root))
        from xauusd_forecaster import news_scheduler
        news_scheduler._runtime_environment_value = environment_value
