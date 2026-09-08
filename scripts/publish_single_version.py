"""Single-active publication in an explicitly scoped maintenance window.

Uses the existing runtime service registry, Git, Wrangler and public-health
probe. It never restores a database or changes authentication configuration.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

try:
    from .check_public_health import check_publication, read_runtime_json, ProbeFailure
except ImportError:
    from check_public_health import check_publication, read_runtime_json, ProbeFailure

REPOSITORY = "yiyousiow000814/XAUUSD-Forecaster"
WORKER = "aurum-signal-room"
CONTROL_FILES = (
    "publish_single_version.py", "check_public_health.py", "main_runtime.ps1",
    "apply_single_revision.ps1", "xauusd_single_runtime.ps1", "xauusd_single_runtime_launcher.vbs",
)


def run(args: list[str], *, cwd: Path, timeout: int = 60, strip: bool = True) -> str:
    with subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}) as process:
        try:
            stdout, _ = process.communicate(timeout=timeout)
        except BaseException:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    process.kill()
            process.communicate(timeout=10)
            raise
    if process.returncode:
        # Raw provider diagnostics can contain credentials. Do not echo them.
        raise RuntimeError(f"{Path(args[0]).name} failed with exit code {process.returncode}")
    return stdout.strip() if strip else stdout


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(value))
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(50):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.02)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def publication_lock(root: Path):
    import msvcrt
    path = root / "single-publication.lock"
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class Publication:
    def __init__(self, runtime: Path, repository: Path, control: Path, journal: Path):
        self.runtime = runtime.resolve()
        self.repository = repository.resolve()
        self.control = control.resolve()
        self.forward = self.runtime / ".local/forward"
        self.journal = journal

    def record(self, event: str, **facts) -> None:
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": datetime.now(UTC).isoformat(), "event": event, **facts}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def git(self, *args: str) -> str:
        return run(["git", "-C", str(self.runtime), *args], cwd=self.runtime)

    def wrangler(self, *args: str, read: bool = False):
        command = ["node", str(self.repository / "web/node_modules/wrangler/bin/wrangler.js"), *args, "--name", WORKER]
        if read:
            command.append("--json")
        output = run(command, cwd=self.repository / "web", timeout=120)
        return json.loads(output) if read else output

    def control_action(self, action: str) -> str:
        return run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(self.control / "xauusd_single_runtime.ps1"), "-Action", action,
             "-RuntimeRoot", str(self.runtime), "-RepositoryRoot", str(self.repository)], cwd=self.control)

    def stop_owned_services(self) -> datetime:
        began = datetime.now(UTC)
        self.record("scoped_stop_requested")
        atomic_json(self.forward / "main-runtime-desired.json", {"state": "stopped"})
        # A dead previous supervisor is not a prerequisite for its own recovery.
        # Start the new controller with the persisted stop already in place.
        self.control_action("SupervisorStart")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status_path = self.forward / "main-runtime-status.json"
            if status_path.exists():
                status = read_runtime_json(status_path)
                stamp = datetime.fromisoformat(status["updated_at"])
                if status.get("state") == "stopped" and began <= stamp <= datetime.now(UTC) and bool(status.get("services")) and all(not pids for pids in status.get("services", {}).values()):
                    return began
            time.sleep(0.5)
        raise RuntimeError("Supervisor did not confirm the scoped stop; no deployment performed")

    def begin_maintenance(self) -> datetime:
        began = self.stop_owned_services()
        atomic_json(self.forward / "main-runtime-desired.json", {"state": "maintenance"})
        self.record("maintenance_entered")
        return began

    def apply(self, target: dict) -> None:
        # Literal parameters are transported as an argument list through the
        # real PowerShell entrypoint; no shell interpolation or reset/clean.
        self.record("local_code_attempt", revision=target["revision"])
        run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(self.control / "apply_single_revision.ps1"), "-RuntimeRoot", str(self.runtime),
             "-RepositoryRoot", str(self.repository), "-Revision", target["revision"]], cwd=self.control, timeout=210)
        self.record("local_code_applied", revision=self.git("rev-parse", "HEAD"))
        self.record("worker_deployment_attempt", version=target["worker_version"])
        self.wrangler("versions", "deploy", target["worker_version"] + "@100", "--yes", "--message", "single-active maintenance")
        self.record("worker_deployed", version=target["worker_version"])

    def verify(self, target: dict, began: datetime) -> dict:
        # Maintenance remains durable while services run for verification.
        # A process/machine restart cannot turn unfinished verification into
        # normal operation. The publisher owns service starts in this state.
        self.control_action("SupervisorStart")
        reload_deadline = time.monotonic() + 30
        while True:
            status = read_runtime_json(self.forward / "main-runtime-status.json")
            if status.get("state") == "maintenance" and status.get("source_revision") == target["revision"] and began <= datetime.fromisoformat(status["updated_at"]) <= datetime.now(UTC):
                break
            if time.monotonic() >= reload_deadline:
                raise RuntimeError("Supervisor has not loaded the fixed runtime revision")
            time.sleep(0.5)
        run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(self.control / "apply_single_revision.ps1"), "-Action", "StartServices",
             "-RuntimeRoot", str(self.runtime), "-RepositoryRoot", str(self.repository)], cwd=self.control)
        deadline = time.monotonic() + 300
        last_error = "No business observation yet"
        while time.monotonic() < deadline:
            try:
                result = check_publication(local_revision=target["revision"], worker_revision=target["worker_revision"],
                                           worker_version=target["worker_version"], started_after=began,
                                           sync_status=self.forward / "dashboard-sync-status.json")
                self.record("business_verified", **result)
                return result
            except (ProbeFailure, OSError, ValueError) as error:
                last_error = getattr(error, "code", type(error).__name__)
            time.sleep(5)
        raise RuntimeError(f"Business verification did not succeed: {last_error}")

    def publish(self, target: dict, recovery: dict, scope: str) -> None:
        self.record("publication_started", target=target, recovery=recovery, scope=scope,
                    runtime=str(self.runtime), repository=str(self.repository), control=str(self.control))
        began = self.begin_maintenance()
        try:
            self.apply(target)
            self.verify(target, began)
            atomic_json(self.forward / "main-runtime-desired.json", {"state": "running"})
            self.record("maintenance_completed", outcome="DEPLOYED")
        except Exception as error:
            self.record("publication_failed", error_type=type(error).__name__)
            self.recover(recovery)
            raise RuntimeError("Publication failed; recorded recovery completed") from error

    def recover(self, recovery: dict) -> None:
        began = self.begin_maintenance()
        try:
            self.apply(recovery)
            self.verify(recovery, began)
            atomic_json(self.forward / "main-runtime-desired.json", {"state": "running"})
            self.record("maintenance_completed", outcome="RECOVERED")
        except Exception as error:
            try:
                self.stop_owned_services()
            finally:
                self.record("recovery_failed", error_type=type(error).__name__)
            raise

    def preflight(self, target: dict, recovery: dict, compatibility: Path) -> dict:
        for identity in (target, recovery):
            validate_identity(identity)
        database = database_contract(self.forward / "forward-evidence.sqlite3")
        review_hash = check_compatibility_review(compatibility, target, recovery, database)
        self.control_action("Preflight")
        if self.git("status", "--porcelain", "--untracked-files=normal"):
            raise RuntimeError("Runtime has local changes; they will not be overwritten")
        self.git("fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main")
        controller = self.check_control_source(target["revision"])
        deployment_tool = self.check_deployment_tool(target["revision"])
        for identity in (target, recovery):
            revision = identity["revision"]
            self.git("cat-file", "-e", revision + "^{commit}")
            self.git("merge-base", "--is-ancestor", revision, "origin/main")
            version = self.wrangler("versions", "view", identity["worker_version"], read=True)
            message = version.get("annotations", {}).get("workers/message", "")
            match = re.search(r"(?:main:|release:)([0-9a-f]{40})", message)
            if version.get("id") != identity["worker_version"] or not match or match[1] != identity["worker_revision"]:
                raise RuntimeError("Native Worker artifact does not match the fixed source identity")
        ci = {revision: required_ci(revision, self.repository)
              for revision in sorted({target["revision"], target["worker_revision"]})}
        return {"target": target, "recovery": recovery, "compatibility_review_sha256": review_hash, "database": database,
                "required_ci": ci, "controller": controller, "deployment_tool": deployment_tool, "current_local_revision": self.git("rev-parse", "HEAD"),
                "current_worker_deployment": self.wrangler("deployments", "status", read=True)}

    def check_deployment_tool(self, revision: str) -> dict:
        locked = json.loads(self.git("show", f"{revision}:web/package-lock.json"))
        expected = locked["packages"]["node_modules/wrangler"]["version"]
        installed = json.loads((self.repository / "web/node_modules/wrangler/package.json").read_text(encoding="utf-8"))
        if installed.get("version") != expected:
            raise ValueError("Installed Wrangler does not match the fixed publication source")
        return {"wrangler_version": expected, "source_revision": revision}

    def check_control_source(self, revision: str) -> dict:
        hashes = {}
        for name in CONTROL_FILES:
            expected = run(["git", "-C", str(self.runtime), "show", f"{revision}:scripts/{name}"], cwd=self.runtime, strip=False)
            installed = (self.control / name).read_text(encoding="utf-8")
            if installed != expected:
                raise ValueError(f"Installed controller does not match the tested source: {name}")
            hashes[name] = hashlib.sha256(installed.encode()).hexdigest()
            if name == "publish_single_version.py" and Path(__file__).read_text(encoding="utf-8") != expected:
                raise ValueError("The running publisher differs from the tested source")
            if name == "check_public_health.py" and Path(check_publication.__code__.co_filename).read_text(encoding="utf-8") != expected:
                raise ValueError("The running business probe differs from the tested source")
        return {"revision": revision, "files": hashes}


def validate_identity(target: dict) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", target.get("revision", "")) or not re.fullmatch(r"[0-9a-f]{40}", target.get("worker_revision", "")):
        raise ValueError("Exact local and Worker source revisions are required")
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", target.get("worker_version", "")):
        raise ValueError("A fixed native Worker version is required")


def database_contract(path: Path) -> dict:
    """Read only schema and epoch, not a copy or scan of growing business data."""
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3) as connection:
        connection.execute("PRAGMA query_only=ON")
        schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        epoch = connection.execute("SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'").fetchone()
    if not epoch:
        raise ValueError("Authoritative data epoch is unavailable")
    return {"path": str(path.resolve()), "forward_epoch": epoch[0],
            "schema_sha256": hashlib.sha256(json.dumps(schema, separators=(",", ":")).encode()).hexdigest()}


def check_compatibility_review(path: Path, target: dict, recovery: dict, database: dict) -> str:
    """Consume the existing review artifact; never manufacture a PASS receipt."""
    content = path.read_bytes()
    review = json.loads(content)
    if review.get("target") != target or review.get("recovery") != recovery:
        raise ValueError("Compatibility review does not cover these exact identities")
    if review.get("database") != database:
        raise ValueError("Compatibility review does not cover the current data schema and epoch")
    if any(review.get(key) != "PASS" for key in ("data_compatibility", "interface_compatibility", "recovery_test")):
        raise ValueError("Data/interface compatibility and recovery evidence are required")
    evidence = review.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("Compatibility review must reference its actual evidence")
    for entry in evidence:
        if not isinstance(entry, dict) or not entry.get("path") or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")):
            raise ValueError("Compatibility evidence needs an existing path and SHA256")
        evidence_path = Path(entry["path"])
        if not evidence_path.is_absolute():
            evidence_path = path.parent / evidence_path
        with evidence_path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != entry["sha256"]:
            raise ValueError("Compatibility evidence changed after review")
    return hashlib.sha256(content).hexdigest()


def required_ci(revision: str, cwd: Path) -> list[str]:
    def api(path: str, *, pages: bool = False):
        args = ["gh", "api", "--method", "GET", f"repos/{REPOSITORY}/{path}"]
        if pages:
            args += ["--paginate", "--slurp"]
        return json.loads(run(args, cwd=cwd))

    rules = api("rules/branches/main")
    required = [check for rule in rules if rule.get("type") == "required_status_checks"
                for check in rule.get("parameters", {}).get("required_status_checks", [])]
    if not required:
        raise RuntimeError("The live required CI contract is unavailable")
    # Retain the explicitly required Windows and security assurance even when
    # repository branch rules do not list those jobs as merge requirements.
    for name in ("Windows runtime contracts", "Analyze (actions)", "Analyze (csharp)",
                 "Analyze (javascript-typescript)", "Analyze (python)"):
        if not any(check["context"] == name for check in required):
            required.append({"context": name})
    pages = api(f"commits/{revision}/check-runs?filter=latest&per_page=100", pages=True)
    runs = [item for page in pages for item in page.get("check_runs", [])]
    passed = []
    for check in required:
        matches = [item for item in runs if item.get("name") == check["context"] and item.get("head_sha") == revision
                   and (check.get("integration_id") in (None, -1) or item.get("app", {}).get("id") == check["integration_id"])]
        latest = max(matches, key=lambda item: item["id"], default={})
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            raise RuntimeError(f"Required CI has not passed on the fixed source: {check['context']}")
        passed.append(check["context"])
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "deploy", "recover"))
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--release", type=Path, help="fixed target and recovery identity JSON")
    parser.add_argument("--compatibility-review", type=Path)
    parser.add_argument("--maintenance-scope")
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args()
    publication = Publication(args.runtime, args.repository, args.control, args.journal)
    if not publication.forward.is_dir():
        parser.error("The existing runtime state directory is required")
    if args.action != "recover" and not (args.release and args.compatibility_review):
        parser.error("Fixed identities and the existing compatibility review are required")
    if args.action in {"deploy", "recover"} and not args.maintenance_scope:
        parser.error("An explicitly authorized maintenance scope is required")
    try:
        with publication_lock(publication.forward):
            if args.action == "recover":
                first = None
                checked = None
                with args.journal.open(encoding="utf-8") as handle:
                    for line in handle:
                        row = json.loads(line)
                        if row["event"] == "preflight_verified":
                            checked = row
                        if row["event"] == "publication_started":
                            first = row
                            break
                if first is None or checked is None:
                    raise ValueError("No fully recorded publication start exists")
                if Path(first["runtime"]).resolve() != publication.runtime or Path(first["repository"]).resolve() != publication.repository:
                    raise ValueError("Recovery journal belongs to another runtime")
                recovery = first["recovery"]
                validate_identity(recovery)
                publication.check_control_source(checked["controller"]["revision"])
                publication.check_deployment_tool(checked["controller"]["revision"])
                publication.control_action("Preflight")
                publication.record("recovery_requested", scope=args.maintenance_scope)
                publication.recover(recovery)
            else:
                release = json.loads(args.release.read_text(encoding="utf-8"))
                facts = publication.preflight(release["target"], release["recovery"], args.compatibility_review)
                if args.action == "preflight":
                    print(json.dumps(facts))
                else:
                    if args.journal.exists():
                        raise ValueError("Use a new journal for publication; recover uses the existing journal")
                    publication.record("preflight_verified", **facts)
                    publication.publish(release["target"], release["recovery"], args.maintenance_scope)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Single publication stopped: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
