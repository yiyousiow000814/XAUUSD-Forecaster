from __future__ import annotations

import hashlib
import base64
import ast
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import site
import subprocess
import textwrap
import uuid
import sys
import ssl
import threading
import time
import socket
import stat

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("powershell.exe") is None,
    reason="Windows PowerShell is required for Control Plane contracts",
)


ROOT = Path(__file__).resolve().parents[1]
OLD_START_TOKEN = "2026-08-26T03:20:00.0000000+00:00"
NEW_START_TOKEN = "2026-08-26T03:22:48.0603020+00:00"
CURRENT_START_TOKEN = "2026-08-26T03:25:00.0000000+00:00"
DEAD_INSTALLER_START_TOKEN = "2026-08-26T03:15:00.0000000+00:00"
CONTROL_FILES = tuple(json.loads(
    (ROOT / "scripts" / "runtime-control-files.json").read_text(encoding="utf-8")
)["files"])
BUNDLE_DIGEST_ALGORITHM = "xauusd.control-bundle.sha256.v1"
BUNDLE_SCHEMA_VERSION = 3


@pytest.mark.parametrize("locator", [r"\\unreachable.invalid\share\fixture.json", r"C:\outside\fixture.json"])
def test_business_configuration_rejects_foreign_locator_before_filesystem(tmp_path, monkeypatch, locator):
    text = (ROOT / "tests/fixtures/business_environment.py").read_text(encoding="utf-8")
    owner = next(node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef) and node.name == "_configuration")
    scope = {"Path": Path, "os": os, "CONFIGURATION": tmp_path / "fixture-user-environment.json"}
    exec(compile(ast.Module(body=[owner], type_ignores=[]), "business_environment.py", "exec"), scope)
    monkeypatch.setenv("XAUUSD_FIXTURE_CONFIGURATION", locator)
    calls = []
    def denied(*args, **kwargs):
        calls.append(args)
        raise AssertionError("foreign locator caused filesystem access")
    monkeypatch.setattr(Path, "resolve", denied)
    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(RuntimeError, match="FIXTURE_CONFIGURATION_REQUIRED"):
        scope["_configuration"]()
    assert calls == []


@pytest.mark.parametrize("entrypoint", [r"\\unreachable.invalid\share\run_dashboard_api.py",
    r"C:\outside\scripts\run_dashboard_api.py", "unknown-script.py"])
def test_legacy_entrypoint_rejection_precedes_filesystem_resolution(tmp_path, monkeypatch, entrypoint):
    source = (ROOT / "tests/fixtures/business_environment.py").read_text(encoding="utf-8")
    boundary = next(node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call) and isinstance(node.test.func, ast.Attribute)
        and isinstance(node.test.func.value, ast.Name) and node.test.func.value.id == "DOCUMENT"
        and node.test.args and isinstance(node.test.args[0], ast.Constant)
        and node.test.args[0].value == "legacy_configuration_revision")
    # Execute the real selection/check before its later exact Git lookup.
    body = boundary.body[:4]
    scope = {"Path": Path, "os": os, "sys": type("Args", (), {"argv": [entrypoint]}),
        "CODE_ROOTS": {tmp_path / "source"}}
    calls = []
    def denied(*args, **kwargs):
        calls.append(args)
        raise AssertionError("unknown entrypoint caused filesystem resolution")
    monkeypatch.setattr(Path, "resolve", denied)
    with pytest.raises(RuntimeError, match="FIXTURE_LEGACY_ENTRYPOINT_UNDECLARED"):
        exec(compile(ast.Module(body=body, type_ignores=[]), "business_environment.py", "exec"), scope)
    assert calls == []


def _new_sealed_fixture_root(request=None) -> Path:
    from xauusd_forecaster.runtime_paths import isolated_rehearsal_root
    owned = isolated_rehearsal_root() / ("xauusd-rehearsal-" + uuid.uuid4().hex)
    owned.mkdir(parents=True)
    if request is not None:
        def cleanup():
            assert owned.parent == isolated_rehearsal_root()
            assert re.fullmatch(r"xauusd-rehearsal-[0-9a-f]{32}", owned.name)
            assert not owned.is_symlink() and not owned.is_junction()
            def remove_readonly(function, path, error):
                target = Path(path)
                if not isinstance(error, PermissionError) or not target.is_relative_to(owned):
                    raise error
                if not target.is_file() or target.is_symlink():
                    raise error
                target.chmod(stat.S_IREAD | stat.S_IWRITE)
                function(path)
            shutil.rmtree(owned, onexc=remove_readonly)
        request.addfinalizer(cleanup)
    return owned


def test_qualification_business_boundary_executes_exact_git_paths_and_http(request, tmp_path):
    """Run the sealed Python boundary with real local Git and raw HTTP only."""
    owned = _new_sealed_fixture_root(request)
    source = owned / "source"
    source.mkdir()
    (source / "scripts").mkdir()
    for name in ("build_release_validation_fixtures.py", "bootstrap_news_projection.py", "check_deferred_projection_parity.py"):
        shutil.copyfile(ROOT / "scripts" / name, source / "scripts" / name)
    environment = _isolated_windows_environment()
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1")
    def git(*args):
        return subprocess.check_output([shutil.which("git"), "-C", str(source), *args],
            env=environment, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW).decode().strip()
    git("init", "-q")
    git("add", "scripts")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "-qm", "Isolated command source")
    revision = git("rev-parse", "HEAD")
    validation_parent = owned / "validation"
    validation_parent.mkdir()
    validation = validation_parent / ("aurum-release-validation-" + uuid.uuid4().hex)
    runtime = owned / "runtime"
    for target in (validation, runtime):
        git("worktree", "add", "--detach", "-q", str(target), revision)
    state_root = runtime / ".local/forward"
    state_root.mkdir(parents=True)
    bundle = owned / "control-bundle"
    bundle.mkdir()
    shutil.copyfile(source / "scripts/check_deferred_projection_parity.py", bundle / "check_deferred_projection_parity.py")
    bootstrap_config = owned / "sync.json"
    bootstrap_config.write_text("{}", encoding="utf-8")
    captured = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            captured.append((self.command, self.path, raw, dict(self.headers)))
            self.send_response(302 if self.path == "/redirect" else 200)
            if self.path == "/redirect":
                self.send_header("Location", "https://outside.invalid/forbidden")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        do_GET = reply
        do_POST = reply
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def cleanup_http():
        if thread.is_alive():
            server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
    request.addfinalizer(cleanup_http)
    port = server.server_address[1]
    assert port != 8765
    origin = "https://fixture-aurum-signal-room.fixture.workers.dev"
    worker = "11111111-1111-4111-8111-111111111111"
    bootstrap_args = ["--config", str(bootstrap_config), "--version-host", origin,
                      "--state-file", str(state_root / "bootstrap.json"), "--max-cycles", "1"]
    values = {"TARGET_SOURCE_REVISION": revision, "TARGET_WORKER_VERSION": worker,
        "VALIDATION_WORKSPACE_PARENT": str(validation_parent), "CONTROL_BUNDLE_ROOT": str(bundle),
        "BOOTSTRAP_ARGUMENTS_JSON": json.dumps(bootstrap_args),
        "WORKER_LOOPBACK_BASE_URL": f"http://127.0.0.1:{port}", "LOCAL_API_BASE_URL": f"http://127.0.0.1:{port}",
        "PROVIDER_HTTP_REQUESTS_JSON": json.dumps([
            {"origin": origin, "method": method, "path_query": route} for method, route in
            (("POST", "/api/news-index"), ("GET", "/api/news-evidence"), ("GET", "/redirect"))])}
    configuration = owned / "fixture-user-environment.json"
    configuration.write_text(json.dumps({"schema_version": 1, "runtime_root": str(runtime),
        "values": values, "loopback_ports": [port]}), encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    retained = outside / "retained.txt"
    retained.write_text("untouched", encoding="utf-8")
    link = owned / "redirected"
    junction = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        f"$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path '{link}' -Target '{outside}' | Out-Null"],
        capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
    assert junction.returncode == 0, junction.stderr
    assert link.is_junction()
    def cleanup_junction():
        if link.is_junction():
            assert link.parent == owned
            link.rmdir()
    request.addfinalizer(cleanup_junction)
    template = (ROOT / "tests/fixtures/business_environment.py").read_text(encoding="utf-8")
    for marker, value in {"__CONFIG_PATH__": configuration.as_posix(), "__FIXTURE_ROOT__": owned.as_posix(),
        "__CONFIG_DIGEST__": hashlib.sha256(configuration.read_bytes()).hexdigest(), "__GIT_PATH__": Path(shutil.which("git")).as_posix()}.items():
        template = template.replace(marker, value)
    (owned / "fixture_business_environment.py").write_text(template, encoding="utf-8")
    probe = owned / "probe.py"
    probe.write_text(textwrap.dedent(f'''\
        import sys, urllib.request
        from pathlib import Path
        import fixture_business_environment as boundary
        def reject(action, reason):
            try: action()
            except RuntimeError as error: assert str(error)==reason, str(error)
            else: raise AssertionError('expected rejection')
        reject(lambda: boundary._owned_existing_path({str(link / 'retained.txt')!r}),'FIXTURE_QUALIFICATION_REPARSE_DENIED')
        sys.argv = [{str(validation / 'scripts/build_release_validation_fixtures.py')!r}, '--output', {str(validation / '.release-validation-fixtures')!r}]
        assert boundary._qualification_entrypoint() == Path(sys.argv[0])
        boundary.DOCUMENT['values']['TARGET_SOURCE_REVISION']='0'*40
        reject(boundary._qualification_entrypoint,'FIXTURE_QUALIFICATION_REVISION_MISMATCH')
        boundary.DOCUMENT['values']['TARGET_SOURCE_REVISION']={revision!r}
        sys.argv=[{str(source / 'scripts/bootstrap_news_projection.py')!r},*{bootstrap_args!r}]
        assert boundary._qualification_entrypoint()==Path(sys.argv[0])
        sys.argv[2]={str(owned.parent / 'outside.json')!r}
        reject(boundary._qualification_entrypoint,'FIXTURE_QUALIFICATION_ARGUMENTS_UNDECLARED')
        sys.argv=[{str(bundle / 'check_deferred_projection_parity.py')!r},'--runtime-root',{str(runtime)!r},'--producer-root',{str(runtime)!r},
            '--version-id',{worker!r},'--git-sha',{revision!r},'--producer-revision',{revision!r},
            '--required-after','2026-09-07T00:00:00+00:00','--observe-attempt','a'*32,'--route','/api/news-evidence']
        assert boundary._qualification_entrypoint()==Path(sys.argv[0])
        Path(sys.argv[0]).write_text('wrong installed bytes',encoding='utf-8')
        reject(boundary._qualification_entrypoint,'FIXTURE_QUALIFICATION_SCRIPT_MISMATCH')
        body='{{"真实":"bytes"}}'.encode('utf-8')
        override='aurum-signal-room="{worker}"'
        request=urllib.request.Request({origin!r}+'/api/news-index',data=body,headers={{'Content-Type':'application/json','Cloudflare-Workers-Version-Overrides':override}})
        with urllib.request.urlopen(request,timeout=2) as response: assert response.read()==body
        headers={{'Cloudflare-Workers-Version-Overrides':override,'Cache-Control':'no-cache','Pragma':'no-cache'}}
        with urllib.request.urlopen(urllib.request.Request({origin!r}+'/api/news-evidence?__release_observe='+('a'*32)+'&mode=all&limit=1',headers=headers),timeout=2) as response: assert response.status==200
        with urllib.request.urlopen('http://127.0.0.1:8765/api/critical-status',timeout=2) as response: assert response.status==200
        for url in ['https://outside.invalid/api/news-index',{origin!r}+'/api/news-index?extra=1', 'http://127.0.0.1:8765/api/unknown']:
            reject(lambda: urllib.request.urlopen(url,timeout=2),'FIXTURE_HTTP_TARGET_UNDECLARED')
        reject(lambda: urllib.request.urlopen({origin!r}+'/redirect',timeout=2),'FIXTURE_HTTP_REDIRECT_DENIED')
        print('EXACT_CHILD_AND_HTTP_BOUNDARIES_PASSED')
        '''), encoding="utf-8")
    environment["XAUUSD_FIXTURE_CONFIGURATION"] = str(configuration)
    try:
        result = subprocess.run([sys.executable, str(probe)], cwd=owned, env=environment,
            capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "EXACT_CHILD_AND_HTTP_BOUNDARIES_PASSED"
        assert len(captured) == 4
        assert captured[0][:3] == ("POST", "/api/news-index", '{"真实":"bytes"}'.encode("utf-8"))
        headers = {key.lower(): value for key, value in captured[0][3].items()}
        assert headers['x-fixture-requested-origin'] == origin
        assert headers['cloudflare-workers-version-overrides'] == f'aurum-signal-room="{worker}"'
        assert retained.read_text(encoding="utf-8") == "untouched"
    finally:
        cleanup_http()
        cleanup_junction()


def test_connected_asset_provider_uses_built_redirects_and_rejects_unknown_rules(tmp_path):
    """Exercise the real isolated ASSETS provider; this is not Worker-build proof."""
    assets = tmp_path / "web/dist/client"
    assets.mkdir(parents=True)
    svg = "<svg>真实 fixture</svg>".encode("utf-8")
    (assets / "favicon.svg").write_bytes(svg)
    code = "\n".join((
        "import assert from 'node:assert/strict';",
        "import {writeFileSync} from 'node:fs';",
        f"import {{assetFetch}} from {json.dumps((ROOT / 'tests/fixtures/connected_worker_adapter.mjs').as_uri())};",
        f"const root={json.dumps(str(tmp_path))};const path={json.dumps(str(assets / '_redirects'))};",
        "const get=(path,method='GET')=>assetFetch(root,new Request('https://connected-worker.invalid'+path,{method}));",
        "writeFileSync(path,'/favicon.ico /favicon.svg 301\\n');",
        "assert.equal(get('/favicon.ico').status,301);assert.equal(get('/favicon.ico').headers.get('location'),'/favicon.svg');",
        f"assert.deepEqual(Buffer.from(await get('/favicon.svg').arrayBuffer()),Buffer.from({json.dumps(list(svg))}));",
        "assert.equal((await get('/favicon.svg','HEAD').arrayBuffer()).byteLength,0);assert.equal(get('/missing').status,404);",
        "assert.equal(get('/favicon.svg','POST').status,405);",
        "for(const rule of ['/favicon.ico https://outside.invalid/ 302','/favicon.ico /../outside 301','/* /favicon.svg 302']){",
        "writeFileSync(path,'/favicon.ico /favicon.svg 301\\n'+rule);",
        "assert.throws(()=>get('/favicon.ico'),{code:'WORKER_ADAPTER_REDIRECT_UNDECLARED'});}",
        "writeFileSync(path,Array(33).fill('/favicon.ico /favicon.svg 301').join('\\n'));",
        "assert.throws(()=>get('/favicon.ico'),{code:'WORKER_ADAPTER_REDIRECT_BOUND'});",
        "writeFileSync(path,'x'.repeat(8193));assert.throws(()=>get('/favicon.ico'),{code:'WORKER_ADAPTER_FILE_BOUND'});",
        "console.log('BUILT_ASSET_CONTRACT_PASSED');",
    ))
    environment = _isolated_windows_environment()
    result = subprocess.run([shutil.which("node"), "--import",
        (ROOT / "web/tests/register-cloudflare-worker-loader.mjs").as_uri(), "--input-type=module", "-e", code],
        cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    # The adapter deliberately routes diagnostic console output to stderr.
    assert 'BUILT_ASSET_CONTRACT_PASSED' in result.stderr


@pytest.mark.parametrize("redirected_component", [None, ".local", "forward", "environment-attestations"])
def test_staged_sync_checks_derived_directories_at_write(tmp_path, monkeypatch, redirected_component):
    """Execute the actual fixture consumer and production writer, including NTFS junctions."""
    import importlib.util
    import runpy
    from xauusd_forecaster import runtime_paths, news_scheduler

    root = tmp_path.resolve()
    (root / "fixture-owned.json").write_text("{}", encoding="utf-8")
    runtime = root / "runtime"
    runtime.mkdir()
    outside = root / "outside"
    outside.mkdir()
    retained = outside / "retained.json"
    retained.write_text('{"unchanged":true}', encoding="utf-8")
    link = None
    if redirected_component:
        link = (root / "environment-attestations" if redirected_component == "environment-attestations"
                else runtime / ".local" if redirected_component == ".local"
                else runtime / ".local/forward")
        link.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            f"$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path '{link}' -Target '{outside}' | Out-Null"],
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
        assert link.is_junction()
    monkeypatch.setattr(runtime_paths, "isolated_runtime_configuration", lambda: {
        "owned_root": str(root), "runtime_root": str(runtime), "fixture_id": "fixture",
    })
    monkeypatch.setattr(news_scheduler, "_runtime_environment_value", lambda _name: "synthetic-configuration-sentinel")
    monkeypatch.setenv("XAUUSD_ISOLATED_CONFIGURATION_SHA256", "a" * 64)
    calls = []
    original_spec = importlib.util.spec_from_file_location

    def load_real_sync_with_bounded_loop(name, *args, **kwargs):
        spec = original_spec(name, *args, **kwargs)
        if name == "staged_sync":
            original_load = spec.loader.exec_module
            def execute(module):
                original_load(module)
                # Network/continuous-loop behavior is covered by the real
                # ACTIVE rehearsal. This test's boundary is filesystem writes.
                module.run_continuous_sync = lambda *arguments, **options: calls.append((arguments, options))
            spec.loader.exec_module = execute
        return spec

    monkeypatch.setattr(importlib.util, "spec_from_file_location", load_real_sync_with_bounded_loop)
    monkeypatch.setattr(sys, "argv", ["staged_sync_owner.py", "--fixture-root", str(root),
        "--source-root", str(ROOT), "--provider", "https://127.0.0.1:18443"])
    try:
        if redirected_component:
            with pytest.raises(ValueError, match="authority is redirected"):
                runpy.run_path(str(ROOT / "tests/fixtures/staged_sync_owner.py"), run_name="__main__")
            assert calls == []
        else:
            runpy.run_path(str(ROOT / "tests/fixtures/staged_sync_owner.py"), run_name="__main__")
            assert len(calls) == 1
            state = runtime / ".local/forward"
            schedule = next(state.glob("dashboard-resource-schedule-state*.json"))
            document = json.loads(schedule.read_text(encoding="utf-8"))
            assert "news_evidence" not in document["resources"]
            assert not list(state.glob("*.tmp"))
        assert retained.read_text(encoding="utf-8") == '{"unchanged":true}'
        assert set(outside.iterdir()) == {retained}
    finally:
        if link and link.is_junction():
            assert link.is_relative_to(root)
            # Delete only the owned junction entry, never recurse into its target.
            link.rmdir()


@pytest.mark.parametrize("case", ["valid", "normalized", "tampered", "missing-digest", "missing-key", "external-url", "production-port", "wrong-root", "oversized", "junction", "production-root", "schema-type", "missing-source", "outside-authority", "utf16", "utf32"])
def test_isolated_configuration_owner_matches_python_and_powershell(tmp_path, monkeypatch, case, request):
    from xauusd_forecaster.news_scheduler import _runtime_environment_value
    owned = _new_sealed_fixture_root(request)
    identity = owned.name.removeprefix("xauusd-rehearsal-")
    profile = owned / "profile"
    config = {
        "schema_version": 1, "mode": "ISOLATED_REHEARSAL", "fixture_id": identity,
        "owned_root": str(owned), "profile_root": str(profile),
        "runtime_root": str(profile / "XAUUSD-Forecaster-runtime"),
        "repository_root": str(owned / "repository"),
        "source_root": str(owned / "source"),
        "task_namespace": f"\\XAUUSD-Contract-{identity}\\",
        "loopback_ports": [18321], "provider_endpoint": "http://127.0.0.1:18321",
        "values": {"GEMINI_API_KEY": "isolated-sentinel", "XAUUSD_DASHBOARD_URL": ""},
    }
    if case == "normalized":
        config["repository_root"] = str(owned / "never-traversed" / ".." / "repository")
    elif case == "missing-key":
        config["values"] = {"OTHER": "sentinel"}
    elif case == "external-url":
        config["provider_endpoint"] = "https://example.invalid:18321"
    elif case == "production-port":
        config["loopback_ports"] = [8765]
        config["provider_endpoint"] = "http://127.0.0.1:8765"
    elif case == "wrong-root":
        config["repository_root"] = str(tmp_path)
    elif case == "oversized":
        config["padding"] = "x" * 40000
    elif case == "schema-type":
        config["schema_version"] = True
    elif case == "missing-source":
        del config["source_root"]
    elif case == "junction":
        destination = tmp_path / "outside-authority"
        destination.mkdir()
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
            f"$null=New-Item -ItemType Junction -Path '{owned / 'repository'}' -Target '{destination}'"],
            check=True, capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    path = owned / "fixture-user-environment.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    if case in ("utf16", "utf32"):
        path.write_bytes(json.dumps(config).encode("utf-16" if case == "utf16" else "utf-32"))
    monkeypatch.setenv("XAUUSD_ISOLATED_CONFIGURATION", str(path))
    monkeypatch.setenv("XAUUSD_ISOLATED_CONFIGURATION_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setenv("GEMINI_API_KEY", "process-decoy")
    if case == "tampered":
        path.write_bytes(path.read_bytes() + b" ")
    elif case == "missing-digest":
        monkeypatch.delenv("XAUUSD_ISOLATED_CONFIGURATION_SHA256")
    elif case == "production-root":
        real_profile = ctypes.create_unicode_buffer(32768)
        assert ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, real_profile) == 0
        forbidden = Path(real_profile.value) / "XAUUSD-Forecaster-runtime" / owned.name / path.name
        monkeypatch.setenv("XAUUSD_ISOLATED_CONFIGURATION", str(forbidden))
    elif case == "outside-authority":
        # A plausible UUID/leaf spelling is not a positive read authority.
        monkeypatch.setenv("XAUUSD_ISOLATED_CONFIGURATION", str(tmp_path / owned.name / path.name))
    if case in ("valid", "normalized"):
        assert _runtime_environment_value("GEMINI_API_KEY") == "isolated-sentinel"
        from xauusd_forecaster.runtime_paths import isolated_runtime_configuration
        assert isolated_runtime_configuration()["repository_root"] == str(owned / "repository")
        assert not (owned / "never-traversed").exists()
    else:
        with monkeypatch.context() as reads:
            if case == "outside-authority":
                def no_read(*args, **kwargs):
                    raise AssertionError("untrusted configuration locator was opened")
                reads.setattr("builtins.open", no_read)
                reads.setattr(Path, "open", no_read)
            with pytest.raises(ValueError, match="ISOLATED_CONFIGURATION_"):
                _runtime_environment_value("GEMINI_API_KEY")
    script = owned / "consume.ps1"
    script.write_text("$ErrorActionPreference='Stop'\n"
        f". '{ROOT / 'scripts/control_center_common.ps1'}'\n"
        "$releaseSecretsPath='NEVER_READ';$collectorSecretsPath='NEVER_READ'\n"
        "if((Get-IsolatedRuntimeConfiguration).repository_root -ne "
        f"'{owned / 'repository'}'){{throw 'UNVALIDATED_RAW_PATH_RETURNED'}}\n"
        "(Get-ReleaseSecret -Name 'GEMINI_API_KEY').value\n"
        "Get-CollectorSecret -Name 'GEMINI_API_KEY'\n", encoding="utf-8")
    for shell in ("powershell.exe", "pwsh.exe"):
        result = subprocess.run([shell, "-NoProfile", "-File", str(script)],
            capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if case in ("valid", "normalized"):
            assert result.returncode == 0, result.stderr
            assert result.stdout.split() == ["isolated-sentinel", "isolated-sentinel"]
            for action, expected_context_rejection in (("CodeRevision", True), ("ControlBundlePreflight", False)):
                context = subprocess.run([shell, "-NoProfile", "-File", str(ROOT / "scripts/xauusd_control_center.ps1"),
                    "-Action", action, "-RuntimeRoot", config["source_root"], "-RepositoryRoot", config["source_root"]],
                    capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                assert context.returncode != 0
                assert ("ISOLATED_CONFIGURATION_CONTEXT_MISMATCH" in context.stderr) == expected_context_rejection, context.stderr
        else:
            assert result.returncode != 0
            assert "ISOLATED_CONFIGURATION_" in result.stderr


@pytest.mark.parametrize("case", ["valid", "mapped-loopback", "provider-placement", "business-api", "business-preflight", "installer-external", "tampered", "critical-override", "top-level-code", "incomplete"])
def test_connected_external_adapter_preserves_control_owners(tmp_path, monkeypatch, case, request):
    owned = _new_sealed_fixture_root(request)
    identity = owned.name.removeprefix("xauusd-rehearsal-")
    source = owned / "source"
    template = source / "tests/fixtures/control_plane_connected_boundary.ps1"
    template.parent.mkdir(parents=True)
    payload = (ROOT / "tests/fixtures/control_plane_connected_boundary.ps1").read_text(encoding="utf-8")
    if case == "critical-override":
        payload += "\nfunction Test-RuntimeObservation { return $true }\n"
    elif case == "top-level-code":
        payload += "\nthrow 'TOP_LEVEL_MUST_NOT_EXECUTE'\n"
    elif case == "incomplete":
        payload = "function Invoke-GitHubChecksRead { throw 'INCOMPLETE' }"
    template.write_text(payload, encoding="utf-8")
    profile = owned / "profile"
    config = {
        "schema_version": 1, "mode": "ISOLATED_REHEARSAL", "fixture_id": identity,
        "owned_root": str(owned), "profile_root": str(profile),
        "runtime_root": str(profile / "XAUUSD-Forecaster-runtime"),
        "repository_root": str(owned / "repository"), "source_root": str(source),
        "task_namespace": f"\\XAUUSD-Contract-{identity}\\",
        "loopback_ports": [18321], "provider_endpoint": "http://127.0.0.1:18321",
        "external_adapter_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
        "values": {"TARGET_SOURCE_REVISION": "a" * 40,
            "GITHUB_CHECK_RUNS_JSON": json.dumps({"check_runs": [{"id": 1, "name": "real-required",
                "head_sha": "a" * 40, "status": "completed", "conclusion": "success"}]}),
            "WRANGLER_READ_RESPONSES_JSON": "[]"},
    }
    requests = []
    if case == "mapped-loopback":
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                requests.append(self.path)
                if self.path in ("/fixture-static", "/fixture-redirect"):
                    raw = "<html>真实 UTF-8 marker</html>".encode("utf-8")
                    self.send_response(307 if self.path == "/fixture-redirect" else 200)
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("X-Aurum-Git-SHA", "a" * 40)
                    if self.path == "/fixture-redirect":
                        self.send_header("Location", "https://isolated-worker.invalid/fixture-static")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                raw = json.dumps({"recent_decisions": [{"decision_time": "provider-owned-sentinel"}],
                    "boundary": "RAW_PROVIDER_BYTES"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup_server():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            assert not thread.is_alive()
        request.addfinalizer(cleanup_server)
        port = server.server_address[1]
        assert port != 8765
        config["loopback_ports"] = [port]
        config["provider_endpoint"] = f"http://127.0.0.1:{port}"
        config["values"]["LOCAL_API_BASE_URL"] = config["provider_endpoint"]
        config["values"]["WORKER_LOOPBACK_BASE_URL"] = config["provider_endpoint"]
        config["values"]["PROVIDER_HTTP_REQUESTS_JSON"] = json.dumps([
            {"origin": "https://isolated-worker.invalid", "method": "GET", "path_query": "/api/ingest"},
            {"origin": "https://isolated-worker.invalid", "method": "GET", "path_query": "/fixture-static"},
            {"origin": "https://isolated-worker.invalid", "method": "GET", "path_query": "/fixture-redirect"},
        ])
    if case == "tampered":
        template.write_text(payload + "\n# unaccepted bytes\n", encoding="utf-8")
    business_body = ""
    if case == "provider-placement":
        stable, candidate = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
        placement = owned / "worker-placement.json"
        config["values"].update(WORKER_PLACEMENT_FILE=str(placement), STABLE_WORKER_VERSION=stable,
                                TARGET_WORKER_VERSION=candidate)
        reads = owned / "worker-read-responses.json"
        reads.write_text("[]", encoding="utf-8")
        config["values"]["WRANGLER_READ_RESPONSES_FILE"] = str(reads)
        config["values"]["WRANGLER_READ_RESPONSES_JSON"] = json.dumps([{
            "arguments": ["versions", "list", "--name", "aurum-signal-room"],
            "response": [{"id": "FORBIDDEN_PRIOR_FALLBACK"}],
        }])
        placement.write_text(json.dumps({"id": "fixture-initial", "versions": [{"version_id": stable, "percentage": 100}]}), encoding="utf-8")
        Path(config["repository_root"], "web").mkdir(parents=True)
        business_body = f"""
        $repositoryRoot='{config['repository_root']}';$workerName='aurum-signal-room'
        $readPath='{reads}'
        $initial=@([pscustomobject]@{{arguments=@('versions','list','--name',$workerName);response=@([pscustomobject]@{{id='{stable}'}})}})
        Write-ControlCenterJsonAtomic -Path $readPath -Value $initial -Depth 6
        if(@(Get-CloudflareVersions).Count -ne 1){{throw 'INITIAL_PROVIDER_UNIVERSE_CHANGED'}}
        $initial[0].response+=([pscustomobject]@{{id='{candidate}'}})
        Write-ControlCenterJsonAtomic -Path $readPath -Value $initial -Depth 6
        if(@(Get-CloudflareVersions).Count -ne 2){{throw 'PROVIDER_PUBLICATION_NOT_OBSERVED'}}
        foreach($bad in @('{{',(' '*131073))){{
            [IO.File]::WriteAllText($readPath,$bad)
            $denied=$false;try{{$null=Invoke-WranglerJson -Arguments @('versions','list','--name',$workerName)}}catch{{$denied=$true}}
            if(-not $denied){{throw 'MALFORMED_PROVIDER_READ_ACCEPTED'}}
        }}
        Remove-Item -LiteralPath $readPath
        $denied=$false;try{{$null=Invoke-WranglerJson -Arguments @('versions','list','--name',$workerName)}}catch{{
            if($_.Exception.Message -cne 'CONNECTED_PROVIDER_READ_UNAVAILABLE'){{throw}};$denied=$true}}
        if(-not $denied){{throw 'MISSING_PROVIDER_READ_FELL_BACK'}}
        [IO.File]::WriteAllText($readPath,'[]')
        $stable=[pscustomobject]@{{worker_version_id='{stable}'}}
        $candidate=[pscustomobject]@{{worker_version_id='{candidate}';validation_key='{candidate}:{'a' * 40}'}}
        Set-CloudflareCandidatePointer -Stable $stable -Candidate $candidate
        $placed=Invoke-WranglerJson -Arguments @('deployments','status','--name',$workerName)
        if(@($placed.versions | Where-Object {{$_.version_id -ceq '{stable}' -and $_.percentage -eq 100}}).Count -ne 1 -or
           @($placed.versions | Where-Object {{$_.version_id -ceq '{candidate}' -and $_.percentage -eq 0}}).Count -ne 1){{throw 'EXACT_PLACEMENT_NOT_ESTABLISHED'}}
        $before=[IO.File]::ReadAllText('{placement}');$denied=0
        foreach($arguments in @(
            @('versions','deploy','{stable}@50','{candidate}@50','--name',$workerName,'--yes','--message','unsupported'),
            @('versions','deploy','00000000-0000-4000-8000-000000000000@100','--name',$workerName,'--yes','--message','unsupported'))
        ){{try{{$null=Invoke-WranglerDeploymentCommand -Arguments $arguments}}catch{{$denied++}}}}
        if($denied -ne 2 -or [IO.File]::ReadAllText('{placement}') -cne $before){{throw 'INVALID_PLACEMENT_MUTATED'}}
        Invoke-CloudflareDeployment -StableVersionId '{candidate}' -CandidateVersionId '{stable}' -Message 'promote release 33333333-3333-4333-8333-333333333333'
        Invoke-CloudflareDeployment -StableVersionId '{stable}' -Message 'reverse stable 33333333-3333-4333-8333-333333333333'
        $restored=Invoke-WranglerJson -Arguments @('deployments','status','--name',$workerName)
        if(@($restored.versions).Count -ne 1 -or $restored.versions[0].version_id -cne '{stable}' -or $restored.versions[0].percentage -ne 100){{throw 'FIXTURE_REVERSE_OWNER_NOT_RESTORED'}}
        $savedPlacement=[IO.File]::ReadAllText('{placement}')
        try {{
            [IO.File]::WriteAllText('{placement}',(' '*8193))
            $denied=$false;try{{$null=Invoke-WranglerJson -Arguments @('deployments','status','--name',$workerName)}}catch{{
                if($_.Exception.Message -cne 'CONNECTED_PLACEMENT_UNAVAILABLE'){{throw}};$denied=$true}}
            if(-not $denied){{throw 'OVERSIZED_PLACEMENT_ACCEPTED'}}
        }} finally {{[IO.File]::WriteAllText('{placement}',$savedPlacement)}}
        """
    if case == "installer-external":
        control = Path(config["repository_root"]) / ".local/runtime-control"
        control.mkdir(parents=True)
        # This unit probe verifies the real VBS process and exact argument
        # vector only. It does not claim to execute the Watchdog lifecycle.
        (control / "xauusd_control_center.ps1").write_text("# launcher-vector probe\n", encoding="utf-8")
        (control / "xauusd_watchdog_launcher.vbs").write_text(
            'If WScript.Arguments.Count <> 3 And WScript.Arguments.Count <> 4 Then WScript.Quit 8\n'
            'If WScript.Arguments.Count = 4 Then\n'
            ' If WScript.Arguments(3) <> "' + "a" * 32 + '" Then WScript.Quit 9\n'
            'End If\nWScript.Quit 0\n', encoding="utf-8")
        for suffix in ("Main", "Guard"):
            name = f"XAUUSD-Contract-{identity}-{suffix}"
            (owned / f"scheduler-{name}.json").write_text(json.dumps({"TaskName": name,
                "TaskPath": config["task_namespace"], "State": "Ready", "Settings": {"Enabled": True}}), encoding="utf-8")
        business_body = f"""
        $repositoryRoot='{config['repository_root']}';$moduleRoot='{config['runtime_root']}'
        $taskName='XAUUSD-Contract-{identity}-Main';$guardTaskName='XAUUSD-Contract-{identity}-Guard'
        $saved=Suspend-ControlPlaneSupervision -CollectorClockRecovery
        foreach($name in @($taskName,$guardTaskName)){{
            if((Get-ScheduledTask -TaskName $name).Settings.Enabled){{throw 'TASK_NOT_QUIESCED'}}
        }}
        Wait-ControlPlaneGuardQuiesced
        Restore-ControlPlaneSupervision -State $saved
        foreach($name in @($taskName,$guardTaskName)){{
            if(-not (Get-ScheduledTask -TaskName $name).Settings.Enabled){{throw 'TASK_NOT_RESTORED'}}
        }}
        $denied=$false
        try{{Disable-ScheduledTask -TaskName 'XAUUSD-Forecaster-Autostart'}}
        catch{{if($_.Exception.Message -cne 'CONNECTED_TASK_UNDECLARED'){{throw}};$denied=$true}}
        if(-not $denied){{throw 'PRODUCTION_TASK_ACCEPTED'}}
        foreach($transaction in @('',('a'*32))){{
            $child=Start-WatchdogReplacement -PassThru -InstallTransactionId $transaction
            try{{if(-not $child.WaitForExit(5000) -or $child.ExitCode -ne 0){{throw 'INSTALLER_VECTOR_FAILED'}}}}
            finally{{if(-not $child.HasExited){{$null=Stop-NativeProcessTree -Process $child}};$child.Dispose()}}
        }}
        """
    business_case = case in ("business-api", "business-preflight")
    if business_case:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        assert port != 8765
        config["loopback_ports"] = [port]
        config["provider_endpoint"] = f"http://127.0.0.1:{port}"
        runtime = Path(config["runtime_root"])
        config["values"].update(LOCAL_API_BASE_URL=f"http://127.0.0.1:{port}",
            PYTHON_EXECUTABLE=sys.executable, PYTHON_STARTUP_GUARD=str(runtime),
            **{key: "" for key in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "GEMINI_API_ACCOUNTS",
                "GEMINI_PRO_API_KEYS", "GEMINI_FLASH_API_KEYS", "GEMINI_FLASH_LITE_API_KEYS",
                "DASHBOARD_OPERATOR_BRIDGE_TOKEN")})
        launch = "Start-ForecasterService -Service $services[0] -SkipExistingCheck"
        preflight_setup = ""
        if case == "business-preflight":
            config["values"]["PREFLIGHT_API_PORT"] = str(port)
            stage = Path(config["repository_root"]) / ".local/runtime-preflight" / ("a" * 40)
            preflight_setup = f"""
            $candidateState=Join-Path $moduleRoot '.local\\preflight'
            $candidateDatabase=Join-Path $candidateState 'forward-evidence.sqlite3'
            New-CandidatePreflightDatabase -Python '{sys.executable}' -StageRoot '{stage}' `
                -SourceDatabase (Join-Path $runtimeForwardRoot 'forward-evidence.sqlite3') -TargetDatabase $candidateDatabase
            $services[0].ScriptPath='{stage}\\scripts\\run_dashboard_api.py'
            if((Get-AvailableLoopbackPort) -ne {port}){{throw 'PREFLIGHT_PORT_IDENTITY_CHANGED'}}
            """
            launch = f"""$ownedProcess=Start-Process -FilePath '{sys.executable}' -ArgumentList @(
                $services[0].ScriptPath,'--state-root',$candidateState,'--runtime-role','preflight',
                '--database',$candidateDatabase,'--host','127.0.0.1','--port','{port}') `
                -WorkingDirectory '{stage}' -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $logRoot 'runtime-preflight.stdout.log') `
                -RedirectStandardError (Join-Path $logRoot 'runtime-preflight.stderr.log')"""
        business_body = f"""
        $moduleRoot='{runtime}'; $runtimeForwardRoot=Join-Path $moduleRoot '.local\\forward'
        $logRoot=Join-Path $runtimeForwardRoot 'logs'
        $services=@([pscustomobject]@{{Key='api';Kind='Python';CodeRoot=$moduleRoot;
            ScriptPath=(Join-Path $moduleRoot 'scripts\\run_dashboard_api.py');
            Arguments=@('--state-root',$runtimeForwardRoot)}})
        $ownedProcess=$null
        New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
        {preflight_setup}
        try {{
            {launch}
            $until=[DateTimeOffset]::UtcNow.AddSeconds(10); $observed=$null
            while([DateTimeOffset]::UtcNow -lt $until) {{
                $owned=@(Get-CimInstance Win32_Process | Where-Object {{
                    $_.Name -ceq 'python.exe' -and $_.CommandLine -and
                    $_.CommandLine.Contains($services[0].ScriptPath)}})
                if($owned.Count -gt 1) {{throw 'CONCURRENT_API_OWNER'}}
                if($owned.Count -eq 1) {{$ownedProcess=Get-Process -Id $owned[0].ProcessId}}
                try {{
                    $reply=Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/health' -UseBasicParsing -TimeoutSec 1
                    if('{case}' -cne 'business-preflight'){{throw 'EMPTY_API_FALSE_HEALTHY'}}
                    if([int]$reply.StatusCode -ne 200){{throw 'UNEXPECTED_API_STATUS'}}
                    $observed=$reply.Content | ConvertFrom-Json
                }} catch {{
                    if($_.Exception.Message -cin @('EMPTY_API_FALSE_HEALTHY','UNEXPECTED_API_STATUS')){{throw}}
                    $reply=$_.Exception.Response
                    if($reply) {{
                        if([int]$reply.StatusCode -ne 503) {{throw 'UNEXPECTED_API_STATUS'}}
                        # Both IWR implementations consume the error response;
                        # the actual body is retained in ErrorDetails, not an
                        # already-drained WebResponse stream in Desktop PS.
                        if($_.ErrorDetails.Message) {{$observed=$_.ErrorDetails.Message | ConvertFrom-Json}}
                    }}
                }}
                if($observed) {{break}}
                Start-Sleep -Milliseconds 100
            }}
            if(-not $ownedProcess -or -not $observed -or
                $observed.readiness_scope -cne 'PROCESS_AND_CRITICAL_STATUS') {{
                $tails=@(Get-ChildItem -LiteralPath $logRoot -Filter '*.log' | ForEach-Object {{
                    $_.Name + ':' + ((Get-Content -LiteralPath $_.FullName -Tail 12) -join '|')}})
                throw ('REAL_API_BOUNDARY_NOT_EXECUTED:owner=' + [bool]$ownedProcess + ';body=' +
                    ($observed | ConvertTo-Json -Compress) + ';' + ($tails -join ';'))
            }}
        }} finally {{
            if($ownedProcess) {{
                $ended=Stop-NativeProcessTree -Process $ownedProcess
                if($ended.state -cnotin @('TERMINATED','ALREADY_EXITED')) {{throw 'API_CLEANUP_UNRESOLVED'}}
            }}
        }}
        $priorGuard=$env:XAUUSD_FIXTURE_STARTUP_GUARD_SHA256
        try {{
            $env:XAUUSD_FIXTURE_STARTUP_GUARD_SHA256='0'*64
            $denied=$false
            try {{{launch}}}
            catch {{if($_.Exception.Message -cne 'CONNECTED_BUSINESS_GUARD_UNDECLARED'){{throw}};$denied=$true}}
            if(-not $denied){{throw 'UNVERIFIED_BUSINESS_CHILD_STARTED'}}
        }} finally {{$env:XAUUSD_FIXTURE_STARTUP_GUARD_SHA256=$priorGuard}}
        """
    path = owned / "fixture-user-environment.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    if business_case:
        _make_isolated_business_source(runtime, path)
        if case == "business-preflight":
            import sqlite3
            _make_isolated_business_source(stage, path)
            (source / "scripts").mkdir(exist_ok=True)
            shutil.copyfile(ROOT / "scripts/control_center_runtime_supervision.ps1",
                            source / "scripts/control_center_runtime_supervision.ps1")
            (runtime / ".local/forward").mkdir(parents=True)
            with sqlite3.connect(runtime / ".local/forward/forward-evidence.sqlite3") as connection:
                connection.execute("CREATE TABLE fixture_original (value INTEGER)")
                connection.execute("INSERT INTO fixture_original VALUES (42)")
            connection.close()
        (runtime / "sitecustomize.py").write_text(
            "import os,sys\ntry:\n import fixture_business_environment\n"
            "except BaseException as error:\n sys.stderr.write(type(error).__name__+':'+str(error)[:160]+'\\n');sys.stderr.flush();os._exit(78)\n", encoding="utf-8")
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(runtime), site.getusersitepackages())))
        monkeypatch.setenv("USERPROFILE", str(profile))
        monkeypatch.setenv("HOME", str(profile))
        monkeypatch.setenv("NO_PROXY", "*")
        monkeypatch.setenv("XAUUSD_FIXTURE_CONFIGURATION", str(path))
        for name, key in (("sitecustomize.py", "XAUUSD_FIXTURE_STARTUP_GUARD_SHA256"),
                          ("fixture_business_environment.py", "XAUUSD_FIXTURE_BUSINESS_GUARD_SHA256")):
            monkeypatch.setenv(key, hashlib.sha256((runtime / name).read_bytes()).hexdigest())
    monkeypatch.setenv("XAUUSD_ISOLATED_CONFIGURATION", str(path))
    monkeypatch.setenv("XAUUSD_ISOLATED_CONFIGURATION_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    script = owned / "consume.ps1"
    script.write_text("$ErrorActionPreference='Stop'\n"
        f". '{ROOT / 'scripts/control_center_common.ps1'}'\n"
        f". '{ROOT / 'scripts/control_center_provider_adapters.ps1'}'\n"
        f". '{ROOT / 'scripts/control_center_runtime_supervision.ps1'}'\n"
        f". '{ROOT / 'scripts/control_center_transaction_engine.ps1'}'\n"
        f". '{ROOT / 'scripts/control_center_persistence_gateway.ps1'}'\n"
        f". '{ROOT / 'scripts/control_center_install.ps1'}'\n"
        "$prior=@{};Get-ChildItem function:|ForEach-Object{$prior[$_.Name]=$_.Definition}\n"
        "foreach($definition in @(Get-IsolatedExternalAdapterDefinitions)){"
        "Set-Item -Path ('function:'+ $definition.name) -Value ([scriptblock]::Create($definition.body))}\n"
        "$allowed=@('Invoke-GitHubChecksRead','Invoke-WranglerJson','Invoke-WranglerDeploymentCommand',"
        "'Invoke-WebRequest','Invoke-RestMethod','Invoke-CandidateStaticAssetRequest','Get-ScheduledTask','Start-ScheduledTask','Stop-ScheduledTask',"
        "'Enable-ScheduledTask','Disable-ScheduledTask','Register-ScheduledTask','Unregister-ScheduledTask','Start-Process','Get-AvailableLoopbackPort')\n"
        "foreach($name in $prior.Keys){if($name -notin $allowed -and (Get-Item ('function:'+ $name)).Definition -cne $prior[$name]){throw 'OWNER_CHANGED'}}\n"
        "$requiredGitHubChecks=@('real-required');$convertFromJsonSupportsDateKind=$false\n"
        "if((Get-RequiredGitHubChecksResult -Revision ('a'*40)).state -cne 'PASSED'){throw 'RAW_GATE_NOT_EXECUTED'}\n"
        "if((Get-RequiredGitHubChecksResult -Revision ('b'*40)).state -ceq 'PASSED'){throw 'WRONG_SHA_ACCEPTED'}\n"
        "$denied=0;try{Invoke-WranglerJson -Arguments @('unknown')}catch{if($_.Exception.Message -eq 'CONNECTED_WRANGLER_REQUEST_UNDECLARED'){$denied++}else{throw}}\n"
        "try{Invoke-WebRequest -Uri 'http://127.0.0.1:8765'}catch{if($_.Exception.Message -eq 'CONNECTED_NETWORK_TARGET_UNDECLARED'){$denied++}else{throw}}\n"
        "if($denied -ne 2){throw 'EXTERNAL_FALLBACK'}\n"
        f"if((Get-IsolatedRuntimeConfiguration).values.LOCAL_API_BASE_URL -and '{case}' -cnotlike 'business-*'){{"
        "if((Get-LatestRuntimeDecisionTime) -cne 'provider-owned-sentinel'){throw 'REAL_OWNER_REMAP_FAILED'};"
        "if((Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/health' -UseBasicParsing).Content -notmatch 'RAW_PROVIDER_BYTES'){throw 'RAW_BYTES_CHANGED'};"
        "if((Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/critical-status').boundary -cne 'RAW_PROVIDER_BYTES'){throw 'RAW_BODY_CHANGED'};"
        "$rejected=0;try{Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -Method Post}catch{$rejected++};"
        "try{Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/status?extra=1'}catch{$rejected++};"
        "if($rejected -ne 2){throw 'UNDECLARED_REMAP_ACCEPTED'}}\n"
        f"if('{case}' -ceq 'mapped-loopback'){{"
        "if((Invoke-RestMethod -Uri 'https://isolated-worker.invalid/api/ingest').boundary -cne 'RAW_PROVIDER_BYTES'){throw 'PROVIDER_BYTES_CHANGED'};"
        "$rejected=0;foreach($uri in @('https://isolated-worker.invalid/api/ingest?extra=1','https://outside.invalid/api/ingest')){"
        "try{Invoke-WebRequest -Uri $uri}catch{if($_.Exception.Message -cne 'CONNECTED_NETWORK_TARGET_UNDECLARED'){throw};$rejected++}};"
        "if($rejected -ne 2){throw 'UNDECLARED_EXTERNAL_NETWORK_ACCEPTED'};"
        "$raw=Invoke-CandidateStaticAssetRequest -RequestUri 'https://isolated-worker.invalid/fixture-static';"
        f"if($raw.status -ne 200 -or $raw.git_sha -cne ('a'*40) -or [Convert]::ToBase64String($raw.body_bytes) -cne '{base64.b64encode('<html>真实 UTF-8 marker</html>'.encode()).decode()}'){{throw 'STATIC_BYTES_CHANGED'}};"
        "$redirect=Invoke-CandidateStaticAssetRequest -RequestUri 'https://isolated-worker.invalid/fixture-redirect';"
        "if($redirect.status -ne 307 -or $redirect.location -cne 'https://isolated-worker.invalid/fixture-static'){throw 'STATIC_REDIRECT_CHANGED'};"
        "$denied=0;foreach($uri in @('https://outside.invalid/fixture-static','https://isolated-worker.invalid/fixture-static?extra=1')){"
        "try{Invoke-CandidateStaticAssetRequest -RequestUri $uri}catch{if($_.Exception.Message -cne 'CONNECTED_STATIC_TARGET_UNDECLARED'){throw};$denied++}};"
        "if($denied -ne 2){throw 'STATIC_NATIVE_NETWORK_FALLBACK'}}\n"
        "$badArguments='\"\\\\server\\share\\outside.vbs\" \"'+(Get-IsolatedRuntimeConfiguration).owned_root+'\\inside\"';"
        "$processDenied=0;foreach($executable in @('C:\\outside\\wscript.exe',(Join-Path ([Environment]::SystemDirectory) 'wscript.exe'))){"
        "try{Start-Process -FilePath $executable -ArgumentList $badArguments}catch{"
        "if($_.Exception.Message -cne 'CONNECTED_PROCESS_START_UNDECLARED'){throw};$processDenied++}};"
        "if($processDenied -ne 2){throw 'UNTRACKED_PROCESS_START'}\n"
        + business_body +
        "'OWNERS_PRESERVED_UNKNOWN_DENIED'\n", encoding="utf-8")
    for shell in ("powershell.exe", "pwsh.exe"):
        shell_environment = dict(os.environ)
        # A PS7 parent can otherwise make PS5 load Utility 7 (Core-only), where
        # Desktop Get-FileHash is absent. Pin only this child to its own modules.
        shell_home = (Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0"
                      if shell == "powershell.exe" else Path(shutil.which(shell)).parent)
        shell_environment["PSModulePath"] = str(shell_home / "Modules")
        result = subprocess.run([shell, "-NoProfile", "-File", str(script)], capture_output=True,
            text=True, env=shell_environment, timeout=22 if business_case or case == "installer-external" else 10,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if case in ("valid", "mapped-loopback", "provider-placement", "business-api", "business-preflight", "installer-external"):
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == "OWNERS_PRESERVED_UNKNOWN_DENIED"
        else:
            assert result.returncode != 0
            assert "ISOLATED_EXTERNAL_ADAPTER_" in result.stderr
            assert "TOP_LEVEL_MUST_NOT_EXECUTE" not in result.stderr
        if case == "business-preflight":
            with sqlite3.connect(runtime / ".local/preflight/forward-evidence.sqlite3") as connection:
                assert connection.execute("SELECT value FROM fixture_original").fetchall() == [(42,)]
                assert connection.execute("SELECT count(*) FROM market_snapshots").fetchone() == (0,)
            connection.close()
    if case == "mapped-loopback":
        assert requests == ["/api/status", "/api/health", "/api/critical-status", "/api/ingest",
                            "/fixture-static", "/fixture-redirect"] * 2


@pytest.mark.parametrize("duration,budget,elapsed,expected", [
    (120, None, 0, 120), (121, None, 0, "INVALID"),
    (2700, 2700, 0, 2700), (2700, 2700, 100, 2600),
    (2701, 2701, 0, "INVALID"), (1000, 1200, 0, "INVALID"),
    (2700, 2700, 2700, "EXHAUSTED"), (2700, 2700, -1, "INVALID"),
])
def test_quote_input_restart_cannot_renew_connected_scenario_budget(duration, budget, elapsed, expected):
    import importlib.util
    spec = importlib.util.spec_from_file_location("quote_budget", ROOT / "tests/fixtures/quote_session_input.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    started = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    config = {"quote_input_seconds": duration}
    if budget is not None:
        config.update(connected_scenario_timeout_seconds=budget,
                      connected_scenario_started_at=started.isoformat())
    now = started + timedelta(seconds=elapsed)
    if isinstance(expected, int):
        assert module.remaining_input_seconds(config, now) == expected
    else:
        with pytest.raises(RuntimeError, match="QUOTE_INPUT_BUDGET_" + expected):
            module.remaining_input_seconds(config, now)


@pytest.mark.parametrize("case", ["valid", "build-only", "wrong-state", "wrong-config", "wrong-code",
    "missing-key", "outside-secret", "explicit-cli-mismatch", "missing-secret-file", "junction"])
def test_quote_launcher_uses_exact_isolated_authority_before_native_work(monkeypatch, request, case):
    owned = _new_sealed_fixture_root(request)
    identity = owned.name.removeprefix("xauusd-rehearsal-")
    profile = owned / "profile"
    runtime = profile / "XAUUSD-Forecaster-runtime"
    repository = owned / "repository"
    source = owned / "source"
    code = source if case in ("wrong-code", "build-only") else runtime
    project = code / "ctrader/XauusdForwardQuoteBridge"
    project.mkdir(parents=True)
    (code / "scripts").mkdir()
    for name in ("run_live_quote_bridge.ps1", "XauusdForwardQuoteBridge.cs"):
        shutil.copyfile(ROOT / "ctrader/XauusdForwardQuoteBridge" / name, project / name)
    shutil.copyfile(ROOT / "scripts/control_center_common.ps1", code / "scripts/control_center_common.ps1")
    cli = owned / "broker-adapter.exe"
    cli.write_bytes(b"not-executed-at-this-contract-boundary")
    secrets = owned / "sentinel-secrets"
    secrets.mkdir()
    for name in ("ctid.txt", "account.txt", "ctrader-cli.pwd"):
        (secrets / name).write_text("isolated-sentinel", encoding="utf-8")
    if case == "missing-secret-file":
        (secrets / "ctrader-cli.pwd").unlink()
    elif case == "junction":
        link = owned / "linked-secrets"
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
            f"$null=New-Item -ItemType Junction -Path '{link}' -Target '{secrets}'"],
            check=True, capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        secrets = link
    config = {
        "schema_version": 1, "mode": "ISOLATED_REHEARSAL", "fixture_id": identity,
        "owned_root": str(owned), "profile_root": str(profile), "runtime_root": str(runtime),
        "repository_root": str(repository), "source_root": str(source),
        "task_namespace": f"\\XAUUSD-Contract-{identity}\\", "loopback_ports": [18321],
        "provider_endpoint": "http://127.0.0.1:18321",
        "values": {"CTRADER_CLI_PATH": str(cli), "CTRADER_SECRET_ROOT": str(secrets)},
    }
    if case == "missing-key":
        del config["values"]["CTRADER_CLI_PATH"]
    elif case == "build-only":
        config["values"] = {"BUILD_ONLY": "NO_BROKER_CREDENTIALS"}
    elif case == "outside-secret":
        config["values"]["CTRADER_SECRET_ROOT"] = str(owned.parent / "never-read-secrets")
    path = owned / "fixture-user-environment.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("XAUUSD_ISOLATED_CONFIGURATION", str(path))
    monkeypatch.setenv("XAUUSD_ISOLATED_CONFIGURATION_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setenv("CTRADER_CLI_PATH", "process-decoy-must-not-run")
    monkeypatch.setenv("CTRADER_SECRET_ROOT", "process-decoy-must-not-read")
    state = owned / "wrong-state" if case == "wrong-state" else runtime / ".local/forward"
    args = f"-StateRoot '{state}'"
    if case == "build-only":
        args = "-BuildOnly"
    elif case == "wrong-config":
        args += f" -ConfigRoot '{owned / 'wrong-config'}'"
    elif case == "explicit-cli-mismatch":
        args += f" -CliPath '{owned / 'wrong-cli.exe'}'"
    script = owned / "quote-boundary.ps1"
    script.write_text("$ErrorActionPreference='Stop';$buildCalls=0\n"
        "function dotnet {$script:buildCalls++;throw 'BUILD_BOUNDARY_REACHED'}\n"
        f"try {{ . '{project / 'run_live_quote_bridge.ps1'}' {args};throw 'UNEXPECTED_EXECUTION' }}"
        " catch {$reason=$_.Exception.Message}\n"
        "@{reason=$reason;build_calls=$buildCalls;state=$StateRoot;output=$OutputDirectory;"
        "cli=$CliPath;secret=$SecretRoot;config=$ConfigRoot}|ConvertTo-Json -Compress\n", encoding="utf-8")
    for shell in ("powershell.exe", "pwsh.exe"):
        result = subprocess.run([shell, "-NoProfile", "-File", str(script)], capture_output=True,
            text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        if case in ("valid", "build-only"):
            # Only the configuration/native boundary is exercised here. The real
            # build and external quote stream remain separate rehearsal evidence.
            assert observed["reason"] == "BUILD_BOUNDARY_REACHED"
            assert observed["build_calls"] == 1
            if case == "build-only":
                assert not (state / "quotes").exists()
                assert observed["cli"] == observed["secret"] == ""
                continue
            for field, expected in (("state", state), ("output", state / "quotes"),
                    ("cli", cli), ("secret", secrets), ("config", repository / ".local/config")):
                assert Path(observed[field]) == expected
        else:
            assert observed["build_calls"] == 0, observed
            assert observed["reason"] != "BUILD_BOUNDARY_REACHED"
            assert not (state / "quotes").exists()


def _canonical_bundle_digest(revision: str, hashes: dict[str, str]) -> str:
    lines = [
        BUNDLE_DIGEST_ALGORITHM,
        f"schema_version={BUNDLE_SCHEMA_VERSION}",
        f"source_revision={revision}",
        f"file_count={len(hashes)}",
        *(f"file={name}\thash={hashes[name].lower()}" for name in sorted(hashes)),
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _legacy_v2_bundle_digest(hashes: dict[str, str]) -> str:
    return hashlib.sha256(
        "\n".join(f"{name}={hashes[name].lower()}" for name in sorted(hashes)).encode()
    ).hexdigest()


def _controller_load_timing_copy(script: Path, directory: Path) -> Path:
    """Explicit timestamp-only diagnostic copy; never the final source acceptance."""
    import difflib
    original = script.read_text(encoding="utf-8-sig")
    mapped = []
    selected = []
    for number, line in enumerate(original.splitlines(), 1):
        stripped = line.strip()
        before = after = None
        if stripped == '$ErrorActionPreference = "Stop"':
            before = "entry-after-param"
        elif stripped.startswith("$controlCenterEntrypointPath = "):
            before, after = "entry-path-enter", "entry-path-return"
        elif stripped.startswith("$scriptRepositoryRoot = Split-Path"):
            before, after = "entry-split-enter", "entry-split-return"
        elif stripped == "$repositoryRoot = if ($RepositoryRoot) {":
            before = "runtime-roots-enter"
        elif stripped.startswith(". (Join-Path $PSScriptRoot "):
            if "'control_center_common.ps1'" in stripped:
                mapped.append("Write-ContractPhase 'runtime-roots-return'")
                selected.append({"source_line": number, "phase": "runtime-roots-return"})
            before, after = f"owner-enter:{number}", f"owner-return:{number}"
        elif stripped.startswith("$runtimeControlSourceManifest = Get-Content"):
            before = "manifest-enter"
        elif stripped == "$runtimeControlSourceManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json":
            after = "manifest-return"
        elif stripped == "$convertFromJsonSupportsDateKind =":
            before = "convertfromjson-command-enter"
        elif stripped == '(Get-Command ConvertFrom-Json).Parameters.ContainsKey("DateKind")':
            after = "convertfromjson-command-return"
        elif stripped.startswith("$serviceContractRevision = Get-BusinessRuntimeRevision"):
            before, after = f"business-revision-enter:{number}", f"business-revision-return:{number}"
        elif stripped.startswith("$services = @(Resolve-ServiceLaunchContracts"):
            before = "service-contract-enter"
        elif stripped == "-CodeRoot $serviceContractCodeRoot)":
            after = "service-contract-return"
        if before:
            mapped.append(f"Write-ContractPhase '{before}'")
            selected.append({"source_line": number, "phase": before})
        if stripped == '"CodeRevision" { Write-Output (Get-CodeRevision) }':
            line = line.replace('Write-Output (Get-CodeRevision)',
                "Write-ContractPhase 'action-enter'; Write-Output (Get-CodeRevision); Write-ContractPhase 'action-return'")
            selected.append({"source_line": number, "phase": "action"})
        mapped.append(line)
        if after:
            mapped.append(f"Write-ContractPhase '{after}'")
            selected.append({"source_line": number, "phase": after})
    required = {"entry-after-param", "entry-path-enter", "entry-path-return", "entry-split-enter", "entry-split-return",
                "runtime-roots-enter", "runtime-roots-return", "manifest-enter", "manifest-return", "convertfromjson-command-enter",
                "convertfromjson-command-return", "service-contract-enter", "service-contract-return", "action"}
    if not required.issubset({row["phase"] for row in selected}):
        raise AssertionError("CONTROLLER_DIAGNOSTIC_SOURCE_MAPPING_INCOMPLETE")
    # The copied entrypoint loads every original owner/manifest from the exact
    # original root. Only these two explicit locator tokens are mapped; no
    # controller fact, function body, return value, or native call is replaced.
    instrumented = "\n".join(mapped) + "\n"
    instrumented = re.sub(r"\$PSScriptRoot\b", lambda _m: "'" + str(script.parent).replace("'", "''") + "'", instrumented)
    instrumented = re.sub(r"\$PSCommandPath\b", lambda _m: "'" + str(script).replace("'", "''") + "'", instrumented)
    copied = directory / "controller-load-diagnostic.ps1"
    copied.write_text(instrumented, encoding="utf-8-sig")
    mapping = {"kind": "INSTRUMENTED_DIAGNOSTIC_NOT_UNMODIFIED_ACCEPTANCE",
        "source_path": str(script), "source_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "copy_sha256": hashlib.sha256(copied.read_bytes()).hexdigest(), "phases": selected}
    (directory / "controller-load-source-map.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    (directory / "controller-load-timestamp.patch").write_text("".join(difflib.unified_diff(
        original.splitlines(keepends=True), instrumented.splitlines(keepends=True),
        fromfile=str(script), tofile=str(copied))), encoding="utf-8")
    print("CONTROLLER_DIAGNOSTIC_SOURCE_BINDING " + json.dumps(mapping, separators=(",", ":")))
    return copied


def _run_contract_with_runtime(
    tmp_path: Path, body: str, runtime_executable: str, *, environment=None,
    execution_timeout: float = 22, controller_script: Path | None = None,
    runtime_root: Path | None = None, repository_root: Path | None = None,
) -> str:
    runtime = runtime_root or tmp_path / "runtime"
    repository = repository_root or tmp_path / "repository"
    runtime.mkdir(parents=True, exist_ok=True)
    repository.mkdir(exist_ok=True)
    script = controller_script or ROOT / "scripts" / "xauusd_control_center.ps1"
    child_environment = environment if environment is not None else os.environ
    if child_environment.get("XAUUSD_CONTROL_LOAD_TIMING") == "1":
        script = _controller_load_timing_copy(script, tmp_path)
    phase_file = tmp_path / "contract-phases.txt"
    def preserve_diagnostic(stdout, stderr):
        shard = child_environment.get("XAUUSD_CONTROL_LOAD_DIAGNOSTIC_SHARD")
        if not shard or not (tmp_path / "controller-load-source-map.json").is_file():
            return "NOT_REQUESTED"
        if shard == "control-install":
            output = ROOT / "output/windows-runtime/control-install"
        elif shard == "control-install-configuration":
            output = ROOT / "output/windows-runtime/control-install-configuration"
        elif shard == "control-install-rehearsal":
            output = ROOT / "output/windows-runtime/control-install-rehearsal"
        else:
            raise AssertionError("CONTROLLER_DIAGNOSTIC_SHARD_UNDECLARED")
        for ancestor in (ROOT, ROOT / "output", ROOT / "output/windows-runtime", output):
            if ancestor.is_symlink() or ancestor.is_junction() or ancestor.resolve() != ancestor:
                raise AssertionError("CONTROLLER_DIAGNOSTIC_OUTPUT_REDIRECTED")
        destination = output / ("controller-load-" + uuid.uuid4().hex)
        destination.mkdir(parents=True)
        for name in ("controller-load-diagnostic.ps1", "controller-load-source-map.json",
                     "controller-load-timestamp.patch", "contract-phases.txt"):
            path = tmp_path / name
            if path.is_file():
                if path.stat().st_size > 2_000_000:
                    raise AssertionError("CONTROLLER_DIAGNOSTIC_ARTIFACT_BOUND")
                shutil.copyfile(path, destination / name)
        (destination / "process-output.json").write_text(json.dumps({
            "runtime": runtime_executable, "pid": process.pid, "exit_code": process.poll(),
            "resolved_runtime": shutil.which(runtime_executable, path=child_environment.get("PATH")),
            "module_path": child_environment.get("PSModulePath", "")[:4096],
            "stdout": (stdout or "")[-16384:], "stderr": (stderr or "")[-16384:],
        }, indent=2), encoding="utf-8")
        return str(destination)
    phase_path_literal = str(phase_file).replace("'", "''")
    task_prefix = f"XAUUSD-Contract-{uuid.uuid4().hex}"
    # Temporary filesystem roots do not isolate machine-global scheduled tasks.
    # Contract tests must opt in through explicit stubs, never native mutations.
    scheduler_guard = r'''
        function Stop-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
        function Start-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
        function Enable-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
        function Disable-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
        function Register-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
        function Unregister-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
    '''
    command = (
        # Shell startup expands the supplied search path. Restore the actual
        # runtime's module authority before invoking any cmdlet or controller.
        "[Environment]::SetEnvironmentVariable('PSModulePath', "
        "[IO.Path]::Combine($PSHOME, 'Modules'), 'Process'); "
        "function Write-ContractPhase { param([string]$Phase); "
        f"[IO.File]::AppendAllText('{phase_path_literal}', "
        "[DateTimeOffset]::UtcNow.ToString('o')+' '+$Phase+[Environment]::NewLine) }; "
        "Write-ContractPhase 'harness-start'; "
        "Write-ContractPhase ('effective-module-path:' + "
        "[Environment]::GetEnvironmentVariable('PSModulePath', 'Process')); "
        "Write-ContractPhase 'load'; "
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{runtime}' "
        f"-RepositoryRoot '{repository}'; "
        "Write-ContractPhase 'body'; "
        f"$taskName='{task_prefix}-Main'; $guardTaskName='{task_prefix}-Guard'; "
        f"{scheduler_guard}; {body}; "
        "Write-ContractPhase 'complete'"
    )
    process = subprocess.Popen(
        [
            runtime_executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        stdout, stderr = process.communicate(timeout=execution_timeout)
    except subprocess.TimeoutExpired as failure:
        phases = phase_file.read_text(encoding="utf-8")[:8192] if phase_file.exists() else "NOT_STARTED"
        # Keep cleanup inside pytest's existing 30-second deadline. A root-only
        # kill cannot prove that a descendant no longer owns the captured pipes.
        try:
            system_directory = ctypes.create_unicode_buffer(32768)
            size = ctypes.windll.kernel32.GetSystemDirectoryW(
                system_directory, len(system_directory),
            )
            if not 0 < size < len(system_directory):
                raise RuntimeError("CONTRACT_SYSTEM_DIRECTORY_UNAVAILABLE")
            termination = subprocess.run(
                [str(Path(system_directory.value) / "taskkill.exe"),
                 "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if termination.returncode != 0:
                raise RuntimeError("CONTRACT_TREE_TERMINATION_UNRESOLVED")
            process.wait(timeout=1)
            stdout, stderr = process.communicate(timeout=1)
        except (subprocess.TimeoutExpired, RuntimeError) as cleanup:
            artifact = preserve_diagnostic("", str(cleanup))
            raise AssertionError(
                f"CONTRACT_TREE_TERMINATION_UNRESOLVED pid={process.pid}; "
                f"phases={phases!r}; cleanup={cleanup}; diagnostic={artifact}"
            ) from failure
        artifact = preserve_diagnostic(stdout, stderr)
        raise AssertionError(
            f"CONTRACT_CHILD_DEADLINE pid={process.pid}; "
            f"stdout={stdout!r}; stderr={stderr!r}; phases={phases!r}; diagnostic={artifact}"
        ) from failure
    if process.returncode:
        artifact = preserve_diagnostic(stdout, stderr)
        raise AssertionError(
            f"{runtime_executable} control-plane contract failed\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}\ndiagnostic: {artifact}"
        )
    return stdout.strip()


def _run_contract(tmp_path: Path, body: str) -> str:
    return _run_contract_with_runtime(tmp_path, body, "powershell.exe")


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_controller_load_timing_preserves_real_action(tmp_path, runtime_executable, request, monkeypatch):
    owned = _new_sealed_fixture_root(request)
    source = owned / "source"
    _make_real_control_source(source,
        boundary="$null = Get-Command Get-FileHash -ErrorAction Stop\n"
                 "if ((Get-UserEnvironmentValue -Name 'GEMINI_API_KEY') -cne "
                 "'synthetic-configuration-sentinel') { throw 'STAGED_CONFIGURATION_SOURCE_MISMATCH' }",
        loopback_ports=[18766], sealed_configuration=True)
    configuration = owned / "fixture-user-environment.json"
    environment = _isolated_windows_environment()
    environment["XAUUSD_ISOLATED_CONFIGURATION"] = str(configuration)
    environment["XAUUSD_ISOLATED_CONFIGURATION_SHA256"] = hashlib.sha256(configuration.read_bytes()).hexdigest()
    shell_home = (Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0"
                  if runtime_executable == "powershell.exe" else Path(shutil.which(runtime_executable)).parent)
    foreign_modules = owned / "foreign-parent-modules"
    foreign_modules.mkdir()
    environment["PSModulePath"] = str(foreign_modules)
    outputs = []
    for mode in ("plain", "instrumented"):
        case = owned / mode
        case.mkdir()
        child = dict(environment)
        child.pop("XAUUSD_CONTROL_LOAD_TIMING", None)
        if mode == "instrumented":
            child["XAUUSD_CONTROL_LOAD_TIMING"] = "1"
        outputs.append(_run_contract_with_runtime(case,
            "Write-Output ((Get-CodeRevision)+':'+$services.Count)", runtime_executable, environment=child,
            controller_script=source / "scripts/xauusd_control_center.ps1",
            runtime_root=owned / "profile/XAUUSD-Forecaster-runtime", repository_root=owned / "repository"))
        phase_labels = [line.split(" ", 1)[1] for line in
                        (case / "contract-phases.txt").read_text(encoding="utf-8").splitlines()]
        observed_paths = [phase.removeprefix("effective-module-path:") for phase in phase_labels
                          if phase.startswith("effective-module-path:")]
        assert len(observed_paths) == 1
        assert ";" not in observed_paths[0] and Path(observed_paths[0]) == shell_home / "Modules"
        assert phase_labels.index("effective-module-path:" + observed_paths[0]) < phase_labels.index("load")
    assert outputs[0] == outputs[1]
    assert not (owned / "plain/controller-load-source-map.json").exists()
    diagnostic = owned / "instrumented"
    mapping = json.loads((diagnostic / "controller-load-source-map.json").read_text(encoding="utf-8"))
    assert mapping["kind"] == "INSTRUMENTED_DIAGNOSTIC_NOT_UNMODIFIED_ACCEPTANCE"
    assert mapping["source_sha256"] == hashlib.sha256((source / "scripts/xauusd_control_center.ps1").read_bytes()).hexdigest()
    phases = (diagnostic / "contract-phases.txt").read_text(encoding="utf-8")
    assert "entry-after-param" in phases
    for phase in ("entry-path", "entry-split", "runtime-roots", "manifest", "service-contract", "action"):
        assert f"{phase}-enter" in phases and f"{phase}-return" in phases
    assert "body" in phases and "complete" in phases
    diagnostic_shard = "control-install-configuration" if runtime_executable == "powershell.exe" else "control-install"
    child["XAUUSD_CONTROL_LOAD_DIAGNOSTIC_SHARD"] = diagnostic_shard
    failure = owned / "failure"
    failure.mkdir()
    with pytest.raises(AssertionError, match="ISOLATED_DIAGNOSTIC_FAILURE_SENTINEL") as failure_result:
        _run_contract_with_runtime(failure, "throw 'ISOLATED_DIAGNOSTIC_FAILURE_SENTINEL'",
            runtime_executable, environment=child, controller_script=source / "scripts/xauusd_control_center.ps1",
            runtime_root=owned / "profile/XAUUSD-Forecaster-runtime", repository_root=owned / "repository")
    archive = Path(str(failure_result.value).rsplit("diagnostic: ", 1)[1])
    assert archive.parent == ROOT / "output/windows-runtime" / diagnostic_shard
    assert re.fullmatch(r"controller-load-[0-9a-f]{32}", archive.name)
    assert not archive.is_symlink() and not archive.is_junction()
    try:
        for name in ("controller-load-diagnostic.ps1", "controller-load-source-map.json",
                     "controller-load-timestamp.patch", "contract-phases.txt"):
            assert (archive / name).read_bytes() == (failure / name).read_bytes()
        captured = json.loads((archive / "process-output.json").read_text(encoding="utf-8"))
        assert captured["exit_code"] != 0 and "ISOLATED_DIAGNOSTIC_FAILURE_SENTINEL" in captured["stderr"]
    finally:
        shutil.rmtree(archive)
    child["XAUUSD_CONTROL_LOAD_DIAGNOSTIC_SHARD"] = str(tmp_path / "outside-output")
    with pytest.raises(AssertionError, match="CONTROLLER_DIAGNOSTIC_SHARD_UNDECLARED"):
        _run_contract_with_runtime(failure, "throw 'ISOLATED_DIAGNOSTIC_FAILURE_SENTINEL'",
            runtime_executable, environment=child, controller_script=source / "scripts/xauusd_control_center.ps1",
            runtime_root=owned / "profile/XAUUSD-Forecaster-runtime", repository_root=owned / "repository")
    assert not (tmp_path / "outside-output").exists()
    diagnostic_root = tmp_path / "diagnostic-source"
    diagnostic_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = diagnostic_root / "output"
    created = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        f"New-Item -ItemType Junction -Path '{link}' -Target '{outside}' | Out-Null"],
        capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    assert created.returncode == 0 and link.is_junction(), created.stderr
    try:
        child["XAUUSD_CONTROL_LOAD_DIAGNOSTIC_SHARD"] = diagnostic_shard
        with monkeypatch.context() as scope:
            # A declared independent test root, never the repository's real
            # artifact directory. The actual write consumer still executes.
            scope.setattr(sys.modules[__name__], "ROOT", diagnostic_root)
            with pytest.raises(AssertionError, match="CONTROLLER_DIAGNOSTIC_OUTPUT_REDIRECTED"):
                _run_contract_with_runtime(failure, "throw 'ISOLATED_DIAGNOSTIC_FAILURE_SENTINEL'",
                    runtime_executable, environment=child, controller_script=source / "scripts/xauusd_control_center.ps1",
                    runtime_root=owned / "profile/XAUUSD-Forecaster-runtime", repository_root=owned / "repository")
        assert list(outside.iterdir()) == []
    finally:
        link.rmdir()


def _isolated_windows_environment():
    # A Python parent otherwise forwards PowerShell 7 module paths into 5.1.
    # No production credentials or endpoint environment enters the fixture tree.
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "USERPROFILE", "USERNAME",
        "USERDOMAIN", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "PROGRAMDATA", "TEMP", "TMP", "PATHEXT", "PATH", "XAUUSD_CONTROL_LOAD_TIMING",
        "XAUUSD_CONTROL_LOAD_DIAGNOSTIC_SHARD",
    }}
    environment["PSModulePath"] = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/Modules")
    return environment


def _exited_windows_child_identity():
    """Capture the kernel creation identity before asking our child to exit."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=_isolated_windows_environment(), creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        get_times = ctypes.WinDLL("kernel32", use_last_error=True).GetProcessTimes
        get_times.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        get_times.restype = wintypes.BOOL
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not get_times(int(child._handle), *(ctypes.byref(value) for value in (
            created, exited, kernel, user,
        ))):
            raise ctypes.WinError(ctypes.get_last_error())
        assert child.poll() is None, "identity fixture exited before observation"
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        assert ticks > 0, "Windows creation time unavailable"
        seconds, fraction = divmod(ticks, 10_000_000)
        instant = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)
        identity = {"pid": child.pid, "token": f"{instant:%Y-%m-%dT%H:%M:%S}.{fraction:07d}Z"}
        child.stdin.close()
        assert child.wait(timeout=5) == 0, "identity fixture did not exit normally"
        return identity
    finally:
        if not child.stdin.closed:
            child.stdin.close()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_news_incident_evidence_and_live_resource_admission(tmp_path, runtime_executable):
    body = r'''
    # This is a read/admission contract. Fixture bytes need no repeated durable
    # writer flush; atomic persistence has its own real boundary tests.
    $null=New-Item -ItemType Directory -Path $runtimeForwardRoot -Force;
    function Write-IncidentFixtureJson {
        param($Path,$Value,$Depth=8)
        [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth $Depth), [Text.UTF8Encoding]::new($false))
    };
    $broken='ffe1de29c0891cc3a3cf3d602f3d3ee657faa9b8'; $target='b'*40;
    $evidence=[pscustomobject]@{
        incident='COLLECTOR_CLOCK_EVENT_ATOMICITY';broken_revision=$broken;target_revision=$target;
        failure=[pscustomobject]@{resource='news_evidence';route='/api/news-evidence';
            stage='LOCAL_GET_BEFORE_REMOTE_PREPARE';root_cause='NEWS_RECEIPT_ALIAS_CORRELATED_SCAN';
            evidence_sha256='86d7b591c06a295fa3cb4085bb47e6b42c40274878735602ea712c12b1234447'};
        copy_rehearsal=[pscustomobject]@{target_revision=$target;state='API_SYNC_COPY_PASSED';
            baseline_sha256='57add242f930671ff800733ef70290bf9186b8230d0134847285300dc7e3171c';
            baseline_input_identity=$baselineIdentity;
            sqlite_input_identity=$workIdentity;
            input_database_copy=@{sha256='c'*64};sqlite_input_unchanged=$true;baseline_input_unchanged=$true;
            old_query_reproduced='NOT_RUN';
            historical_failure_evidence_sha256='86d7b591c06a295fa3cb4085bb47e6b42c40274878735602ea712c12b1234447';
            semantic_equality_verified=$true;ack_verified=$true;
            records=1919;max_local_get_seconds=5.04;max_post_bytes=25972};
        obligation='EXACT_TARGET_NEWS_READ_AND_REMOTE_ACK_BEFORE_COMMIT'
    };
    $report=$evidence.copy_rehearsal;
    $report | Add-Member -NotePropertyName source_revision -NotePropertyValue $target;
    $report | Add-Member -NotePropertyName source_dirty -NotePropertyValue $false;
    $report | Add-Member -NotePropertyName execution_boundary -NotePropertyValue 'REAL_API_CONTINUOUS_SYNC_ISOLATED_ACK';
    $reportJson=$report | ConvertTo-Json -Depth 8;
    $reportPath=Join-Path $runtimeForwardRoot 'copy-rehearsal.json';
    Write-IncidentFixtureJson -Path $reportPath -Value $report -Depth 8;
    $evidence.copy_rehearsal=[pscustomobject]@{path=$reportPath;sha256=(Get-FileHash $reportPath).Hash.ToLowerInvariant()};
    Assert-CollectorNewsRecoveryEvidence $evidence $broken $target;
    $original=$evidence | ConvertTo-Json -Depth 8;
    foreach($case in @('revision','resource','stage','cause-hash','history','hash','wal-change','wal-missing','wal-malformed','input-digest','input-binding','ack','ack-type','timeout','nan','size','dirty','source','boundary','tampered','missing')) {
        Write-ContractPhase "evidence:$case";
        $bad=$original | ConvertFrom-Json;
        $bad.copy_rehearsal=$reportJson | ConvertFrom-Json;
        switch($case) {
            revision {$bad.target_revision='d'*40}
            resource {$bad.failure.resource='market_history'}
            stage {$bad.failure.stage='REMOTE_POST'}
            cause-hash {$bad.failure.evidence_sha256='c'*64}
            history {$bad.copy_rehearsal.historical_failure_evidence_sha256='c'*64}
            hash {$bad.copy_rehearsal.baseline_sha256='e'*64}
            wal-change {$bad.copy_rehearsal.sqlite_input_unchanged=$false}
            wal-missing {$bad.copy_rehearsal.sqlite_input_identity.files.PSObject.Properties.Remove('wal')}
            wal-malformed {$bad.copy_rehearsal.sqlite_input_identity.files.wal.exists='False'}
            input-digest {$bad.copy_rehearsal.sqlite_input_identity.digest='0'*64}
            input-binding {$bad.copy_rehearsal.sqlite_input_identity.files.main.sha256='e'*64}
            ack {$bad.copy_rehearsal.ack_verified=$false}
            ack-type {$bad.copy_rehearsal.ack_verified='True'}
            timeout {$bad.copy_rehearsal.max_local_get_seconds=20}
            nan {$bad.copy_rehearsal.max_local_get_seconds=[double]::NaN}
            size {$bad.copy_rehearsal.max_post_bytes=80001}
            dirty {$bad.copy_rehearsal.source_dirty=$true}
            source {$bad.copy_rehearsal.source_revision='c'*40}
            boundary {$bad.copy_rehearsal.execution_boundary='DIRECT_HELPER_LOOP'}
        };
        Write-IncidentFixtureJson -Path $reportPath -Value $bad.copy_rehearsal -Depth 8;
        $bad.copy_rehearsal=[pscustomobject]@{path=$reportPath;sha256=(Get-FileHash $reportPath).Hash.ToLowerInvariant()};
        if($case -eq 'tampered'){$bad.copy_rehearsal.sha256='0'*64};
        if($case -eq 'missing'){$bad.copy_rehearsal.path=Join-Path $runtimeForwardRoot 'missing.json'};
        try {Assert-CollectorNewsRecoveryEvidence $bad $broken $target;throw 'unsafe acceptance'}
        catch {if($_.Exception.Message -cne 'COLLECTOR_NEWS_RECOVERY_EVIDENCE_INVALID'){throw}}
    };
    Write-IncidentFixtureJson -Path $reportPath -Value ($reportJson | ConvertFrom-Json) -Depth 8;
    $now=[DateTimeOffset]::UtcNow.ToString('o');
    $status=@{status='DEGRADED';last_success=$now;last_error=$null;
        degraded_resources=@(@{target='cloudflare';resource='news_evidence';error_type='TimeoutError';error_code='TRANSPORT_UNAVAILABLE';error='timed out'});
        resource_observations=@(@{target='cloudflare';resource='heartbeat';status='OK';completed_at=$now},
            @{target='cloudflare';resource='news_evidence';status='ERROR';completed_at=$now})};
    $path=Join-Path $runtimeForwardRoot 'dashboard-sync-status.json';
    $statusJson=$status | ConvertTo-Json -Depth 8;
    foreach($case in @('valid','stale','missing-heartbeat','other-resource','remote-invariant','second-error')) {
        Write-ContractPhase "observation:$case";
        $current=$statusJson | ConvertFrom-Json;
        switch($case) {
            stale {$current.last_success=[DateTimeOffset]::UtcNow.AddMinutes(-3).ToString('o')}
            missing-heartbeat {$current.resource_observations=@($current.resource_observations[1])}
            other-resource {$current.degraded_resources[0].resource='audit'}
            remote-invariant {$current.degraded_resources[0].error_code='REMOTE_STATE_INVARIANT_VIOLATION'}
            second-error {$current.degraded_resources+=@($current.degraded_resources[0])}
        };
        Write-IncidentFixtureJson -Path $path -Value $current -Depth 8;
        $before=(Get-FileHash -LiteralPath $path).Hash;
        try {$result=Get-CollectorNewsDegradedObservation $evidence $broken $target;
            if($case -ne 'valid' -or $result.state -cne 'DEGRADED_RECOVERY_BASELINE'){throw 'unsafe acceptance'}}
        catch {if($case -eq 'valid' -or $_.Exception.Message -cne 'COLLECTOR_NEWS_RECOVERY_OBSERVATION_CHANGED'){throw}};
        if((Get-FileHash -LiteralPath $path).Hash -cne $before){throw 'status was rewritten'}
    };
    Write-Output 'incident evidence scoped; live observation checked; degradation retained'
    '''
    def input_identity(main_hash):
        value = {"schema": "sqlite-main-wal-input-v1", "files": {
            "main": {"exists": True, "size": 4096, "mtime_ns": 1788587930631337700, "sha256": main_hash},
            "wal": {"exists": False, "size": 0, "mtime_ns": 0, "sha256": None},
        }, "logical": {"page_size": 4096, "page_count": 1, "schema_version": 1,
                       "user_version": 0, "journal_mode": "wal"}}
        value["digest"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return value
    baseline = input_identity("57add242f930671ff800733ef70290bf9186b8230d0134847285300dc7e3171c")
    work = input_identity("c" * 64)
    body = ("$baselineIdentity='" + json.dumps(baseline) + "'|ConvertFrom-Json;"
            "$workIdentity='" + json.dumps(work) + "'|ConvertFrom-Json;" + body)
    assert _run_contract_with_runtime(
        tmp_path, body, runtime_executable, environment=_isolated_windows_environment(),
    ) == (
        "incident evidence scoped; live observation checked; degradation retained"
    )


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_incident_termination_preserves_explicit_collector_absence(tmp_path, runtime_executable):
    body = r'''
    $services=@([pscustomobject]@{Key='collector'});
    $script:held=$true; $script:present=$false;
    function Get-CollectorClockRecoveryContext { [pscustomobject]@{broken_revision=('a'*40);target_revision=('b'*40)} };
    function Test-CollectorClockRecoveryHold { return $script:held };
    function Get-ForecasterProcessSnapshot { param([switch]$RequireCompleteInventory);
        if(-not $RequireCompleteInventory){throw 'incomplete inventory'};
        if($script:present){[pscustomobject]@{ProcessId=123}}
    };
    function Get-ForecasterProcesses { @() };
    function Test-ForecasterServiceProcess { return $true };
    $before=@(Get-WatchdogBusinessOwnerBaseline);
    if($before.Count -ne 1 -or -not $before[0].incident_absence){throw 'absence not captured'};
    Assert-WatchdogBusinessOwnerBaselineUnchanged -Baseline $before;
    $script:present=$true;
    try{Assert-WatchdogBusinessOwnerBaselineUnchanged -Baseline $before;throw 'WRONG'}catch{
        if($_.Exception.Message -cne 'WATCHDOG_TERMINATION_CHANGED_BUSINESS_RUNTIME'){throw}
    };
    $script:present=$false; $script:held=$false;
    try{$null=Get-WatchdogBusinessOwnerBaseline;throw 'WRONG'}catch{
        if($_.Exception.Message -cne 'WATCHDOG_TERMINATION_BUSINESS_OWNER_INVALID:collector'){throw}
    };
    try{Assert-WatchdogBusinessOwnerBaselineUnchanged -Baseline $before;throw 'WRONG'}catch{
        if($_.Exception.Message -cne 'WATCHDOG_TERMINATION_CHANGED_BUSINESS_RUNTIME'){throw}
    };
    $script:held=$true;
    function Get-ForecasterProcessSnapshot { param([switch]$RequireCompleteInventory); throw 'INVENTORY_UNKNOWN' };
    try{$null=Get-WatchdogBusinessOwnerBaseline;throw 'WRONG'}catch{
        if($_.Exception.Message -cne 'INVENTORY_UNKNOWN'){throw}
    };
    'incident absence preserved; normal/changed/unknown rejected'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == (
        "incident absence preserved; normal/changed/unknown rejected"
    )


def _write_bundle(
    root: Path,
    revision: str,
    label: str,
    *,
    dependency_closed: bool = False,
    schema_version: int | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name in CONTROL_FILES:
        payload = (
            json.dumps(
                {
                    "schema_version": 1,
                    "entrypoints": [
                        "xauusd_control_center.ps1",
                        "xauusd_watchdog_guard.ps1",
                    ],
                    "files": list(CONTROL_FILES),
                }
            ).encode()
            if name == "runtime-control-files.json"
            else f"{label}|{name}\n".encode()
        )
        (root / name).write_bytes(payload)
        hashes[name] = hashlib.sha256(payload).hexdigest()
    schema_version = schema_version or (
        BUNDLE_SCHEMA_VERSION if dependency_closed else 1
    )
    file_digest = (
        _canonical_bundle_digest(revision, hashes)
        if schema_version == BUNDLE_SCHEMA_VERSION
        else _legacy_v2_bundle_digest(hashes)
    )
    manifest = {
        "schema_version": schema_version,
        "source_revision": revision,
        "exact_revision": True,
        "created_at": "2026-08-23T00:00:00+00:00",
        "dependency_closed": dependency_closed,
        "source_manifest_sha256": hashes["runtime-control-files.json"],
        "bundle_digest": file_digest,
        "files": hashes,
    }
    if schema_version == BUNDLE_SCHEMA_VERSION:
        manifest["bundle_digest_algorithm"] = BUNDLE_DIGEST_ALGORITHM
    (root / "runtime-control-bundle.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def _make_detached_source(
    root: Path,
    *,
    manifest_files: tuple[str, ...] = CONTROL_FILES,
    payloads: dict[str, str] | None = None,
) -> str:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    payloads = payloads or {}
    source_files = tuple(dict.fromkeys((*manifest_files, *payloads)))
    for name in source_files:
        payload = (
            json.dumps(
                {
                    "schema_version": 1,
                    "entrypoints": [
                        "xauusd_control_center.ps1",
                        "xauusd_watchdog_guard.ps1",
                    ],
                    "files": list(manifest_files),
                }
            )
            if name == "runtime-control-files.json"
            else payloads.get(name, f"committed|{name}\n")
        )
        (scripts / name).write_text(payload, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Contract Test"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "contract-test@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", "scripts"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "immutable bundle"], cwd=root, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", "--detach", "-q", revision], cwd=root, check=True)
    return revision


def _make_real_control_source(root: Path, *, boundary: str = "", loopback_ports=(), sealed_configuration=False) -> str:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name in CONTROL_FILES:
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    if boundary:
        pattern = re.compile(r'\[Environment\]::GetEnvironmentVariable\(\s*("[^"]+"|\$Name)\s*,\s*"User"\s*\)')
        configuration = root.parent / "fixture-user-environment.json"
        values = {name: "" for name in (
            "XAUUSD_DASHBOARD_URL", "GEMINI_API_KEY", "GEMINI_API_KEYS", "SITES_BYPASS_TOKEN",
            "CLOUDFLARE_INGEST_URL", "CLOUDFLARE_INGEST_TOKEN", "DASHBOARD_OPERATOR_BRIDGE_TOKEN",
            "AURUM_LIVE_BROADCAST_PUBLISHER_ENABLED", "LIVE_BROADCAST_PUBLISH_TOKEN",
            "BLS_API_KEY", "BEA_API_KEY", "FRED_API_KEY", "EIA_API_KEY",
        )}
        values["GEMINI_API_KEY"] = "synthetic-configuration-sentinel"
        fixture_id = root.parent.name.removeprefix("xauusd-rehearsal-") if sealed_configuration else uuid.uuid4().hex
        document = {"schema_version": 1, "fixture_id": fixture_id,
                    "values": values, "loopback_ports": list(loopback_ports)}
        if sealed_configuration:
            document.update(mode="ISOLATED_REHEARSAL", owned_root=str(root.parent),
                source_root=str(root),
                profile_root=str(root.parent / "profile"),
                runtime_root=str(root.parent / "profile/XAUUSD-Forecaster-runtime"),
                repository_root=str(root.parent / "repository"),
                task_namespace=f"\\XAUUSD-Contract-{fixture_id}\\",
                provider_endpoint=f"http://127.0.0.1:{loopback_ports[0]}")
        configuration.write_text(json.dumps(document), encoding="utf-8")
        attestation_root = root.parent / "environment-attestations"
        attestation_root.mkdir(exist_ok=True)
        prelude = (ROOT / "tests/fixtures/control_plane_user_environment.ps1").read_text(encoding="utf-8")
        for marker, value in {"__CONFIG_PATH__": str(configuration),
                              "__CONFIG_DIGEST__": hashlib.sha256(configuration.read_bytes()).hexdigest(),
                              "__FIXTURE_ID__": fixture_id,
                              "__ATTESTATION_ROOT__": str(attestation_root)}.items():
            prelude = prelude.replace(marker, value.replace("'", "''"))
        if sealed_configuration:
            prelude = ""  # No second credential/configuration reader before the real owner.
        replaced = 0
        for name in CONTROL_FILES:
            if not name.endswith(".ps1"):
                continue
            if sealed_configuration:
                continue  # Exercise the committed shared configuration reader.
            path = scripts / name
            text = path.read_text(encoding="utf-8-sig")
            text, count = pattern.subn(r'(Get-FixtureUserEnvironmentValue -Name \1)', text)
            replaced += count
            if re.search(r'GetEnvironmentVariable\([^)]*,\s*["\'](?:User|Machine)["\']', text):
                raise AssertionError(f"uncontained persistent environment read in {name}")
            path.write_text(text, encoding="utf-8")
        assert replaced == (0 if sealed_configuration else 1), "only the shared configuration owner may read persistent user values"
        shutil.copy2(ROOT / "scripts/windows-service-launch-contract.json", scripts / "windows-service-launch-contract.json")
        entrypoint = scripts / "xauusd_control_center.ps1"
        source = entrypoint.read_text(encoding="utf-8")
        if sealed_configuration:
            marker = "$script:nativeProcessOwnershipReceiptPath = $NativeProcessReceiptPath"
            assert source.count(marker) == 1
            attestation = (
                "if (-not $isolatedConfiguration) { throw 'ISOLATED_CONFIGURATION_REQUIRED' }\n"
                f"[IO.File]::WriteAllText(('{attestation_root}' + '\\' + $PID + '.json'), "
                "(@{pid=$PID;action=$Action;fixture_id=$isolatedConfiguration.fixture_id;"
                "configuration_sha256=$env:XAUUSD_ISOLATED_CONFIGURATION_SHA256} | ConvertTo-Json -Compress),"
                "[Text.UTF8Encoding]::new($false))\n"
            )
            source = source.replace(marker, attestation + marker, 1)
        assert source.count("switch ($Action) {") == 1
        diagnostic, boundary = boundary.split("$null = Get-Command Get-FileHash -ErrorAction Stop", 1)
        source = source.replace('$ErrorActionPreference = "Stop"',
                                '$ErrorActionPreference = "Stop"\n' + diagnostic + "\n" + prelude, 1)
        boundary = "$null = Get-Command Get-FileHash -ErrorAction Stop" + boundary
        entrypoint.write_text(source.replace("switch ($Action) {", boundary + "\nswitch ($Action) {"), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Contract Test"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "contract-test@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", "scripts"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "real control bundle"], cwd=root, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", "--detach", "-q", revision], cwd=root, check=True)
    return revision


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("case", ["configured", "missing", "tampered", "unknown"])
def test_generated_bundle_user_environment_is_explicit(tmp_path, runtime_executable, case):
    boundary = (ROOT / "tests/fixtures/control_plane_staged_boundary.ps1").read_text(encoding="utf-8")
    boundary = boundary.replace("__FIXTURE_ROOT__", str(tmp_path)).replace("__FIXTURE_ID__", uuid.uuid4().hex)
    source = tmp_path / "source"
    _make_real_control_source(source, boundary=boundary)
    (tmp_path / "business.json").write_text("{}", encoding="utf-8")
    environment = _isolated_windows_environment()
    environment["XAUUSD_FIXTURE_CONFIGURATION"] = str(tmp_path / "fixture-user-environment.json")
    environment["GEMINI_API_KEY"] = "synthetic-process-decoy"
    if case == "missing":
        environment.pop("XAUUSD_FIXTURE_CONFIGURATION")
    elif case == "tampered":
        with (tmp_path / "fixture-user-environment.json").open("ab") as stream:
            stream.write(b" ")
    body = "Get-FixtureUserEnvironmentValue -Name 'GEMINI_API_KEY'"
    if case == "unknown":
        body = "Get-FixtureUserEnvironmentValue -Name 'UNDECLARED_SENTINEL'"
    if case == "configured":
        assert _run_contract_with_runtime(tmp_path, body, runtime_executable,
            environment=environment, controller_script=source / "scripts/xauusd_control_center.ps1") == "synthetic-configuration-sentinel"
    else:
        reason = {"missing": "FIXTURE_CONFIGURATION_REQUIRED",
                  "tampered": "FIXTURE_CONFIGURATION_IDENTITY_MISMATCH",
                  "unknown": "FIXTURE_ENVIRONMENT_KEY_UNDECLARED"}[case]
        with pytest.raises(AssertionError, match=reason):
            _run_contract_with_runtime(tmp_path, body, runtime_executable,
                environment=environment, controller_script=source / "scripts/xauusd_control_center.ps1")


def _make_isolated_business_source(root: Path, configuration: Path) -> None:
    """Generate a declared test copy; never modify the actual business checkout.

    Only the persistent credential owner is adapted. Entrypoints retain their
    actual CLI, imports and main routines. Unknown native children fail closed;
    this helper alone does not authorize a complete lifecycle rehearsal.
    """
    shutil.copytree(ROOT / "xauusd_forecaster", root / "xauusd_forecaster",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    bootstrap = (ROOT / "tests/fixtures/business_environment.py").read_text(encoding="utf-8")
    for marker, value in {
        "__CONFIG_PATH__": configuration.as_posix(),
        "__CONFIG_DIGEST__": hashlib.sha256(configuration.read_bytes()).hexdigest(),
        "__FIXTURE_ROOT__": configuration.parent.as_posix(),
        "__GIT_PATH__": Path(shutil.which("git")).as_posix(),
    }.items():
        bootstrap = bootstrap.replace(marker, value)
    (root / "fixture_business_environment.py").write_text(bootstrap, encoding="utf-8")
    provenance = root / "xauusd_forecaster/dashboard/deployment_provenance.py"
    provenance_source = provenance.read_text(encoding="utf-8")
    assert provenance_source.count('("git", *args)') == 1
    provenance.write_text(provenance_source.replace('("git", *args)',
        f'({Path(shutil.which("git")).as_posix()!r}, *args)'), encoding="utf-8")
    entries = ("run_forward_collector.py", "run_dashboard_api.py", "run_dashboard_sync.py",
               "run_news_annotator.py", "run_live_broadcast_publisher.py")
    mapping = {"dashboard/deployment_provenance.py": {
        "source_sha256": hashlib.sha256(provenance_source.encode()).hexdigest(),
        "boundary": "exact trusted Git executable; arguments unchanged",
    }}
    for name in entries:
        original = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        marker = "sys.path.insert(0, str(MODULE_ROOT))"
        assert original.count(marker) == 1
        altered = original.replace(marker, marker + "\nimport fixture_business_environment  # generated isolation boundary", 1)
        (scripts / name).write_text(altered, encoding="utf-8")
        mapping[name] = {"source_sha256": hashlib.sha256(original.encode()).hexdigest(),
                         "fixture_sha256": hashlib.sha256(altered.encode()).hexdigest()}
    owner = root / "xauusd_forecaster/news_scheduler.py"
    original = owner.read_text(encoding="utf-8")
    start = original.index("def _runtime_environment_value(name: str) -> str:")
    end = original.index("\ndef configured_api_credentials(", start)
    replacement = ('def _runtime_environment_value(name: str) -> str:\n'
                   '    from fixture_business_environment import environment_value\n'
                   '    return environment_value(name)\n\n')
    owner.write_text(original[:start] + replacement + original[end:], encoding="utf-8")
    mapping["news_scheduler.py"] = {"source_sha256": hashlib.sha256(original.encode()).hexdigest(),
                                   "boundary": "persistent environment owner only"}
    (root.parent / "business-source-map.json").write_text(json.dumps(mapping), encoding="utf-8")




def test_control_center_real_api_start_inherits_isolated_configuration(tmp_path):
    """Actual controller service launcher, inherited config, API and cleanup."""
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    source = tmp_path / "source"
    state = tmp_path / "runtime/.local/forward"
    arguments = ["--state-root", str(state), "--host", "127.0.0.1", "--port", str(port)]
    boundary = (ROOT / "tests/fixtures/control_plane_staged_boundary.ps1").read_text(encoding="utf-8")
    boundary = boundary.replace("__FIXTURE_ROOT__", str(tmp_path)).replace("__FIXTURE_ID__", uuid.uuid4().hex)
    marker = "function Start-All { throw 'STAGED_UNEXPECTED_BUSINESS_START' }"
    assert boundary.count(marker) == 1
    boundary = boundary.replace(marker,
        "$script:fixtureRealBusinessStart = ${function:Start-ForecasterService}\n" + marker)
    api_boundary = (ROOT / "tests/fixtures/control_plane_api_start_boundary.ps1").read_text(encoding="utf-8")
    for marker, value in {"__SOURCE_ROOT__": str(source), "__PYTHON_EXE__": sys.executable,
                          "__API_ARGUMENTS__": "|".join(arguments)}.items():
        api_boundary = api_boundary.replace(marker, value.replace("'", "''"))
    _make_real_control_source(source, boundary=boundary + "\n" + api_boundary, loopback_ports=[port])
    configuration = tmp_path / "fixture-user-environment.json"
    _make_isolated_business_source(source, configuration)
    runtime_owner = source / "xauusd_forecaster/runtime_paths.py"
    original = runtime_owner.read_text(encoding="utf-8")
    marker = 'Path.home() / "XAUUSD-Forecaster-runtime"'
    assert original.count(marker) == 2
    runtime_owner.write_text(original.replace(marker, f'Path({str(tmp_path / "runtime")!r})'), encoding="utf-8")
    mapping_path = tmp_path / "business-source-map.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    mapping["runtime_paths.py"] = {"source_sha256": hashlib.sha256(original.encode()).hexdigest(),
                                  "boundary": "explicit owned fixture runtime locator"}
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    state.mkdir(parents=True)
    (tmp_path / "business.json").write_text("{}", encoding="utf-8")
    environment = _isolated_windows_environment()
    environment["XAUUSD_FIXTURE_CONFIGURATION"] = str(configuration)
    ps_args = ",".join("'" + argument.replace("'", "''") + "'" for argument in arguments)
    body = f'''
    $service = [pscustomobject]@{{Key='api';Kind='Python';CodeRoot='{source}';
        ScriptPath='{source}\\scripts\\run_dashboard_api.py';Arguments=@({ps_args})}}
    try {{
        Start-ForecasterService -Service $service -SkipExistingCheck
        $until = [DateTimeOffset]::UtcNow.AddSeconds(10)
        $observed = $null
        while ([DateTimeOffset]::UtcNow -lt $until -and -not $script:fixtureAPIProcess.HasExited) {{
            $request = [Net.WebRequest]::Create('http://127.0.0.1:{port}/api/health')
            $request.Timeout = 1000
            try {{ $response = $request.GetResponse() }}
            catch [Net.WebException] {{ $response = $_.Exception.Response }}
            if ($response) {{
                try {{
                    $reader = [IO.StreamReader]::new($response.GetResponseStream())
                    $observed = $reader.ReadToEnd() | ConvertFrom-Json
                    if ([int]$response.StatusCode -ne 503) {{ throw 'FIXTURE_FALSE_HEALTHY' }}
                }} finally {{ $response.Close() }}
                break
            }}
            Start-Sleep -Milliseconds 100
        }}
        if (-not $observed -or $observed.readiness_scope -ne 'PROCESS_AND_CRITICAL_STATUS') {{
            throw 'FIXTURE_API_HEALTH_NOT_OBSERVED'
        }}
        'CONTROL_CENTER_REAL_API_DEGRADED_HEALTH_OBSERVED'
    }} finally {{
        if ($script:fixtureAPIProcess) {{
            $termination = Stop-NativeProcessTree -Process $script:fixtureAPIProcess
            if ($termination.state -notin @('TERMINATED','ALREADY_EXITED')) {{
                throw 'FIXTURE_API_TREE_TERMINATION_UNRESOLVED'
            }}
        }}
    }}
    '''
    assert "CONTROL_CENTER_REAL_API_DEGRADED_HEALTH_OBSERVED" in _run_contract_with_runtime(
        tmp_path, body, "powershell.exe", environment=environment,
        controller_script=source / "scripts/xauusd_control_center.ps1")


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_business_entrypoint_configuration_is_inherited_and_fail_closed(tmp_path, runtime_executable):
    """Real PowerShell -> Python CLI startup, without invoking a business loop."""
    configuration = tmp_path / "fixture-user-environment.json"
    configuration.write_text(json.dumps({"schema_version": 1, "values": {
        "GEMINI_API_KEY": "synthetic-business-sentinel", "GEMINI_API_KEYS": "",
        "GEMINI_API_ACCOUNTS": "", "CLOUDFLARE_INGEST_URL": "",
    }}), encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    _make_isolated_business_source(source, configuration)
    environment = _isolated_windows_environment()
    environment["XAUUSD_FIXTURE_CONFIGURATION"] = str(configuration)
    environment["GEMINI_API_KEY"] = "synthetic-process-decoy"
    probe = tmp_path / "probe.py"
    probe.write_text(textwrap.dedent(f'''\
        import sys, runpy, socket, subprocess, sqlite3
        sys.path.insert(0, {str(source)!r})
        import fixture_business_environment
        from xauusd_forecaster.news_scheduler import _runtime_environment_value
        assert _runtime_environment_value('GEMINI_API_KEY') == 'synthetic-business-sentinel'
        import winreg
        for action, reason in [
            (lambda: winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment'), 'FIXTURE_PERSISTENT_CONFIGURATION_DENIED'),
            (lambda: socket.create_connection(('127.0.0.1', 8765), timeout=0.1), 'FIXTURE_NETWORK_TARGET_DENIED'),
            (lambda: socket.getaddrinfo('example.invalid', 443), 'FIXTURE_NETWORK_TARGET_DENIED'),
            (lambda: sqlite3.connect({str(tmp_path.parent / ('outside-' + uuid.uuid4().hex + '.sqlite3'))!r}), 'FIXTURE_DATABASE_TARGET_DENIED'),
            (lambda: sqlite3.connect({(tmp_path.parent / ('outside-' + uuid.uuid4().hex + '.sqlite3')).as_uri()!r} + '?mode=ro', uri=True), 'FIXTURE_DATABASE_TARGET_DENIED'),
            (lambda: subprocess.Popen([sys.executable, '-c', 'pass']), 'FIXTURE_UNDECLARED_BUSINESS_CHILD')]:
            try: action()
            except RuntimeError as error: assert str(error) == reason
            else: raise AssertionError('boundary allowed forbidden access')
        for entry in ('run_forward_collector.py', 'run_dashboard_api.py', 'run_dashboard_sync.py',
                      'run_news_annotator.py', 'run_live_broadcast_publisher.py'):
            sys.argv = [entry, '--help']
            try: runpy.run_path(str(__import__('pathlib').Path({str(source / 'scripts')!r}) / entry), run_name='__main__')
            except SystemExit as result: assert result.code == 0
            else: raise AssertionError('real business CLI did not return:' + entry)
        print('BUSINESS_STARTUP_BOUNDARY_PASS')
    '''), encoding="utf-8")
    command = f"& '{sys.executable}' '{probe}'; exit $LASTEXITCODE"
    for case in ("valid", "missing", "tampered"):
        child_environment = dict(environment)
        if case == "missing":
            child_environment.pop("XAUUSD_FIXTURE_CONFIGURATION")
        elif case == "tampered":
            with configuration.open("ab") as stream:
                stream.write(b" ")
        result = subprocess.run([runtime_executable, "-NoProfile", "-NonInteractive", "-Command", command],
            env=child_environment, capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if case == "valid":
            assert result.returncode == 0, result.stderr
            assert "BUSINESS_STARTUP_BOUNDARY_PASS" in result.stdout
        else:
            assert result.returncode != 0
            assert ("FIXTURE_CONFIGURATION_REQUIRED" if case == "missing" else
                    "FIXTURE_CONFIGURATION_IDENTITY_MISMATCH") in result.stderr


def run_staged_installer_active_rehearsal(tmp_path, *, sealed_configuration=False):
    """Real lifecycle with child-inherited, fail-before-mutation environment adapters."""
    # Hosted Windows TEMP can use an 8.3 user alias while PowerShell resolves
    # the same directory to its long name. Compare one physical root identity.
    tmp_path = tmp_path.resolve(strict=True)
    if sealed_configuration:
        tmp_path = _new_sealed_fixture_root()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        fixture_port = reservation.getsockname()[1]
    boundary = (ROOT / "tests/fixtures/control_plane_staged_boundary.ps1").read_text(encoding="utf-8")
    boundary_identity = tmp_path.name.removeprefix("xauusd-rehearsal-") if sealed_configuration else uuid.uuid4().hex
    boundary = boundary.replace("__FIXTURE_ROOT__", str(tmp_path)).replace("__FIXTURE_ID__", boundary_identity)
    source = tmp_path / "source"
    revision = _make_real_control_source(source, boundary=boundary,
        loopback_ports=[fixture_port], sealed_configuration=sealed_configuration)
    runtime = tmp_path / ("profile/XAUUSD-Forecaster-runtime" if sealed_configuration else "runtime")
    broken = _make_real_control_source(runtime)
    shutil.copyfile(ROOT / "scripts/windows-service-launch-contract.json", runtime / "scripts/windows-service-launch-contract.json")
    subprocess.run(["git", "add", "scripts/windows-service-launch-contract.json"], cwd=runtime, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture business launch contract"], cwd=runtime, check=True)
    broken = subprocess.run(["git", "rev-parse", "HEAD"], cwd=runtime, check=True, capture_output=True, text=True).stdout.strip()
    repository = tmp_path / "repository"
    repository.mkdir()
    sleeper = tmp_path / "business.py"
    sleeper.write_text("import sys\nsys.stdin.buffer.read()\n", encoding="utf-8")
    sync_script = tmp_path / "staged_sync_owner.py"
    shutil.copyfile(ROOT / "tests/fixtures/staged_sync_owner.py", sync_script)
    owners = {}
    children = []
    environment = _isolated_windows_environment()
    environment["XAUUSD_FIXTURE_CONFIGURATION"] = str(tmp_path / "fixture-user-environment.json")
    if sealed_configuration:
        environment["XAUUSD_ISOLATED_CONFIGURATION"] = environment["XAUUSD_FIXTURE_CONFIGURATION"]
        environment["XAUUSD_ISOLATED_CONFIGURATION_SHA256"] = hashlib.sha256(
            (tmp_path / "fixture-user-environment.json").read_bytes()).hexdigest()
        environment["USERPROFILE"] = str(tmp_path / "profile")
        environment["HOME"] = str(tmp_path / "profile")
    environment["GEMINI_API_KEY"] = "synthetic-process-decoy"
    (tmp_path / "fixture-owned.json").write_text(json.dumps({"fixture": str(tmp_path)}), encoding="utf-8")
    certificate, key = tmp_path / "loopback.crt", tmp_path / "loopback.key"
    openssl = shutil.which("openssl") or str(Path(shutil.which("git")).parents[1] / "usr/bin/openssl.exe")
    subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=isolated-fixture",
                    "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True,
                   timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    environment["SSL_CERT_FILE"] = str(certificate)
    timeout_mode, release_request = threading.Event(), threading.Event()
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, value):
            payload = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (ConnectionError, ssl.SSLError):
                if not (timeout_mode.is_set() and self.path.startswith("/api/news-evidence?")):
                    raise
                # Only the intentionally timed-out client is allowed to close.

        def do_GET(self):
            requests.append(("GET", self.path, 0))
            if self.path == "/api/critical-status":
                self.respond({"generated_at": "2026-09-05T00:00:00+00:00", "system": {"online": True}})
            elif self.path.startswith("/api/news-evidence?"):
                if timeout_mode.is_set():
                    release_request.wait(35)
                self.respond({"snapshot_id": "a" * 64, "total": 0, "items": [], "has_more": False})
            else:
                self.send_error(404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            assert length < 80_000
            raw = self.rfile.read(length)
            payload = json.loads(raw)
            requests.append(("POST", self.path, length))
            if self.path == "/api/ingest":
                self.respond({"ok": True})
            elif self.path == "/api/news-evidence":
                assert "prepare_snapshot" in payload or "cleanup_active_snapshot" in payload
                result = ({"active": True, "next_offset": 0} if "prepare_snapshot" in payload else {
                    "cleanup": "advanced", "cleanup_pending": False,
                    "deleted_records": 0, "deleted_batches": 0, "deleted_staging": 0,
                })
                self.respond({**result, "status": "OK", "snapshot_id": "a" * 64,
                              "contract_version": "news-evidence-paged-v2",
                              "request_sha256": hashlib.sha256(raw).hexdigest()})
            else:
                self.send_error(404)

    provider = ThreadingHTTPServer(("127.0.0.1", fixture_port), Provider)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    provider.socket = context.wrap_socket(provider.socket, server_side=True)
    serving = threading.Thread(target=provider.serve_forever, daemon=True)
    serving.start()
    status_path = runtime / ".local/forward/dashboard-sync-status.json"

    def await_resource(expected):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if status_path.exists():
                try:
                    status = json.loads(status_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    time.sleep(0.1)
                    continue
                news = next((row for row in status.get("resource_observations", []) if row["resource"] == "news_evidence"), {})
                if news.get("status") == expected:
                    return status
            time.sleep(0.1)
        raise AssertionError(f"real Sync resource did not reach {expected}")

    try:
        for key in ("quote", "annotator", "api", "sync"):
            command = [sys.executable, str(sleeper), key] if key != "sync" else [
                sys.executable, str(sync_script), "--fixture-root", str(tmp_path),
                "--source-root", str(ROOT), "--provider", f"https://127.0.0.1:{provider.server_port}",
            ]
            child = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW, env=environment,
                                     stdin=subprocess.PIPE if key != "sync" else subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            children.append(child)
            owners[key] = child.pid
        (tmp_path / "business.json").write_text(json.dumps(owners), encoding="utf-8")
        healthy = await_resource("OK")
        assert healthy["status"] == "OK"
        prior = _exited_windows_child_identity()
        body = rf'''
        $null = . '{source / 'scripts/xauusd_control_center.ps1'}' -Action CodeRevision -RuntimeRoot '{runtime}' -RepositoryRoot '{repository}';
        $control=Join-Path $repositoryRoot '.local\runtime-control';
        $bundle=New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' -SourceRevision '{revision}' -StageRoot $control -RequireImmutableSource;
        $descriptor=Get-WatchdogSingletonDescriptor;
        $prior=[pscustomobject]@{{schema_version='watchdog-owner-v2';instance_id=[guid]::NewGuid().ToString('N');
            process_id={prior['pid']};process_start_token='{prior['token']}';launcher_pid={prior['pid']};launcher_start_token='{prior['token']}';
            user_sid=$descriptor.user_sid;runtime_root_hash=$descriptor.runtime_root_hash;repository_root_hash=$descriptor.repository_root_hash;
            mutex_identity_hash=$descriptor.mutex_identity_hash;installed_control_revision='{revision}';bundle_digest=$bundle.bundle_digest;
            mode='ACTIVE';acquired_at='{prior['token']}';install_transaction_id=$null}};
        $null=Write-WatchdogOwnerReceipt -Receipt $prior;
        Write-ControlCenterJsonAtomic -Path $releaseControlStatePath -Value @{{stable=@{{windows_revision='{broken}';worker_version_id='fixture-worker'}};transaction=$null}} -Depth 6;
        # Only source/provider/snapshot admission is supplied by the fixture.
        # Isolation assertions, mutex reservation, install, handoff and commit are real.
        function Get-CollectorClockRecoveryBaseline {{
            param($VerifiedSourceRoot,$TargetRevision)
            Assert-FixturePath $VerifiedSourceRoot;
            $snapshot=Get-ControlPlaneIsolationSnapshot -RequireCompleteInventory;
            [pscustomobject]@{{incident='COLLECTOR_CLOCK_EVENT_ATOMICITY';state='DEGRADED_RECOVERY_BASELINE';
                broken_revision='{broken}';target_revision=$TargetRevision;user_sid=$descriptor.user_sid;
                runtime_root_hash=$descriptor.runtime_root_hash;repository_root_hash=$descriptor.repository_root_hash;
                previous_watchdog_receipt=$prior;services=$snapshot.services;
                snapshot=[pscustomobject]@{{decision_time='2026-09-04T16:05:00.000000+00:00';snapshot_hash='b139c8a9d913c237e8e9e3ebc677a1144cd8ad2f9e0adee6b62ed8cd2a7fa5ee'}}}}
        }};
        $owner=$null;
        try {{
            $before=Get-ControlPlaneIsolationSnapshot -RequireCompleteInventory;
            $result=Invoke-ControlPlaneInstall -VerifiedSourceRoot '{source}' -TargetRevision '{revision}' -CollectorClockRecovery;
            if($result.status -cne 'COMMITTED'){{throw 'install not committed'}};
            $owner=$result.new_watchdog_identity;
            $heartbeat=Assert-CurrentWatchdogHeartbeat -Owner $owner -ExpectedRevision '{revision}';
            if($heartbeat.supervision_mode -cne 'ACTIVE'){{throw 'not active'}};
            Assert-ControlPlaneIsolationSnapshot -Before $before -After (Get-ControlPlaneIsolationSnapshot -RequireCompleteInventory);
            # An additional real launcher cannot acquire the held OS mutex.
            $second=Start-WatchdogReplacement -PassThru;
            if(-not $second.WaitForExit(10000)){{throw 'second watchdog did not exit'}};
            $same=@(Get-VerifiedWatchdogOwners -RequireCompleteInventory);
            if($same.Count -ne 1 -or $same[0].process_id -ne $owner.process_id){{throw 'singleton changed'}};
            Write-Output 'ACTIVE_COMMITTED|BUSINESS_PRESERVED|SECOND_OWNER_REJECTED';
        }} finally {{
            foreach($live in @(Get-VerifiedWatchdogOwners -RequireCompleteInventory)){{ Stop-VerifiedWatchdogOwner -Identity $live }};
            if(@(Get-VerifiedWatchdogOwners -RequireCompleteInventory).Count -ne 0){{throw 'staged owner remained'}};
        }}
        '''
        script = tmp_path / "installer.ps1"
        script.write_text(body, encoding="utf-8")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                capture_output=True, text=True, timeout=150, creationflags=subprocess.CREATE_NO_WINDOW,
                                env=environment, cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ACTIVE_COMMITTED|BUSINESS_PRESERVED|SECOND_OWNER_REJECTED" in result.stdout
        assert all(child.poll() is None for child in children)
        # Advance only the isolated resource's due marker to exercise the same
        # live owner's next legal scheduled attempt, never call its worker twice.
        timeout_mode.set()
        schedule_path = runtime / ".local/forward/dashboard-resource-schedule-state-fixture.json"
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
        schedule["resources"]["news_evidence"]["next_run_at"] = "2000-01-01T00:00:00+00:00"
        temporary = schedule_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(schedule), encoding="utf-8")
        temporary.replace(schedule_path)
        degraded = await_resource("ERROR")
        assert degraded["status"] == "DEGRADED"
        assert degraded["last_success"] != healthy["last_success"]
        assert children[-1].poll() is None
        failure = degraded["degraded_resources"]
        assert len(failure) == 1 and failure[0]["resource"] == "news_evidence"
        assert failure[0]["error_type"] == "TimeoutError"
        assert failure[0]["duration_ms"] >= 19_000
        assert len([row for row in requests if row[0] == "GET" and row[1].startswith("/api/news-evidence?")]) == 2
    finally:
        release_request.set()
        (tmp_path / "stop-sync").touch()
        # The OS owner receipt is also the emergency fixture cleanup authority.
        # Never rely on successful installer completion to contain its children.
        cleanup = rf'''
        $ErrorActionPreference='Stop';
        $fixture='{tmp_path}';
        $path='{runtime / '.local/forward/watchdog-owner-v2.json'}';
        if(Test-Path -LiteralPath $path){{
            $r=Get-Content -LiteralPath $path -Raw -Encoding UTF8|ConvertFrom-Json;
            foreach($e in @(@{{id=$r.process_id;token=$r.process_start_token}},@{{id=$r.launcher_pid;token=$r.launcher_start_token}})){{
                $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$e.id);
                if($p){{
                    if(-not $p.CommandLine.Contains($fixture+'\') -or ([DateTimeOffset]$p.CreationDate).UtcTicks -ne ([DateTimeOffset]$e.token).UtcTicks){{throw 'FIXTURE_CLEANUP_IDENTITY_MISMATCH'}};
                    Stop-Process -Id ([int]$e.id) -Force;
                }}
            }}
        }}
        '''
        cleaned = subprocess.run(["powershell.exe", "-NoProfile", "-Command", cleanup],
                                 capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        for child in children:
            if child.poll() is None:
                if child.pid == owners.get("sync"):
                    try:
                        child.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        child.terminate()
                else:
                    child.stdin.close()
            child.wait(timeout=10)
            child.stderr.close()
        provider.shutdown()
        provider.server_close()
        serving.join(timeout=3)
        assert cleaned.returncode == 0, cleaned.stderr
    return tmp_path


def _identity(pid: int, token: str) -> str:
    return (
        f"[pscustomobject]@{{process_id={pid};parent_process_id={pid + 1};"
        f"process_start_token='{token}';launcher_identity=[pscustomobject]@{{"
        f"process_id={pid + 1};process_start_token='{token}-launcher'}}}}"
    )


def _state_machine_mocks(old_revision: str, target_revision: str) -> str:
    old = _identity(100, "old-token")
    new = _identity(200, "new-token")
    return textwrap.dedent(
        f"""
        $script:timeline=@(); $script:owners=@({old});
        function Get-RuntimeControlBundleIdentityAtRoot {{ param($ControlRoot); [pscustomobject]@{{source_revision='{old_revision}';exact_revision=$true}} }};
        function Get-VerifiedControlCenterGuiOwners {{ @() }};
        function Get-ReleaseControlState {{ $null }};
        function Enter-ReleaseTransactionLock {{ $script:timeline+='lock'; return $true }};
        function Exit-ReleaseTransactionLock {{ $script:timeline+='unlock' }};
        function Get-VerifiedWatchdogOwners {{ @($script:owners) }};
        function Assert-CurrentWatchdogHeartbeat {{ param($Owner,$ExpectedRevision); [pscustomobject]@{{process_id=$Owner.process_id;control_bundle_revision=$ExpectedRevision}} }};
        function Get-ControlPlaneIsolationSnapshot {{
          $p=[pscustomobject]@{{process_id=10;process_start_token='service-token'}};
          [pscustomobject]@{{business_runtime_revision='runtime';services=[pscustomobject]@{{quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@($p);broadcast=@()}}}}
        }};
        function Assert-ControlPlaneIsolationBaseline {{ param($Snapshot,$ReleaseState); $script:timeline+='baseline' }};
        function Assert-ControlPlaneIsolationSnapshot {{ param($Before,$After,$ReleaseState); $script:timeline+='isolation' }};
        function New-VerifiedRuntimeControlBundleStage {{ param($SourceRoot,$SourceRevision,$StageRoot,[switch]$RequireImmutableSource); $script:timeline+='stage'; [pscustomobject]@{{source_revision='{target_revision}'}} }};
        function Invoke-RuntimeControlBundleStartupPreflight {{ param($StageRoot,$ExpectedRevision,$RepositoryRootForPreflight); $script:timeline+='preflight'; [pscustomobject]@{{control_bundle_revision=$ExpectedRevision}} }};
        function Suspend-ControlPlaneSupervision {{ $script:timeline+='suspend'; @{{}} }};
        function Wait-ControlPlaneGuardQuiesced {{ $script:timeline+='guard' }};
        function Restore-ControlPlaneSupervision {{ param($State); $script:timeline+='supervision' }};
        function Stop-VerifiedWatchdogOwner {{ param($Identity); $script:timeline+='stop'; $script:owners=@() }};
        function Stop-ScheduledTask {{ param($TaskName); if ($TaskName -notlike 'XAUUSD-Contract-*') {{throw 'unsafe task name'}} }};
        function Install-VerifiedRuntimeControlBundleStage {{ param($StageRoot,$ControlRoot,$BackupRoot); if($script:owners.Count-ne 0){{throw 'two owners'}}; $script:timeline+='install'; [pscustomobject]@{{source_revision='{target_revision}'}} }};
        function Start-WatchdogReplacement {{ param([switch]$PassThru,$InstallTransactionId); if($script:owners.Count-ne 0){{throw 'two owners'}}; $script:timeline+='start'; $script:owners=@({new}); [pscustomobject]@{{Id=201}} }};
        function Wait-VerifiedWatchdogHandoff {{ param($ExpectedRevision,$PreviousIdentity,$ExpectedMode,$ExpectedInstallTransactionId,$Timeout); if($script:owners.Count-ne 1){{throw 'owner count'}}; $script:timeline+="heartbeat:$ExpectedMode"; return $script:owners[0] }};
        """
    ).replace("\n", " ")


def test_repository_entrypoint_bootstraps_from_exact_origin_main_worktree() -> None:
    installer = (ROOT / "scripts" / "install_control_plane.ps1").read_text(encoding="utf-8")
    assert "fetch origin main" in installer
    assert "merge-base --is-ancestor" in installer
    assert "CONTROL_PLANE_TARGET_MUST_EQUAL_ORIGIN_MAIN" in installer
    assert "worktree add --detach" in installer
    assert "-File $controlScript -Action InstallControlPlane" in installer
    assert "-SourceRoot $temporaryRoot -SourceRevision $Revision" in installer
    assert ".local\\runtime-control" not in installer
    assert "InstallRuntime" not in installer


def test_repository_entrypoint_ignores_old_bundle_and_dirty_checkout(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    checkout = tmp_path / "checkout"
    runtime = tmp_path / "runtime"
    subprocess.run(["git", "init", "--bare", "-q", origin], check=True)
    subprocess.run(["git", "init", "-q", checkout], check=True)
    subprocess.run(["git", "config", "user.name", "Contract Test"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "config", "user.email", "contract-test@example.invalid"],
        cwd=checkout,
        check=True,
    )
    scripts = checkout / "scripts"
    scripts.mkdir()
    (scripts / "payload.txt").write_text("committed\n", encoding="utf-8")
    (scripts / "xauusd_control_center.ps1").write_text(
        textwrap.dedent(
            """
            param($Action,$RuntimeRoot,$RepositoryRoot,$SourceRoot,$SourceRevision)
            $payload=(Get-Content -LiteralPath (Join-Path $SourceRoot 'scripts\\payload.txt') -Raw).Trim()
            [pscustomobject]@{action=$Action;revision=$SourceRevision;payload=$payload;source_root=$SourceRoot} |
              ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RepositoryRoot 'bootstrap-result.json')
            """
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "scripts"], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "bootstrap target"], cwd=checkout, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=checkout, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=checkout, check=True)
    subprocess.run(["git", "push", "-qu", "origin", "main"], cwd=checkout, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (scripts / "payload.txt").write_text("dirty\n", encoding="utf-8")
    old_control = checkout / ".local" / "runtime-control"
    old_control.mkdir(parents=True)
    (old_control / "xauusd_control_center.ps1").write_text(
        "throw 'old installed controller was called'\n", encoding="utf-8"
    )
    installer = ROOT / "scripts" / "install_control_plane.ps1"
    command = (
        f". '{installer}' -TargetRevision '{revision}' -RuntimeRoot '{runtime}' "
        f"-RepositoryRoot '{checkout}'; "
        f"Invoke-ExactControlPlaneInstaller -CheckoutRoot '{checkout}' "
        f"-RuntimePath '{runtime}' -Revision '{revision}'"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads((checkout / "bootstrap-result.json").read_text(encoding="utf-8-sig"))
    assert evidence["action"] == "InstallControlPlane"
    assert evidence["revision"] == revision
    assert evidence["payload"] == "committed"
    assert not Path(evidence["source_root"]).exists()


def test_immutable_stage_requires_exact_clean_detached_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    revision = _make_detached_source(source)
    stage = tmp_path / "stage"
    result = _run_contract(
        tmp_path,
        f"$bundle=New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' "
        f"-SourceRevision '{revision}' -StageRoot '{stage}' -RequireImmutableSource; "
        '$count=@($bundle.files.PSObject.Properties).Count; '
        'Write-Output "$($bundle.source_revision),$count"',
    )
    assert result == f"{revision},{len(CONTROL_FILES)}"

    (source / "scripts" / CONTROL_FILES[0]).write_text("dirty\n", encoding="utf-8")
    rejected = _run_contract(
        tmp_path,
        f"try {{ New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' "
        f"-SourceRevision '{revision}' -StageRoot '{tmp_path / 'dirty-stage'}' "
        "-RequireImmutableSource | Out-Null; Write-Output accepted } "
        "catch { Write-Output $_.Exception.Message }",
    )
    assert rejected == "CONTROL_BUNDLE_IMMUTABLE_SOURCE_REQUIRED"


@pytest.mark.skipif(shutil.which("pwsh.exe") is None, reason="PowerShell 7 is required")
def test_legacy_v2_bundle_digest_is_reconstructed_identically_across_runtimes(
    tmp_path: Path,
) -> None:
    control = tmp_path / "legacy-control"
    revision = "a" * 40
    _write_bundle(
        control,
        revision,
        "legacy",
        dependency_closed=True,
        schema_version=2,
    )
    body = (
        f"$bundle=Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{control}' "
        "-RequireDependencyClosure; "
        'Write-Output "$($bundle.bundle_digest)|$($bundle.legacy_v2_digest_verified)"'
    )
    expected = json.loads(
        (control / "runtime-control-bundle.json").read_text(encoding="utf-8")
    )["bundle_digest"]
    assert _run_contract_with_runtime(tmp_path, body, "powershell.exe") == (
        f"{expected}|True"
    )
    assert _run_contract_with_runtime(tmp_path, body, "pwsh.exe") == (
        f"{expected}|True"
    )

    manifest_path = control / "runtime-control-bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_digest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    rejected = body.replace(
        'Write-Output "$($bundle.bundle_digest)|$($bundle.legacy_v2_digest_verified)"',
        "if($bundle){Write-Output accepted}else{Write-Output rejected}",
    )
    assert _run_contract_with_runtime(tmp_path, rejected, "powershell.exe") == "rejected"
    assert _run_contract_with_runtime(tmp_path, rejected, "pwsh.exe") == "rejected"


@pytest.mark.skipif(shutil.which("pwsh.exe") is None, reason="PowerShell 7 is required")
def test_canonical_bundle_install_is_runtime_format_and_root_independent(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    revision = _make_detached_source(source)
    stage5 = tmp_path / "stage-ps5"
    stage7 = tmp_path / "stage-ps7"

    def stage_with(runtime: str, stage: Path) -> str:
        return _run_contract_with_runtime(
            tmp_path,
            f"$bundle=New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' "
            f"-SourceRevision '{revision}' -StageRoot '{stage}' "
            "-RequireImmutableSource; Write-Output $bundle.bundle_digest",
            runtime,
        )

    digest5 = stage_with("powershell.exe", stage5)
    digest7 = stage_with("pwsh.exe", stage7)
    assert digest5 == digest7

    hashes = json.loads(
        (stage5 / "runtime-control-bundle.json").read_text(encoding="utf-8-sig")
    )["files"]
    entries = list(reversed(list(hashes.items())))
    powershell_entries = ";".join(
        f"$reversed['{name}']='{digest}'" for name, digest in entries
    )
    digest_body = (
        "$forward=@{};$reversed=@{};"
        + ";".join(f"$forward['{name}']='{digest}'" for name, digest in hashes.items())
        + ";"
        + powershell_entries
        + f";$a=Get-RuntimeControlBundleDigest -SchemaVersion 3 "
        f"-SourceRevision '{revision}' -Hashes $forward;"
        f"$b=Get-RuntimeControlBundleDigest -SchemaVersion 3 "
        f"-SourceRevision '{revision}' -Hashes $reversed;"
        'Write-Output "$a|$b"'
    )
    for runtime in ("powershell.exe", "pwsh.exe"):
        assert _run_contract_with_runtime(tmp_path, digest_body, runtime) == (
            f"{digest5}|{digest5}"
        )

    manifest_path = stage5 / "runtime-control-bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    reordered = {
        "files": dict(reversed(list(manifest["files"].items()))),
        "bundle_digest": manifest["bundle_digest"],
        "source_manifest_sha256": manifest["source_manifest_sha256"],
        "bundle_digest_algorithm": manifest["bundle_digest_algorithm"],
        "dependency_closed": manifest["dependency_closed"],
        "created_at": manifest["created_at"],
        "exact_revision": manifest["exact_revision"],
        "source_revision": manifest["source_revision"],
        "schema_version": manifest["schema_version"],
    }
    manifest_path.write_text(
        json.dumps(reordered, indent=3).replace("\n", "\r\n") + "\r\n",
        encoding="utf-8",
        newline="",
    )
    verify_body = (
        f"$bundle=Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{stage5}' "
        "-RequireDependencyClosure; Write-Output $bundle.bundle_digest"
    )
    assert _run_contract_with_runtime(tmp_path, verify_body, "powershell.exe") == digest5
    assert _run_contract_with_runtime(tmp_path, verify_body, "pwsh.exe") == digest5

    control = tmp_path / "control"
    backup = tmp_path / "backup"
    _write_bundle(control, "b" * 40, "old")
    install_body = (
        f"$bundle=Install-VerifiedRuntimeControlBundleStage -StageRoot '{stage5}' "
        f"-ControlRoot '{control}' -BackupRoot '{backup}'; "
        "Write-Output $bundle.bundle_digest"
    )
    assert _run_contract_with_runtime(tmp_path, install_body, "pwsh.exe") == digest5
    moved = tmp_path / "moved-control"
    shutil.copytree(control, moved)
    moved_body = (
        f"$bundle=Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{moved}' "
        "-RequireDependencyClosure; Write-Output $bundle.bundle_digest"
    )
    assert _run_contract_with_runtime(tmp_path, moved_body, "powershell.exe") == digest5


@pytest.mark.parametrize(
    "mutation",
    (
        "changed_file",
        "changed_hash",
        "changed_path",
        "missing_file",
        "extra_file_entry",
        "changed_revision",
        "malformed_manifest",
    ),
)
def test_canonical_bundle_identity_mutations_fail_closed(
    tmp_path: Path, mutation: str,
) -> None:
    source = tmp_path / f"source-{mutation}"
    revision = _make_detached_source(source)
    stage = tmp_path / f"stage-{mutation}"
    _run_contract(
        tmp_path,
        f"New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' "
        f"-SourceRevision '{revision}' -StageRoot '{stage}' "
        "-RequireImmutableSource | Out-Null",
    )
    manifest_path = stage / "runtime-control-bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    first = CONTROL_FILES[0]
    if mutation == "changed_file":
        (stage / first).write_text("tampered\n", encoding="utf-8")
    elif mutation == "changed_hash":
        manifest["files"][first] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "changed_path":
        manifest["files"][f"renamed-{first}"] = manifest["files"].pop(first)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "missing_file":
        (stage / first).unlink()
    elif mutation == "extra_file_entry":
        manifest["files"]["unexpected.ps1"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "changed_revision":
        manifest["source_revision"] = "f" * 40
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "malformed_manifest":
        manifest_path.write_text("{not-json", encoding="utf-8")
    result = _run_contract(
        tmp_path,
        f"$bundle=Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{stage}' "
        "-RequireDependencyClosure; if($bundle){Write-Output accepted}else{Write-Output rejected}",
    )
    assert result == "rejected"


def test_canonical_digest_binds_revision_and_relative_path(tmp_path: Path) -> None:
    first_hash = "1" * 64
    revision = "a" * 40
    body = (
        f"$base=@{{'control.ps1'='{first_hash}'}};"
        f"$renamed=@{{'renamed.ps1'='{first_hash}'}};"
        "$a=Get-RuntimeControlBundleDigest -SchemaVersion 3 "
        f"-SourceRevision '{revision}' -Hashes $base;"
        "$b=Get-RuntimeControlBundleDigest -SchemaVersion 3 "
        f"-SourceRevision '{'b' * 40}' -Hashes $base;"
        "$c=Get-RuntimeControlBundleDigest -SchemaVersion 3 "
        f"-SourceRevision '{revision}' -Hashes $renamed;"
        'Write-Output "$($a-ne$b)|$($a-ne$c)"'
    )
    assert _run_contract(tmp_path, body) == "True|True"


def test_bundle_manifest_owns_direct_and_transitive_runtime_dependencies(
    tmp_path: Path,
) -> None:
    without_worker = tuple(
        name for name in CONTROL_FILES if name != "worker_cpu_evidence.ps1"
    )
    direct = tmp_path / "direct"
    direct_revision = _make_detached_source(
        direct,
        manifest_files=without_worker,
        payloads={
            "xauusd_control_center.ps1": (
                '. (Join-Path $PSScriptRoot "worker_cpu_evidence.ps1")\n'
            ),
            "worker_cpu_evidence.ps1": "# present but undeclared\n",
        },
    )
    rejected = _run_contract(
        tmp_path,
        f"try {{ New-VerifiedRuntimeControlBundleStage -SourceRoot '{direct}' "
        f"-SourceRevision '{direct_revision}' -StageRoot '{tmp_path / 'direct-stage'}' "
        "| Out-Null; Write-Output accepted } catch { Write-Output $_.Exception.Message }",
    )
    assert rejected == (
        "CONTROL_BUNDLE_UNDECLARED_DEPENDENCY:"
        "xauusd_control_center.ps1:worker_cpu_evidence.ps1"
    )

    transitive = tmp_path / "transitive"
    transitive_revision = _make_detached_source(
        transitive,
        payloads={
            "worker_cpu_evidence.ps1": (
                '. (Join-Path $PSScriptRoot "runtime_nested.ps1")\n'
            ),
            "runtime_nested.ps1": "# present but undeclared\n",
        },
    )
    rejected = _run_contract(
        tmp_path,
        f"try {{ New-VerifiedRuntimeControlBundleStage -SourceRoot '{transitive}' "
        f"-SourceRevision '{transitive_revision}' "
        f"-StageRoot '{tmp_path / 'transitive-stage'}' | Out-Null; "
        "Write-Output accepted } catch { Write-Output $_.Exception.Message }",
    )
    assert rejected == (
        "CONTROL_BUNDLE_UNDECLARED_DEPENDENCY:"
        "worker_cpu_evidence.ps1:runtime_nested.ps1"
    )


def test_clean_staged_bundle_produces_quiesced_preflight_without_checkout_fallback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    revision = _make_real_control_source(source)
    stage = tmp_path / "stage"
    result = _run_contract(
        tmp_path,
        f"$bundle=New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' "
        f"-SourceRevision '{revision}' -StageRoot '{stage}' -RequireImmutableSource; "
        f"$receipt=Invoke-RuntimeControlBundleStartupPreflight -StageRoot '{stage}' "
        f"-ExpectedRevision '{revision}' -RepositoryRootForPreflight '{ROOT}'; "
        'Write-Output "$($receipt.supervision_mode)|$($receipt.dependency_closed)"',
    )
    assert result == "QUIESCED|True"

    (stage / "worker_cpu_evidence.ps1").unlink()
    rejected = _run_contract(
        tmp_path,
        f"try {{ Invoke-RuntimeControlBundleStartupPreflight -StageRoot '{stage}' "
        f"-ExpectedRevision '{revision}' -RepositoryRootForPreflight '{ROOT}' "
        "| Out-Null; Write-Output accepted } "
        "catch { Write-Output $_.Exception.Message }",
    )
    assert rejected == "CONTROL_BUNDLE_STARTUP_PREFLIGHT_FAILED"


def run_staged_activation_withdrawal_rehearsal(tmp_path):
    """Real launcher/bundle/mutex/heartbeat; withdraw before granting ACTIVE."""
    tmp_path = tmp_path.resolve(strict=True)
    source = tmp_path / "source"
    boundary = (ROOT / "tests/fixtures/control_plane_staged_boundary.ps1").read_text(encoding="utf-8")
    boundary = boundary.replace("__FIXTURE_ROOT__", str(tmp_path)).replace("__FIXTURE_ID__", uuid.uuid4().hex)
    (tmp_path / "business.json").write_text("{}", encoding="utf-8")
    revision = _make_real_control_source(source, boundary=boundary)
    runtime = tmp_path / "runtime"
    _make_real_control_source(runtime)
    shutil.copyfile(ROOT / "scripts/windows-service-launch-contract.json",
                    runtime / "scripts/windows-service-launch-contract.json")
    subprocess.run(["git", "add", "scripts/windows-service-launch-contract.json"], cwd=runtime, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture runtime launch authority"], cwd=runtime, check=True)
    prior = _exited_windows_child_identity()
    # Living stand-ins prove preservation, not the health of real business services.
    preserved = [subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) for _ in range(4)]
    body = rf'''
    $null = . '{source / 'scripts/xauusd_control_center.ps1'}' -Action CodeRevision -RuntimeRoot $moduleRoot -RepositoryRoot $repositoryRoot;
    $control=Join-Path $repositoryRoot '.local\runtime-control';
    $bundle=New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' -SourceRevision '{revision}' -StageRoot $control -RequireImmutableSource;
    $descriptor=Get-WatchdogSingletonDescriptor;
    $old=[pscustomobject]@{{schema_version='watchdog-owner-v2';instance_id=[guid]::NewGuid().ToString('N');
        process_id={prior['pid']};process_start_token='{prior['token']}';launcher_pid={prior['pid']};launcher_start_token='{prior['token']}';
        user_sid=$descriptor.user_sid;runtime_root_hash=$descriptor.runtime_root_hash;repository_root_hash=$descriptor.repository_root_hash;
        mutex_identity_hash=$descriptor.mutex_identity_hash;installed_control_revision='{revision}';bundle_digest=$bundle.bundle_digest;
        mode='ACTIVE';acquired_at='{prior['token']}';install_transaction_id=$null}};
    $null=Write-WatchdogOwnerReceipt -Receipt $old;
    $transaction=[guid]::NewGuid().ToString('N');
    $installer=Get-ControlPlaneProcessIdentity -ProcessId $PID -RequireCompleteInventory;
    Write-ControlPlaneInstallState @{{transaction_id=$transaction;phase='VERIFY_QUIESCED_HANDOFF';target_revision='{revision}';install_owner_identity=$installer}};
    $launcher=$null; $owner=$null;
    try {{
        $launcher=Start-WatchdogReplacement -InstallTransactionId $transaction -PassThru;
        $owner=Wait-VerifiedWatchdogHandoff -ExpectedRevision '{revision}' -PreviousIdentity $old `
            -ExpectedMode QUIESCED -ExpectedInstallTransactionId $transaction -RequireCompleteInventory;
        if ([int]$owner.process_id -eq [int]$old.process_id -and $owner.process_start_token -eq $old.process_start_token) {{throw 'stale owner reused'}};
        if ($owner.watchdog_owner_receipt.mode -cne 'QUIESCED_INSTALL') {{throw 'unsafe activation'}};
        Write-ControlPlaneInstallState @{{phase='FAILED';failure='fixture withdrawal before activation'}};
        if (-not $launcher.WaitForExit(10000)) {{throw 'launcher did not exit after withdrawal'}};
        if (Get-ControlPlaneProcessIdentity -ProcessId ([int]$owner.process_id)) {{throw 'watchdog survived withdrawal'}};
        if (Test-Path -LiteralPath $watchdogOwnerReceiptPath) {{throw 'receipt remained after exact exit'}};
        Write-Output 'real quiesced handoff and clean withdrawal passed'
    }} catch {{
        $details=[ordered]@{{failure=$_.Exception.Message;heartbeat=$null;launcher_exited=$null}};
        if(Test-Path -LiteralPath $watchdogHeartbeatPath){{
            $details.heartbeat=Get-Content -LiteralPath $watchdogHeartbeatPath -Raw -Encoding UTF8 | ConvertFrom-Json
        }};
        if($launcher){{$details.launcher_exited=$launcher.HasExited}};
        Write-ControlCenterJsonAtomic -Path (Join-Path $script:fixtureRoot 'handoff-failure.json') -Value $details;
        throw
    }} finally {{
        Write-ControlPlaneInstallState @{{phase='FAILED';failure='fixture cleanup'}};
        if ($owner -and (Get-ControlPlaneProcessIdentity -ProcessId ([int]$owner.process_id))) {{
            Stop-VerifiedWatchdogOwner -Identity $owner
        }};
        if ($launcher -and -not $launcher.HasExited) {{
            if (-not $launcher.WaitForExit(10000)) {{throw 'staged launcher containment unresolved'}}
        }}
    }}
    '''
    try:
        environment = _isolated_windows_environment()
        environment["XAUUSD_FIXTURE_CONFIGURATION"] = str(tmp_path / "fixture-user-environment.json")
        environment["GEMINI_API_KEY"] = "synthetic-process-decoy"
        assert _run_contract_with_runtime(
            tmp_path, body, "powershell.exe", environment=environment,
            execution_timeout=90, controller_script=source / "scripts/xauusd_control_center.ps1",
        ) == "real quiesced handoff and clean withdrawal passed"
        assert all(process.poll() is None for process in preserved)
    finally:
        for process in preserved:
            process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
            process.wait(timeout=5)


def test_bundle_install_is_complete_and_restorable(tmp_path: Path) -> None:
    old_revision, new_revision = "a" * 40, "b" * 40
    control = tmp_path / "control"
    stage = tmp_path / "stage"
    backup = tmp_path / "backup"
    _write_bundle(control, old_revision, "old")
    _write_bundle(stage, new_revision, "new", dependency_closed=True)
    result = _run_contract(
        tmp_path,
        f"$new=Install-VerifiedRuntimeControlBundleStage -StageRoot '{stage}' "
        f"-ControlRoot '{control}' -BackupRoot '{backup}'; "
        f"$old=Restore-RuntimeControlBundleBackup -BackupRoot '{backup}' "
        f"-ControlRoot '{control}'; Write-Output \"$($new.source_revision),$($old.source_revision)\"",
    )
    assert result == f"{new_revision},{old_revision}"
    assert all(
        (control / name).read_text() == f"old|{name}\n"
        for name in CONTROL_FILES
        if name != "runtime-control-files.json"
    )
    assert json.loads((control / "runtime-control-files.json").read_text())[
        "files"
    ] == list(CONTROL_FILES)


def test_watchdog_repairs_interrupted_bundle_copy_after_installer_exit(
    tmp_path: Path,
) -> None:
    target_revision = "b" * 40
    repository = tmp_path / "repository"
    control = repository / ".local" / "runtime-control"
    stage = repository / ".local" / ".cps-recovery"
    backup = repository / ".local" / ".cpb-recovery"
    _write_bundle(control, "a" * 40, "old")
    _write_bundle(stage, target_revision, "new")
    _write_bundle(backup, "a" * 40, "old")
    (control / CONTROL_FILES[0]).write_text("interrupted\n", encoding="utf-8")
    body = textwrap.dedent(
        f"""
        Write-ControlPlaneInstallState @{{transaction_id='txn';target_revision='{target_revision}';phase='INSTALL_BUNDLE';stage_root='{stage}';backup_root='{backup}';install_owner_identity=[pscustomobject]@{{process_id=999999;process_start_token='gone'}}}};
        function Get-ControlPlaneProcessIdentity {{ param($ProcessId); return $null }};
        $repaired=Repair-AbandonedControlPlaneBundleForWatchdog;
        Write-Output "$($repaired.source_revision)|$((Get-ControlPlaneInstallState).recovery)"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == (
        f"{target_revision}|FORWARD_REPAIRED_INTERRUPTED_BUNDLE_COPY"
    )


def _abandoned_activation_mocks(
    target_revision: str,
    phase: str,
    *,
    transaction_id: str = "txn",
    bundle_verified: str = "$true",
) -> str:
    return textwrap.dedent(
        f"""
        $script:phase='{phase}'; $script:checks=@();
        $current=[pscustomobject]@{{process_id=$PID;process_start_token='{CURRENT_START_TOKEN}'}};
        $old=[pscustomobject]@{{process_id=100;process_start_token='{OLD_START_TOKEN}'}};
        $baseline=[pscustomobject]@{{business_runtime_revision='runtime';release_state_hash='state';release_history_hash='history';services=[pscustomobject]@{{}}}};
        $script:state=[pscustomobject]@{{transaction_id='{transaction_id}';phase=$script:phase;target_revision='{target_revision}';previous_revision='{'a' * 40}';bundle_hash_verified={bundle_verified};install_owner_identity=[pscustomobject]@{{process_id=999999;process_start_token='{DEAD_INSTALLER_START_TOKEN}'}};old_watchdog_identity=$old;isolation_before=$baseline;backup_root='backup';supervision_state=$null}};
        function Get-ControlPlaneInstallState {{ $script:state.phase=$script:phase; $script:state }};
        function Write-ControlPlaneInstallState {{ param($Values); if($Values.phase){{$script:phase=[string]$Values.phase}} }};
        function Get-ControlPlaneProcessIdentity {{ param($ProcessId); if($ProcessId-eq $PID){{$current}}else{{$null}} }};
        function Get-RuntimeControlBundleIdentity {{ [pscustomobject]@{{source_revision='{target_revision}';exact_revision=$true}} }};
        function Get-VerifiedWatchdogOwners {{ @($current) }};
        function Get-ReleaseControlState {{ [pscustomobject]@{{transaction=$null}} }};
        function Assert-ControlPlaneIsolationBaseline {{ param($Snapshot,$ReleaseState); $script:checks+='baseline' }};
        function Get-ControlPlaneIsolationSnapshot {{ $baseline }};
        function Assert-ControlPlaneIsolationSnapshot {{ param($Before,$After); $script:checks+='isolation' }};
        function Write-WatchdogHeartbeat {{ param($SupervisionMode,$InstallTransactionId); New-Item -ItemType Directory -Path (Split-Path -Parent $watchdogHeartbeatPath) -Force | Out-Null; [pscustomobject]@{{install_transaction_id=$InstallTransactionId;supervision_mode=$SupervisionMode;control_bundle_revision='{target_revision}';control_bundle_exact_revision=$true;control_bundle_hash_verified=$true;process_id=$PID;process_start_token='{CURRENT_START_TOKEN}'}} | ConvertTo-Json | Set-Content -LiteralPath $watchdogHeartbeatPath }};
        """
    ).replace("\n", " ")


def test_staged_hash_mismatch_stops_before_watchdog_termination(tmp_path: Path) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        function New-VerifiedRuntimeControlBundleStage {{ throw 'CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED' }};
        try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision '{target_revision}' | Out-Null }} catch {{ }};
        Write-Output (($script:timeline -contains 'stop').ToString())
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "False"


def test_handoff_orders_single_ownership_and_preserves_runtime(tmp_path: Path) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + (
        f"$result=Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' "
        f"-TargetRevision '{target_revision}'; "
        'Write-Output "$($result.status)|$($script:timeline -join ",")"'
    )
    result = _run_contract(tmp_path, body)
    assert result == (
        "COMMITTED|lock,stage,preflight,suspend,guard,stop,baseline,install,start,"
        "heartbeat:QUIESCED,isolation,heartbeat:ACTIVE,supervision,unlock"
    )


def test_install_fails_closed_when_stable_sync_owner_is_missing(
    tmp_path: Path,
) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        function Get-ControlPlaneIsolationSnapshot {{
          $p=[pscustomobject]@{{process_id=10;process_start_token='service-token'}};
          [pscustomobject]@{{business_runtime_revision='runtime';services=[pscustomobject]@{{
            quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@();broadcast=@()
          }}}}
        }};
        function Assert-ControlPlaneIsolationBaseline {{ param($Snapshot,$ReleaseState);
          if(@($Snapshot.services.sync).Count-ne 1){{throw 'CONTROL_PLANE_SERVICE_OWNER_REQUIRED:sync'}}
        }};
        try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable'
          -TargetRevision '{target_revision}' | Out-Null }} catch {{ $reason=$_.Exception.Message }};
        Write-Output "$reason|$($script:timeline -join ',')"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert result.startswith(
        "CONTROL_PLANE_INSTALL_FAILED: "
        "CONTROL_PLANE_SERVICE_OWNER_REQUIRED:sync; ROLLED_BACK|"
    )
    assert "install" not in result


def test_install_captures_service_isolation_only_after_old_watchdog_stops(
    tmp_path: Path,
) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        $script:isolationCalls=0;
        function Get-ControlPlaneIsolationSnapshot {{
          $script:isolationCalls++;
          if($script:isolationCalls-eq 1 -and $script:owners.Count-ne 0){{
            throw 'ISOLATION_CAPTURED_BEFORE_QUIESCE'
          }};
          $p=[pscustomobject]@{{process_id=10;process_start_token='service-token'}};
          [pscustomobject]@{{business_runtime_revision='runtime';services=[pscustomobject]@{{
            quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@($p);broadcast=@()
          }}}}
        }};
        $result=Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable'
          -TargetRevision '{target_revision}';
        Write-Output "$($result.status)|$script:isolationCalls"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "COMMITTED|2"


def test_install_reloads_release_context_after_old_supervisor_is_fenced(
    tmp_path: Path,
) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        $script:releaseReads=0;
        function Get-ReleaseControlState {{
          $script:releaseReads++;
          [pscustomobject]@{{marker=if($script:releaseReads-eq 1){{'stale'}}else{{'fresh'}};transaction=$null}}
        }};
        function Assert-ControlPlaneIsolationBaseline {{ param($Snapshot,$ReleaseState); if($ReleaseState.marker-ne 'fresh'){{throw 'STALE_RELEASE_CONTEXT'}}; $script:timeline+='baseline' }};
        $result=Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable'
          -TargetRevision '{target_revision}';
        Write-Output "$($result.status)|$script:releaseReads"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "COMMITTED|2"


def test_supervision_quiesce_keeps_main_task_enabled_for_restart(tmp_path: Path) -> None:
    body = textwrap.dedent(
        """
        $script:disabled=@(); $script:enabled=@(); $script:stopped=@(); $script:mainTask=$taskName;
        function Get-ScheduledTask { param($TaskName,$ErrorAction); [pscustomobject]@{Settings=[pscustomobject]@{Enabled=$true}} };
        function Disable-ScheduledTask { param($TaskName); $script:disabled+=$TaskName };
        function Enable-ScheduledTask { param($TaskName); $script:enabled+=$TaskName };
        function Stop-ScheduledTask { param($TaskName,$ErrorAction); $script:stopped+=$TaskName };
        $state=Suspend-ControlPlaneSupervision;
        Write-Output "$($script:disabled.Count),$($script:enabled.Count),$($script:stopped.Count -eq 1 -and $script:stopped[0] -ceq $guardTaskName)"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert result == "1,0,True"


@pytest.mark.parametrize(
    ("exact", "hashed", "heartbeat_token", "expected"),
    [
        ("$true", "$true", OLD_START_TOKEN, "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"),
        ("$false", "$true", NEW_START_TOKEN, "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"),
        ("$true", "$false", NEW_START_TOKEN, "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"),
        ("$true", "$true", NEW_START_TOKEN, NEW_START_TOKEN),
    ],
)
def test_heartbeat_requires_new_process_exact_revision_and_hashes(
    tmp_path: Path, exact: str, hashed: str, heartbeat_token: str, expected: str,
) -> None:
    revision = "b" * 40
    previous = _identity(100, OLD_START_TOKEN)
    owner_pid = 100 if heartbeat_token == OLD_START_TOKEN else 200
    owner = _identity(owner_pid, heartbeat_token)
    body = textwrap.dedent(
        f"""
        $previous={previous}; $owner={owner};
        $receipt=[pscustomobject]@{{instance_id=('a'*32);mode='ACTIVE';install_transaction_id=$null}};
        $owner | Add-Member -NotePropertyName watchdog_owner_receipt -NotePropertyValue $receipt -Force;
        function Get-WatchdogOwnerReceiptDigest {{ return ('c'*64) }};
        function Start-Sleep {{ }};
        function Get-VerifiedWatchdogOwners {{ @($owner) }};
        New-Item -ItemType Directory -Path (Split-Path -Parent $watchdogHeartbeatPath) -Force | Out-Null;
        [pscustomobject]@{{control_bundle_revision='{revision}';control_bundle_exact_revision={exact};control_bundle_hash_verified={hashed};supervision_mode='ACTIVE';install_transaction_id=$null;process_id={owner_pid};process_start_token='{heartbeat_token}';instance_id=('a'*32);owner_receipt_digest=('c'*64)}} | ConvertTo-Json | Set-Content -LiteralPath $watchdogHeartbeatPath;
        try {{ $accepted=Wait-VerifiedWatchdogHandoff -ExpectedRevision '{revision}' -PreviousIdentity $previous -Timeout ([TimeSpan]::FromMilliseconds(20)); Write-Output $accepted.process_start_token }} catch {{ Write-Output $_.Exception.Message }}
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == expected


def test_handoff_requires_quiesced_ack_from_exact_install_transaction(tmp_path: Path) -> None:
    revision = "b" * 40
    previous = _identity(100, OLD_START_TOKEN)
    owner = _identity(200, NEW_START_TOKEN)
    body = textwrap.dedent(
        f"""
        $previous={previous}; $owner={owner};
        function Start-Sleep {{ }};
        function Get-VerifiedWatchdogOwners {{ @($owner) }};
        New-Item -ItemType Directory -Path (Split-Path -Parent $watchdogHeartbeatPath) -Force | Out-Null;
        [pscustomobject]@{{control_bundle_revision='{revision}';control_bundle_exact_revision=$true;control_bundle_hash_verified=$true;supervision_mode='QUIESCED';install_transaction_id='wrong';process_id=200;process_start_token='{NEW_START_TOKEN}'}} | ConvertTo-Json | Set-Content -LiteralPath $watchdogHeartbeatPath;
        try {{ Wait-VerifiedWatchdogHandoff -ExpectedRevision '{revision}' -PreviousIdentity $previous -ExpectedMode 'QUIESCED' -ExpectedInstallTransactionId 'expected' -Timeout ([TimeSpan]::FromMilliseconds(20)) | Out-Null; Write-Output accepted }} catch {{ Write-Output $_.Exception.Message }}
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"


@pytest.mark.parametrize(
    "phase",
    ("START_NEW_WATCHDOG", "VERIFY_QUIESCED_HANDOFF", "ACTIVATE_NEW_WATCHDOG"),
)
def test_installer_death_rechecks_every_fact_before_active_grant(
    tmp_path: Path, phase: str,
) -> None:
    target_revision = "b" * 40
    body = _abandoned_activation_mocks(target_revision, phase) + textwrap.dedent(
        """
        $result=Wait-ControlPlaneInstallActivation -TransactionId 'txn';
        Write-Output "$result|$script:phase|$($script:checks -join ',')"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == (
        "RECOVERED|ACTIVATE_NEW_WATCHDOG|baseline,isolation"
    )


def test_installer_death_before_bundle_swap_restores_safe_supervisor_path(
    tmp_path: Path,
) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    repository = tmp_path / "repository"
    control = repository / ".local" / "runtime-control"
    _write_bundle(control, old_revision, "old")
    body = textwrap.dedent(
        f"""
        Write-ControlPlaneInstallState @{{transaction_id='txn';target_revision='{target_revision}';previous_revision='{old_revision}';phase='STOP_OLD_WATCHDOG';install_owner_identity=[pscustomobject]@{{process_id=999999;process_start_token='gone'}};supervision_state=[pscustomobject]@{{}}}};
        function Get-ControlPlaneProcessIdentity {{ param($ProcessId); return $null }};
        function Get-RuntimeControlBundleIdentity {{ Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{control}' }};
        function Restore-ControlPlaneSupervision {{ param($State); $script:restored=$true }};
        $bundle=Repair-AbandonedControlPlaneBundleForWatchdog;
        $state=Get-ControlPlaneInstallState;
        Write-Output "$($bundle.source_revision)|$($state.phase)|$script:restored"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == f"{old_revision}|ROLLED_BACK|True"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("$script:state.transaction_id='wrong'", "CONTROL_PLANE_INSTALL_FENCE_LOST"),
        ("$script:state.bundle_hash_verified=$false", "CONTROL_PLANE_ABANDONED_BUNDLE_NOT_VERIFIED"),
        ("function Get-RuntimeControlBundleIdentity { [pscustomobject]@{source_revision='wrong';exact_revision=$true} }", "CONTROL_PLANE_ABANDONED_BUNDLE_IDENTITY_MISMATCH"),
        ("function Get-ControlPlaneProcessIdentity { param($ProcessId); if($ProcessId-eq 100){$old}elseif($ProcessId-eq $PID){$current}else{$null} }", "CONTROL_PLANE_OLD_WATCHDOG_STILL_OWNS"),
        ("function Get-VerifiedWatchdogOwners { @($current,$current) }", "CONTROL_PLANE_RECOVERY_EXACTLY_ONE_REPLACEMENT_REQUIRED"),
        ("function Assert-ControlPlaneIsolationSnapshot { param($Before,$After); throw 'CONTROL_PLANE_INSTALL_CHANGED_SERVICE_SYNC' }", "CONTROL_PLANE_INSTALL_CHANGED_SERVICE_SYNC"),
        ("function Get-ReleaseControlState { [pscustomobject]@{transaction=[pscustomobject]@{type='PROMOTE'}} }", "CONTROL_PLANE_RECOVERY_RELEASE_TRANSACTION_APPEARED"),
        ("New-Item -ItemType Directory -Path $releaseLockPath -Force | Out-Null; [pscustomobject]@{owner_pid=123} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseLockPath 'owner.json')", "CONTROL_PLANE_RECOVERY_CONCURRENT_RELEASE_LOCK"),
    ),
)
def test_abandoned_install_safety_facts_fail_closed(
    tmp_path: Path, mutation: str, expected: str,
) -> None:
    target_revision = "b" * 40
    body = _abandoned_activation_mocks(
        target_revision, "VERIFY_QUIESCED_HANDOFF"
    ) + f"Write-WatchdogHeartbeat -SupervisionMode 'QUIESCED' -InstallTransactionId 'txn'; {mutation}; try {{ Assert-AbandonedControlPlaneInstallActivation -State $script:state -TransactionId 'txn' | Out-Null; Write-Output accepted }} catch {{ Write-Output $_.Exception.Message }}"
    assert _run_contract(tmp_path, body) == expected


def test_abandoned_install_accepts_only_exact_dead_installer_lock_identity(
    tmp_path: Path,
) -> None:
    target_revision = "b" * 40
    body = _abandoned_activation_mocks(
        target_revision, "VERIFY_QUIESCED_HANDOFF"
    ) + textwrap.dedent(
        f"""
        Write-WatchdogHeartbeat -SupervisionMode 'QUIESCED' -InstallTransactionId 'txn';
        New-Item -ItemType Directory -Path $releaseLockPath -Force | Out-Null;
        [pscustomobject]@{{owner_pid=999999;owner_process_start_token='{DEAD_INSTALLER_START_TOKEN}'}} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseLockPath 'owner.json');
        $verified=Assert-AbandonedControlPlaneInstallActivation -State $script:state -TransactionId 'txn';
        Write-Output "$($verified.owner.process_start_token)|$($script:checks -join ',')"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == (
        f"{CURRENT_START_TOKEN}|baseline,isolation"
    )


def test_failed_abandoned_activation_restores_verified_backup(tmp_path: Path) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    repository = tmp_path / "repository"
    control = repository / ".local" / "runtime-control"
    backup = repository / ".local" / ".cpb-recovery"
    _write_bundle(control, target_revision, "new")
    _write_bundle(backup, old_revision, "old")
    body = _abandoned_activation_mocks(
        target_revision, "VERIFY_QUIESCED_HANDOFF", bundle_verified="$false"
    ) + textwrap.dedent(
        f"""
        $script:state.backup_root='{backup}';
        function Restore-ControlPlaneSupervision {{ param($State); $script:restored=$true }};
        try {{ Wait-ControlPlaneInstallActivation -TransactionId 'txn' | Out-Null }} catch {{ $message=$_.Exception.Message }};
        $bundle=Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{control}';
        Write-Output "$message|$($bundle.source_revision)|$script:phase|$script:restored"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert result == (
        "CONTROL_PLANE_ABANDONED_INSTALL_ROLLED_BACK: "
        "CONTROL_PLANE_ABANDONED_BUNDLE_NOT_VERIFIED|"
        f"{old_revision}|ROLLED_BACK|True"
    )


def test_failure_starting_new_watchdog_restores_old_bundle_and_owner(tmp_path: Path) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        $script:startCount=0;
        function Start-WatchdogReplacement {{ param([switch]$PassThru,$InstallTransactionId); $script:startCount++; if($script:startCount-eq 1){{throw 'new start failed'}}; $script:timeline+='restore-start'; $script:owners=@({_identity(300, 'restored-token')}) }};
        function Restore-RuntimeControlBundleBackup {{ param($BackupRoot,$ControlRoot); $script:timeline+='restore-bundle'; [pscustomobject]@{{source_revision='{old_revision}'}} }};
        function Wait-VerifiedWatchdogHandoff {{ param($ExpectedRevision,$PreviousIdentity,$Timeout); $script:timeline+="restore-heartbeat:$ExpectedRevision"; return $script:owners[0] }};
        try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision '{target_revision}' | Out-Null }} catch {{ $message=$_.Exception.Message }};
        Write-Output "$message|$($script:timeline -join ',')"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert "ROLLED_BACK" in result
    assert "install,restore-bundle,isolation,restore-start" in result
    assert f"restore-heartbeat:{old_revision}" in result


def test_baseline_capture_failure_can_restart_previous_supervisor(tmp_path: Path) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        function Get-ControlPlaneIsolationSnapshot {{ throw 'baseline unavailable' }};
        function Start-WatchdogReplacement {{ param([switch]$PassThru,$InstallTransactionId); $script:timeline+='restore-start'; $script:owners=@({_identity(300, 'restored-token')}) }};
        function Wait-VerifiedWatchdogHandoff {{ param($ExpectedRevision,$PreviousIdentity,$ExpectedMode,$ExpectedInstallTransactionId,$Timeout); $script:timeline+='restore-heartbeat'; return $script:owners[0] }};
        try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision '{target_revision}' | Out-Null }} catch {{ $message=$_.Exception.Message }};
        Write-Output "$message|$($script:timeline -join ',')"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert "ROLLED_BACK" in result
    assert "stop,restore-start,restore-heartbeat" in result


def test_release_transaction_blocks_control_plane_install(tmp_path: Path) -> None:
    target_revision = "b" * 40
    body = _state_machine_mocks("a" * 40, target_revision) + (
        "function Get-ReleaseControlState { [pscustomobject]@{transaction=[pscustomobject]@{type='PROMOTE'}} }; "
        f"try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision '{target_revision}' | Out-Null }} "
        "catch { Write-Output $_.Exception.Message }"
    )
    assert _run_contract(tmp_path, body) == "CONTROL_PLANE_INSTALL_BLOCKED_BY_RELEASE_TRANSACTION"


def test_operator_lifecycle_collapses_internal_release_states(tmp_path: Path) -> None:
    body = textwrap.dedent(
        """
        $stable=[pscustomobject]@{deployment_status='READY';candidate=$null;transaction=$null};
        $prepare=[pscustomobject]@{deployment_status='READY';candidate=[pscustomobject]@{validation_state='NEW';validation=$null};transaction=$null};
        $migration=[pscustomobject]@{deployment_status='READY';candidate=[pscustomobject]@{validation_state='REVIEW_REQUIRED';validation=[pscustomobject]@{reason='COORDINATED_STORAGE_MIGRATION_REQUIRED'}};transaction=$null};
        $verify=[pscustomobject]@{deployment_status='READY';candidate=[pscustomobject]@{validation_state='REVIEW_REQUIRED';validation=[pscustomobject]@{reason='CPU_REVIEW_REQUIRED'}};transaction=$null};
        $switch=[pscustomobject]@{deployment_status='PROMOTING';candidate=$verify.candidate;transaction=[pscustomobject]@{phase='CUTOVER'}};
        $observe=[pscustomobject]@{deployment_status='OBSERVING';candidate=$verify.candidate;transaction=[pscustomobject]@{phase='OBSERVING'}};
        Write-Output (@($stable,$prepare,$migration,$verify,$switch,$observe | ForEach-Object { Get-ReleaseLifecyclePhase $_ }) -join ',')
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "STABLE,PREPARE,PREPARE,VERIFY,SWITCH,OBSERVE"


def test_control_plane_isolation_and_visible_identity_are_explicit() -> None:
    install_source = (
        ROOT / "scripts" / "control_center_install.ps1"
    ).read_text(encoding="utf-8")
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "scripts" / "control_center_install.ps1",
            ROOT / "scripts" / "control_center_runtime_supervision.ps1",
            ROOT / "scripts" / "control_center_presentation.ps1",
            ROOT / "scripts" / "xauusd_control_center.ps1",
        )
    )
    xaml = (ROOT / "scripts" / "control_center.xaml").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "xauusd_control_center_launcher.vbs").read_text(encoding="utf-8")
    install_body = install_source.split("function Invoke-ControlPlaneInstall", 1)[1]
    assert "InstallRuntime" not in install_body
    assert "Stop-All" not in install_body
    assert "Restart-All" not in install_body
    assert "Restart-CodeReloadableServices" not in install_body
    assert "Assert-ControlPlaneIsolationSnapshot" in install_body
    supervision = install_source.split("function Suspend-ControlPlaneSupervision", 1)[1].split(
        "function Restore-ControlPlaneSupervision", 1
    )[0]
    assert "Disable-ScheduledTask -TaskName $guardTaskName" in supervision
    assert "Stop-ScheduledTask -TaskName $guardTaskName" in supervision
    # Normal installation retains its reboot entrypoint; the explicit zero-owner
    # incident is covered by the executable two-mode task containment contract.
    assert "[switch]$CollectorClockRecovery" in supervision
    assert "ControlPlaneIdentity" in xaml
    assert "BusinessRuntimeIdentity" in xaml
    assert "EXACT | HASH VERIFIED" in source
    assert 'BuildPath(scriptDirectory, "xauusd_control_center.ps1")' in launcher


@pytest.mark.parametrize(
    ("enabled", "before_broadcast", "after_broadcast", "expected"),
    (
        ("$false", "@()", "@()", "PASSED"),
        ("$false", "@($p)", "@($p)", "CONTROL_PLANE_UNEXPECTED_SERVICE_OWNER:broadcast"),
        ("$true", "@()", "@()", "CONTROL_PLANE_SERVICE_OWNER_REQUIRED:broadcast"),
    ),
)
def test_control_plane_isolation_respects_optional_broadcast_ownership(
    tmp_path: Path,
    enabled: str,
    before_broadcast: str,
    after_broadcast: str,
    expected: str,
) -> None:
    body = textwrap.dedent(
        f"""
        function Test-BroadcastPublisherEnabled {{ return {enabled} }};
        $p=[pscustomobject]@{{process_id=10;process_start_token='same'}};
        $before=[pscustomobject]@{{business_runtime_revision='runtime';release_state_hash='state';release_history_hash='history';services=[pscustomobject]@{{quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@($p);broadcast={before_broadcast}}}}};
        $after=[pscustomobject]@{{business_runtime_revision='runtime';release_state_hash='state';release_history_hash='history';services=[pscustomobject]@{{quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@($p);broadcast={after_broadcast}}}}};
        try {{ Assert-ControlPlaneIsolationBaseline -Snapshot $before; Write-Output 'PASSED' }} catch {{ Write-Output $_.Exception.Message }}
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == expected


def test_control_plane_isolation_always_requires_stable_sync_owner(
    tmp_path: Path,
) -> None:
    body = textwrap.dedent(
        f"""
        function Test-BroadcastPublisherEnabled {{ return $false }};
        $p=[pscustomobject]@{{process_id=10;process_start_token='same'}};
        $before=[pscustomobject]@{{business_runtime_revision='runtime';release_state_hash='state';release_history_hash='history';services=[pscustomobject]@{{quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@();broadcast=@()}}}};
        try {{ Assert-ControlPlaneIsolationBaseline -Snapshot $before; Write-Output 'PASSED' }} catch {{ Write-Output $_.Exception.Message }}
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "CONTROL_PLANE_SERVICE_OWNER_REQUIRED:sync"


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_collector_incident_baseline_is_read_only_and_rejects_uncertain_owners(tmp_path, runtime_executable):
    if shutil.which(runtime_executable) is None:
        pytest.skip(f"{runtime_executable} is unavailable")
    body = r'''
    $script:scenario = 'valid';
    function Assert-ControlPlaneSourceRevision { param($SourceRoot,$SourceRevision,[switch]$RequireImmutableSource); if (-not $RequireImmutableSource) {throw 'immutable source required'} };
    function Get-ReleaseControlState { [pscustomobject]@{transaction=$null;stable=[pscustomobject]@{windows_revision=('a'*40);worker_version_id='stable'}} };
    function Get-ControlPlaneInstallState { $null };
    function Get-CodeRevision { 'a'*40 };
    function Get-CimInstance {
        [CmdletBinding()]param($ClassName, $Filter);
        if ($script:scenario -eq 'enumeration') { throw 'enumeration failed' };
        foreach ($key in @('quote','annotator','api','sync')) {
            if ($script:scenario -eq 'missing' -and $key -eq 'api') { continue };
            [pscustomobject]@{Name='python.exe';ProcessId=10;CommandLine=$key;Key=$key};
            if ($script:scenario -eq 'duplicate' -and $key -eq 'sync') {
                [pscustomobject]@{Name='python.exe';ProcessId=11;CommandLine=$key;Key=$key}
            }
        }
    };
    function Get-WatchdogOwnershipInventory { param([switch]$RequireCompleteInventory);
        if (-not $RequireCompleteInventory) { throw 'strict inventory omitted' };
        [pscustomobject]@{authoritative=@();duplicate_shaped=@();legacy_orphaned=@();unknown=@();receipt=[pscustomobject]@{process_id=99;process_start_token='old'}}
    };
    function Get-WatchdogSingletonDescriptor { [pscustomobject]@{user_sid='sid';runtime_root_hash='runtime';repository_root_hash='repository'} };
    function Test-WatchdogOwnerReceiptShape { $true };
    function Get-ControlPlaneProcessIdentity { param($ProcessId,[switch]$RequireCompleteInventory);
        if (-not $RequireCompleteInventory) { throw 'strict identity omitted' };
        if ($ProcessId -eq 99 -and $script:scenario -ne 'old-alive') { return $null };
        [pscustomobject]@{process_id=$ProcessId;process_start_token='old';owner_sid='sid'}
    };
    function Test-ControlPlaneStartTokenEqual { $true };
    function Test-ForecasterServiceProcess { param($Process,$Service); $Process.Key -eq $Service.Key };
    function Test-ControlPlaneServiceOwnerRequired { param($Service); $Service.Key -ne 'broadcast' };
    function Get-ServiceState { if ($script:scenario -eq 'unhealthy') { 'SYNC STALE' } else { 'RUNNING' } };
    function Get-BrokerMarketSession { [pscustomobject]@{IsOpen=$false} };
    function Get-ReleaseProviderRuntimeFacts { [pscustomobject]@{active_worker_observation=[pscustomobject]@{
        status='AVAILABLE';traffic_percent=100;version_id=$(if ($script:scenario -eq 'traffic') {'wrong'} else {'stable'})}}
    };
    function Invoke-Utf8NativeProcess { param($FilePath,$Arguments,$WorkingDirectory,$TimeoutMilliseconds);
        if ('--inspect-snapshot-only' -notin $Arguments) { throw 'mutation attempted' };
        [pscustomobject]@{exit_code=0;stdout=(@{decision_time='2026-09-04T16:05:00.000000+00:00';snapshot_hash=$(if ($script:scenario -eq 'snapshot') {'wrong'} else {'b139c8a9d913c237e8e9e3ebc677a1144cd8ad2f9e0adee6b62ed8cd2a7fa5ee'})}|ConvertTo-Json -Compress)}
    };
    function Write-ControlPlaneInstallState { throw 'mutation attempted' };
    function Start-WatchdogReplacement { throw 'mutation attempted' };
    $expected = @{
        valid='DEGRADED_RECOVERY_BASELINE'; enumeration='enumeration failed';
        missing='COLLECTOR_RECOVERY_SERVICE_OWNER_INVALID:api';
        duplicate='COLLECTOR_RECOVERY_SERVICE_OWNER_INVALID:sync';
        'old-alive'='COLLECTOR_RECOVERY_PRIOR_OWNER_ALIVE';
        unhealthy='COLLECTOR_RECOVERY_SERVICE_UNHEALTHY:quote:SYNC STALE';
        traffic='COLLECTOR_RECOVERY_STABLE_TRAFFIC_UNPROVED';
        snapshot='COLLECTOR_RECOVERY_SNAPSHOT_INSPECTION_MISMATCH'
    };
    foreach ($case in $expected.Keys) {
        $script:scenario=$case;
        try { $actual=(Get-CollectorClockRecoveryBaseline -VerifiedSourceRoot $repositoryRoot -TargetRevision ('b'*40)).state }
        catch { $actual=$_.Exception.Message };
        if ($actual -cne $expected[$case]) { throw "${case}: expected $($expected[$case]); actual $actual" }
    };
    Write-Output '8 baseline cases passed; production mutation=0'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == (
        "8 baseline cases passed; production mutation=0"
    )


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_incident_hold_survives_reload_and_only_exact_normal_switch_can_release_it(tmp_path, runtime_executable):
    if shutil.which(runtime_executable) is None:
        pytest.skip(f"{runtime_executable} is unavailable")
    body = r'''
    function Get-WatchdogSingletonDescriptor { [pscustomobject]@{user_sid='sid';runtime_root_hash='runtime';repository_root_hash='repository'} };
    $context = [pscustomobject]@{
        incident='COLLECTOR_CLOCK_EVENT_ATOMICITY';state='DEGRADED_RECOVERY_BASELINE';
        broken_revision=('a'*40);target_revision=('b'*40);user_sid='sid';
        runtime_root_hash='runtime';repository_root_hash='repository';
        snapshot=[pscustomobject]@{decision_time='2026-09-04T16:05:00.000000+00:00';snapshot_hash='b139c8a9d913c237e8e9e3ebc677a1144cd8ad2f9e0adee6b62ed8cd2a7fa5ee'}
    };
    Write-ControlPlaneInstallState @{phase='COMMITTED';collector_clock_recovery=$context};
    $script:businessRevision='a'*40;
    $script:release=[pscustomobject]@{stable=[pscustomobject]@{windows_revision=('a'*40)};transaction=$null};
    function Get-CodeRevision { $script:businessRevision };
    function Get-ReleaseControlState { $script:release };
    if (-not (Test-CollectorClockRecoveryHold)) { throw 'old collector was not held' };
    try { Start-ForecasterService ($services | Where-Object Key -eq collector); throw 'old collector started' }
    catch { if ($_.Exception.Message -cne 'COLLECTOR_CLOCK_RECOVERY_REQUIRED') { throw } };
    if (-not (Test-WatchdogRecoverySuppressed -ServiceKey collector -ServiceState STOPPED)) { throw 'watchdog may restart broken collector' };
    $script:businessRevision='b'*40;
    try { $null=Test-CollectorClockRecoveryHold; throw 'uncoordinated target accepted' }
    catch { if ($_.Exception.Message -cne 'COLLECTOR_RECOVERY_RUNTIME_TRANSITION_UNPROVED') { throw } };
    $script:release.transaction=[pscustomobject]@{type='PROMOTE';target=[pscustomobject]@{windows_revision=('b'*40)}};
    if (Test-CollectorClockRecoveryHold) { throw 'normal exact switch did not release hold' };
    $script:release.transaction=$null; $script:release.stable.windows_revision='b'*40;
    if (Test-CollectorClockRecoveryHold) { throw 'committed target held' };
    $script:businessRevision='a'*40;
    if (-not (Test-CollectorClockRecoveryHold)) { throw 'rollback falsely restarted broken code' };
    $context.runtime_root_hash='wrong';
    Write-ControlPlaneInstallState @{collector_clock_recovery=$context};
    try { $null=Get-CollectorClockRecoveryContext; throw 'wrong root accepted' }
    catch { if ($_.Exception.Message -cne 'COLLECTOR_RECOVERY_CONTEXT_INVALID') { throw } };
    Write-Output 'incident hold verified'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == "incident hold verified"


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_install_task_containment_preserves_normal_restart_but_fences_incident_bootstrap(tmp_path, runtime_executable):
    if shutil.which(runtime_executable) is None:
        pytest.skip(f"{runtime_executable} is unavailable")
    body = r'''
    function Get-ScheduledTask { [pscustomobject]@{Settings=[pscustomobject]@{Enabled=$true}} };
    function Disable-ScheduledTask { param($TaskName); $script:disabled+=@($TaskName) };
    function Stop-ScheduledTask { param($TaskName); $script:stopped+=@($TaskName) };
    foreach ($incident in @($false,$true)) {
        $script:disabled=@(); $script:stopped=@();
        $state=Suspend-ControlPlaneSupervision -CollectorClockRecovery:$incident;
        $expected=@($guardTaskName); if ($incident) { $expected+=@($taskName) };
        if (($script:disabled -join ',') -cne ($expected -join ',') -or
            ($script:stopped -join ',') -cne ($expected -join ',')) { throw 'task containment mismatch' };
        if (-not $state[$taskName] -or -not $state[$guardTaskName]) { throw 'prior task state lost' }
    };
    Write-Output 'both task containment modes passed'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == "both task containment modes passed"


@pytest.mark.parametrize("fail_start", [False, True])
def test_zero_owner_install_releases_real_mutex_before_handoff_and_never_starts_old_code(tmp_path, fail_start):
    body = _state_machine_mocks("a" * 40, "b" * 40) + r'''
    $script:owners=@();
    $script:mutexName='Local\XAUUSD-Contract-'+[guid]::NewGuid().ToString('N');
    function Get-WatchdogSingletonDescriptor { [pscustomobject]@{mutex_name=$script:mutexName} };
    function Test-OtherProcessCanAcquire {
        $command='$m=[Threading.Mutex]::new($false,"'+$script:mutexName+'");$held=$m.WaitOne(0);try{if($held){"FREE"}else{"BUSY"}}finally{if($held){$m.ReleaseMutex()};$m.Dispose()}';
        $result=Invoke-Utf8NativeProcess -FilePath powershell.exe -Arguments @('-NoProfile','-NonInteractive','-Command',$command) -TimeoutMilliseconds 10000;
        if ($result.exit_code -ne 0) { throw $result.stderr };
        return $result.stdout.Trim()
    };
    function Get-CollectorClockRecoveryBaseline {
        if ((Test-OtherProcessCanAcquire) -cne 'BUSY') { throw 'bootstrap reservation missing' };
        [pscustomobject]@{incident='COLLECTOR_CLOCK_EVENT_ATOMICITY';broken_revision=('a'*40);target_revision=('b'*40);
            previous_watchdog_receipt=[pscustomobject]@{process_id=100;process_start_token='old-token'}}
    };
    function Start-WatchdogReplacement {
        if ((Test-OtherProcessCanAcquire) -cne 'FREE') { throw 'handoff deadlock' };
        $script:timeline+='start-target';
        if ($script:failStart) { throw 'fixture target startup failure' };
        $script:owners=@([pscustomobject]@{process_id=200;process_start_token='new-token'})
    };
    function Restore-RuntimeControlBundleBackup { $script:timeline+='restore-bundle'; [pscustomobject]@{source_revision=('a'*40)} };
    function Disable-ScheduledTask { param($TaskName); if ($TaskName -notlike 'XAUUSD-Contract-*') {throw 'unsafe task name'}; $script:disabled+=@($TaskName) };
    $script:disabled=@();
    ''' + f"$script:failStart=${str(fail_start).lower()};" + r'''
    try { $result=Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision ('b'*40) -CollectorClockRecovery }
    catch { if (-not $script:failStart) { throw }; $failure=$_.Exception.Message };
    if ($script:timeline -contains 'stop') { throw 'stale old PID was terminated' };
    if (@($script:timeline | Where-Object {$_ -eq 'start-target'}).Count -ne 1) { throw 'repeated bootstrap' };
    if ((Test-OtherProcessCanAcquire) -cne 'FREE') { throw 'mutex leak' };
    $state=Get-ControlPlaneInstallState;
    if ($script:failStart) {
        if ($state.rollback_result -cne 'ROLLED_BACK_DEGRADED_BASELINE' -or
            $script:owners.Count -ne 0 -or $script:disabled.Count -ne 2) { throw 'unsafe rollback' };
        Write-Output 'degraded baseline restored; old collector not started'
    } else {
        if ($result.status -cne 'COMMITTED' -or $script:owners.Count -ne 1) { throw 'handoff failed' };
        Write-Output 'single replacement; mutex handoff verified'
    }
    '''
    expected = ("degraded baseline restored; old collector not started" if fail_start
                else "single replacement; mutex handoff verified")
    assert _run_contract(tmp_path, body) == expected


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_clock_recovery_operation_requires_lock_and_preserves_accepted_repair(tmp_path, runtime_executable):
    if shutil.which(runtime_executable) is None:
        pytest.skip(f"{runtime_executable} is unavailable")
    body = r'''
    $script:releaseTransactionLockHeld=$false; $script:calls=@();
    function Get-CollectorClockRecoveryContext {
        [pscustomobject]@{target_revision=('b'*40);snapshot=[pscustomobject]@{
            decision_time='2026-09-04T16:05:00.000000+00:00';snapshot_hash=('c'*64)}}
    };
    function Invoke-Utf8NativeProcess {
        param($FilePath,$Arguments,$WorkingDirectory,$TimeoutMilliseconds);
        $script:calls+=@($FilePath);
        if ($FilePath -cne 'git.exe') { throw 'unexpected repair replay' };
        [pscustomobject]@{exit_code=0;stdout=''}
    };
    function Get-CollectorClockRecoveryBaseline {
        param($VerifiedSourceRoot,$TargetRevision,[switch]$SupervisionRecovered);
        if (-not $SupervisionRecovered -or $TargetRevision -cne ('b'*40)) { throw 'wrong admission' };
        [pscustomobject]@{snapshot=[pscustomobject]@{exclusion_recorded=$script:repaired}}
    };
    try { $null=Invoke-CollectorClockRecoveryOperation -Apply; throw 'lock bypass' }
    catch { if ($_.Exception.Message -cne 'COLLECTOR_RECOVERY_RELEASE_LOCK_REQUIRED') { throw } };
    if ($script:calls.Count -ne 0) { throw 'work started before lock' };
    $script:releaseTransactionLockHeld=$true;
    $script:repaired=$true;
    foreach ($apply in @($false,$true)) {
        $result=Invoke-CollectorClockRecoveryOperation -Apply:$apply;
        if (-not $result.snapshot.exclusion_recorded) { throw 'accepted evidence lost' }
    };
    $script:repaired=$false;
    try { $null=Invoke-CollectorClockRecoveryOperation; throw 'unrepaired baseline admitted' }
    catch { if ($_.Exception.Message -cne 'COLLECTOR_RECOVERY_EXISTING_STATE_NOT_REPAIRED') { throw } };
    if ($script:calls.Count -ne 6) { throw 'owned checkout cleanup missing' };
    Write-Output 'lock required; accepted repair reused; unrepaired inspection rejected'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == (
        "lock required; accepted repair reused; unrepaired inspection rejected"
    )


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_incident_rollback_reports_degraded_and_requires_live_session_and_inventory(tmp_path, runtime_executable):
    if shutil.which(runtime_executable) is None:
        pytest.skip(f"{runtime_executable} is unavailable")
    body = r'''
    $plan=[pscustomobject]@{body=[pscustomobject]@{stable_revision=('a'*40);
        collector_clock_recovery=[pscustomobject]@{incident='fixture'};running_service_keys=@('quote')}};
    function Convert-RecoveryPlanContracts { @([pscustomobject]@{Key='quote'},[pscustomobject]@{Key='collector'}) };
    $script:scenario='valid';
    function Get-ForecasterProcesses {
        param($Service,[switch]$RequireCompleteInventory);
        if (-not $RequireCompleteInventory) { throw 'strict inventory required' };
        if ($script:scenario -eq 'enumeration') { throw 'fixture enumeration failed' };
        if ($Service.Key -eq 'quote') { [pscustomobject]@{ProcessId=1} }
    };
    function Test-CodeReloadHealth { $true };
    function Get-ServiceState { 'MARKET CLOSED' };
    function Get-BrokerMarketSession { if ($script:scenario -ne 'stale-session') { [pscustomobject]@{IsOpen=$false} } };
    function Write-WatchdogEvent { param($Event,$Service,$State); $script:event=$Event };
    function Start-Sleep {};
    $serviceStartupTimeout=[TimeSpan]::Zero;
    $result=Wait-RuntimeRecoveryPlanHealth -Plan $plan;
    if ($result.baseline_health -cne 'DEGRADED_RECOVERY_BASELINE' -or
        $script:event -cne 'RUNTIME_RECOVERY_DEGRADED_BASELINE_RESTORED') { throw 'false healthy rollback' };
    foreach ($case in @('stale-session','enumeration')) {
        $script:scenario=$case;
        try { $null=Wait-RuntimeRecoveryPlanHealth -Plan $plan; throw 'unsafe baseline accepted' }
        catch {
            $expected=if ($case -eq 'enumeration') {'fixture enumeration failed'} else {'RUNTIME_RECOVERY_HEALTH_FAILED'};
            if ($_.Exception.Message -cne $expected) { throw }
        }
    };
    Write-Output 'degraded truth preserved; unknown inventory and stale session rejected'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == (
        "degraded truth preserved; unknown inventory and stale session rejected"
    )
