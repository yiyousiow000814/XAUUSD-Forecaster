from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3
from pathlib import Path
import hashlib
import os

import pytest

from scripts import publish_single_version as release

TARGET = {"revision": "a" * 40, "worker_revision": "a" * 40,
          "worker_version": "00000000-0000-4000-8000-000000000001"}
RECOVERY = {"revision": "b" * 40, "worker_revision": "b" * 40,
            "worker_version": "00000000-0000-4000-8000-000000000002"}


@pytest.mark.parametrize("failure", [None, "deployment", "health", "recovery"])
def test_publication_failure_uses_explicit_recovery_and_keeps_latest_data(tmp_path, failure):
    facts = tmp_path / "authoritative-facts.txt"
    facts.write_text("original")
    journal = tmp_path / "maintenance.jsonl"
    (tmp_path / ".local/forward").mkdir(parents=True)

    class Fixture(release.Publication):
        def begin_maintenance(self):
            self.record("maintenance_entered")
            return datetime.now(UTC)

        def stop_owned_services(self):
            self.record("fixture_stopped")
            return datetime.now(UTC)

        def apply(self, target):
            self.record("fixture_code_applied", **target)
            if target == TARGET:
                facts.write_text("new authoritative fact")
                if failure == "deployment":
                    raise RuntimeError("fixture provider failure")
            elif failure == "recovery":
                raise RuntimeError("fixture recovery failure")

        def verify(self, target, began):
            if target == TARGET and failure in {"health", "recovery"}:
                raise RuntimeError("fixture health failure")
            self.record("business_verified", fixture=True, revision=target["revision"])

        def control_action(self, action):
            self.record("fixture_control", action=action)

    publisher = Fixture(tmp_path, tmp_path, tmp_path, journal)
    if failure:
        with pytest.raises(RuntimeError):
            publisher.publish(TARGET, RECOVERY, "isolated fixture")
    else:
        publisher.publish(TARGET, RECOVERY, "isolated fixture")
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    assert facts.read_text() == "new authoritative fact"
    assert events[0]["recovery"] == RECOVERY
    if failure == "recovery":
        assert events[-1]["event"] == "recovery_failed"
        assert not any(row.get("outcome") == "RECOVERED" for row in events)
    else:
        assert events[-1]["outcome"] == ("RECOVERED" if failure else "DEPLOYED")


def test_compatibility_binds_exact_code_and_live_schema_without_scanning_data(tmp_path):
    database = tmp_path / "facts.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE runtime_metadata(key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO runtime_metadata VALUES('FORWARD_EPOCH','2026-09-01T00:00:00+00:00')")
        connection.execute("CREATE TABLE facts(value TEXT)")
        connection.execute("INSERT INTO facts VALUES('preserved')")
    contract = release.database_contract(database)
    review = tmp_path / "compatibility.json"
    evidence = tmp_path / "fixture-evidence.txt"
    evidence.write_text("isolated fixture only")
    review.write_text(json.dumps({"target": TARGET, "recovery": RECOVERY, "database": contract,
                                 "data_compatibility": "PASS", "interface_compatibility": "PASS",
                                 "recovery_test": "PASS", "evidence": [{"path": evidence.name, "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]}))
    assert len(release.check_compatibility_review(review, TARGET, RECOVERY, contract)) == 64
    evidence.write_text("changed fixture evidence")
    with pytest.raises(ValueError, match="changed after review"):
        release.check_compatibility_review(review, TARGET, RECOVERY, contract)
    evidence.write_text("isolated fixture only")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO facts VALUES('newer fact')")
    assert release.database_contract(database) == contract
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE facts ADD COLUMN new_field TEXT")
    with pytest.raises(ValueError, match="current data schema"):
        release.check_compatibility_review(review, TARGET, RECOVERY, release.database_contract(database))
    with pytest.raises(ValueError, match="exact identities"):
        release.check_compatibility_review(review, RECOVERY, TARGET, contract)


def test_native_worker_command_has_one_fixed_version_at_full_traffic(tmp_path, monkeypatch):
    commands = []

    def native(args, **kwargs):
        commands.append(args)
        return "{}"

    monkeypatch.setattr(release, "run", native)
    publisher = release.Publication(tmp_path, tmp_path, tmp_path, tmp_path / "journal.jsonl")
    monkeypatch.setattr(publisher, "git", lambda *args: TARGET["revision"])
    publisher.apply(TARGET)
    assert commands[0][commands[0].index("-Revision") + 1] == TARGET["revision"]
    assert commands[1][2:5] == ["versions", "deploy", TARGET["worker_version"] + "@100"]
    assert "@0" not in " ".join(commands[1])
    assert commands[1][-2:] == ["--name", "aurum-signal-room"]


def test_maintenance_starts_a_missing_supervisor_with_stop_already_persisted(tmp_path, monkeypatch):
    forward = tmp_path / ".local/forward"
    forward.mkdir(parents=True)
    publisher = release.Publication(tmp_path, tmp_path, tmp_path, tmp_path / "journal.jsonl")
    actions = []

    def control(action):
        actions.append(action)
        if action == "Stop":
            release.atomic_json(forward / "main-runtime-desired.json", {"state": "stopped"})
        if action == "SupervisorStart":
            assert json.loads((forward / "main-runtime-desired.json").read_text())["state"] == "stopped"
            release.atomic_json(forward / "main-runtime-status.json", {
                "state": "stopped", "updated_at": datetime.now(UTC).isoformat(), "services": {"collector": []},
            })

    monkeypatch.setattr(publisher, "control_action", control)
    publisher.begin_maintenance()
    assert actions == ["SupervisorStart"]
    assert json.loads((forward / "main-runtime-desired.json").read_text())["state"] == "maintenance"


def test_controller_source_binding_rejects_old_green_sha_and_local_edits(tmp_path):
    root = tmp_path / "source"
    control = tmp_path / "installed"
    root.mkdir()
    control.mkdir()
    def git(*args):
        return release.run(["git", "-C", str(root), *args], cwd=root)
    git("init", "-b", "main")
    git("config", "user.name", "Publication fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("commit", "--allow-empty", "-m", "old source")
    old = git("rev-parse", "HEAD")
    (root / "scripts").mkdir()
    for name in release.CONTROL_FILES:
        content = (Path(release.__file__).parent / name).read_bytes()
        (root / "scripts" / name).write_bytes(content)
        (control / name).write_bytes(content)
    git("add", ".")
    git("commit", "-m", "fixed controller artifact")
    revision = git("rev-parse", "HEAD")
    publisher = release.Publication(root, root, control, tmp_path / "journal.jsonl")
    assert publisher.check_control_source(revision)["revision"] == revision
    with pytest.raises(RuntimeError):
        publisher.check_control_source(old)
    with (control / "main_runtime.ps1").open("a") as handle:
        handle.write("# unpublished edit\n")
    with pytest.raises(ValueError, match="tested source"):
        publisher.check_control_source(revision)


@pytest.mark.skipif(os.name != "nt", reason="actual PowerShell maintenance boundary")
def test_actual_apply_entry_requires_maintenance_and_recovers_code_only(tmp_path):
    root = tmp_path / "runtime with spaces"
    root.mkdir()
    def git(*args):
        return release.run(["git", "-C", str(root), *args], cwd=root)
    git("init", "-b", "main")
    git("config", "user.name", "Publication fixture")
    git("config", "user.email", "fixture@example.invalid")
    (root / ".gitignore").write_text(".local/\n")
    (root / "pyproject.toml").write_text("# unchanged fixture dependencies\n")
    (root / "version.txt").write_text("first")
    (root / "scripts").mkdir()
    (root / "scripts/windows-service-launch-contract.json").write_text(json.dumps({"schema_version": "windows-service-launch-contract-v1", "services": []}))
    git("add", ".")
    git("commit", "-m", "first")
    first = git("rev-parse", "HEAD")
    (root / "version.txt").write_text("second")
    git("commit", "-am", "second")
    second = git("rev-parse", "HEAD")
    git("checkout", "--detach", first)
    forward = root / ".local/forward"
    (forward / "logs").mkdir(parents=True)
    (forward / "main-installed-dependencies.sha256").write_text(hashlib.sha256((root / "pyproject.toml").read_bytes()).hexdigest().upper())
    fact = forward / "authoritative.txt"
    fact.write_text("preserved")
    desired = forward / "main-runtime-desired.json"
    release.atomic_json(desired, {"state": "stopped"})
    control = Path(release.__file__).parent
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(control / "apply_single_revision.ps1"),
               "-RuntimeRoot", str(root), "-RepositoryRoot", str(root), "-Revision"]
    with pytest.raises(RuntimeError):
        release.run([*command, second], cwd=root)
    assert git("rev-parse", "HEAD") == first
    release.atomic_json(desired, {"state": "maintenance"})
    assert json.loads(release.run([*command, second], cwd=root))["revision"] == second
    fact.write_text("new authoritative fact")
    assert json.loads(release.run([*command, first], cwd=root))["revision"] == first
    assert fact.read_text() == "new authoritative fact"


def test_publication_tool_matches_fixed_source_before_provider_calls(tmp_path, monkeypatch):
    package = tmp_path / "web/node_modules/wrangler/package.json"
    package.parent.mkdir(parents=True)
    publisher = release.Publication(tmp_path, tmp_path, tmp_path, tmp_path / "journal.jsonl")
    monkeypatch.setattr(publisher, "git", lambda *args: json.dumps({
        "packages": {"node_modules/wrangler": {"version": "4.129.0"}}}))
    package.write_text(json.dumps({"version": "4.123.0"}))
    with pytest.raises(ValueError, match="Installed Wrangler"):
        publisher.check_deployment_tool(TARGET["revision"])
    package.write_text(json.dumps({"version": "4.129.0"}))
    assert publisher.check_deployment_tool(TARGET["revision"])["wrangler_version"] == "4.129.0"


@pytest.mark.skipif(os.name != "nt", reason="real Windows cross-runtime byte lock")
def test_operator_command_cannot_compete_with_publication_or_exit_maintenance(tmp_path):
    forward = tmp_path / ".local/forward"
    forward.mkdir(parents=True)
    desired = forward / "main-runtime-desired.json"
    desired.write_text(json.dumps({"state": "stopped"}))
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", str(Path(release.__file__).parent / "xauusd_single_runtime.ps1"),
               "-Action", "Stop", "-RuntimeRoot", str(tmp_path)]
    with release.publication_lock(forward):
        with pytest.raises(RuntimeError):
            release.run(command, cwd=tmp_path)
        assert json.loads(desired.read_text())["state"] == "stopped"
    desired.write_text(json.dumps({"state": "maintenance"}))
    with pytest.raises(RuntimeError):
        release.run(command, cwd=tmp_path)
    assert json.loads(desired.read_text())["state"] == "maintenance"
    desired.write_text(json.dumps({"state": "running"}))
    release.run(command, cwd=tmp_path)
    assert json.loads(desired.read_text())["state"] == "stopped"
